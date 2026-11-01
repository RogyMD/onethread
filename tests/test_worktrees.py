import os
import unittest

from onethread import worktrees
from tests.helpers import TempHomeCase, git, make_repo


class WorktreeTest(TempHomeCase):
    def setUp(self):
        super().setUp()
        self.repo = make_repo(self.tmp, "app-a")
        self.store.add_repo(self.repo)

    def ensure(self, tid):
        return worktrees.ensure(self.store, self.home, tid)

    def test_each_task_gets_its_own_worktree_and_branch(self):
        self.store.add_task("app-a", "Add login")
        self.store.add_task("app-a", "Dark mode")
        a, b = self.ensure("T1"), self.ensure("T2")
        self.assertEqual(a.worktree, os.path.join(self.home, "worktrees", "app-a", "T1"))
        self.assertEqual(a.branch, "ot/T1-add-login")
        self.assertNotEqual(a.worktree, b.worktree)
        with open(os.path.join(a.worktree, "only-a.txt"), "w") as f:
            f.write("a")
        self.assertFalse(os.path.exists(os.path.join(b.worktree, "only-a.txt")))
        self.assertEqual(git(a.worktree, "branch", "--show-current"), a.branch)
        self.assertEqual(a.base_branch, "main")

    def test_ensure_is_idempotent(self):
        self.store.add_task("app-a", "Add login")
        first = self.ensure("T1")
        self.assertEqual(self.ensure("T1").worktree, first.worktree)

    def test_same_repo_dependency_branches_from_its_unmerged_branch(self):
        self.store.add_task("app-a", "Auth")
        dep = self.ensure("T1")
        with open(os.path.join(dep.worktree, "auth.py"), "w") as f:
            f.write("x")
        git(dep.worktree, "add", ".")
        git(dep.worktree, "commit", "-q", "-m", "auth")
        self.store.add_task("app-a", "Profile page")
        self.store.add_dep("T2", "T1")
        t2 = self.ensure("T2")
        self.assertEqual(t2.base_branch, dep.branch)
        self.assertTrue(os.path.exists(os.path.join(t2.worktree, "auth.py")))

    def test_merged_dependency_branches_from_repo_base(self):
        self.store.add_task("app-a", "Auth")
        dep = self.ensure("T1")          # branch has no new commits: merged
        self.store.add_task("app-a", "Profile")
        self.store.add_dep("T2", "T1")
        self.assertEqual(self.ensure("T2").base_branch, "main")
        self.assertTrue(worktrees.is_merged(self.repo, dep.branch, "main"))


class HasCommitsTest(TempHomeCase):
    def test_unknown_base_counts_as_having_commits(self):
        repo = make_repo(self.tmp, "app-a")
        self.assertTrue(worktrees.has_commits(repo, "no-such-branch"))
        self.assertFalse(worktrees.has_commits(repo, "main"))


if __name__ == "__main__":
    unittest.main()
