import os
import signal
import subprocess
import time
import unittest

from onethread import cast, scheduler, worktrees
from onethread.agents.fake import FakeAgent
from onethread.supervisor import Supervisor
from tests.helpers import TempHomeCase, git, make_repo


def wait_until(cond, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


class SupervisorTest(TempHomeCase):
    def setUp(self):
        super().setUp()
        self.store.add_repo(make_repo(self.tmp, "app-a"))
        self.sup = Supervisor(self.store, self.home, {"fake": FakeAgent(delay=0.1)})
        self.children = []

    def restart(self):
        """Simulate an orchestrator restart: new store and supervisor."""
        self.children += list(self.sup.children.values())
        self.store = self.reopen()
        self.sup = Supervisor(self.store, self.home, {"fake": FakeAgent()})
        return self.sup

    def tearDown(self):
        for run in self.store.q("SELECT pid FROM runs WHERE open=1"):
            try:
                os.killpg(run["pid"], signal.SIGKILL)
            except OSError:
                pass
        for proc in self.children + list(self.sup.children.values()):
            proc.kill()
            proc.wait()
        super().tearDown()

    def start(self, title, description=""):
        t = self.store.add_task("app-a", title, description=description, agent="fake")
        self.store.set_status(t.id, "queued")
        self.sup.start_work(self.store.get_task(t.id))
        return t.id

    def settle(self, tid, timeout=10.0):
        ok = wait_until(lambda: (self.sup.poll() or True)
                        and self.store.get_task(tid).status != "running", timeout)
        self.assertTrue(ok, "worker never finished")
        return self.store.get_task(tid)

    def kinds(self):
        return [(i["task"], i["kind"]) for i in self.store.open_attention()]

    def test_valid_result_moves_to_review_and_asks_for_attention(self):
        tid = self.start("Add login")
        self.assertEqual(self.store.get_task(tid).status, "running")
        self.assertTrue(self.store.get_task(tid).session_id)
        task = self.settle(tid)
        self.assertEqual(task.status, "review")
        self.assertEqual(task.result["status"], "done")
        self.assertIn(task.result["commits"][0], git(task.worktree, "log", "--format=%h"))
        self.assertEqual(self.kinds(), [(tid, "review_ready")])
        self.assertTrue(task.summary)

    def test_needs_input_becomes_blocked_with_question(self):
        tid = self.start("Pick provider", "[fake:needs_input]")
        self.assertEqual(self.settle(tid).status, "blocked")
        item = self.store.open_attention()[0]
        self.assertEqual((item["kind"], item["note"]), ("blocked", "Which option should I use?"))

    def test_failed_result_becomes_failed(self):
        tid = self.start("Break", "[fake:fail]")
        self.assertEqual(self.settle(tid).status, "failed")
        self.assertEqual(self.kinds(), [(tid, "failed")])

    def test_crash_without_result_is_interrupted_not_done(self):
        tid = self.start("Crash", "[fake:crash]")
        self.assertEqual(self.settle(tid).status, "interrupted")
        self.assertEqual(self.kinds(), [(tid, "interrupted")])

    def test_exit_zero_without_result_is_interrupted(self):
        tid = self.start("Quiet", "[fake:noresult]")
        self.assertEqual(self.settle(tid).status, "interrupted")
        note = self.store.open_attention()[0]["note"]
        self.assertTrue(note.startswith("Fake agent gave no readable reply (log: "), note)

    def test_lost_session_is_forgotten_so_the_next_start_is_fresh(self):
        tid = self.start("Lost", "[fake:lost]")
        t = self.settle(tid)
        self.assertEqual((t.status, t.session_id), ("interrupted", ""))
        self.assertIn("fresh session", self.store.open_attention()[0]["note"])

    def test_new_result_keeps_the_pull_request_and_published_link(self):
        t = self.store.add_task("app-a", "Again", agent="fake")
        self.store.update_task(t.id, result={"pr": "https://example.com/pr/1",
                                             "published": "https://example.com/app"})
        self.store.set_status(t.id, "queued")
        self.sup.start_work(self.store.get_task(t.id))
        result = self.settle(t.id).result
        self.assertEqual(result["pr"], "https://example.com/pr/1")
        self.assertEqual(result["published"], "https://example.com/app")
        self.assertEqual(result["status"], "done")

    def test_redirected_worktree_is_never_run_by_onethread(self):
        t = worktrees.ensure(self.store, self.home, self.store.add_task("app-a", "R", agent="fake").id)
        evil = os.path.join(self.tmp, "evil")
        git(self.tmp, "init", "-q", evil)
        git(evil, "config", "core.fsmonitor", f"touch {os.path.join(self.tmp, 'ran')}")
        with open(os.path.join(t.worktree, ".git"), "w") as f:
            f.write(f"gitdir: {os.path.join(evil, '.git')}\n")
        with open(os.path.join(t.worktree, "new.txt"), "w") as f:
            f.write("x")
        payload = {"status": "done", "summary": "s", "question": "", "tests": "", "commits": []}
        with self.assertRaises(ValueError):
            self.sup._settle_changes(t, payload)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "ran")))

    def test_killed_worker_is_interrupted(self):
        tid = self.start("Slow", "[fake:delay=30]")
        run = self.store.q("SELECT pid FROM runs WHERE task=?", (tid,))[0]
        os.killpg(run["pid"], signal.SIGKILL)
        self.assertEqual(self.settle(tid).status, "interrupted")

    def test_restart_reattaches_live_worker_and_does_not_relaunch(self):
        tid = self.start("Slow", "[fake:delay=1.5]")
        report = self.restart().recover()
        self.assertEqual(report["reattached"], [tid])
        launched = []
        scheduler.tick(self.store, lambda t: launched.append(t.id))
        self.assertEqual(launched, [])
        self.assertEqual(self.settle(tid).status, "review")
        self.assertEqual(len(self.store.q("SELECT id FROM runs WHERE task=?", (tid,))), 1)

    def test_restart_after_worker_died_marks_interrupted(self):
        tid = self.start("Slow", "[fake:delay=30]")
        run = self.store.q("SELECT pid FROM runs WHERE task=?", (tid,))[0]
        os.killpg(run["pid"], signal.SIGKILL)
        time.sleep(0.3)
        report = self.restart().recover()
        self.assertEqual(report["finalized"], [tid])
        self.assertEqual(self.store.get_task(tid).status, "interrupted")

    def test_reused_pid_is_treated_as_dead(self):
        tid = self.start("Slow", "[fake:delay=30]")
        run = self.store.q("SELECT id, pid FROM runs WHERE task=?", (tid,))[0]
        os.killpg(run["pid"], signal.SIGKILL)
        other = subprocess.Popen(["sleep", "30"])
        try:
            self.store.x("UPDATE runs SET pid=?, pid_start=? WHERE id=?",
                         (other.pid, "Sat Jan  1 00:00:00 2000", run["id"]))
            self.assertEqual(self.restart().recover()["finalized"], [tid])
            self.assertEqual(self.store.get_task(tid).status, "interrupted")
        finally:
            other.kill()
            other.wait()

    def test_pause_stops_the_process_and_stays_paused(self):
        tid = self.start("Slow", "[fake:delay=30]")
        pid = self.store.q("SELECT pid FROM runs WHERE task=?", (tid,))[0]["pid"]
        self.assertTrue(self.sup.pause(tid, grace=2))
        self.assertEqual(self.store.get_task(tid).status, "paused")
        self.assertTrue(wait_until(lambda: not _alive(pid), 5))
        self.sup.poll()
        self.assertEqual(self.store.get_task(tid).status, "paused")
        self.assertEqual(self.kinds(), [])

    def test_followups_go_into_the_next_prompt_once(self):
        t = self.store.add_task("app-a", "Add login", agent="fake")
        self.store.update_task(t.id, followups=["Use passkeys too"])
        self.store.set_status(t.id, "queued")
        self.sup.start_work(self.store.get_task(t.id))
        run = self.store.q("SELECT log_path FROM runs WHERE task=?", (t.id,))[0]
        with open(os.path.join(os.path.dirname(run["log_path"]), "prompt.txt")) as f:
            self.assertIn("Use passkeys too", f.read())
        self.assertEqual(self.store.get_task(t.id).followups, [])


    def test_worker_prompt_plays_its_cast_member(self):
        cast.set_me(self.store, "Caesar")
        cast.set_cast(self.store, {"address": "", "right_hand": {"name": "Agrippa", "style": ""},
                                   "members": [{"name": "Cicero", "style": "dry"}]})
        tid = self.start("Add login")
        run = self.store.q("SELECT log_path FROM runs WHERE task=?", (tid,))[0]
        with open(os.path.join(os.path.dirname(run["log_path"]), "prompt.txt")) as f:
            text = f.read()
        self.assertIn("You are Cicero", text)
        self.assertIn("Write your result summary in that voice too.", text)

    def test_dependent_task_gets_dependency_work_even_if_planned_early(self):
        from onethread import worktrees
        self.store.add_task("app-a", "Auth", agent="fake")
        self.store.add_task("app-a", "Profile", agent="fake")
        self.store.add_dep("T2", "T1")
        worktrees.ensure(self.store, self.home, "T2")          # planned while T1 not done
        self.store.set_status("T1", "queued")
        self.sup.start_work(self.store.get_task("T1"))
        self.settle("T1")
        self.store.set_status("T1", "done")
        self.store.set_status("T2", "queued")
        self.sup.start_work(self.store.get_task("T2"))
        t2 = self.store.get_task("T2")
        self.assertEqual(t2.base_branch, self.store.get_task("T1").branch)
        self.assertTrue(os.path.exists(os.path.join(t2.worktree, "ot-fake-T1.txt")))

    def test_starting_urgent_task_closes_its_preempt_offer(self):
        t = self.store.add_task("app-a", "Fire", agent="fake", priority="urgent")
        self.store.set_status(t.id, "queued")
        self.store.add_attention(t.id, "preempt", "T9")
        self.sup.start_work(self.store.get_task(t.id))
        self.assertEqual(self.kinds(), [])

    def test_uncommitted_worker_changes_are_committed_by_onethread(self):
        tid = self.start("Edit only", "[fake:nocommit]")
        task = self.settle(tid)
        self.assertEqual(task.status, "review")
        self.assertEqual(git(task.worktree, "status", "--porcelain"), "")
        self.assertTrue(task.result["commits"])
        self.assertIn(task.result["commits"][-1], git(task.worktree, "log", "--format=%h"))

    def test_done_without_any_changes_is_flagged(self):
        tid = self.start("Nothing", "[fake:nochange]")
        task = self.settle(tid)
        self.assertEqual(task.status, "review")
        self.assertIn("no changes", task.result["summary"].lower())

    def test_unreadable_result_still_leaves_running(self):
        tid = self.start("Garbage", "[fake:delay=30]")
        run = self.store.q("SELECT * FROM runs WHERE task=?", (tid,))[0]
        with open(run["out_path"], "wb") as f:
            f.write(b"\xff\xfe not utf8")
        with open(run["log_path"], "ab") as f:
            f.write(b'{"type": "item.completed", "item": "oops"}\n\xff\n')
        os.killpg(run["pid"], signal.SIGKILL)
        self.assertEqual(self.settle(tid).status, "interrupted")
        self.assertEqual(self.store.q("SELECT open FROM runs WHERE task=?", (tid,))[0]["open"], 0)

    def test_pause_kills_a_reattached_worker_that_ignores_signals(self):
        tid = self.start("Slow", "[fake:delay=30]")
        stubborn = subprocess.Popen(["sh", "-c", "trap '' INT TERM; sleep 30"],
                                    start_new_session=True)
        self.children.append(stubborn)
        from onethread.procs import process_start
        time.sleep(0.2)
        self.store.x("UPDATE runs SET pid=?, pid_start=? WHERE task=?",
                     (stubborn.pid, process_start(stubborn.pid), tid))
        self.restart().recover()
        self.sup.pause(tid, grace=0.5)
        self.assertTrue(wait_until(lambda: stubborn.poll() is not None, 5))

def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    out = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)],
                         capture_output=True, text=True).stdout.strip()
    return bool(out) and not out.startswith("Z")


if __name__ == "__main__":
    unittest.main()
