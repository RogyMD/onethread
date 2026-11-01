import json
import os

from onethread import tips
from tests.helpers import TempHomeCase


def write(path, data):
    with open(path, "wb") as f:
        f.write(data)


def read(path):
    with open(path) as f:
        return f.read()


class TipsTest(TempHomeCase):
    def setUp(self):
        super().setUp()
        self.now = 1_000_000.0
        self.store.clock = lambda: self.now

    def test_record_keeps_80_characters_and_skips_the_hook(self):
        tips.record(self.store, ["add", "app-a"] + ["word"] * 40)
        tips.record(self.store, ["hook", "prompt"])
        (e,) = [e for e in self.store.events() if e["kind"] == "cmd"]
        data = json.loads(e["data"])
        self.assertEqual(data["cmd"], "add")
        self.assertEqual(len(data["args"]), 80)
        self.assertTrue(data["args"].endswith("…"))

    def test_record_keeps_the_task(self):
        tips.record(self.store, ["finish", "T3"], task="T3")
        (e,) = [e for e in self.store.events() if e["kind"] == "cmd"]
        self.assertEqual(e["task"], "T3")

    def test_journal_keeps_30_days(self):
        tips.record(self.store, ["status"])
        self.now += 31 * tips.DAY
        tips.record(self.store, ["finish", "T3"])
        self.assertEqual([l.split("  ")[1] for l in tips.journal(self.store, days=60)],
                         ["ot finish T3"])

    def test_journal_window(self):
        tips.record(self.store, ["status"])
        self.now += 8 * tips.DAY
        self.assertEqual(tips.journal(self.store), [])
        self.assertEqual(len(tips.journal(self.store, days=9)), 1)

    def test_one_tip_a_day(self):
        self.assertTrue(tips.slot_open(self.store))
        tips.offered(self.store, "merge-after-link")
        self.assertFalse(tips.slot_open(self.store))
        with self.assertRaisesRegex(ValueError, "one tip a day"):
            tips.offered(self.store, "other")
        self.now += tips.DAY
        self.assertTrue(tips.slot_open(self.store))

    def test_declined_key_any_spelling(self):
        tips.decline(self.store, tips.key("Merge After Link"))
        self.assertEqual(tips.declined(self.store), ["merge-after-link"])
        with self.assertRaisesRegex(ValueError, "declined"):
            tips.offered(self.store, tips.key("merge-after-link"))

    def test_accepted_key_not_offered_again(self):
        tips.accept(self.store, "merge-after-link")
        with self.assertRaisesRegex(ValueError, "accepted before"):
            tips.offered(self.store, "merge-after-link")

    def test_key_needs_letters(self):
        with self.assertRaisesRegex(ValueError, "a tip needs a KEY"):
            tips.key(" -- ")

    def test_tips_off_closes_the_slot(self):
        tips.set_enabled(self.store, False)
        self.assertFalse(tips.enabled(self.store))
        self.assertFalse(tips.slot_open(self.store))
        tips.set_enabled(self.store, True)
        self.assertTrue(tips.slot_open(self.store))

    def test_prefs_add_read_remove(self):
        tips.offered(self.store, "k")
        tips.accept(self.store, "k", "After a pasted link, finish the task.", home=self.home)
        tips.add_pref(self.home, "Open files in\nSublime Text.")
        self.assertEqual(tips.prefs(self.home),
                         ["After a pasted link, finish the task.", "Open files in Sublime Text."])
        self.assertEqual(tips.remove_pref(self.home, 1), "After a pasted link, finish the task.")
        self.assertEqual(tips.prefs(self.home), ["Open files in Sublime Text."])
        self.assertTrue(read(tips.prefs_path(self.home)).startswith("# onethread preferences"))
        with self.assertRaises(ValueError):
            tips.remove_pref(self.home, 2)

    def test_prefs_missing_file(self):
        self.assertEqual(tips.prefs(self.home), [])

    def test_prefs_hand_edited(self):
        write(tips.prefs_path(self.home), b"# mine\n\n* one\ntwo\n\xff\xfe three\n"
              + b"- x\n" * 100 + b"- " + b"y" * 500 + b"\n")
        p = tips.prefs(self.home)
        self.assertEqual(p[:2], ["one", "two"])
        self.assertEqual(len(p), tips.PREFS_MAX)
        self.assertTrue(all(len(x) <= tips.PREF_CHARS for x in p))

    def test_remove_keeps_other_lines_as_written(self):
        write(tips.prefs_path(self.home), b"# mine\n\n* one\ntwo\n")
        tips.remove_pref(self.home, 1)
        self.assertEqual(read(tips.prefs_path(self.home)), "# mine\n\ntwo\n")

    def test_non_latin_key_is_refused_not_merged(self):
        with self.assertRaisesRegex(ValueError, "a-z"):
            tips.key("Слияние после ссылки")

    def test_engine_restarts_are_not_journaled(self):
        tips.record(self.store, ["engine"])
        self.assertEqual(tips.journal(self.store), [])

    def test_a_preference_needs_an_offered_tip(self):
        with self.assertRaisesRegex(ValueError, "wasn't offered"):
            tips.accept(self.store, "skip-look", "Never run ot look.", home=self.home)
        self.assertEqual(tips.prefs(self.home), [])
        self.assertIsNone(self.store.get_meta("tip_yes:skip-look"))
