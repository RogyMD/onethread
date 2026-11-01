"""Findings from the whole-branch review, each pinned by a test."""
import io
import os
import signal
import subprocess
import sys
import time
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from onethread import cli, confirm, engine, terminal, worktrees
from onethread.agents.fake import FakeAgent
from onethread.model import TransitionError
from onethread.procs import process_start
from tests.helpers import TempHomeCase, make_repo


class Base(TempHomeCase):
    def setUp(self):
        super().setUp()
        self.store.add_repo(make_repo(self.tmp, "pillbox"))
        self.store.set_meta("orchestrator", "fake")
        self.store.add_task("pillbox", "Medication reminders", description="[fake:delay=30]")
        self.eng = engine.Engine(self.store, self.home, {"fake": FakeAgent()})
        self.tabs, self.ensured = [], []

    def tearDown(self):
        for run in self.store.q("SELECT pid FROM runs WHERE open=1"):
            try:
                os.killpg(run["pid"], signal.SIGKILL)
            except OSError:
                pass
        for proc in self.eng.sup.children.values():
            proc.kill()
            proc.wait()
        self.eng.close()
        super().tearDown()

    def ot(self, *argv, run_session=None):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(list(argv), home=self.home, confirm=lambda q: False,
                            ensure=self.ensured.append,
                            open_tab=lambda cmd: self.tabs.append(cmd) or True,
                            run_session=run_session or (lambda a, c, e: 0))
        return code, out.getvalue(), err.getvalue()

    def open_runs(self, tid="T1"):
        return self.store.q("SELECT pid FROM runs WHERE task=? AND open=1", (tid,))


class TakeoverRaceTest(Base):
    def test_set_status_is_compare_and_set(self):
        self.store.set_status("T1", "queued")
        stale = self.store.get_task("T1")
        self.store.set_status("T1", "with_you")
        real = self.store.get_task
        calls = []

        def first_stale(tid):
            calls.append(tid)
            return stale if len(calls) == 1 else real(tid)
        with mock.patch.object(self.store, "get_task", side_effect=first_stale):
            with self.assertRaises(TransitionError):
                self.store.set_status("T1", "running")
        self.assertEqual(self.store.get_task("T1").status, "with_you")

    def test_take_over_kills_open_runs_whatever_the_status_says(self):
        self.store.set_status("T1", "queued")
        self.eng.tick()
        self.assertEqual(len(self.open_runs()), 1)
        self.store.x("UPDATE tasks SET status='queued' WHERE id='T1'")   # a stale view
        code, _, err = self.ot("open", "T1")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.open_runs(), [])
        self.assertEqual(self.store.get_task("T1").status, "with_you")

    def test_launch_aborts_when_owner_takes_over_during_start(self):
        self.store.set_status("T1", "queued")
        real_ensure = worktrees.ensure

        def owner_switches_in(store, home, tid):
            t = real_ensure(store, home, tid)
            store.x("UPDATE tasks SET status='with_you' WHERE id=?", (tid,))
            return t
        with mock.patch("onethread.supervisor.worktrees.ensure", owner_switches_in):
            self.eng.tick()                         # must not raise
        time.sleep(0.2)
        self.eng.sup.poll()
        self.assertEqual(self.store.get_task("T1").status, "with_you")
        self.assertEqual(self.open_runs(), [])

    def test_tick_survives_errors(self):
        with mock.patch.object(self.eng.sup, "poll", side_effect=RuntimeError("boom")):
            self.eng.tick()                         # logged, not raised

    def test_second_open_of_a_task_in_a_tab_opens_no_second_tab(self):
        self.store.set_status("T1", "with_you")
        me = os.getpid()
        self.store.set_meta("tab_pid:T1", f"{me}:{process_start(me)}")
        code, out, _ = self.ot("open", "T1")
        self.assertEqual(code, 0)
        self.assertEqual(self.tabs, [])
        self.assertIn("already open", out)

    def test_tab_pid_is_recorded_before_the_session_is_prepared(self):
        seen = []
        real = cli._session_id

        def spy(ctx, t, agent):
            seen.append(self.store.get_meta("tab_pid:T1", ""))
            return real(ctx, t, agent)
        with mock.patch("onethread.cli._session_id", spy):
            self.ot("session", "T1")
        self.assertTrue(seen[0].startswith(f"{os.getpid()}:"))


class ConfirmInLobbyTest(Base):
    def test_lobby_env_forces_dialog_even_with_a_tty(self):
        shown = []
        c = confirm.make_confirm(isatty=lambda: True, ask=lambda q: "y",
                                 dialog=lambda q: shown.append(q) or False)
        with mock.patch.dict(os.environ, {"ONETHREAD_LOBBY": "1"}):
            self.assertFalse(c("Cancel T1?"))
        self.assertEqual(shown, ["Cancel T1?"])

    def test_start_marks_the_orchestrator_environment(self):
        launched = []
        with redirect_stdout(io.StringIO()):
            cli.main(["claude"], home=self.home, ensure=lambda h: None, ask=lambda p: "",
                     launch=lambda a, c, e: launched.append(e),
                     limits={"claude": lambda: None, "codex": lambda: None})
        self.assertEqual(launched[0]["ONETHREAD_LOBBY"], "1")
        self.assertTrue(self.store.get_meta("lobby_pid", "").startswith(f"{os.getpid()}:"))


class ResolveTest(Base):
    def test_every_word_must_match(self):
        found = terminal.resolve(self.store, "payments in pillbox")
        self.assertIsInstance(found, list)
        self.assertEqual(terminal.resolve(self.store, "pillbox medication").id, "T1")


class EngineLifecycleTest(Base):
    def test_engine_stop_when_not_running_kills_nothing(self):
        bystander = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        with open(engine.lock_path(self.home), "w") as f:
            f.write(str(bystander.pid))
        self.ot("engine", "stop")
        time.sleep(0.3)
        self.assertIsNone(bystander.poll())
        bystander.kill()
        bystander.wait()

    def test_hook_makes_sure_the_engine_runs(self):
        with redirect_stdout(io.StringIO()), mock.patch("sys.stdin", io.StringIO("{}")):
            cli.main(["hook", "prompt"], home=self.home, ensure=self.ensured.append)
        self.assertEqual(self.ensured, [self.home])

    def test_no_engine_spawned_inside_a_codex_sandbox(self):
        spawned = []
        with mock.patch.dict(os.environ, {"CODEX_SANDBOX": "seatbelt"}):
            self.assertFalse(engine.ensure_running(self.home, spawn=lambda: spawned.append(1)))
        self.assertEqual(spawned, [])

    def test_engine_stays_while_the_orchestrator_lives(self):
        me = os.getpid()
        self.store.set_meta("lobby_pid", f"{me}:{process_start(me)}")
        self.assertFalse(self.eng.idle())
        self.store.set_meta("lobby_pid", "")
        self.assertTrue(self.eng.idle())

    def test_hook_store_waits_at_most_a_second(self):
        from onethread.store import Store
        with mock.patch("onethread.cli.Store", wraps=Store) as made:
            with redirect_stdout(io.StringIO()), mock.patch("sys.stdin", io.StringIO("{}")):
                cli.main(["hook", "prompt"], home=self.home, ensure=lambda h: None)
        self.assertEqual(made.call_args.kwargs.get("timeout"), 1)
