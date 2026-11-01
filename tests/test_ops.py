import os
import unittest

from onethread import attention
from onethread.ops import Ops, parse_when
from onethread.schemas import OPERATION
from tests.helpers import TempHomeCase, git, make_repo


def op(name, **kw):
    base = {k: "" for k in OPERATION["properties"]}
    base["depends_on"] = []
    base.update(op=name, **kw)
    return base


class StubSupervisor:
    def __init__(self, store):
        self.store = store
        self.stopped = []

    def stop(self, task_id, new_status, grace=10.0):
        self.stopped.append((task_id, new_status))
        self.store.set_status(task_id, new_status)
        return True

    def pause(self, task_id, grace=10.0):
        return self.stop(task_id, "paused")


class OpsTest(TempHomeCase):
    def setUp(self):
        super().setUp()
        self.repo = make_repo(self.tmp, "app-a")
        self.store.add_repo(self.repo)
        self.store.add_repo(make_repo(self.tmp, "app-b"))
        self.sup = StubSupervisor(self.store)
        self.questions = []
        self.answer = True
        self.ops = Ops(self.store, self.sup, self.confirm, self.home)
        self.t1 = self.store.add_task("app-a", "Auth")

    def confirm(self, question):
        self.questions.append(question)
        return self.answer

    def run_task(self, tid):
        self.store.set_status(tid, "queued")
        self.store.set_status(tid, "running")

    # validation ----------------------------------------------------------
    def test_unknown_task_or_repo_is_rejected_without_changes(self):
        res = self.ops.apply(op("approve_implementation", task="T99"), origin="agent")
        self.assertFalse(res.ok)
        self.assertIn("T99", res.message)
        res = self.ops.apply(op("add_task", repo="nope", title="x"), origin="agent")
        self.assertFalse(res.ok)
        self.assertEqual(len(self.store.list_tasks()), 1)

    def test_unknown_operation_from_agent_is_rejected(self):
        self.assertFalse(self.ops.apply(op("merge", task="T1"), origin="agent").ok)

    def test_add_task_defaults_repo_to_focused_task(self):
        res = self.ops.apply(op("add_task", title="Fix crash", priority="urgent",
                                when="tomorrow", depends_on=["T1"]), focus="T1")
        self.assertTrue(res.ok, res.message)
        t2 = self.store.get_task("T2")
        self.assertEqual((t2.repo, t2.priority), ("app-a", "urgent"))
        self.assertGreater(t2.not_before, self.store.clock())
        self.assertEqual(self.store.deps_of("T2"), ["T1"])
        self.assertIn("T2", res.message)

    def test_approve_moves_draft_to_queued_only(self):
        self.assertTrue(self.ops.apply(op("approve_implementation"), focus="T1").ok)
        self.assertEqual(self.store.get_task("T1").status, "queued")
        self.assertFalse(self.ops.apply(op("approve_implementation", task="T1")).ok)

    # destructive operations ------------------------------------------------
    def test_pause_running_task_asks_and_does_nothing_on_no(self):
        self.run_task("T1")
        self.answer = False
        res = self.ops.apply(op("pause", task="T1"))
        self.assertFalse(res.ok)
        self.assertEqual(self.store.get_task("T1").status, "running")
        self.assertIn("T1", self.questions[0])
        self.answer = True
        self.assertTrue(self.ops.apply(op("pause", task="T1")).ok)
        self.assertEqual(self.sup.stopped, [("T1", "paused")])

    def test_cancel_always_asks(self):
        self.answer = False
        self.assertFalse(self.ops.apply(op("cancel", task="T1")).ok)
        self.assertEqual(self.store.get_task("T1").status, "draft")
        self.answer = True
        self.assertTrue(self.ops.apply(op("cancel", task="T1")).ok)
        self.assertEqual(self.store.get_task("T1").status, "cancelled")

    def test_agent_focus_needs_agreement_user_focus_does_not(self):
        self.store.add_task("app-b", "Bug")
        self.answer = False
        res = self.ops.apply(op("focus", task="T2"), origin="agent")
        self.assertIsNone(res.focus)
        self.answer = True
        res = self.ops.apply(op("focus", task="T2"), origin="agent")
        self.assertEqual(res.focus, "T2")
        self.assertEqual(len(self.questions), 2)
        res = self.ops.apply(op("focus", task=""), origin="user")
        self.assertEqual(res.focus, "")
        self.assertEqual(len(self.questions), 2)

    # offers ----------------------------------------------------------------
    def test_accept_review_offer_focuses_task_and_closes_item(self):
        self.run_task("T1")
        self.store.set_status("T1", "review")
        item = self.store.add_attention("T1", "review_ready")
        offer = attention.Offer(item, "T1", "review_ready", "")
        res = self.ops.apply(op("accept_offer"), offer=offer)
        self.assertEqual(res.focus, "T1")
        self.assertEqual(self.store.open_attention(), [])

    def test_accept_preempt_offer_pauses_victim_after_confirm(self):
        self.run_task("T1")
        self.store.add_task("app-b", "Fire", priority="urgent")
        item = self.store.add_attention("T2", "preempt", "T1")
        res = self.ops.apply(op("accept_offer"), offer=attention.Offer(item, "T2", "preempt", ""))
        self.assertTrue(res.ok)
        self.assertEqual(self.sup.stopped, [("T1", "paused")])

    def test_postpone_offer_snoozes(self):
        item = self.store.add_attention("T1", "review_ready")
        res = self.ops.apply(op("postpone_offer"),
                             offer=attention.Offer(item, "T1", "review_ready", ""))
        self.assertTrue(res.postponed)
        self.assertEqual(self.store.get_attention(item)["snooze_boundaries"], 3)

    # reviews and answers ---------------------------------------------------
    def test_review_accept_marks_done(self):
        self.run_task("T1")
        self.store.set_status("T1", "review")
        self.store.add_attention("T1", "review_ready")
        self.assertTrue(self.ops.apply(op("review_accept", task="T1")).ok)
        self.assertEqual(self.store.get_task("T1").status, "done")
        self.assertEqual(self.store.open_attention(), [])

    def test_request_changes_and_answer_requeue_with_followup(self):
        self.run_task("T1")
        self.store.set_status("T1", "review")
        self.ops.apply(op("request_changes", task="T1", text="Add logout"))
        t = self.store.get_task("T1")
        self.assertEqual((t.status, t.followups), ("queued", ["Add logout"]))
        self.store.set_status("T1", "running")
        self.store.set_status("T1", "blocked")
        self.ops.apply(op("answer", task="T1", text="Use passkeys"))
        t = self.store.get_task("T1")
        self.assertEqual((t.status, t.followups), ("queued", ["Add logout", "Use passkeys"]))

    # merge / PR ------------------------------------------------------------
    def _done_with_commit(self):
        from onethread import worktrees
        t = worktrees.ensure(self.store, self.home, "T1")
        with open(os.path.join(t.worktree, "auth.py"), "w") as f:
            f.write("x")
        git(t.worktree, "add", ".")
        git(t.worktree, "commit", "-q", "-m", "auth")
        self.run_task("T1")
        self.store.set_status("T1", "review")
        self.store.set_status("T1", "done")
        return t

    def test_merge_asks_and_merges_into_base(self):
        t = self._done_with_commit()
        self.answer = False
        self.assertFalse(self.ops.merge("T1").ok)
        self.assertFalse(os.path.exists(os.path.join(self.repo, "auth.py")))
        self.answer = True
        res = self.ops.merge("T1")
        self.assertTrue(res.ok, res.message)
        self.assertTrue(os.path.exists(os.path.join(self.repo, "auth.py")))
        self.assertIn(t.branch, self.questions[-1])

    def test_merge_stacked_task_waits_for_its_dependency(self):
        from onethread import worktrees
        from onethread.model import label
        self._done_with_commit()
        t2 = self.store.add_task("app-a", "Logout")
        self.store.add_dep(t2.id, "T1")
        worktrees.ensure(self.store, self.home, t2.id)
        self.run_task(t2.id)
        self.store.set_status(t2.id, "review")
        self.store.set_status(t2.id, "done")
        res = self.ops.merge(t2.id)
        self.assertFalse(res.ok)
        self.assertIn("waits for", res.message)
        self.assertIn(label(self.store.get_task("T1")), res.message)
        self.assertEqual(self.questions, [])

    def test_merge_refuses_dirty_main_checkout(self):
        self._done_with_commit()
        with open(os.path.join(self.repo, "README.md"), "a") as f:
            f.write("local edit\n")
        res = self.ops.merge("T1")
        self.assertFalse(res.ok)
        self.assertIn("uncommitted", res.message)
        self.assertEqual(self.questions, [])

    def test_open_pr_does_nothing_without_confirmation(self):
        self._done_with_commit()
        self.answer = False
        res = self.ops.open_pr("T1")
        self.assertFalse(res.ok)
        self.assertIn("push", self.questions[0])


    def test_agent_review_accept_and_resume_ask_first(self):
        self.run_task("T1")
        self.store.set_status("T1", "review")
        self.answer = False
        self.assertFalse(self.ops.apply(op("review_accept", task="T1"), origin="agent").ok)
        self.assertEqual(self.store.get_task("T1").status, "review")
        self.store.add_task("app-a", "Other")
        self.run_task("T2")
        self.store.set_status("T2", "paused")
        self.assertFalse(self.ops.apply(op("resume", task="T2"), origin="agent").ok)
        self.assertEqual(self.store.get_task("T2").status, "paused")

    def test_postpone_with_when_later_snoozes(self):
        item = self.store.add_attention("T1", "review_ready")
        res = self.ops.apply(op("postpone_offer", when="later"),
                             offer=attention.Offer(item, "T1", "review_ready", ""))
        self.assertTrue(res.ok, res.message)
        self.assertEqual(self.store.get_attention(item)["snooze_boundaries"], 3)

    # names first: the owner reads names, not bare ids ------------------------
    def test_messages_lead_with_the_task_name(self):
        name = '"Auth" in app-a (T1)'
        msgs = [self.ops.apply(op("set_priority", task="T1", priority="high")).message,
                self.ops.apply(op("approve_implementation", task="T1")).message,
                self.ops.apply(op("pause", task="T1")).message]
        self.answer = True
        msgs.append(self.ops.apply(op("cancel", task="T1")).message)
        for m in msgs:
            self.assertIn(name, m)
            self.assertFalse(m.startswith("T1"), m)

    def test_views_and_offers_put_the_name_first(self):
        from onethread import views, voice
        t = self.store.get_task("T1")
        self.assertEqual(views.task_card(t), 'Switching you to "Auth" in app-a (T1, Claude)')
        line = views.status_lines(self.store)[-1]
        self.assertLess(line.index('"Auth"'), line.index("T1"))
        offer = voice.offer("review_ready", t)
        self.assertIn('"Auth" in app-a (T1)', offer)


    def test_status_and_inbox_lead_with_cast_name(self):
        from onethread import views
        self.store.update_task("T1", cast_name="Cicero")
        self.store.add_attention("T1", "review_ready")
        self.assertIn('  Cicero · "Auth" · app-a · draft · normal', "\n".join(views.status_lines(self.store)))
        self.assertIn('  Cicero · "Auth" · app-a · review ready · T1', views.inbox_lines(self.store))
        self.store.update_task("T1", cast_name="")
        self.assertIn('  "Auth" · app-a · draft', "\n".join(views.status_lines(self.store)))


class WhenTest(unittest.TestCase):
    def test_parse_when(self):
        now = 1_000_000.0
        self.assertEqual(parse_when("", now), 0.0)
        self.assertEqual(parse_when("in 2 hours", now), now + 7200)
        self.assertGreater(parse_when("tomorrow", now), now + 3600)
        self.assertGreater(parse_when("2030-01-01T09:00", now), now)
        with self.assertRaises(ValueError):
            parse_when("whenever", now)


if __name__ == "__main__":
    unittest.main()
