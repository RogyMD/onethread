import io
import json
import os
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from onethread import cli, tips
from tests.helpers import TempHomeCase, git, make_repo


class CliCase(TempHomeCase):
    """The orchestrator runs these commands; the owner answers popups."""

    def setUp(self):
        super().setUp()
        self.store.add_repo(make_repo(self.tmp, "app-a"))
        self.questions, self.answer = [], True

    def ot(self, *argv, stdin=""):
        out, err = io.StringIO(), io.StringIO()

        def confirm(q):
            self.questions.append(q)
            return self.answer
        with redirect_stdout(out), redirect_stderr(err), mock.patch("sys.stdin", io.StringIO(stdin)):
            code = cli.main(list(argv), home=self.home, confirm=confirm, ensure=lambda h: None)
        return code, out.getvalue(), err.getvalue()

    def cmds(self):
        return [json.loads(e["data"])["cmd"] for e in self.store.events() if e["kind"] == "cmd"]


class TipsCliTest(CliCase):
    def test_commands_are_journaled(self):
        self.ot("status")
        code, out, _ = self.ot("journal")
        self.assertEqual(code, 0)
        self.assertIn("ot status", out)
        self.assertEqual(self.cmds(), ["status"])

    def test_task_commands_carry_the_task(self):
        self.ot("add", "app-a", "Auth")
        self.ot("cancel", "T1")
        (e,) = [e for e in self.store.events("T1") if e["kind"] == "cmd"]
        self.assertIn("cancel", e["data"])

    def test_empty_journal(self):
        self.assertIn("Nothing in the journal for the last 3 days.", self.ot("journal", "--days", "3")[1])

    def test_hook_is_not_journaled(self):
        self.ot("hook", "prompt", stdin='{"prompt": "please merge my secret thing"}')
        self.assertEqual(self.cmds(), [])
        self.assertFalse(any("secret" in (e["data"] or "") for e in self.store.events()))

    def test_tip_flow(self):
        self.assertEqual(self.ot("tip", "offered", "Merge after link")[0], 0)
        code, _, err = self.ot("tip", "offered", "other")
        self.assertEqual(code, 1)
        self.assertIn("one tip a day", err)
        code, out, _ = self.ot("tip", "yes", "merge-after-link", "After a pasted link, finish the task.")
        self.assertIn("Saved: After a pasted link, finish the task.", out)
        self.assertIn("1. After a pasted link, finish the task.", self.ot("prefs")[1])
        self.assertIn("Removed: After a pasted link, finish the task.", self.ot("prefs", "remove", "1")[1])
        self.assertEqual(self.ot("prefs", "remove", "1")[0], 1)
        self.assertIn("No preferences yet.", self.ot("prefs")[1])

    def test_tip_yes_without_text(self):
        self.assertIn("Noted: yes to merge-after-link.", self.ot("tip", "yes", "merge-after-link")[1])
        self.assertEqual(tips.prefs(self.home), [])

    def test_tip_no(self):
        self.assertIn("won't be offered again", self.ot("tip", "no", "Close After Merge")[1])
        self.assertEqual(tips.declined(self.store), ["close-after-merge"])

    def test_tips_off_and_on(self):
        self.assertIn("Tips are off.", self.ot("tips", "off")[1])
        self.assertFalse(tips.enabled(self.store))
        self.assertIn("Tips are on.", self.ot("tips", "on")[1])
        self.assertTrue(tips.enabled(self.store))


class RepoNewTest(CliCase):
    def test_repo_new_creates_and_registers(self):
        path = os.path.join(self.tmp, "pill-reminder")
        code, out, err = self.ot("repo", "new", path)
        self.assertEqual(code, 0, err)
        self.assertIn("Create a new git repository at", self.questions[0])
        self.assertIn("Registered pill-reminder", out)
        self.assertEqual(self.store.get_repo("pill-reminder")["base_branch"], "main")
        self.assertEqual(git(path, "rev-list", "--count", "HEAD"), "1")

    def test_repo_new_in_an_empty_folder(self):
        path = os.path.join(self.tmp, "empty")
        os.makedirs(path)
        self.assertEqual(self.ot("repo", "new", path)[0], 0)
        self.assertIsNotNone(self.store.get_repo("empty"))

    def test_repo_new_refuses_a_folder_with_files(self):
        path = os.path.join(self.tmp, "mine")
        os.makedirs(path)
        with open(os.path.join(path, "notes.txt"), "w") as f:
            f.write("keep")
        code, _, err = self.ot("repo", "new", path)
        self.assertEqual(code, 1)
        self.assertIn("already exists and isn't empty", err)
        self.assertEqual(self.questions, [])
        self.assertEqual(os.listdir(path), ["notes.txt"])

    def test_repo_new_refuses_a_registered_name(self):
        code, _, err = self.ot("repo", "new", os.path.join(self.tmp, "other", "app-a"))
        self.assertEqual(code, 1)
        self.assertIn("already registered", err)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "other")))

    def test_repo_new_no_creates_nothing(self):
        self.answer = False
        path = os.path.join(self.tmp, "x")
        code, _, err = self.ot("repo", "new", path)
        self.assertEqual(code, 1)
        self.assertIn("Not created.", err)
        self.assertFalse(os.path.exists(path))

    def test_repo_new_cleans_up_when_git_cannot_commit(self):
        no_identity = {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null",
                       "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "user.useConfigOnly",
                       "GIT_CONFIG_VALUE_0": "true"}
        drop = ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME",
                "GIT_COMMITTER_EMAIL", "EMAIL")
        env = {k: v for k, v in os.environ.items() if k not in drop}
        env.update(no_identity)
        path = os.path.join(self.tmp, "y")
        with mock.patch.dict(os.environ, env, clear=True):
            code, _, err = self.ot("repo", "new", path)
        self.assertEqual(code, 1)
        self.assertIn("git", err)
        self.assertFalse(os.path.exists(path))
        self.assertIsNone(self.store.get_repo("y"))

    def test_repo_new_failure_in_an_empty_folder_keeps_the_folder(self):
        path = os.path.join(self.tmp, "kept")
        os.makedirs(path)
        with mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null",
                                          "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "user.useConfigOnly",
                                          "GIT_CONFIG_VALUE_0": "true", "GIT_AUTHOR_NAME": "",
                                          "GIT_AUTHOR_EMAIL": "", "EMAIL": ""}):
            self.assertEqual(self.ot("repo", "new", path)[0], 1)
        self.assertEqual(os.listdir(path), [])
