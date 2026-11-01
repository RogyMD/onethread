import unittest

from demo.run_demo import main


class DemoScenarioTest(unittest.TestCase):
    """The owner's scenario, end to end, with fake agents and no real tabs."""

    @classmethod
    def setUpClass(cls):
        cls.log = main(quiet=True)

    def test_five_tasks_across_three_repos(self):
        for tid in ("T1", "T2", "T3", "T4", "T5"):
            self.assertIn(f"({tid})", self.log["added"])

    def test_tasks_are_cast(self):
        names = ("Sir Lancelot", "Sir Gawain", "Merlin", "Sir Percival", "Sir Galahad")
        for line in self.log["added"].splitlines():
            if line.startswith("Added"):
                self.assertTrue(any(f"Added {n}: " in line for n in names), line)
        self.assertIn("Right hand: Chancellor", self.log["cast"])

    def test_switching_prints_card_and_tab_command(self):
        self.assertIn('Switching you to Sir Gawain: "Fix settings UI bug" in app-b (T2, Fake)', self.log["open"])
        self.assertIn("session T2", self.log["open"])
        self.assertIn('Sir Gawain: "Fix settings UI bug" in app-b (T2) waits for you', self.log["session"])

    def test_urgent_task_preempts_only_after_approval(self):
        self.assertIn('Pause Sir Percival: \\"Update dependencies\\" in shared-lib (T4)', self.log["preempt_offer"])
        self.assertIn('Sir Percival: "Update dependencies" in shared-lib (T4) paused', self.log["pause"])

    def test_takeover_of_a_running_task(self):
        self.assertIn('Switching you to Sir Kay: "Fix login crash on launch" in app-a (T6, Fake)', self.log["takeover"])

    def test_finished_work_is_offered_at_the_next_message(self):
        self.assertIn("T1", self.log["review_offer"])
        self.assertIn("ready for review", self.log["review_offer"])

    def test_restart_reattaches_running_workers(self):
        self.assertTrue(self.log["restart"]["reattached"])
        self.assertEqual(self.log["restart"]["finalized"], [])

    def test_review_and_accept(self):
        self.assertIn("Worker result", self.log["review"])
        self.assertIn("merged into main", self.log["accept"])
        self.assertIn("fake: work for T1", self.log["commits_T1"])

    def test_final_state_is_consistent(self):
        final = self.log["final"]
        self.assertEqual(final["T1"], "done")
        self.assertEqual(final["T2"], "review")
        self.assertEqual(final["T6"], "review")
        self.assertEqual((final["T3"], final["T5"]), ("draft", "draft"))
        self.assertIn(final["T4"], ("queued", "running"))
        self.assertEqual(sorted(self.log["open_runs"]),
                         sorted(t for t, s in final.items() if s == "running"))
        self.assertEqual(self.log["runs_per_task"]["T1"], 1)    # never relaunched


if __name__ == "__main__":
    unittest.main()
