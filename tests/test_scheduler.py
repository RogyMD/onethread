import unittest

from onethread import scheduler
from tests.helpers import TempHomeCase, make_repo


class SchedulerTest(TempHomeCase):
    def setUp(self):
        super().setUp()
        for name in ("app-a", "app-b"):
            self.store.add_repo(make_repo(self.tmp, name))
        self.launched = []

    def launch(self, task):
        self.launched.append(task.id)
        self.store.set_status(task.id, "running")

    def queued(self, repo, title, **kw):
        t = self.store.add_task(repo, title, **kw)
        self.store.set_status(t.id, "queued")
        return t

    def test_default_capacity_is_ten(self):
        self.assertEqual(scheduler.capacity(self.store), 10)
        for i in range(12):
            self.queued("app-a", f"t{i}")
        scheduler.tick(self.store, self.launch)
        self.assertEqual(len(self.launched), 10)

    def test_priority_order_then_fifo(self):
        self.store.set_meta("capacity", "2")
        self.queued("app-a", "low", priority="low")
        self.queued("app-a", "normal1")
        self.queued("app-b", "urgent", priority="urgent")
        self.queued("app-b", "normal2")
        scheduler.tick(self.store, self.launch)
        self.assertEqual(self.launched, ["T3", "T2"])

    def test_capacity_counts_running_tasks(self):
        self.store.set_meta("capacity", "2")
        self.queued("app-a", "a")
        scheduler.tick(self.store, self.launch)
        self.queued("app-a", "b")
        self.queued("app-a", "c")
        scheduler.tick(self.store, self.launch)
        self.assertEqual(self.launched, ["T1", "T2"])
        self.store.set_status("T1", "review")
        scheduler.tick(self.store, self.launch)
        self.assertEqual(self.launched, ["T1", "T2", "T3"])

    def test_cross_repo_dependency_blocks_until_done(self):
        lib = self.queued("app-b", "shared change")
        app = self.queued("app-a", "use it")
        self.store.add_dep(app.id, lib.id)
        scheduler.tick(self.store, self.launch)
        self.assertEqual(self.launched, ["T1"])
        self.store.set_status("T1", "review")      # finished but not accepted
        scheduler.tick(self.store, self.launch)
        self.assertEqual(self.launched, ["T1"])
        self.store.set_status("T1", "done")
        scheduler.tick(self.store, self.launch)
        self.assertEqual(self.launched, ["T1", "T2"])

    def test_not_before_is_respected(self):
        self.queued("app-a", "tomorrow", not_before=self.store.clock() + 3600)
        scheduler.tick(self.store, self.launch)
        self.assertEqual(self.launched, [])
        scheduler.tick(self.store, self.launch, now=self.store.clock() + 7200)
        self.assertEqual(self.launched, ["T1"])

    def test_urgent_with_full_capacity_offers_preempt_and_pauses_nothing(self):
        self.store.set_meta("capacity", "2")
        self.queued("app-a", "normal")
        self.queued("app-a", "low", priority="low")
        scheduler.tick(self.store, self.launch)
        self.queued("app-b", "fire", priority="urgent")
        scheduler.tick(self.store, self.launch)
        scheduler.tick(self.store, self.launch)   # no duplicate offer
        self.assertEqual(self.launched, ["T1", "T2"])
        self.assertEqual(self.store.get_task("T3").status, "queued")
        items = self.store.open_attention()
        self.assertEqual([(i["task"], i["kind"], i["note"]) for i in items],
                         [("T3", "preempt", "T2")])
        self.assertEqual(self.store.get_task("T2").status, "running")

    def test_equal_priority_prefers_task_whose_resources_are_free(self):
        self.store.set_meta("capacity", "1")
        t1 = self.queued("app-a", "ui test")
        self.store.update_task(t1.id, resources=["xcode"])
        self.queued("app-b", "docs")
        scheduler.tick(self.store, self.launch, busy_classes=lambda: {"xcode"})
        self.assertEqual(self.launched, ["T2"])


if __name__ == "__main__":
    unittest.main()
