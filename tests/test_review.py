import os

from onethread import review, worktrees
from tests.helpers import TempHomeCase, git, make_repo


class ReviewTest(TempHomeCase):
    def setUp(self):
        super().setUp()
        self.repo = make_repo(self.tmp, "app-a")
        self.store.add_repo(self.repo)
        self.store.add_task("app-a", "Add login")
        self.wt = worktrees.ensure(self.store, self.home, "T1").worktree

    def commit(self, name="a.txt"):
        with open(os.path.join(self.wt, name), "w") as f:
            f.write("work")
        git(self.wt, "add", ".")
        git(self.wt, "commit", "-q", "-m", "work")

    def task(self):
        return self.store.get_task("T1")

    def at(self, now):
        self.store.clock = lambda: now

    def run_ended(self, ended):
        self.store.x("INSERT INTO runs (task, kind, started, ended, open) VALUES ('T1','work',?,?,0)",
                     (ended - 10, ended))

    def to_review(self):
        for s in ("queued", "running", "review"):
            self.store.set_status("T1", s)

    def ids(self):
        return [i["id"] for i in review.items(self.store, self.task())]

    def test_no_runs_counts_as_looked(self):
        self.assertTrue(review.looked(self.store, self.task()))

    def test_new_work_is_not_looked(self):
        self.run_ended(100)
        self.assertFalse(review.looked(self.store, self.task()))

    def test_mark_after_work_is_looked(self):
        self.run_ended(100)
        self.at(150)
        review.mark(self.store, "T1", "show")
        self.assertTrue(review.looked(self.store, self.task()))

    def test_newer_work_after_mark_is_not_looked(self):
        self.run_ended(100)
        self.at(150)
        review.mark(self.store, "T1", "show")
        self.run_ended(200)
        self.assertFalse(review.looked(self.store, self.task()))

    def test_items_for_task_in_review(self):
        self.commit()
        self.store.update_task("T1", result={"summary": "Added login", "commits": [], "tests": ""})
        self.to_review()
        self.assertEqual(self.ids(), ["summary", "diff", "files", "agent", "nothing"])
        self.assertEqual(review.items(self.store, self.task())[-1]["label"], "Nothing, go ahead")
        self.assertEqual(review.items(self.store, self.task())[0]["label"], "What the worker says")

    def test_pr_item_only_with_stored_pr(self):
        self.assertNotIn("pr", self.ids())
        self.store.update_task("T1", result={"summary": "x", "pr": "https://example.invalid/pr/1"})
        self.assertIn("pr", self.ids())

    def test_cleaned_task_offers_diff_but_not_files_or_agent(self):
        self.commit()
        self.store.update_task("T1", result={"summary": "Added login"})
        self.to_review()
        self.store.set_status("T1", "done")
        git(self.repo, "worktree", "remove", "--force", self.wt)
        self.store.update_task("T1", worktree="")
        self.assertEqual(self.ids(), ["summary", "diff", "nothing"])

    def test_no_changes_no_diff_or_files(self):
        self.assertEqual(self.ids(), ["agent", "nothing"])

    def test_changed_files_lists_branch_changes(self):
        self.commit()
        self.assertEqual(review.changed_files(self.store, self.task()), ["a.txt"])
