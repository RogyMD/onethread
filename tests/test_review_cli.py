import io
import json
import os
from contextlib import redirect_stderr, redirect_stdout

from onethread import cli, worktrees
from tests.helpers import TempHomeCase, git, make_repo


class ReviewCliTest(TempHomeCase):
    """`ot look` and `ot show`: what the orchestrator runs before accept and merge."""

    def setUp(self):
        super().setUp()
        self.repo = make_repo(self.tmp, "app-a")
        self.store.add_repo(self.repo)
        self.store.set_meta("orchestrator", "fake")
        self.store.add_task("app-a", "Add login")
        self.wt = worktrees.ensure(self.store, self.home, "T1").worktree
        self.commit("a.txt")
        self.store.update_task("T1", result={"summary": "Added login", "commits": [], "tests": "ok"})
        for s in ("queued", "running", "review"):
            self.store.set_status("T1", s)
        self.store.x("INSERT INTO runs (task, kind, started, ended, open) VALUES ('T1','work',90,100,0)")
        self.store.clock = lambda: 150
        self.tabs, self.opened, self.sessions, self.tab_ok = [], [], [], True
        self.as_text = {}

    def commit(self, *names, remove=()):
        for name in names:
            with open(os.path.join(self.wt, name), "w") as f:
                f.write("work")
        for name in remove:
            os.remove(os.path.join(self.wt, name))
        git(self.wt, "add", "-A")
        git(self.wt, "commit", "-q", "-m", "work")

    def ot(self, *argv):
        out, err = io.StringIO(), io.StringIO()

        def no_confirm(q):
            raise AssertionError(f"asked: {q}")
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(list(argv), home=self.home, confirm=no_confirm, ensure=lambda h: None,
                            open_tab=lambda c: self.tabs.append(c) or self.tab_ok,
                            open_path=self.open_path, run_session=self.fake_session)
        return code, out.getvalue(), err.getvalue()

    def open_path(self, path, as_text=False):
        self.opened.append(path)
        self.as_text[os.path.basename(path)] = as_text

    def fake_session(self, argv, cwd, env):
        self.sessions.append(argv)
        return 0

    def looked(self):
        return json.loads(self.ot("look", "T1")[1])["looked"]

    def test_look_reports_not_looked_and_items(self):
        code, out, _ = self.ot("look", "T1")
        data = json.loads(out)
        self.assertEqual((code, data["task"], data["looked"]), (0, "T1", False))
        self.assertEqual(data["items"][0], {"id": "summary", "label": "What the worker says"})
        self.assertEqual(data["items"][-1]["id"], "nothing")

    def test_show_nothing_marks_looked(self):
        self.assertEqual(self.ot("show", "T1", "nothing")[0], 0)
        self.assertTrue(self.looked())

    def test_show_summary_prints_worker_words(self):
        code, out, _ = self.ot("show", "T1", "summary")
        self.assertEqual(code, 0)
        self.assertIn("Added login", out)
        self.assertNotIn("a.txt", out)                     # the diffstat stays out

    def test_show_diff_opens_a_tab(self):
        self.ot("show", "T1", "diff")
        self.assertIn("diff main...", self.tabs[0])
        self.assertIn("less -R", self.tabs[0])

    def test_show_diff_without_iterm_prints_command(self):
        self.tab_ok = False
        code, out, _ = self.ot("show", "T1", "diff")
        self.assertEqual(code, 0)
        self.assertIn("less -R", out)

    def test_show_files_opens_existing_files_only(self):
        self.commit("b.txt", remove=("README.md",))
        self.ot("show", "T1", "files")
        self.assertEqual(sorted(os.path.basename(p) for p in self.opened), ["a.txt", "b.txt"])

    def test_show_files_never_runs_what_the_worker_wrote(self):
        self.commit("setup.command", "shot.png", "notes.pdf")
        self.ot("show", "T1", "files")
        self.assertEqual(self.as_text, {"a.txt": True, "setup.command": True,
                                        "shot.png": False, "notes.pdf": False})

    def test_show_files_caps_at_twenty(self):
        self.commit(*[f"f{i:02}.txt" for i in range(24)])   # 25 with a.txt
        code, out, _ = self.ot("show", "T1", "files")
        self.assertEqual(len(self.opened), 20)
        self.assertIn("…and 5 more changed files.", out)

    def test_show_unavailable_item_fails_and_records_nothing(self):
        code, _, err = self.ot("show", "T1", "summary", "pr")
        self.assertEqual(code, 1)
        self.assertIn("pr", err)
        self.assertFalse(self.looked())
        self.assertEqual(self.opened + self.tabs, [])

    def test_show_unknown_item_fails_with_exit_1(self):
        code, _, err = self.ot("show", "T1", "everything")
        self.assertEqual(code, 1)
        self.assertIn("everything", err)
        self.assertFalse(self.looked())

    def test_show_pr_opens_stored_url(self):
        self.store.update_task("T1", result={"summary": "x", "pr": "https://example.invalid/pr/1"})
        self.ot("show", "T1", "pr")
        self.assertEqual(self.opened, ["https://example.invalid/pr/1"])

    def test_show_agent_opens_task_tab(self):
        self.ot("show", "T1", "agent")
        self.assertIn("session T1", self.tabs[0])

    def test_tab_that_fails_to_start_is_not_looked(self):
        def broken(argv, cwd, env):
            raise FileNotFoundError("agent not on PATH")
        self.fake_session = broken
        code, _, err = self.ot("session", "T1")
        self.assertEqual(code, 1)
        self.assertIn("agent not on PATH", err)
        self.assertNotIn("Traceback", err)
        self.assertFalse(self.looked())

    def test_after_talking_to_the_agent_review_then_accept_works(self):
        self.ot("show", "T1", "agent")
        self.ot("session", "T1")                          # the owner talks, then closes the tab
        self.assertEqual(self.store.get_task("T1").status, "parked")
        self.ot("review", "T1")
        self.assertEqual(self.store.get_task("T1").status, "review")
        self.assertIn("ot review T", __import__("onethread.lobby").lobby.INSTRUCTIONS.split("Talk to the agent")[1][:200])

    def test_own_tab_counts_as_looked(self):
        self.ot("session", "T1")
        self.assertTrue(self.sessions)
        self.assertTrue(self.looked())
