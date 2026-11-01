"""Launches worker agents as detached processes, watches them, and recovers
them after `ot` restarts.

Workers outlive the orchestrator (own session, output to files). A task
leaves `running` only through a schema-valid result; a process that ends
without one is `interrupted`, never done.
"""
import os
import signal
import subprocess
import time

from . import cast, prompts, schemas, worktrees
from .model import first_sentence
from .procs import pid_alive, process_start

RESULT_TO_STATUS = {"done": ("review", "review_ready"),
                    "needs_input": ("blocked", "blocked"),
                    "failed": ("failed", "failed")}


class Supervisor:
    def __init__(self, store, home, agents, env_for=lambda task: {}, leased=(), extra_dirs=()):
        self.store = store
        self.home = home
        self.agents = agents
        self.env_for = env_for          # extra env per task (leases, PATH)
        self.leased = list(leased)              # command patterns that take leases
        self.extra_dirs = list(extra_dirs)      # the only folders outside the worktree workers may write
        self.children = {}              # run id -> Popen, for runs we started
        self.schema_dir = os.path.join(home, "schemas")
        os.makedirs(self.schema_dir, exist_ok=True)
        self.schemas = schemas.write_schema_files(self.schema_dir)

    # launching -----------------------------------------------------------
    def start_work(self, task):
        # Claim the task before anything starts, so a takeover can't slip in unseen.
        self.store.set_status(task.id, "running")
        task = worktrees.ensure(self.store, self.home, task.id)
        task = worktrees.refresh_base(self.store, task.id)
        self.store.close_attention(task.id, {"preempt"})
        repo = self.store.get_repo(task.repo)
        agent = self.agents[task.agent]
        resumed = bool(self.store.q(
            "SELECT 1 FROM runs WHERE task=? AND kind='work'", (task.id,)))
        cast.fill(self.store)               # a worker gets a name when one is free
        task = self.store.get_task(task.id)
        prompt = prompts.work_prompt(task, repo, resumed=resumed,
                                     persona=cast.task_persona(self.store, task))
        n = len(self.store.q("SELECT id FROM runs WHERE task=?", (task.id,))) + 1
        run_dir = os.path.join(self.home, "runs", task.id, str(n))
        os.makedirs(run_dir, exist_ok=True)
        out_path = os.path.join(run_dir, "result.json")
        inv = agent.invocation(mode="work", cwd=task.worktree, prompt=prompt,
                               session_id=task.session_id,
                               schema_path=self.schemas["result"], out_path=out_path,
                               extra_dirs=self.extra_dirs, test_cmd=repo.get("test_cmd", ""),
                               leased=self.leased)
        with open(os.path.join(run_dir, "prompt.txt"), "w") as f:
            f.write(prompt)
        env = dict(os.environ, **inv.env, **self.env_for(task))
        log_path = os.path.join(run_dir, "log.jsonl")
        with open(os.path.join(run_dir, "prompt.txt")) as stdin, \
                open(log_path, "w") as log, \
                open(os.path.join(run_dir, "stderr.log"), "w") as err:
            proc = subprocess.Popen(inv.argv, cwd=inv.cwd, stdin=stdin, stdout=log,
                                    stderr=err, env=env, start_new_session=True)
        cur = self.store.x(
            "INSERT INTO runs (task, kind, pid, pid_start, started, log_path, out_path)"
            " VALUES (?,?,?,?,?,?,?)",
            (task.id, "work", proc.pid, process_start(proc.pid), self.store.clock(),
             log_path, out_path))
        self.children[cur.lastrowid] = proc
        self.store.update_task(task.id, followups=[],
                               session_id=inv.session_id or task.session_id)
        self.store.log(task.id, "worker_started", {"pid": proc.pid, "agent": agent.name})
        if self.store.get_task(task.id).status != "running":    # the owner took over meanwhile
            self.kill_runs(task.id, grace=2.0)

    # watching --------------------------------------------------------------
    def _open_runs(self):
        return [dict(r) for r in self.store.q("SELECT * FROM runs WHERE open=1")]

    def _alive(self, run):
        proc = self.children.get(run["id"])
        if proc is not None:
            return proc.poll() is None
        return pid_alive(run["pid"], run["pid_start"])

    def poll(self):
        """Finalize runs whose process has ended. Returns finished task ids."""
        finished = []
        for run in self._open_runs():
            if self._alive(run):
                self._learn_session_id(run)
                continue
            self._finalize(run)
            finished.append(run["task"])
        return finished

    def recover(self):
        """After a restart: reattach live workers, finalize dead ones."""
        report = {"reattached": [], "finalized": []}
        for run in self._open_runs():
            if self._alive(run):
                report["reattached"].append(run["task"])
            else:
                self._finalize(run)
                report["finalized"].append(run["task"])
        open_tasks = {r["task"] for r in self._open_runs()}
        for task in self.store.list_tasks({"running"}):
            if task.id not in open_tasks:       # running with no process at all
                self.store.set_status(task.id, "interrupted")
                self.store.add_attention(task.id, "interrupted")
                report["finalized"].append(task.id)
        self.store.log(None, "recovered", report)
        return report

    def _read(self, path):
        if path and os.path.exists(path):
            with open(path, errors="replace") as f:
                return f.read()
        return ""

    def _learn_session_id(self, run):
        task = self.store.get_task(run["task"])
        if not task.session_id:
            sid, _ = self.agents[task.agent].parse(self._read(run["log_path"]), "")
            if sid:
                self.store.update_task(task.id, session_id=sid)

    def _finalize(self, run):
        """Apply a finished run's result, then close the run. Any surprise
        while reading it means `interrupted`, never a task stuck running."""
        try:
            self._apply_result(run)
        except Exception as exc:
            task = self.store.get_task(run["task"])
            if task.status == "running":
                self.store.set_status(task.id, "interrupted")
                self.store.add_attention(task.id, "interrupted", f"Could not read the result: {exc}")
        finally:
            self.store.x("UPDATE runs SET open=0, ended=? WHERE id=?",
                         (self.store.clock(), run["id"]))
            self.children.pop(run["id"], None)

    def _apply_result(self, run):
        task = self.store.get_task(run["task"])
        if task.status != "running":            # paused or cancelled by the user
            return
        sid, payload = self.agents[task.agent].parse(
            self._read(run["log_path"]), self._read(run["out_path"]))
        if sid and not task.session_id:
            self.store.update_task(task.id, session_id=sid)
        if payload is None or schemas.validate(payload, schemas.RESULT):
            stderr = self._read(os.path.join(os.path.dirname(run["log_path"]), "stderr.log"))
            raw = self._read(run["log_path"]) + "\n" + stderr
            note = self.agents[task.agent].explain(raw, log_path=run["log_path"])
            if self.agents[task.agent].lost_session(raw):
                self.store.update_task(task.id, session_id="")
                note += " Its session never started, so the next start begins a fresh session."
            self.store.set_status(task.id, "interrupted")
            self.store.add_attention(task.id, "interrupted", note)
            return
        if payload["status"] == "done":
            payload = self._settle_changes(task, payload)
        status, kind = RESULT_TO_STATUS[payload["status"]]
        kept = {k: task.result[k] for k in ("pr", "published") if task.result.get(k)}
        self.store.update_task(task.id, result={**kept, **payload},
                               summary=first_sentence(payload["summary"]))
        self.store.set_status(task.id, status)
        note = {"blocked": payload["question"], "failed": payload["summary"]}.get(kind, "")
        self.store.add_attention(task.id, kind, note)

    def _settle_changes(self, task, payload):
        """Commit work the agent left uncommitted (a sandbox may block git
        commit), and say so plainly when the branch has no changes at all."""
        payload = dict(payload, commits=list(payload["commits"]))
        if not task.worktree or not os.path.isdir(task.worktree):
            return payload
        worktrees.check(task.worktree, self.store.get_repo(task.repo)["path"])
        if worktrees.is_dirty(task.worktree):
            sha = worktrees.commit_all(task.worktree, f"onethread: uncommitted work from {task.id}'s worker")
            if sha:
                payload["commits"].append(sha)
                payload["summary"] += f" (onethread committed leftover changes as {sha}.)"
            else:
                payload["summary"] += " Note: changes are left uncommitted in the worktree."
        elif not worktrees.has_commits(task.worktree, task.base_branch):
            payload["summary"] += " Note: no changes were committed on this branch."
        return payload

    # stopping ----------------------------------------------------------------
    def stop(self, task_id, new_status, grace=10.0):
        """Move the task to paused/cancelled first, then stop its process
        group: SIGINT, wait `grace` seconds, then SIGTERM."""
        self.store.set_status(task_id, new_status)
        return self.kill_runs(task_id, grace)

    def kill_runs(self, task_id, grace=10.0):
        """Stop every open run of the task, whatever its status says."""
        for run in [r for r in self._open_runs() if r["task"] == task_id]:
            for sig, wait in ((signal.SIGINT, grace), (signal.SIGTERM, grace), (signal.SIGKILL, 5)):
                if not self._alive(run):
                    break
                try:
                    os.killpg(run["pid"], sig)
                except (ProcessLookupError, PermissionError):
                    break
                end = time.time() + wait
                while time.time() < end and self._alive(run):
                    time.sleep(0.05)
            if self._alive(run):                # never close a run whose process lives on
                self.store.log(task_id, "stop_failed", {"pid": run["pid"]})
                continue
            self._finalize(run)
        return True

    def pause(self, task_id, grace=10.0):
        return self.stop(task_id, "paused", grace)
