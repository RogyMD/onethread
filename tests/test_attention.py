import unittest

from onethread import attention
from tests.helpers import TempHomeCase, make_repo


class AttentionTest(TempHomeCase):
    def setUp(self):
        super().setUp()
        self.store.add_repo(make_repo(self.tmp, "app-a"))
        self.t1 = self.store.add_task("app-a", "Auth")
        self.t2 = self.store.add_task("app-a", "Dark mode")

    def to_review(self, tid):
        for status in ("queued", "running", "review"):
            self.store.set_status(tid, status)

    def boundary(self, focus=None):
        offer = attention.pick(self.store, boundary=True, focus=focus)
        attention.after_boundary(self.store)
        return offer

    def test_no_offer_on_a_non_boundary_turn(self):
        self.store.add_attention("T1", "review_ready")
        self.assertIsNone(attention.pick(self.store, boundary=False))

    def test_one_offer_per_boundary_highest_score(self):
        self.store.add_attention("T1", "review_ready")
        self.store.add_attention("T2", "blocked", "Which OAuth provider?")
        offer = self.boundary()
        self.assertEqual((offer.task, offer.kind), ("T2", "blocked"))
        self.assertIn("Which OAuth provider?", offer.text)

    def test_ties_go_to_the_oldest(self):
        self.store.add_attention("T2", "review_ready")
        self.store.add_attention("T1", "review_ready")
        self.assertEqual(self.boundary().task, "T2")

    def test_offer_text_names_task_title_and_repo(self):
        self.store.add_attention("T1", "review_ready")
        text = self.boundary().text
        for part in ("T1", "Auth", "app-a"):
            self.assertIn(part, text)

    def test_postpone_hides_item_for_three_boundaries(self):
        self.to_review("T1")
        item = self.store.add_attention("T1", "review_ready")
        attention.postpone(self.store, item)
        seen = [self.boundary(focus="T2") for _ in range(4)]
        self.assertEqual([o and o.task for o in seen], [None, None, None, "T1"])

    def test_postpone_until_a_time(self):
        self.to_review("T1")
        item = self.store.add_attention("T1", "review_ready")
        attention.postpone(self.store, item, until=self.store.clock() + 3600)
        for _ in range(5):
            self.assertIsNone(self.boundary(focus="T2"))
        later = attention.pick(self.store, boundary=True, focus="T2",
                               now=self.store.clock() + 7200)
        self.assertEqual(later.task, "T1")

    def test_urgent_items_are_offered_on_any_turn_end(self):
        self.store.add_attention("T1", "blocked", "need key")
        self.assertIsNone(attention.pick(self.store, boundary=False))
        self.store.update_task("T1", priority="urgent")
        offer = attention.pick(self.store, boundary=False)
        self.assertEqual((offer.task, offer.kind), ("T1", "blocked"))

    def test_interrupted_worker_is_offered_on_any_turn_end(self):
        self.store.add_attention("T2", "interrupted")
        self.assertEqual(attention.pick(self.store, boundary=False).kind,
                         "interrupted")

    def test_next_draft_task_is_suggested_but_not_the_focused_one(self):
        offer = self.boundary(focus="T1")
        self.assertEqual((offer.task, offer.kind), ("T2", "next_task"))
        self.assertIn("Next up", offer.text)

    def test_next_task_can_be_left_out(self):
        self.assertIsNone(attention.pick(self.store, boundary=True, suggest_next=False))

    def test_accept_closes_the_item(self):
        item = self.store.add_attention("T1", "review_ready")
        attention.accept(self.store, item)
        self.assertEqual(self.store.open_attention(), [])


    def test_preempt_offer_disappears_once_urgent_task_is_no_longer_queued(self):
        self.store.update_task("T2", priority="urgent")
        self.store.set_status("T2", "queued")
        self.store.add_attention("T2", "preempt", "T1")
        self.store.set_status("T2", "running")
        self.assertIsNone(attention.pick(self.store, boundary=False))

if __name__ == "__main__":
    unittest.main()
