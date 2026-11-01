import json
import os

from onethread import attention, finish, worktrees
from tests.helpers import TempHomeCase, git, make_repo


def write(repo, name, text):
    with open(os.path.join(repo, name), "w") as f:
        f.write(text)


def read(repo, name):
    with open(os.path.join(repo, name)) as f:
        return f.read()


class FinishCase(TempHomeCase):
    path = ("queued", "running", "review", "done")

    def setUp(self):
        super().setUp()
        self.repo = make_repo(self.tmp, "app-a")
        self.store.add_repo(self.repo)
        self.add_worked_task("app-a", "Auth", "a.txt")
        self.to(*self.path)

    def add_worked_task(self, repo, title, filename):
        t = self.store.add_task(repo, title)
        t = worktrees.ensure(self.store, self.home, t.id)
        write(t.worktree, filename, f"{t.id} work\n")
        git(t.worktree, "add", ".")
        git(t.worktree, "commit", "-q", "-m", f"{t.id} work")
        return t

    def task(self, tid="T1"):
        return self.store.get_task(tid)

    def to(self, *statuses, tid="T1"):
        for s in statuses:
            self.store.set_status(tid, s)

    def t2(self):
        """T2 depends on T1 and was created on T1's unmerged branch."""
        if not self.store.get_task("T2"):
            t = self.store.add_task("app-a", "Logout")
            self.store.add_dep(t.id, "T1")
            t = worktrees.ensure(self.store, self.home, t.id)
            write(t.worktree, "b.txt", "T2 work\n")
            git(t.worktree, "add", ".")
            git(t.worktree, "commit", "-q", "-m", "T2 work")
            self.to("queued", "running", "review", "done", tid="T2")
        return self.task("T2")

    def refuse_merges(self):
        hook = os.path.join(self.repo, ".git", "hooks", "pre-merge-commit")
        with open(hook, "w") as f:
            f.write("#!/bin/sh\necho 'signing key not found' >&2\nexit 1\n")
        os.chmod(hook, 0o755)

    def make_conflict(self):
        write(self.repo, "a.txt", "main's own a.txt\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-q", "-m", "main moved")


class MergeTest(FinishCase):
    def test_merge_into_repo_base(self):
        self.assertEqual(finish.merge(self.store, self.task())[0], "merged")
        self.assertTrue(worktrees.is_merged(self.repo, self.task().branch, "main"))
        self.assertIn("merged", [e["kind"] for e in self.store.events("T1")])

    def test_already_merged_is_merged(self):
        finish.merge(self.store, self.task())
        head = git(self.repo, "rev-parse", "HEAD")
        self.assertEqual(finish.merge(self.store, self.task())[0], "merged")
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), head)

    def test_dirty_repo_blocks_and_touches_nothing(self):
        write(self.repo, "README.md", "owner's edit")
        self.assertEqual(finish.merge(self.store, self.task()),
                         ("blocked", f"{self.repo} has uncommitted changes."))
        self.assertEqual(read(self.repo, "README.md"), "owner's edit")
        self.assertFalse(worktrees.is_merged(self.repo, self.task().branch, "main"))

    def test_other_branch_checked_out_blocks(self):
        git(self.repo, "checkout", "-q", "-b", "feature")
        outcome, detail = finish.merge(self.store, self.task())
        self.assertEqual(outcome, "blocked")
        self.assertIn("'feature'", detail)
        self.assertEqual(git(self.repo, "symbolic-ref", "--short", "HEAD"), "feature")

    def test_conflict_is_aborted(self):
        self.make_conflict()
        self.assertEqual(finish.merge(self.store, self.task())[0], "conflict")
        self.assertFalse(os.path.exists(os.path.join(self.repo, ".git", "MERGE_HEAD")))
        self.assertEqual(git(self.repo, "status", "--porcelain"), "")
        self.assertEqual(read(self.repo, "a.txt"), "main's own a.txt\n")

    def test_failure_that_is_not_a_conflict_says_what_failed(self):
        self.refuse_merges()
        outcome, detail = finish.merge(self.store, self.task())
        self.assertEqual(outcome, "error")
        self.assertIn("signing key not found", detail)
        self.assertEqual(git(self.repo, "status", "--porcelain"), "")

    def test_stacked_task_waits_for_its_dependency(self):
        self.assertEqual(self.t2().base_branch, self.task().branch)
        self.assertIsNone(finish.target(self.store, self.t2()))
        outcome, detail = finish.merge(self.store, self.t2())
        self.assertEqual(outcome, "wait")
        self.assertIn("(T1)", detail)
        finish.merge(self.store, self.task())
        self.assertEqual(finish.target(self.store, self.t2()), "main")
        self.assertEqual(finish.merge(self.store, self.t2())[0], "merged")
        self.assertTrue(worktrees.is_merged(self.repo, self.t2().branch, "main"))

    def test_cleaned_up_task_has_nothing_left_to_merge(self):
        from onethread import cleanup
        finish.merge(self.store, self.task())
        cleanup.clean(self.store, self.home)
        self.assertEqual(self.task().branch, "")
        self.assertEqual(finish.merge(self.store, self.task())[0], "merged")


class OrderTest(FinishCase):
    path = ("queued", "running", "review")
    LINK = "https://example.invalid/p/1"

    def done_with_order(self):
        self.to("done")
        finish.order(self.store, "T1")

    def kinds(self):
        return [i["kind"] for i in self.store.open_attention()]

    def test_order_on_review_accepts_then_merges(self):
        finish.order(self.store, "T1")
        self.assertEqual(finish.step(self.store, self.task()), "accepted")
        self.assertEqual(self.task().status, "done")
        self.assertEqual(finish.step(self.store, self.task()), "merged")
        self.assertIsNone(finish.get_order(self.store, "T1"))
        self.assertTrue(worktrees.is_merged(self.repo, self.task().branch, "main"))

    def test_engine_merge_is_marked_as_the_engines(self):
        finish.order(self.store, "T1")
        finish.tick(self.store)                                # accepts
        finish.tick(self.store)                                # merges
        merged = [json.loads(e["data"]) for e in self.store.events("T1") if e["kind"] == "merged"]
        self.assertEqual(merged, [{"into": "main", "by": "engine"}])

    def test_owner_merge_is_not_marked(self):
        finish.order(self.store, "T1")
        finish.step(self.store, self.task())
        finish.step(self.store, self.task())
        merged = [json.loads(e["data"]) for e in self.store.events("T1") if e["kind"] == "merged"]
        self.assertEqual(merged, [{"into": "main"}])

    def test_link_goes_to_the_worker_once_then_finishes(self):
        finish.order(self.store, "T1", link=self.LINK)
        self.assertEqual(finish.step(self.store, self.task()), "requeued")
        self.assertEqual(self.task().status, "queued")
        self.assertIn(self.LINK, self.task().followups[-1])
        self.to("running", "review")                      # the worker recorded it
        self.assertEqual(finish.step(self.store, self.task()), "accepted")
        self.assertEqual(self.task().result["published"], self.LINK)
        self.assertEqual(len(self.task().followups), 1)

    def test_link_on_queued_task_is_added_at_once(self):
        self.to("queued")
        finish.order(self.store, "T1", link=self.LINK)
        self.assertIn(self.LINK, self.task().followups[-1])
        self.assertFalse(finish.get_order(self.store, "T1")["link_pending"])

    def test_link_on_done_task_is_never_sent_to_the_worker(self):
        self.to("done")
        finish.order(self.store, "T1", link=self.LINK)
        self.assertFalse(finish.get_order(self.store, "T1")["link_pending"])

    def test_running_task_waits(self):
        finish.order(self.store, "T1")
        self.to("queued", "running")
        self.assertEqual(finish.step(self.store, self.task()), "waiting")
        self.assertIsNotNone(finish.get_order(self.store, "T1"))

    def test_cancelled_task_drops_order(self):
        finish.order(self.store, "T1")
        self.store.set_status("T1", "cancelled")
        finish.tick(self.store)
        self.assertIsNone(finish.get_order(self.store, "T1"))
        self.assertFalse(worktrees.is_merged(self.repo, self.task().branch, "main"))

    def test_blocked_offers_once_and_merges_later(self):
        self.done_with_order()
        write(self.repo, "README.md", "owner's edit")
        for _ in range(3):
            finish.tick(self.store)
        self.assertEqual(self.kinds(), ["merge_blocked"])
        offer = attention.pick(self.store, boundary=True, suggest_next=False)
        self.assertIn("is ready to merge, but", offer.text)
        self.assertIn("uncommitted changes", offer.text)
        git(self.repo, "checkout", "--", "README.md")
        finish.tick(self.store)
        self.assertIsNone(finish.get_order(self.store, "T1"))
        self.assertEqual(self.store.open_attention(), [])
        self.assertTrue(worktrees.is_merged(self.repo, self.task().branch, "main"))

    def test_conflict_goes_back_to_worker_then_offers_on_second(self):
        self.done_with_order()
        self.make_conflict()
        self.assertEqual(finish.step(self.store, self.task()), "conflict")
        self.assertEqual(self.task().status, "queued")
        self.assertIn("do not rebase", self.task().followups[-1])
        self.to("running", "review")
        self.assertEqual(finish.step(self.store, self.task()), "accepted")
        self.assertEqual(finish.step(self.store, self.task()), "failed")
        self.assertEqual(self.kinds(), ["merge_failed"])
        self.assertIsNone(finish.get_order(self.store, "T1"))
        self.assertFalse(os.path.exists(os.path.join(self.repo, ".git", "MERGE_HEAD")))
        self.assertEqual(git(self.repo, "status", "--porcelain"), "")

    def test_order_with_a_failing_merge_tells_the_owner_not_the_worker(self):
        self.done_with_order()
        self.refuse_merges()
        self.assertEqual(finish.step(self.store, self.task()), "error")
        self.assertEqual(self.task().status, "done")
        self.assertEqual(self.task().followups, [])
        self.assertEqual(self.kinds(), ["merge_failed"])
        self.assertIn("signing key not found", self.store.open_attention()[0]["note"])
        self.assertIn("can't merge", finish.say(self.store, self.task(), "error"))

    def test_conflict_resolved_by_worker_then_merges(self):
        self.done_with_order()
        self.make_conflict()
        finish.step(self.store, self.task())
        wt = self.task().worktree
        git(wt, "merge", "-q", "main", "-X", "ours", "-m", "merge main")
        self.to("running", "review")
        finish.tick(self.store)
        finish.tick(self.store)
        self.assertIsNone(finish.get_order(self.store, "T1"))
        self.assertTrue(worktrees.is_merged(self.repo, self.task().branch, "main"))

    def test_one_broken_order_does_not_stop_others(self):
        repo_b = make_repo(self.tmp, "app-b")
        self.store.add_repo(repo_b)
        self.add_worked_task("app-b", "Logout", "b.txt")
        self.to("queued", "running", "review", "done", tid="T2")
        self.done_with_order()
        finish.order(self.store, "T2")
        os.rename(self.repo, self.repo + "-moved")
        finish.tick(self.store)
        self.assertTrue(worktrees.is_merged(repo_b, self.store.get_task("T2").branch, "main"))
        self.assertIn("finish_failed", [e["kind"] for e in self.store.events("T1")])
        self.assertIsNotNone(finish.get_order(self.store, "T1"))


class ReviewFixesTest(FinishCase):
    path = ("queued", "running", "review")

    def test_yes_to_merge_blocked_is_not_asked_again(self):
        self.to("done")
        finish.order(self.store, "T1")
        write(self.repo, "README.md", "owner's edit")
        finish.tick(self.store)
        item = self.store.open_attention()[0]
        attention.accept(self.store, item["id"])            # the owner said yes
        for _ in range(3):
            finish.tick(self.store)
        self.assertEqual(self.store.open_attention(), [])
        git(self.repo, "checkout", "--", "README.md")
        finish.tick(self.store)
        self.assertIsNone(finish.get_order(self.store, "T1"))
        self.assertTrue(worktrees.is_merged(self.repo, self.task().branch, "main"))

    def test_task_that_cannot_merge_can_be_cancelled(self):
        self.to("done")
        self.store.set_status("T1", "cancelled")
        self.assertEqual(self.task().status, "cancelled")

    def test_broken_order_gives_up_with_one_offer(self):
        self.to("done")
        finish.order(self.store, "T1")
        os.rename(self.repo, self.repo + "-moved")
        for _ in range(5):
            finish.tick(self.store)
        self.assertIsNone(finish.get_order(self.store, "T1"))
        items = self.store.open_attention()
        self.assertEqual([i["kind"] for i in items], ["merge_failed"])
        self.assertIn("git can't read", items[0]["note"])
        self.assertEqual([e["kind"] for e in self.store.events("T1")].count("finish_failed"), 3)

    def test_order_without_link_keeps_a_pending_link(self):
        self.to("queued", "running")
        finish.order(self.store, "T1", link="https://example.invalid/p/1")
        finish.order(self.store, "T1")
        o = finish.get_order(self.store, "T1")
        self.assertEqual((o["link"], o["link_pending"]), ("https://example.invalid/p/1", True))
