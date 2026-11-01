import io
import os
import signal
import subprocess
from contextlib import redirect_stderr, redirect_stdout

from onethread import cast, cli, prompts, terminal
from onethread.engine import Engine
from onethread.agents.fake import FakeAgent
from tests.helpers import TempHomeCase, make_repo


class OpenTest(TempHomeCase):
    def setUp(self):
        super().setUp()
        self.store.add_repo(make_repo(self.tmp, "pillbox"))
        self.store.add_repo(make_repo(self.tmp, "app-b"))
        self.store.set_meta("orchestrator", "fake")
        self.store.add_task("pillbox", "Medication reminders")
        self.store.add_task("pillbox", "Login screen")
        self.store.add_task("app-b", "Medication export")
        self.tabs, self.sessions = [], []

    def tearDown(self):
        for run in self.store.q("SELECT pid FROM runs WHERE open=1"):
            try:
                os.killpg(run["pid"], signal.SIGKILL)
            except OSError:
                pass
        super().tearDown()

    def ot(self, *argv):
        out, err = io.StringIO(), io.StringIO()

        def no_confirm(q):
            raise AssertionError(f"asked: {q}")
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(list(argv), home=self.home, confirm=no_confirm,
                            ensure=lambda h: None,
                            open_tab=lambda cmd: self.tabs.append(cmd) or True,
                            run_session=self.fake_session)
        return code, out.getvalue(), err.getvalue()

    def fake_session(self, argv, cwd, env):
        self.sessions.append((argv, cwd, env))
        tid = cwd.rsplit(os.sep, 1)[-1]
        self.during = self.store.get_task(tid).status
        return 0

    def test_resolve_by_id_and_words(self):
        self.assertEqual(terminal.resolve(self.store, "t2").id, "T2")
        self.assertEqual(terminal.resolve(self.store, "the task in pillbox about medication").id, "T1")
        self.assertEqual(terminal.resolve(self.store, "login").id, "T2")

    def test_resolve_by_cast_name(self):
        self.store.update_task("T1", cast_name="Cicero")
        for ref in ("Cicero", "cicero", "talk to Cicero"):
            self.assertEqual(terminal.resolve(self.store, ref).id, "T1", ref)
        self.assertEqual(terminal.resolve(self.store, "Brutus"), [])

    def test_resolve_by_non_latin_cast_name(self):
        self.store.update_task("T1", cast_name="Лавров")
        self.store.update_task("T2", cast_name="Séchin")
        for ref, tid in (("Лавров", "T1"), ("switch to лавров", "T1"), ("Séchin", "T2"),
                         ("séchin login", "T2")):
            found = terminal.resolve(self.store, ref)
            self.assertEqual(getattr(found, "id", found), tid, ref)

    def test_card_names_the_person(self):
        from onethread import views
        self.store.update_task("T1", cast_name="Cicero")
        self.assertEqual(views.task_card(self.store.get_task("T1")),
                         'Switching you to Cicero: "Medication reminders" in pillbox (T1, Fake)')

    def test_resolve_ambiguous_lists_candidates(self):
        found = terminal.resolve(self.store, "medication")
        self.assertEqual(sorted(t.id for t in found), ["T1", "T3"])
        self.assertEqual(terminal.resolve(self.store, "payments"), [])
        code, _, err = self.ot("open", "medication")
        self.assertEqual(code, 1)
        self.assertIn("T1", err)
        self.assertIn("T3", err)
        self.assertEqual(self.tabs, [])

    def test_open_prints_card_and_tab_command(self):
        code, out, _ = self.ot("open", "pillbox", "medication")
        self.assertEqual(code, 0)
        self.assertIn('Switching you to "Medication reminders" in pillbox (T1, Fake)', out)
        self.assertEqual(self.store.get_task("T1").status, "with_you")
        self.assertTrue(self.tabs[0].endswith("session T1"), self.tabs)
        self.assertIn(f"ONETHREAD_HOME={self.home}", self.tabs[0])

    def test_open_running_task_stops_worker_without_asking(self):
        self.store.update_task("T1", description="[fake:delay=30]")
        self.store.set_status("T1", "queued")
        eng = Engine(self.store, self.home, {"fake": FakeAgent()})
        eng.tick()
        pid = self.store.q("SELECT pid FROM runs WHERE task='T1'")[0]["pid"]
        code, out, err = self.ot("open", "T1")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.store.get_task("T1").status, "with_you")
        self.assertEqual(self.store.q("SELECT open FROM runs WHERE task='T1'")[0]["open"], 0)
        for proc in eng.sup.children.values():     # this test process is its parent
            self.assertIsNotNone(proc.poll() if proc.poll() is not None else proc.wait(timeout=5))
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)
        eng.close()

    def test_session_exit_parks_task(self):
        cast.set_me(self.store, "king")
        code, _, err = self.ot("session", "T1")
        self.assertEqual(code, 0, err)
        argv, cwd, env = self.sessions[0]
        self.assertEqual(self.during, "with_you")
        self.assertTrue(cwd.endswith(os.path.join("worktrees", "pillbox", "T1")))
        self.assertTrue(env["PATH"].startswith(os.path.join(self.home, "shims")))
        t = self.store.get_task("T1")
        self.assertEqual(t.status, "parked")
        self.assertTrue(t.session_id)
        self.assertEqual([(i["task"], i["kind"]) for i in self.store.open_attention()],
                         [("T1", "parked")])
        self.assertEqual(self.store.get_meta("tab_pid:T1", ""), "")

    def test_session_greets_in_character(self):
        cast.set_me(self.store, "Caesar")
        cast.set_cast(self.store, {"address": "Imperator",
                                   "right_hand": {"name": "Agrippa", "style": "smooth"},
                                   "members": [{"name": "Cicero", "style": "dry diplomat"}]})
        self.store.update_task("T1", agent="claude", session_id="uuid-1")
        self.ot("session", "T1")
        argv = self.sessions[0][0]
        self.assertEqual(argv[:3], ["claude", "--resume", "uuid-1"])
        note = prompts.session_note(cast.task_persona(self.store, self.store.get_task("T1")))
        self.assertIn(note, argv)
        self.assertIn("You are Cicero", note)
        self.assertEqual(argv[-1], prompts.TAKEOVER)

    def test_session_tells_agent_to_talk_not_reply_json(self):
        # The work prompt asked for JSON-only replies; the owner wants a conversation.
        for agent, extra in (("claude", {"session_id": "uuid-1"}), ("codex", {"session_id": "sid-2"})):
            self.store.update_task("T1", agent=agent, **extra)
            cast.set_me(self.store, "plain")
            self.sessions.clear()
            self.ot("session", "T1")
            argv = self.sessions[0][0]
            self.assertTrue(any(prompts.TALK in a for a in argv), (agent, argv))
            self.ot("handback", "T1")

    def test_open_tab_false_when_osascript_fails(self):
        def fails(argv, **kw):
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="no iTerm2")
        have = lambda: True  # noqa: E731
        self.assertFalse(terminal.open_tab("ot session T1", run=fails, installed=have))

        def missing(argv, **kw):
            raise FileNotFoundError("osascript")
        self.assertFalse(terminal.open_tab("ot session T1", run=missing, installed=have))
        ok = lambda argv, **kw: subprocess.CompletedProcess(argv, 0, stdout="")  # noqa: E731
        self.assertTrue(terminal.open_tab("ot session T1", run=ok, installed=have))

    def test_open_tab_without_iterm_never_runs_osascript(self):
        def never(argv, **kw):
            raise AssertionError("ran osascript")
        self.assertFalse(terminal.open_tab("ot session T1", run=never, installed=lambda: False))

    def test_new_tab_session_that_never_started_is_forgotten(self):
        self.fake_session = lambda argv, cwd, env: 1        # e.g. quit at the trust prompt
        self.ot("session", "T1")
        self.assertEqual(self.store.get_task("T1").session_id, "")
