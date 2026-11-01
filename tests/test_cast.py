import os
import sqlite3

from onethread import cast
from onethread.model import label
from onethread.store import Store
from tests.helpers import TempHomeCase, make_repo

CAST = {"address": "Imperator",
        "right_hand": {"name": "Agrippa", "style": "smooth spokesman"},
        "members": [{"name": "Cicero", "style": "dry diplomat"},
                    {"name": "Brutus", "style": "stern"}]}

SCHEMA_1_TASKS = """CREATE TABLE tasks (
  id TEXT PRIMARY KEY, num INTEGER, repo TEXT, title TEXT, description TEXT,
  priority TEXT, status TEXT, agent TEXT, not_before REAL, session_id TEXT,
  worktree TEXT, branch TEXT, base_branch TEXT, resources TEXT, summary TEXT,
  result TEXT, followups TEXT, created REAL, updated REAL, queued_at REAL)"""


class CastTest(TempHomeCase):
    def setUp(self):
        super().setUp()
        self.store.add_repo(make_repo(self.tmp, "app-a"))

    def add(self, title):
        return self.store.add_task("app-a", title)

    def test_me_defaults_to_plain(self):
        self.assertEqual(cast.me(self.store), "plain")
        self.assertTrue(cast.is_plain(self.store))

    def test_old_database_migrates(self):
        path = os.path.join(self.tmp, "old.db")
        db = sqlite3.connect(path)
        db.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
        db.execute(SCHEMA_1_TASKS)
        db.execute("INSERT INTO meta VALUES ('schema_version', '1')")
        db.execute("INSERT INTO meta VALUES ('character', 'king')")
        db.execute("INSERT INTO tasks VALUES ('T1',1,'app-a','Old','','normal','draft','codex',"
                   "0,'','','','','[]','','{}','[]',0,0,0)")
        db.commit()
        db.close()
        store = Store(path)
        try:
            self.assertEqual(store.get_task("T1").cast_name, "")
            self.assertEqual(store.get_meta("schema_version"), "2")
            self.assertEqual(cast.me(store), "king")
            self.assertEqual(store.get_meta("me"), "king")
        finally:
            store.close()

    def test_set_cast_names_open_tasks_in_order(self):
        cast.set_me(self.store, "Caesar")
        self.add("A")
        self.add("B")
        self.assertEqual(cast.set_cast(self.store, CAST), ["T1", "T2"])
        self.assertEqual(self.store.get_task("T1").cast_name, "Cicero")
        self.assertEqual(self.store.get_task("T2").cast_name, "Brutus")
        self.assertEqual(cast.right_hand(self.store)["name"], "Agrippa")
        self.assertEqual(cast.address(self.store), "Imperator")
        self.assertEqual(cast.member(self.store, "Cicero")["style"], "dry diplomat")

    def test_fill_skips_when_no_free_member(self):
        cast.set_me(self.store, "Caesar")
        cast.set_cast(self.store, CAST)
        self.add("A")
        self.add("B")
        self.add("C")
        cast.fill(self.store)
        self.assertEqual(self.store.get_task("T3").cast_name, "")
        self.assertEqual([t.id for t in cast.unnamed(self.store)], ["T3"])
        cast.add_members(self.store, [{"name": "Maecenas", "style": "quiet"}])
        self.assertEqual(self.store.get_task("T3").cast_name, "Maecenas")

    def test_done_task_frees_its_name(self):
        cast.set_me(self.store, "Caesar")
        cast.set_cast(self.store, {**CAST, "members": CAST["members"][:1]})
        self.add("A")
        cast.fill(self.store)
        self.assertEqual(self.store.get_task("T1").cast_name, "Cicero")
        self.store.set_status("T1", "cancelled")
        self.add("B")
        cast.fill(self.store)
        self.assertEqual(self.store.get_task("T2").cast_name, "Cicero")

    def test_set_me_clears_names(self):
        cast.set_me(self.store, "Caesar")
        self.add("A")
        cast.set_cast(self.store, CAST)
        cast.set_me(self.store, "Caesar")
        self.assertEqual(self.store.get_task("T1").cast_name, "")
        self.assertIsNone(cast.right_hand(self.store))
        self.assertEqual(cast.members(self.store), [])
        self.assertEqual(cast.address(self.store), "")
        self.assertEqual(cast.me(self.store), "Caesar")

    def test_set_me_empty_is_plain(self):
        self.assertEqual(cast.set_me(self.store, "  "), "plain")
        self.assertTrue(cast.is_plain(self.store))

    def test_plain_never_names(self):
        cast.set_cast(self.store, CAST)
        self.add("A")
        cast.fill(self.store)
        self.assertEqual(self.store.get_task("T1").cast_name, "")

    def test_bad_cast_changes_nothing(self):
        cast.set_me(self.store, "Caesar")
        cast.set_cast(self.store, CAST)
        bad = [{}, {**CAST, "members": []}, {k: v for k, v in CAST.items() if k != "right_hand"},
               {**CAST, "members": CAST["members"] * 2},
               {**CAST, "members": [{"name": "Agrippa", "style": "x"}]},
               {**CAST, "members": [{"name": "", "style": "x"}]}, "not a dict"]
        for data in bad:
            with self.assertRaises(ValueError, msg=data):
                cast.set_cast(self.store, data)
        with self.assertRaises(ValueError):
            cast.add_members(self.store, [{"name": "Cicero", "style": "again"}])
        self.assertEqual(cast.right_hand(self.store)["name"], "Agrippa")
        self.assertEqual([m["name"] for m in cast.members(self.store)], ["Cicero", "Brutus"])

    def test_label_with_and_without_name(self):
        self.add("Medication reminders")
        self.assertEqual(label(self.store.get_task("T1")), '"Medication reminders" in app-a (T1)')
        self.store.update_task("T1", cast_name="Cicero")
        self.assertEqual(label(self.store.get_task("T1")),
                         'Cicero: "Medication reminders" in app-a (T1)')

    def test_personas(self):
        t = self.add("A")
        self.assertEqual(cast.orchestrator_persona(self.store), "")
        self.assertEqual(cast.task_persona(self.store, t), "")
        cast.set_me(self.store, "Caesar")
        lines = [cast.orchestrator_persona(self.store), cast.task_persona(self.store, t)]
        self.assertIn("the right hand of Caesar", lines[0])
        self.assertIn("You are one of Caesar's people", lines[1])
        self.assertNotIn("address them as", lines[0])
        cast.set_cast(self.store, CAST)
        t = self.store.get_task("T1")
        lines += [cast.orchestrator_persona(self.store), cast.task_persona(self.store, t)]
        self.assertIn("You are Agrippa (smooth spokesman), the right hand of Caesar", lines[2])
        self.assertIn("address them as Imperator", lines[2])
        self.assertIn("You are Cicero (dry diplomat), one of Caesar's people", lines[3])
        for line in lines:
            self.assertIn("never invent real-world claims", line)
