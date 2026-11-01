import fcntl
import os
import signal
import subprocess
import sys
import time

from onethread import attention, engine
from onethread.agents.fake import FakeAgent
from onethread.procs import process_start
from tests.helpers import TempHomeCase, make_repo


class EngineTest(TempHomeCase):
    def setUp(self):
        super().setUp()
        self.store.add_repo(make_repo(self.tmp, "app-a"))
        self.store.set_meta("orchestrator", "fake")
        self.engine = engine.Engine(self.store, self.home, {"fake": FakeAgent(delay=0.1)})

    def tearDown(self):
        for proc in self.engine.sup.children.values():
            proc.kill()
            proc.wait()
        self.engine.close()
        super().tearDown()

    def test_tick_starts_queued_worker(self):
        t = self.store.add_task("app-a", "Auth", description="[fake:delay=5]")
        self.store.set_status(t.id, "queued")
        self.engine.tick()
        self.assertEqual(self.store.get_task(t.id).status, "running")

    def test_tick_runs_standing_orders(self):
        from onethread import finish, worktrees
        from tests.helpers import git
        t = worktrees.ensure(self.store, self.home, self.store.add_task("app-a", "Auth").id)
        with open(os.path.join(t.worktree, "a.txt"), "w") as f:
            f.write("work")
        git(t.worktree, "add", ".")
        git(t.worktree, "commit", "-q", "-m", "work")
        for s in ("queued", "running", "review", "done"):
            self.store.set_status(t.id, s)
        finish.order(self.store, t.id)
        self.engine.tick()
        self.assertIn("merged", [e["kind"] for e in self.store.events(t.id)])
        self.assertIsNone(finish.get_order(self.store, t.id))

    def rounds(self, n):
        """A sleep that fails the test instead of looping forever."""
        left = iter(range(n))
        return lambda s: next(left, None) is not None or self.fail(f"run went on past {n} rounds")

    def other_engine(self):
        eng = engine.Engine(self.store, self.home, {"fake": FakeAgent(delay=0.1)})
        self.addCleanup(eng.close)
        return eng

    def test_code_stamp_moves_when_a_file_changes(self):
        root = os.path.join(self.tmp, "code")
        os.makedirs(os.path.join(root, "onethread", "agents"))
        path = os.path.join(root, "onethread", "agents", "a.py")
        open(path, "w").close()
        before = engine.code_stamp(root)
        os.utime(path, (before + 10, before + 10))
        self.assertEqual(engine.code_stamp(root), before + 10)

    def test_run_returns_restart_after_code_change(self):
        stamps = iter([1.0, 2.0])
        self.engine.stamp, self.engine.code_stamp = 1.0, lambda: next(stamps)
        self.assertEqual(self.engine.run(sleep=self.rounds(10)), "restart")
        self.assertTrue(self.other_engine().run(stop_after_idle=0, sleep=lambda s: None))

    def test_restart_leaves_running_worker_alone(self):
        t = self.store.add_task("app-a", "Auth", description="[fake:delay=30]")
        self.store.set_status(t.id, "queued")
        self.engine.stamp, self.engine.code_stamp = 1.0, lambda: 2.0
        self.assertEqual(self.engine.run(sleep=self.rounds(10)), "restart")
        self.assertEqual(self.store.get_task(t.id).status, "running")
        self.assertTrue(all(p.poll() is None for p in self.engine.sup.children.values()))
        self.assertEqual(self.other_engine().recover()["reattached"], [t.id])

    def test_reap_tabs_parks_dead_launcher(self):
        t = self.store.add_task("app-a", "Auth")
        self.store.set_status(t.id, "with_you")
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        start = process_start(dead.pid)
        dead.wait()
        self.store.set_meta(f"tab_pid:{t.id}", f"{dead.pid}:{start}")
        live = self.store.add_task("app-a", "Live")
        self.store.set_status(live.id, "with_you")
        self.store.set_meta(f"tab_pid:{live.id}", f"{os.getpid()}:{process_start(os.getpid())}")
        self.engine.reap_tabs()
        self.assertEqual(self.store.get_task(t.id).status, "parked")
        self.assertEqual(self.store.get_task(live.id).status, "with_you")
        self.assertEqual([(i["task"], i["kind"]) for i in self.store.open_attention()],
                         [(t.id, "parked")])
        self.assertEqual(self.store.get_meta(f"tab_pid:{t.id}", ""), "")

    def test_tick_cleans_at_most_once_a_minute(self):
        from unittest import mock
        calls, now = [], [1000.0]
        self.store.clock = lambda: now[0]
        with mock.patch("onethread.cleanup.clean", lambda store, home: calls.append(now[0])):
            for t in (1000.0, 1030.0, 1061.0):
                now[0] = t
                self.engine.tick()
        self.assertEqual(calls, [1000.0, 1061.0])

    def test_tab_that_never_started_is_parked_after_a_minute(self):
        t = self.store.add_task("app-a", "Auth")
        self.store.set_status(t.id, "with_you")
        self.engine.reap_tabs()
        self.assertEqual(self.store.get_task(t.id).status, "with_you")
        self.store.clock = lambda: time.time() + 120
        self.engine.reap_tabs()
        self.assertEqual(self.store.get_task(t.id).status, "parked")

    def test_engine_stays_for_a_pending_reminder(self):
        t = self.store.add_task("app-a", "Auth")
        item = self.store.add_attention(t.id, "review_ready")
        attention.postpone(self.store, item, until=self.store.clock() + 600)
        self.assertFalse(self.engine.idle())

    def test_tick_records_notices(self):
        self.engine.tick()                                   # primes
        t = self.store.add_task("app-a", "Auth")
        self.store.add_attention(t.id, "blocked", "Which key?")
        self.engine.tick()
        self.assertTrue(any(e["kind"] == "notified" for e in self.store.events(t.id)))

    def test_engine_exits_when_idle(self):
        start = time.time()
        self.assertTrue(self.engine.run(stop_after_idle=0.2, interval=0.05))
        self.assertLess(time.time() - start, 3)

    def test_ensure_running_is_single_instance(self):
        spawned = []
        self.assertTrue(engine.ensure_running(self.home, spawn=lambda: spawned.append(1)))
        with open(engine.lock_path(self.home), "w") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertFalse(engine.ensure_running(self.home, spawn=lambda: spawned.append(2)))
            self.assertFalse(self.engine.run(stop_after_idle=0.1, interval=0.05))
        self.assertEqual(spawned, [1])
