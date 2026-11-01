import io
import json
import os
from contextlib import redirect_stderr, redirect_stdout

from onethread import cleanup, cli, finish, worktrees
from tests.helpers import TempHomeCase, git, make_repo


class FinishCliTest(TempHomeCase):
    """`ot finish` and `ot accept`: the orchestrator runs them, the popup asks once."""

    def setUp(self):
        super().setUp()
        self.repo = make_repo(self.tmp, "app-a")
        self.store.add_repo(self.repo)
        self.store.set_meta("orchestrator", "fake")
        self.store.add_task("app-a", "Add login")
        wt = worktrees.ensure(self.store, self.home, "T1").worktree
        with open(os.path.join(wt, "a.txt"), "w") as f:
            f.write("work")
        git(wt, "add", ".")
        git(wt, "commit", "-q", "-m", "work")
        for s in ("queued", "running", "review"):
            self.store.set_status("T1", s)
        self.store.x("INSERT INTO runs (task, kind, started, ended, open) VALUES ('T1','work',90,100,0)")
        self.store.clock = lambda: 150
        self.questions, self.answer = [], True

    def ot(self, *argv):
        out, err = io.StringIO(), io.StringIO()

        def confirm(q):
            self.questions.append(q)
            return self.answer
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(list(argv), home=self.home, confirm=confirm, ensure=lambda h: None)
        return code, out.getvalue(), err.getvalue()

    def task(self):
        return self.store.get_task("T1")

    def merged(self):
        return worktrees.is_merged(self.repo, self.task().branch, "main")

    def test_finish_asks_once_and_merges_a_task_in_review(self):
        code, out, _ = self.ot("finish", "T1")
        self.assertEqual(code, 0)
        self.assertEqual(len(self.questions), 1)
        self.assertIn("accept it and merge it into main", self.questions[0])
        self.assertNotIn("published link", self.questions[0])
        self.assertIn("is merged into main", out)
        self.assertEqual(self.task().status, "done")
        self.assertTrue(self.merged())

    def test_finish_no_gives_no_order(self):
        self.answer = False
        code, _, err = self.ot("finish", "T1")
        self.assertEqual(code, 1)
        self.assertIn("Not finished.", err)
        self.assertIsNone(finish.get_order(self.store, "T1"))
        self.assertEqual(self.task().status, "review")

    def test_finish_with_link_counts_as_looked_and_asks_worker(self):
        code, out, _ = self.ot("finish", "T1", "--link", "https://example.invalid/p/1")
        self.assertEqual(code, 0)
        self.assertIn("record the published link, then accept it", self.questions[0])
        self.assertEqual(self.task().status, "queued")
        self.assertIn("https://example.invalid/p/1", self.task().followups[-1])
        self.assertTrue(json.loads(self.ot("look", "T1")[1])["looked"])

    def test_finish_running_task_waits_for_its_worker(self):
        self.store.set_status("T1", "queued")
        self.store.set_status("T1", "running")
        code, out, _ = self.ot("finish", "T1")
        self.assertEqual(code, 0)
        self.assertIn("will be accepted and merged when its worker is done", out)
        self.assertIsNotNone(finish.get_order(self.store, "T1"))

    def test_finish_link_on_cleaned_done_task(self):
        self.store.set_status("T1", "done")
        finish.merge(self.store, self.task())
        cleanup.clean(self.store, self.home)
        self.assertEqual(self.task().worktree, "")
        code, out, err = self.ot("finish", "T1", "--link", "https://example.invalid/p/2")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.task().result["published"], "https://example.invalid/p/2")
        self.assertEqual(self.task().status, "done")
        self.assertEqual(self.task().followups, [])
        self.assertIsNone(finish.get_order(self.store, "T1"))

    def test_finish_draft_refused(self):
        self.store.add_task("app-a", "Later idea")
        code, _, err = self.ot("finish", "T2")
        self.assertEqual(code, 1)
        self.assertIn("is draft; nothing to finish", err)
        self.assertEqual(self.questions, [])

    def test_accept_merges_by_default(self):
        code, out, _ = self.ot("accept", "T1")
        self.assertEqual(code, 0)
        self.assertEqual(len(self.questions), 1)
        self.assertIn("and merge it into main", self.questions[0])
        self.assertIn("is merged into main", out)
        self.assertTrue(self.merged())

    def test_accept_with_dirty_repo_leaves_order(self):
        with open(os.path.join(self.repo, "README.md"), "w") as f:
            f.write("edit")
        code, out, _ = self.ot("accept", "T1")
        self.assertEqual(code, 0)
        self.assertIn("uncommitted changes", out)
        self.assertEqual(self.task().status, "done")
        self.assertIsNotNone(finish.get_order(self.store, "T1"))
        self.assertFalse(self.merged())

    def test_merge_on_accept_off(self):
        self.assertEqual(self.ot("config", "set", "merge_on_accept", "0")[0], 0)
        self.ot("accept", "T1")
        self.assertIn("as done", self.questions[0])
        self.assertEqual(self.task().status, "done")
        self.assertFalse(self.merged())
        self.assertIsNone(finish.get_order(self.store, "T1"))

    def test_merge_on_accept_takes_only_0_or_1(self):
        self.assertEqual(self.ot("config", "set", "merge_on_accept", "no")[0], 1)
