import os
from unittest import mock

from onethread import attention, notify
from onethread.model import label
from onethread.procs import process_start
from tests.helpers import TempHomeCase, make_repo


class NotifyCase(TempHomeCase):
    def setUp(self):
        super().setUp()
        self.now = 100.0
        self.store.clock = lambda: self.now
        self.store.add_repo(make_repo(self.tmp, "app-a"))
        self.t = self.store.add_task("app-a", "Fix login")

    def at(self, now):
        self.now = now
        return self


class NotifyTest(NotifyCase):
    def prime(self):
        notify.tick(self.store)
        self.now += 2

    def test_first_tick_announces_nothing_already_waiting(self):
        self.store.add_attention(self.t.id, "review_ready")
        self.assertEqual(notify.tick(self.store), [])
        self.now += 2
        self.assertEqual(notify.tick(self.store), [])

    def test_new_review_is_announced_once(self):
        self.prime()
        self.store.add_attention(self.t.id, "review_ready")
        got = notify.tick(self.store)
        self.assertEqual([(n["task"], n["kind"]) for n in got], [(self.t.id, "review_ready")])
        self.assertIn("is ready for review", got[0]["text"])
        self.now += 2
        self.assertEqual(notify.tick(self.store), [])
        self.assertEqual([e["kind"] for e in self.store.events() if e["kind"] == "notified"],
                         ["notified"])

    def test_quiet_kinds_are_not_announced(self):
        self.prime()
        for kind in ("next_task", "dirty_worktree", "parked"):
            self.store.add_attention(self.t.id, kind)
        self.assertEqual(notify.tick(self.store), [])

    def test_reminder_fires_when_its_time_is_up(self):
        self.prime()
        item = self.store.add_attention(self.t.id, "review_ready")
        self.assertEqual(len(notify.tick(self.store)), 1)
        attention.postpone(self.store, item, until=700)
        self.now = 600
        self.assertEqual(notify.tick(self.store), [])
        self.now = 701
        self.assertEqual([n["item"] for n in notify.tick(self.store)], [item])
        self.now = 703
        self.assertEqual(notify.tick(self.store), [])

    def test_reminder_on_item_older_than_first_tick_fires(self):
        item = self.store.add_attention(self.t.id, "review_ready")
        self.prime()                                           # it already waited: not announced
        attention.postpone(self.store, item, until=700)
        self.now = 701
        self.assertEqual([n["item"] for n in notify.tick(self.store)], [item])

    def test_owner_merge_is_not_announced(self):
        self.prime()
        self.store.log(self.t.id, "merged", {"into": "main"})
        self.assertEqual(notify.tick(self.store), [])

    def test_engine_merge_is_announced(self):
        self.prime()
        self.store.log(self.t.id, "merged", {"into": "main", "by": "engine"})
        got = notify.tick(self.store)
        self.assertEqual([(n["kind"], n["item"]) for n in got], [("merged", None)])
        self.assertEqual(got[0]["text"], f"{label(self.t)} was merged into main.")
        self.now += 2
        self.assertEqual(notify.tick(self.store), [])

    def test_post_gets_each_text_unless_notify_is_off(self):
        self.store.x("DELETE FROM meta WHERE key='notify'")     # the default: on
        self.prime()
        posted = []
        self.store.add_attention(self.t.id, "failed", "Tests broke.")
        notify.tick(self.store, post=lambda s, text: posted.append(text))
        self.assertEqual(len(posted), 1)
        self.assertIn("failed", posted[0])
        self.store.set_meta("notify", "0")
        self.store.add_attention(self.t.id, "blocked", "Which key?")
        self.now += 2
        got = notify.tick(self.store, post=lambda s, text: posted.append(text))
        self.assertEqual(len(got), 1)
        self.assertEqual(len(posted), 1)

    def test_failing_post_does_not_raise(self):
        self.prime()
        self.store.add_attention(self.t.id, "review_ready")

        def boom(store, text):
            raise OSError("no terminal")
        self.assertEqual(len(notify.tick(self.store, post=boom)), 1)

    def test_reminder_pending(self):
        self.assertFalse(notify.reminder_pending(self.store, self.now))
        item = self.store.add_attention(self.t.id, "review_ready")
        attention.postpone(self.store, item, until=self.now + 600)
        self.assertTrue(notify.reminder_pending(self.store, self.now))
        self.store.update_attention(item, open=0)
        self.assertFalse(notify.reminder_pending(self.store, self.now))


class BannerTest(TempHomeCase):
    TMUX_LIST = ["tmux", "-S", "/private/tmp/tmux-501/default", "list-clients", "-t", "%12",
                 "-F", "#{client_tty}"]

    def setUp(self):
        super().setUp()
        me = os.getpid()
        self.store.set_meta("lobby_pid", f"{me}:{process_start(me)}")
        self.store.set_meta("lobby_tty", "/dev/ttys004")
        self.store.set_meta("lobby_term", "iTerm.app")
        self.written, self.ran = [], []
        self.clients = ""

    def write(self, path, data):
        self.written.append((path, data))

    def run_ok(self, argv, **kw):
        self.ran.append(argv)
        return mock.Mock(returncode=0, stdout=self.clients)

    def banner(self, text="T4 is ready", write=None, run=None):
        return notify.banner(self.store, text, write=write or self.write, run=run or self.run_ok)

    def in_tmux(self, clients):
        self.store.set_meta("lobby_tmux", "/private/tmp/tmux-501/default,31067,12")
        self.store.set_meta("lobby_pane", "%12")
        self.clients = clients

    def test_clean_strips_escapes_and_newlines(self):
        self.assertEqual(notify.clean('Fix \x1b]9;x\x07 log\nin\x9b'), "Fix ]9;x log in")
        self.assertEqual(len(notify.clean("x" * 500)), 200)

    def test_iterm_lobby_gets_osc9(self):
        self.assertEqual(self.banner(), "iterm")
        self.assertEqual(self.written, [("/dev/ttys004", b"\x1b]9;T4 is ready\x07")])
        self.assertEqual(self.ran, [])

    def test_text_reaches_tty_without_control_characters(self):
        self.banner("a\x1b[2Jb\x07c")
        self.assertEqual(self.written, [("/dev/ttys004", b"\x1b]9;a[2Jbc\x07")])

    def test_non_tty_path_is_never_written(self):
        for path in ("/tmp/x", "/dev/disk0", "/dev/ttys004/../disk0"):
            self.store.set_meta("lobby_tty", path)
            self.assertEqual(self.banner(), "")
        self.assertEqual(self.written, [])

    def test_closed_lobby_posts_nothing(self):
        self.store.set_meta("lobby_pid", "")
        self.assertEqual(self.banner(), "")
        self.assertEqual((self.written, self.ran), ([], []))

    def test_apple_terminal_rings_the_tab_bell(self):
        self.store.set_meta("lobby_term", "Apple_Terminal")
        self.assertEqual(self.banner(), "bell")
        self.assertEqual(self.written, [("/dev/ttys004", b"\x07")])
        self.assertEqual(self.ran, [])

    def test_other_terminal_posts_nothing(self):
        self.store.set_meta("lobby_term", "WezTerm")
        self.assertEqual(self.banner(), "")
        self.assertEqual((self.written, self.ran), ([], []))

    def test_tty_write_error_posts_nothing(self):
        def broken(path, data):
            raise OSError("gone")
        self.assertEqual(self.banner(write=broken), "")
        self.assertEqual(self.ran, [])

    def test_tmux_lobby_banner_goes_to_the_iterm_tab_showing_it(self):
        self.in_tmux("\n/dev/ttys006\n")                       # a control client has no tty
        self.assertEqual(self.banner(), "iterm")
        self.assertEqual(self.ran, [self.TMUX_LIST])
        self.assertEqual(self.written, [("/dev/ttys006", b"\x1b]9;T4 is ready\x07")])

    def test_tmux_lobby_nobody_watching_posts_nothing(self):
        self.in_tmux("\n")
        self.assertEqual(self.banner(), "")
        self.assertEqual(self.written, [])

    def test_tmux_failure_posts_nothing(self):
        self.in_tmux("/dev/ttys006")
        self.assertEqual(self.banner(run=lambda argv, **kw: mock.Mock(returncode=1, stdout="")), "")

        def broken(argv, **kw):
            raise OSError("no tmux")
        self.assertEqual(self.banner(run=broken), "")
        self.assertEqual(self.written, [])

    def test_odd_tmux_pane_is_never_passed_to_tmux(self):
        self.in_tmux("/dev/ttys006")
        self.store.set_meta("lobby_pane", "%12; rm -rf ~")
        self.assertEqual(self.banner(), "")
        self.assertEqual((self.written, self.ran), ([], []))


class WaitTest(NotifyCase):
    def setUp(self):
        super().setUp()
        me = os.getpid()
        self.lobby = f"{me}:{process_start(me)}"
        self.store.set_meta("lobby_pid", self.lobby)
        notify.since(self.store)                                   # sets the cursor

    def notice(self, item, kind="review_ready", text="T ready"):
        self.store.log(self.t.id, "notified", {"item": item, "kind": kind, "text": text})

    def test_since_reports_new_notices_once(self):
        item = self.store.add_attention(self.t.id, "review_ready")
        self.notice(item)
        self.assertEqual(notify.since(self.store), [f"[offer {item} review_ready {self.t.id}] T ready"])
        self.assertEqual(notify.since(self.store), [])

    def test_since_skips_handled_and_snoozed_offers(self):
        done = self.store.add_attention(self.t.id, "review_ready")
        self.store.update_attention(done, open=0)
        later = self.store.add_attention(self.t.id, "blocked", "Which key?")
        attention.postpone(self.store, later, until=self.now + 600)
        self.notice(done)
        self.notice(later, "blocked")
        self.assertEqual(notify.since(self.store), [])

    def test_since_skips_an_offer_the_hook_already_made(self):
        item = self.store.add_attention(self.t.id, "review_ready")
        self.notice(item)
        self.now += 1
        notify.offered(self.store, item)
        self.assertEqual(notify.since(self.store), [])

    def test_since_skips_an_offer_put_off_after_its_notice(self):
        item = self.store.add_attention(self.t.id, "review_ready")
        self.notice(item)
        self.now += 1
        attention.postpone(self.store, item)                   # "later": a few messages
        self.assertEqual(notify.since(self.store), [])

    def test_since_reports_a_reminder_after_a_later(self):
        item = self.store.add_attention(self.t.id, "review_ready")
        attention.postpone(self.store, item)
        self.now += 1
        self.notice(item)
        self.assertEqual(len(notify.since(self.store)), 1)

    def test_since_reports_each_offer_once_per_batch(self):
        item = self.store.add_attention(self.t.id, "review_ready")
        self.notice(item)
        self.notice(item)
        self.assertEqual(len(notify.since(self.store)), 1)

    def test_merge_line_has_no_offer_id(self):
        self.notice(None, "merged", "T1 was merged into main.")
        self.assertEqual(notify.since(self.store), ["T1 was merged into main."])

    def test_wait_returns_news(self):
        item = self.store.add_attention(self.t.id, "review_ready")
        slept = []

        def sleep(s):
            slept.append(s)
            self.notice(item)
        got = notify.wait(self.store, self.lobby, sleep=sleep)
        self.assertEqual(got, [f"[offer {item} review_ready {self.t.id}] T ready"])
        self.assertEqual(slept, [2])

    def test_wait_ends_when_a_new_lobby_starts(self):
        def sleep(s):
            self.store.set_meta("lobby_pid", "1:Thu Jan  1 00:00:00 1970")
        self.assertEqual(notify.wait(self.store, self.lobby, sleep=sleep), [])

    def test_wait_ends_when_the_lobby_dies(self):
        dead = "999999:Thu Jan  1 00:00:00 1970"
        self.store.set_meta("lobby_pid", dead)

        def sleep(s):
            raise AssertionError("slept")
        self.assertEqual(notify.wait(self.store, dead, sleep=sleep), [])
