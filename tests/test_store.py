import os
import unittest

from onethread.model import TransitionError, check_transition
from tests.helpers import TempHomeCase, make_repo


class LifecycleTest(unittest.TestCase):
    def test_allowed_transitions_pass(self):
        for old, new in [("draft", "queued"), ("queued", "running"),
                         ("running", "review"), ("running", "blocked"),
                         ("running", "failed"), ("running", "interrupted"),
                         ("running", "paused"), ("review", "done"),
                         ("review", "queued"), ("blocked", "queued"),
                         ("paused", "queued"), ("interrupted", "queued"),
                         ("failed", "queued"), ("draft", "cancelled"),
                         ("running", "cancelled"), ("done", "queued")]:
            check_transition(old, new)

    def test_disallowed_transitions_raise(self):
        for old, new in [("draft", "running"), ("running", "done"),
                         ("queued", "review"), ("done", "review"),
                         ("cancelled", "queued"), ("draft", "review")]:
            with self.assertRaises(TransitionError):
                check_transition(old, new)


class TakeoverLifecycleTest(unittest.TestCase):
    OPEN = ("draft", "queued", "running", "review", "blocked", "paused",
            "failed", "interrupted", "parked")

    def test_any_open_status_can_go_with_you(self):
        for old in self.OPEN:
            check_transition(old, "with_you")
        for old in ("done", "cancelled"):
            with self.assertRaises(TransitionError):
                check_transition(old, "with_you")

    def test_with_you_only_to_parked(self):
        check_transition("with_you", "parked")
        for new in ("queued", "running", "review", "done", "cancelled"):
            with self.assertRaises(TransitionError):
                check_transition("with_you", new)

    def test_parked_transitions(self):
        for new in ("queued", "review", "with_you", "cancelled"):
            check_transition("parked", new)
        for new in ("running", "done"):
            with self.assertRaises(TransitionError):
                check_transition("parked", new)


class StoreTest(TempHomeCase):
    def test_new_task_uses_orchestrator_agent(self):
        self.store.add_repo(make_repo(self.tmp, "app-a"))
        self.assertEqual(self.store.add_task("app-a", "A").agent, "claude")
        self.store.set_meta("orchestrator", "codex")
        self.assertEqual(self.store.add_task("app-a", "B").agent, "codex")
        self.assertEqual(self.store.add_task("app-a", "C", agent="claude").agent, "claude")

    def test_runnable_ignores_with_you_and_parked(self):
        from onethread import scheduler
        self.store.add_repo(make_repo(self.tmp, "app-a"))
        for title in ("A", "B", "C"):
            self.store.add_task("app-a", title)
            self.store.set_status(self.store.list_tasks()[-1].id, "queued")
        self.store.set_status("T1", "with_you")
        self.store.set_status("T2", "with_you")
        self.store.set_status("T2", "parked")
        self.assertEqual([t.id for t in scheduler.runnable(self.store, now=1e12)], ["T3"])

    def test_repo_add_rejects_non_git_path(self):
        plain = os.path.join(self.tmp, "plain")
        os.makedirs(plain)
        with self.assertRaises(ValueError):
            self.store.add_repo(plain)

    def test_repo_add_uses_folder_name_and_current_branch(self):
        path = make_repo(self.tmp, "app-a")
        repo = self.store.add_repo(path)
        self.assertEqual(repo["name"], "app-a")
        self.assertEqual(repo["base_branch"], "main")

    def test_task_ids_are_sequential_and_default_fields(self):
        self.store.add_repo(make_repo(self.tmp, "app-a"))
        t1 = self.store.add_task("app-a", "Auth")
        t2 = self.store.add_task("app-a", "Dark mode", priority="low")
        self.assertEqual((t1.id, t2.id), ("T1", "T2"))
        self.assertEqual(t1.status, "draft")
        self.assertEqual(t1.priority, "normal")
        self.assertEqual(t2.priority, "low")

    def test_add_task_rejects_unknown_repo_and_priority(self):
        with self.assertRaises(ValueError):
            self.store.add_task("nope", "x")
        self.store.add_repo(make_repo(self.tmp, "app-a"))
        with self.assertRaises(ValueError):
            self.store.add_task("app-a", "x", priority="asap")

    def test_set_status_enforces_lifecycle_and_logs_event(self):
        self.store.add_repo(make_repo(self.tmp, "app-a"))
        t = self.store.add_task("app-a", "Auth")
        self.store.set_status(t.id, "queued")
        with self.assertRaises(TransitionError):
            self.store.set_status(t.id, "done")
        kinds = [e["kind"] for e in self.store.events(t.id)]
        self.assertIn("status:draft->queued", kinds)

    def test_dependencies_reject_unknown_and_cycles(self):
        self.store.add_repo(make_repo(self.tmp, "app-a"))
        a = self.store.add_task("app-a", "A")
        b = self.store.add_task("app-a", "B")
        self.store.add_dep(b.id, a.id)
        self.assertEqual(self.store.deps_of(b.id), ["T1"])
        with self.assertRaises(ValueError):
            self.store.add_dep(a.id, b.id)
        with self.assertRaises(ValueError):
            self.store.add_dep(a.id, "T99")

    def test_everything_survives_reopen(self):
        self.store.add_repo(make_repo(self.tmp, "app-a"))
        t = self.store.add_task("app-a", "Auth", description="login",
                                priority="high")
        self.store.update_task(t.id, session_id="abc", agent="claude")
        self.store.set_meta("capacity", "4")
        store = self.reopen()
        again = store.get_task("T1")
        self.assertEqual((again.title, again.description, again.priority,
                          again.session_id, again.agent),
                         ("Auth", "login", "high", "abc", "claude"))
        self.assertEqual(store.get_meta("capacity"), "4")
        self.assertEqual(store.add_task("app-a", "Next").id, "T2")


if __name__ == "__main__":
    unittest.main()
