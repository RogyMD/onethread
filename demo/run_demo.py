"""Scripted demonstration of onethread with the orchestrator in your agent
window.

SIMULATED: the orchestrator is played by this script (it runs the same `ot`
commands Claude or Codex would run for you), task tabs are not opened (the
command is printed), and every worker is the fake agent: a small local
process that commits a file. The rest is the real code: store, schedulers,
worktrees, engine, supervisor, leases, confirmations, restart recovery.

Run:  python3 demo/run_demo.py
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import time
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from onethread import cli  # noqa: E402
from onethread.agents.fake import FakeAgent  # noqa: E402
from onethread.engine import Engine  # noqa: E402
from onethread.store import Store  # noqa: E402


class Demo:
    def __init__(self, quiet=False):
        self.quiet = quiet
        self.tmp = tempfile.TemporaryDirectory()
        root = os.path.realpath(self.tmp.name)
        self.home = os.path.join(root, "home")
        os.makedirs(self.home)
        self.repos = [self._repo(root, n) for n in ("app-a", "app-b", "shared-lib")]
        self.orphans = []                       # worker handles from before a restart
        self.open()

    def _repo(self, root, name):
        path = os.path.join(root, name)
        os.makedirs(path)
        for args in (["init", "-q", "-b", "main"], ["config", "user.email", "demo@example.invalid"],
                     ["config", "user.name", "Demo"]):
            subprocess.run(["git", *args], cwd=path, check=True)
        with open(os.path.join(path, "README.md"), "w") as f:
            f.write(f"# {name}\n")
        subprocess.run(["git", "add", "."], cwd=path, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=path, check=True)
        return path

    def open(self):
        self.store = Store(os.path.join(self.home, "state.db"))
        self.engine = Engine(self.store, self.home, {"fake": FakeAgent(delay=1)})

    def show(self, text=""):
        if not self.quiet:
            print(text)

    def confirm(self, question):
        self.show(f"  ? {question} [y/N] y   (the owner answers)")
        return True

    def session(self, argv, cwd, env):
        self.show("  (the owner talks with the agent in its tab, then closes it)")
        return 0

    def ot(self, *args, note=""):
        """Run an `ot` command the way the orchestrator agent would."""
        self.show(f"\n$ ot {' '.join(args)}" + (f"      # {note}" if note else ""))
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            cli.main(list(args), home=self.home, confirm=self.confirm, ensure=lambda h: None,
                     open_tab=lambda cmd: False, run_session=self.session)
        text = (out.getvalue() + err.getvalue()).rstrip()
        for line in text.splitlines():
            self.show(f"  {line}")
        return text

    def owner(self, text):
        self.show(f"\n> {text}")

    def wait(self, what, cond, timeout=20):
        self.show(f"\n  … time passes ({what}) …")
        end = time.time() + timeout
        while time.time() < end and not cond():
            self.engine.tick()
            time.sleep(0.1)
        assert cond(), f"timed out waiting for: {what}"

    def status(self, tid):
        return self.store.get_task(tid).status

    def restart(self):
        self.show("\n  … the engine stops and starts again; workers kept running …")
        self.orphans += self.engine.sup.children.values()
        self.engine.close()
        self.store.close()
        self.open()
        report = self.engine.recover()
        self.show(f"  Reconnected to workers: {', '.join(report['reattached']) or 'none'}. "
                  f"Stopped while away: {', '.join(report['finalized']) or 'none'}.")
        return report

    def close(self):
        for row in self.store.q("SELECT pid FROM runs WHERE open=1"):
            try:
                os.killpg(row["pid"], 9)
            except OSError:
                pass
        for proc in [*self.engine.sup.children.values(), *self.orphans]:
            proc.kill()
            proc.wait()
        self.engine.close()
        self.store.close()
        self.tmp.cleanup()


def main(quiet=False):
    d = Demo(quiet)
    log = {}
    try:
        d.show("onethread demo (SIMULATED agents and tabs, real orchestration). Capacity: 2.")
        for path in d.repos:
            d.ot("repo", "add", path)
        d.ot("config", "set", "capacity", "2")
        d.store.set_meta("orchestrator", "fake")
        d.ot("me", "king")
        d.ot("cast", "set", json.dumps({            # what the orchestrator writes for a king
            "address": "Your Majesty",
            "right_hand": {"name": "Chancellor", "style": "dry, loyal, precise"},
            "members": [{"name": n, "style": st} for n, st in (
                ("Sir Lancelot", "bold"), ("Sir Gawain", "earnest"), ("Merlin", "cryptic"),
                ("Sir Percival", "patient"), ("Sir Galahad", "pure-hearted"),
                ("Sir Kay", "blunt"))]}))
        log["cast"] = d.ot("cast")

        # 1. Five tasks across three repos, said in one message.
        d.owner("Five tasks: auth for app-a, a settings bug and payments research for app-b, "
                "deps for shared-lib, and a profile page after auth.")
        added = [d.ot("add", "app-a", "Implement authentication", "-p", "high", "--desc", "[fake:delay=2]"),
                 d.ot("add", "app-b", "Fix settings UI bug"),
                 d.ot("add", "app-b", "Research payments API", "-p", "low"),
                 d.ot("add", "shared-lib", "Update dependencies", "--desc", "[fake:delay=30]"),
                 d.ot("add", "app-a", "Profile page", "-p", "low", "--after", "T1")]
        log["added"] = "\n".join(added)
        d.owner("Start deps and auth now.")
        d.ot("start", "T4")
        d.ot("start", "T1")
        d.engine.tick()
        assert d.status("T1") == "running" and d.status("T4") == "running"

        # 2. Switch into a task by talking: a new tab with that task's session.
        d.owner("Let me talk to the settings bug in app-b.")
        log["open"] = d.ot("open", "settings", "bug", "app-b")
        assert d.status("T2") == "with_you"
        log["session"] = d.ot("session", "T2", note="what the new tab runs")
        assert d.status("T2") == "parked"

        # 3. Urgent sixth task while both workers are busy.
        d.owner("Login crashes on launch in app-a. ASAP.")
        d.ot("add", "app-a", "Fix login crash on launch", "-p", "urgent", "--desc", "[fake:delay=6]")
        d.ot("start", "T6")
        d.engine.tick()
        log["preempt_offer"] = d.ot("hook", "prompt", note="added to the owner's next message")
        assert 'Pause Sir Percival: \\"Update dependencies\\"' in log["preempt_offer"], log["preempt_offer"]
        d.owner("Yes, pause the deps update.")
        log["pause"] = d.ot("pause", "T4")
        d.engine.tick()
        assert d.status("T4") == "paused" and d.status("T6") == "running"

        # 4. Hand the settings bug back; take over the urgent task mid-work.
        d.owner("Hand the settings bug back to its agent.")
        d.ot("handback", "T2")
        d.owner("Actually, let me look at the login crash myself.")
        log["takeover"] = d.ot("open", "T6")
        assert d.status("T6") == "with_you"
        d.ot("session", "T6", note="what the new tab runs")
        d.ot("handback", "T6")

        # 5. Auth finishes in the background; it is offered at the next message.
        d.wait("T1's worker finishes", lambda: d.status("T1") == "review")
        log["review_offer"] = d.ot("hook", "prompt")
        d.ot("inbox")

        # 6. Restart in the middle; state stays consistent.
        log["restart"] = d.restart()
        d.ot("status")

        # 7. Review later, accept.
        d.wait("T2 and T6 finish", lambda: d.status("T2") == "review" and d.status("T6") == "review")
        d.owner("Show me auth.")
        log["review"] = d.ot("review", "T1")
        log["accept"] = d.ot("accept", "T1")
        d.ot("resume", "T4")
        d.ot("status")
        log["final"] = {t.id: t.status for t in d.store.list_tasks()}
        log["open_runs"] = [r["task"] for r in d.store.q("SELECT task FROM runs WHERE open=1")]
        log["runs_per_task"] = {t: len(d.store.q("SELECT id FROM runs WHERE task=?", (t,)))
                                for t in log["final"]}
        log["commits_T1"] = subprocess.run(
            ["git", "log", "--format=%s", d.store.get_task("T1").branch],
            cwd=d.repos[0], capture_output=True, text=True).stdout
        d.show("\nDemo finished: every step above ran through the real `ot` commands, "
               "engine, supervisor and store.")
    finally:
        d.close()
    return log


if __name__ == "__main__":
    main()
