import fcntl
import json
import os
from unittest import mock

from onethread import attention, notify
from tests.test_tips_cli import CliCase


class WaitCliTest(CliCase):
    def test_wait_refuses_inside_codex_sandbox(self):
        with mock.patch.dict(os.environ, {"CODEX_SANDBOX": "seatbelt"}):
            code, _, err = self.ot("wait")
        self.assertEqual(code, 1)
        self.assertIn("ot wait is for the Claude orchestrator; Codex gets the banner.", err)

    def test_second_wait_says_already_waiting(self):
        with open(os.path.join(self.home, "wait.lock"), "a") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            code, out, _ = self.ot("wait")
        self.assertEqual((code, out), (0, "Already waiting.\n"))

    def test_wait_prints_news(self):
        with mock.patch.object(notify, "wait", return_value=["[offer 3 review_ready T1] x"]):
            code, out, _ = self.ot("wait")
        self.assertEqual((code, out), (0, "[offer 3 review_ready T1] x\n"))

    def test_wait_is_not_journaled(self):
        with mock.patch.object(notify, "wait", return_value=[]):
            self.ot("wait")
        self.assertNotIn("wait", self.cmds())


class HookWaitTest(CliCase):
    def context(self):
        code, out, _ = self.ot("hook", "prompt", stdin='{"prompt": "hi"}')
        return json.loads(out)["hookSpecificOutput"]["additionalContext"]

    def test_hook_asks_for_a_waiter_when_none_runs(self):
        self.store.set_meta("orchestrator", "claude")
        self.assertIn("No `ot wait` is running: start one in the background now.", self.context())

    def test_hook_quiet_about_waiting_while_one_runs(self):
        self.store.set_meta("orchestrator", "claude")
        with open(os.path.join(self.home, "wait.lock"), "a") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertNotIn("ot wait", self.context())

    def test_hook_quiet_about_waiting_for_codex(self):
        self.store.set_meta("orchestrator", "codex")
        self.assertNotIn("ot wait", self.context())


class OwnLobbyTest(CliCase):
    def test_wait_serves_the_lobby_it_runs_in(self):
        self.store.set_meta("lobby_pid", "2:B")
        with mock.patch.dict(os.environ, {"ONETHREAD_LOBBY_PID": "1:A"}), \
             mock.patch.object(notify, "wait", return_value=[]) as waited:
            self.ot("wait")
        self.assertEqual(waited.call_args[0][1], "1:A")

    def test_hook_quiet_about_waiting_in_an_older_lobby(self):
        self.store.set_meta("orchestrator", "claude")
        self.store.set_meta("lobby_pid", "2:B")
        with mock.patch.dict(os.environ, {"ONETHREAD_LOBBY_PID": "1:A"}):
            self.assertNotIn("ot wait", HookWaitTest.context(self))


class BannerHintTest(CliCase):
    def setUp(self):
        super().setUp()
        self.store.x("DELETE FROM meta WHERE key='notify'")     # the default: on

    def context(self, own=None):
        env = {"ONETHREAD_LOBBY_PID": own} if own else {}
        with mock.patch.dict(os.environ, env):
            if not own:
                os.environ.pop("ONETHREAD_LOBBY_PID", None)
            return HookWaitTest.context(self)

    def test_hook_says_once_a_lobby_from_before_banners_gets_none(self):
        self.assertIn("opened before onethread had banners", self.context())
        self.assertNotIn("Banners", self.context())

    def test_hook_says_once_banners_need_iterm(self):
        self.store.set_meta("lobby_pid", "1:A")
        self.store.set_meta("lobby_term", "WezTerm")
        self.assertIn("Banners need iTerm2", self.context("1:A"))
        self.assertNotIn("Banners", self.context("1:A"))

    def test_hook_says_it_again_for_a_new_lobby(self):
        self.store.set_meta("lobby_term", "WezTerm")
        self.store.set_meta("lobby_pid", "1:A")
        self.context("1:A")
        self.store.set_meta("lobby_pid", "2:B")
        self.assertIn("Banners need iTerm2", self.context("2:B"))

    def test_hook_quiet_about_banners_in_iterm(self):
        self.store.set_meta("lobby_pid", "1:A")
        self.store.set_meta("lobby_term", "iTerm.app")
        self.assertNotIn("Banners", self.context("1:A"))

    def test_hook_quiet_about_banners_when_they_are_off(self):
        self.store.set_meta("notify", "0")
        self.assertNotIn("Banners", self.context())


class SeenTest(CliCase):
    def setUp(self):
        super().setUp()
        self.t = self.store.add_task("app-a", "Fix login")
        notify.tick(self.store)                                # primes

    def test_task_command_counts_its_open_items_as_heard(self):
        self.store.add_attention(self.t.id, "merge_blocked", "your repo has changes.")
        self.ot("look", self.t.id)
        self.assertEqual(notify.tick(self.store), [])

    def test_hook_offer_is_not_repeated_by_wait(self):
        item = self.store.add_attention(self.t.id, "review_ready")
        self.store.set_meta(f"notified:{item}", str(self.store.clock()))
        notify.since(self.store)
        self.store.log(self.t.id, "notified", {"item": item, "kind": "review_ready", "text": "x"})
        self.assertIn(f"[offer {item} ", HookWaitTest.context(self))
        self.assertEqual(notify.since(self.store), [])

    def test_later_with_a_time_clears_message_snoozes(self):
        item = self.store.add_attention(self.t.id, "review_ready")
        self.store.update_attention(item, snooze_boundaries=2)
        self.store.set_meta("pending_offer", str(item))
        self.ot("later", self.t.id, "in", "10", "minutes")
        self.assertEqual(self.store.get_attention(item)["snooze_boundaries"], 0)
        self.assertEqual(self.store.get_meta("pending_offer"), "")
