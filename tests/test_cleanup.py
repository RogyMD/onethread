import os
import time

from onethread import cleanup, worktrees
from tests.helpers import TempHomeCase, git, make_repo

DAY = 86400


class CleanupTest(TempHomeCase):
    def setUp(self):
        super().setUp()
        self.repo = make_repo(self.tmp, "app-a")
        self.store.add_repo(self.repo)

    def task(self, status, commit=False, dirty=False):
        t = self.store.add_task("app-a", f"Task {len(self.store.list_tasks()) + 1}")
        t = worktrees.ensure(self.store, self.home, t.id)
        if commit:
            with open(os.path.join(t.worktree, f"{t.id}.txt"), "w") as f:
                f.write("work")
            git(t.worktree, "add", ".")
            git(t.worktree, "commit", "-q", "-m", "work")
        if dirty:
            with open(os.path.join(t.worktree, "wip.txt"), "w") as f:
                f.write("wip")
        path = {"done": ["queued", "running", "review", "done"], "cancelled": ["cancelled"],
                "running": ["queued", "running"], "review": ["queued", "running", "review"],
                "parked": ["with_you", "parked"]}[status]
        for s in path:
            self.store.set_status(t.id, s)
        os.makedirs(os.path.join(self.home, "runs", t.id, "1"), exist_ok=True)
        return self.store.get_task(t.id)

    def branch_exists(self, branch):
        return git(self.repo, "branch", "--list", branch) != ""

    def logs(self, tid):
        return os.path.isdir(os.path.join(self.home, "runs", tid))

    def test_clean_finished_worktree_removed_branch_kept(self):
        t = self.task("done", commit=True)
        result = cleanup.clean(self.store, self.home)
        self.assertEqual(result["worktrees"], ["T1"])
        self.assertFalse(os.path.exists(t.worktree))
        self.assertEqual(self.store.get_task("T1").worktree, "")
        self.assertTrue(self.branch_exists(t.branch))
        self.assertEqual(result["branches"], [])
        self.assertGreater(result["freed"], 0)

    def test_merged_branch_deleted(self):
        t = self.task("done", commit=True)
        git(self.repo, "merge", "-q", "--no-ff", "--no-edit", t.branch)
        result = cleanup.clean(self.store, self.home)
        self.assertEqual(result["branches"], [t.branch])
        self.assertFalse(self.branch_exists(t.branch))
        self.assertEqual(self.store.get_task("T1").branch, "")

    def test_cancelled_unmerged_branch_kept(self):
        t = self.task("cancelled", commit=True)
        cleanup.clean(self.store, self.home)
        self.assertFalse(os.path.exists(t.worktree))
        self.assertTrue(self.branch_exists(t.branch))

    def ignore(self, t, pattern, name, folder=False):
        with open(os.path.join(t.worktree, ".gitignore"), "w") as f:
            f.write(pattern + "\n")
        git(t.worktree, "add", ".gitignore")
        git(t.worktree, "commit", "-q", "-m", "ignore")
        path = os.path.join(t.worktree, name)
        if folder:
            os.makedirs(path)
            path = os.path.join(path, "x.js")
        with open(path, "w") as f:
            f.write("x")

    def test_ignored_files_the_owner_made_are_offered_not_deleted(self):
        t = self.task("done", commit=True)
        self.ignore(t, ".env", ".env")
        result = cleanup.clean(self.store, self.home)
        self.assertEqual((result["worktrees"], result["asked"]), ([], [t.id]))
        self.assertTrue(os.path.exists(os.path.join(t.worktree, ".env")))

    def test_ignored_build_output_does_not_keep_a_worktree(self):
        t = self.task("done", commit=True)
        self.ignore(t, "node_modules/", "node_modules", folder=True)
        self.assertEqual(cleanup.clean(self.store, self.home)["worktrees"], [t.id])

    def test_redirected_worktree_is_left_alone(self):
        t = self.task("done", commit=True)
        evil = os.path.join(self.tmp, "evil")
        git(self.tmp, "init", "-q", evil)
        git(evil, "config", "core.fsmonitor", f"touch {os.path.join(self.tmp, 'ran')}")
        with open(os.path.join(t.worktree, ".git"), "w") as f:
            f.write(f"gitdir: {os.path.join(evil, '.git')}\n")
        cleanup.clean(self.store, self.home)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "ran")))

    def test_dirty_worktree_offered_not_deleted(self):
        t = self.task("done", dirty=True)
        result = cleanup.clean(self.store, self.home)
        self.assertTrue(os.path.isdir(t.worktree))
        self.assertEqual(result["asked"], ["T1"])
        cleanup.clean(self.store, self.home)
        kinds = [(i["task"], i["kind"]) for i in self.store.open_attention()]
        self.assertEqual(kinds, [("T1", "dirty_worktree")])

    def test_kept_dirty_worktree_never_offered(self):
        t = self.task("done", dirty=True)
        self.store.set_meta("keep_worktree:T1", "1")
        self.assertEqual(cleanup.clean(self.store, self.home)["asked"], [])
        self.assertEqual(self.store.open_attention(), [])
        self.assertTrue(os.path.isdir(t.worktree))

    def test_manual_clean_asks_for_dirty(self):
        t = self.task("done", dirty=True)
        asked = []
        cleanup.clean(self.store, self.home, confirm=lambda q: asked.append(q) or False)
        self.assertTrue(os.path.isdir(t.worktree))
        self.assertIn("uncommitted changes", asked[0])
        cleanup.clean(self.store, self.home, confirm=lambda q: True)
        self.assertFalse(os.path.exists(t.worktree))

    def test_open_tasks_untouched(self):
        tasks = [self.task(s, commit=True) for s in ("running", "review", "parked")]
        result = cleanup.clean(self.store, self.home, confirm=lambda q: True)
        self.assertEqual((result["worktrees"], result["branches"], result["logs"]), ([], [], []))
        for t in tasks:
            self.assertTrue(os.path.isdir(t.worktree))
            self.assertTrue(self.logs(t.id))

    def test_only_cleans_one_task(self):
        self.task("done", commit=True)
        t2 = self.task("done", commit=True)
        result = cleanup.clean(self.store, self.home, confirm=lambda q: True, only="T1")
        self.assertEqual(result["worktrees"], ["T1"])
        self.assertTrue(os.path.isdir(t2.worktree))

    def test_logs_after_seven_days(self):
        self.task("done", commit=True)
        now = time.time()
        cleanup.clean(self.store, self.home, now=now + 6 * DAY)
        self.assertTrue(self.logs("T1"))
        result = cleanup.clean(self.store, self.home, now=now + 8 * DAY)
        self.assertEqual(result["logs"], ["T1"])
        self.assertFalse(self.logs("T1"))

    def test_manual_clean_removes_logs_at_once(self):
        self.task("done", commit=True)
        cleanup.clean(self.store, self.home, confirm=lambda q: True)
        self.assertFalse(self.logs("T1"))

    def test_missing_worktree_folder_is_forgotten(self):
        t = self.task("done", commit=True)
        git(self.repo, "worktree", "remove", "--force", t.worktree)
        cleanup.clean(self.store, self.home)
        self.assertEqual(self.store.get_task("T1").worktree, "")

    def test_dependency_branch_kept_while_dependent_open(self):
        t1 = self.task("done", commit=True)
        t2 = self.store.add_task("app-a", "Child")
        self.store.add_dep("T2", "T1")
        t2 = worktrees.ensure(self.store, self.home, "T2")
        self.assertEqual(t2.base_branch, t1.branch)
        with open(os.path.join(t2.worktree, "b.txt"), "w") as f:
            f.write("b")
        git(t2.worktree, "add", ".")
        git(t2.worktree, "commit", "-q", "-m", "T2 work")
        for s in ("queued", "running", "review"):
            self.store.set_status("T2", s)
        git(self.repo, "merge", "-q", "--no-ff", "--no-edit", t1.branch)
        result = cleanup.clean(self.store, self.home)
        self.assertEqual(result["branches"], [])
        self.assertTrue(self.branch_exists(t1.branch))
        self.store.set_status("T2", "queued")              # the owner asks for changes
        worktrees.refresh_base(self.store, "T2")
        self.assertTrue(os.path.exists(os.path.join(t2.worktree, "b.txt")))

    def test_one_broken_task_does_not_stop_the_round(self):
        other = make_repo(self.tmp, "app-b")
        self.store.add_repo(other)
        broken = self.store.add_task("app-b", "Broken")
        worktrees.ensure(self.store, self.home, broken.id)
        self.store.set_status(broken.id, "cancelled")
        t2 = self.task("done", commit=True)
        os.rename(other, other + "-moved")                 # the owner moved the repo
        result = cleanup.clean(self.store, self.home)
        self.assertEqual(result["worktrees"], [t2.id])
        self.assertIn(f"cleanup_failed", [e["kind"] for e in self.store.events(broken.id)])

    def test_manual_clean_skips_kept_worktrees(self):
        t = self.task("done", dirty=True)
        self.store.set_meta("keep_worktree:T1", "1")
        cleanup.clean(self.store, self.home, confirm=lambda q: self.fail(f"asked: {q}"))
        self.assertTrue(os.path.isdir(t.worktree))
        asked = []
        cleanup.clean(self.store, self.home, confirm=lambda q: asked.append(q) or True, only="T1")
        self.assertTrue(asked)
        self.assertFalse(os.path.exists(t.worktree))

    def test_missing_worktree_leaves_owners_other_worktrees_registered(self):
        mine = os.path.join(self.tmp, "my-worktree")
        git(self.repo, "worktree", "add", "-q", "-b", "mine", mine)
        t = self.task("done", commit=True)
        git(self.repo, "worktree", "remove", "--force", t.worktree)
        os.rename(mine, mine + "-unmounted")
        cleanup.clean(self.store, self.home)
        self.assertIn(mine, git(self.repo, "worktree", "list", "--porcelain"))

    def test_human_sizes(self):
        self.assertEqual(cleanup.human(1_500_000_000), "1.4 GB")
        self.assertEqual(cleanup.human(84_000_000), "80 MB")
        self.assertEqual(cleanup.human(12_000), "12 KB")
        self.assertEqual(cleanup.human(0), "0 KB")

    def test_size_counts_files(self):
        folder = os.path.join(self.tmp, "x")
        os.makedirs(os.path.join(folder, "sub"))
        for name in ("a", os.path.join("sub", "b")):
            with open(os.path.join(folder, name), "w") as f:
                f.write("x" * 100)
        self.assertEqual(cleanup.size(folder), 200)
        self.assertEqual(cleanup.size(os.path.join(self.tmp, "missing")), 0)
