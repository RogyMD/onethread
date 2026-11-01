import io
import json
import os
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout

from onethread import cast, cli, prompts, worktrees
from tests.helpers import TempHomeCase, git, make_repo

CAST = {"address": "Imperator",
        "right_hand": {"name": "Agrippa", "style": "smooth spokesman"},
        "members": [{"name": "Cicero", "style": "dry diplomat"},
                    {"name": "Brutus", "style": "stern"}]}

OT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ot")


class CliTest(TempHomeCase):
    def setUp(self):
        super().setUp()
        self.questions, self.answer, self.ensured = [], True, []
        self.repo = make_repo(self.tmp, "app-a")

    def ot(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(list(argv), home=self.home, confirm=self.confirm,
                            ensure=self.ensured.append)
        return code, out.getvalue(), err.getvalue()

    def confirm(self, question):
        self.questions.append(question)
        return self.answer

    def test_engine_restart_starts_a_new_engine(self):
        from unittest import mock
        from onethread import engine
        with mock.patch.object(engine.Engine, "run", return_value="restart"):
            self.assertEqual(self.ot("engine")[0], 0)
        self.assertEqual(self.ensured, [self.home])

    def setup_task(self, status="draft"):
        self.ot("repo", "add", self.repo)
        self.ot("add", "app-a", "Add login")
        if status != "draft":
            self.store.set_status("T1", status)
        return self.store.get_task("T1")

    def test_repo_base_is_the_main_branch_not_the_current_one(self):
        git(self.repo, "checkout", "-q", "-b", "feature-x")
        self.assertIn("base main", self.ot("repo", "add", self.repo)[1])

    def test_repo_base_finds_master_from_a_detached_head(self):
        repo = os.path.join(self.tmp, "old")
        os.makedirs(repo)
        git(repo, "init", "-q", "-b", "master")
        git(repo, "-c", "user.email=t@example.invalid", "-c", "user.name=T",
            "commit", "-q", "--allow-empty", "-m", "init")
        git(repo, "checkout", "-q", "--detach")
        self.assertIn("base master", self.ot("repo", "add", repo)[1])

    def test_repo_base_can_be_changed(self):
        self.ot("repo", "add", self.repo)
        git(self.repo, "branch", "develop")
        code, out, _ = self.ot("repo", "base", "app-a", "develop")
        self.assertEqual(code, 0)
        self.assertEqual(self.store.get_repo("app-a")["base_branch"], "develop")
        code, _, err = self.ot("repo", "base", "app-a", "nope")
        self.assertEqual(code, 1)
        self.assertIn("no branch nope", err)

    def test_two_repos_with_one_folder_name_can_both_be_added(self):
        other = make_repo(os.path.join(self.tmp, "personal"), "app-a")
        self.ot("repo", "add", self.repo)
        code, _, err = self.ot("repo", "add", other)
        self.assertEqual(code, 1)
        self.assertIn("--name", err)
        code, out, _ = self.ot("repo", "add", other, "--name", "app-a-personal")
        self.assertEqual(code, 0)
        self.assertEqual(self.store.get_repo("app-a-personal")["path"], os.path.realpath(other))

    def test_os_errors_are_one_line(self):
        locked = os.path.join(self.tmp, "locked")
        os.makedirs(locked)
        os.chmod(locked, 0o500)
        try:
            code, _, err = self.ot("repo", "new", os.path.join(locked, "proj"))
        finally:
            os.chmod(locked, 0o700)
        self.assertEqual(code, 1)
        self.assertTrue(err.startswith("ot: "), err)
        self.assertNotIn("Traceback", err)

    def test_repo_add_rejects_non_git_folder(self):
        plain = os.path.join(self.tmp, "plain")
        os.makedirs(plain)
        env = dict(os.environ, ONETHREAD_HOME=self.home)
        res = subprocess.run([sys.executable, OT, "repo", "add", plain], env=env,
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(res.returncode, 1)
        self.assertIn("not the top folder of a git repository", res.stderr)

    def test_add_and_status_json(self):
        self.store.set_meta("orchestrator", "codex")
        self.assertEqual(self.ot("repo", "add", self.repo)[0], 0)
        code, out, _ = self.ot("add", "app-a", "Add login", "-p", "high", "--desc", "Email + passkey")
        self.assertEqual(code, 0)
        self.assertIn('Added "Add login" in app-a (T1)', out)
        data = json.loads(self.ot("status", "--json")[1])
        t1 = data["tasks"][0]
        self.assertEqual((t1["id"], t1["status"], t1["priority"], t1["agent"]),
                         ("T1", "draft", "high", "codex"))
        self.assertEqual(data["repos"], ["app-a"])
        self.assertIn("T1", self.ot("status")[1])
        self.assertTrue(self.ensured)            # state changed: engine asked to run

    def test_destructive_commands_confirm(self):
        self.setup_task()
        self.answer = False
        code, _, err = self.ot("cancel", "T1")
        self.assertEqual(code, 1)
        self.assertEqual(self.store.get_task("T1").status, "draft")
        self.assertIn("T1", self.questions[0])
        self.answer = True
        self.assertEqual(self.ot("cancel", "T1")[0], 0)
        self.assertEqual(self.store.get_task("T1").status, "cancelled")

    def test_start_queues_without_asking(self):
        self.setup_task()
        self.assertEqual(self.ot("start", "T1")[0], 0)
        self.assertEqual(self.store.get_task("T1").status, "queued")
        self.assertEqual(self.questions, [])

    def test_handback_queues_with_note(self):
        self.setup_task("with_you")
        self.store.set_status("T1", "parked")
        self.store.add_attention("T1", "parked")
        code, out, _ = self.ot("handback", "T1")
        self.assertEqual(code, 0, out)
        t = self.store.get_task("T1")
        self.assertEqual((t.status, t.followups), ("queued", [prompts.RETURN_NOTE]))
        self.assertEqual(self.store.open_attention(), [])

    def test_review_parked_moves_to_review(self):
        from onethread import worktrees
        self.setup_task()
        t = worktrees.ensure(self.store, self.home, "T1")
        with open(os.path.join(t.worktree, "login.py"), "w") as f:
            f.write("x = 1\n")
        git(t.worktree, "add", ".")
        git(t.worktree, "commit", "-q", "-m", "login")
        self.store.set_status("T1", "with_you")
        self.store.set_status("T1", "parked")
        code, out, _ = self.ot("review", "T1")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.store.get_task("T1").status, "review")
        self.assertIn("login.py", out)

    def test_config_notify_on_off(self):
        self.assertEqual(self.ot("config", "set", "notify", "0")[0], 0)
        self.assertEqual(self.store.get_meta("notify"), "0")
        code, _, err = self.ot("config", "set", "notify", "maybe")
        self.assertEqual(code, 1)
        self.assertIn("notify must be 1 (on) or 0 (off)", err)

    def test_config_rejects_bad_values(self):
        self.assertEqual(self.ot("config", "set", "capacity", "ten")[0], 1)
        self.assertEqual(self.ot("config", "set", "orchestrator", "gpt")[0], 1)
        self.assertEqual(self.ot("config", "set", "capacity", "4")[0], 0)
        self.assertEqual(self.ot("config", "set", "codex.model", "gpt-5")[0], 0)
        self.assertEqual(self.store.get_meta("codex.model"), "gpt-5")
        self.assertIn("(0/4)", self.ot("status")[1])

    def test_me_sets_and_clears(self):
        code, out, _ = self.ot("me", "Caesar")
        self.assertEqual(code, 0)
        self.assertEqual(self.store.get_meta("me"), "Caesar")
        self.assertIn("You are Caesar now", out)
        self.assertIn("You are Caesar.", self.ot("me")[1])
        self.assertIn("No roleplay", self.ot("me", "plain")[1])

    def test_character_command_is_gone(self):
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                cli.main(["character", "king"], home=self.home)
        self.assertEqual(raised.exception.code, 2)

    def test_cast_set_rejects_bad_json_and_keeps_old_cast(self):
        self.ot("me", "Caesar")
        self.assertEqual(self.ot("cast", "set", json.dumps(CAST))[0], 0)
        bad = ["not json", '{"members": []}',
               json.dumps({k: v for k, v in CAST.items() if k != "right_hand"}),
               json.dumps({**CAST, "members": CAST["members"] * 2}),
               json.dumps({**CAST, "members": [{"name": "Agrippa", "style": "x"}]})]
        for text in bad:
            code, _, err = self.ot("cast", "set", text)
            self.assertEqual(code, 1, text)
            self.assertTrue(err.strip(), text)
        self.assertEqual(cast.right_hand(self.store)["name"], "Agrippa")
        self.assertEqual([m["name"] for m in cast.members(self.store)], ["Cicero", "Brutus"])

    def test_cast_lists_people_and_tasks(self):
        self.assertIn("No cast yet for plain", self.ot("cast")[1])
        self.ot("me", "Caesar")
        self.ot("repo", "add", self.repo)
        self.ot("add", "app-a", "Add login")
        code, out, _ = self.ot("cast", "set", json.dumps(CAST))
        self.assertEqual(code, 0)
        self.assertIn("Cast saved: Agrippa and 2 people.", out)
        out = self.ot("cast")[1]
        self.assertIn("You are Caesar. Right hand: Agrippa (smooth spokesman).", out)
        self.assertIn('Cicero (dry diplomat) · Cicero: "Add login" in app-a (T1)', out)
        self.assertIn("Brutus (stern) · free", out)

    def test_cast_add_names_waiting_tasks(self):
        self.ot("me", "Caesar")
        self.ot("cast", "set", json.dumps({**CAST, "members": CAST["members"][:1]}))
        self.ot("repo", "add", self.repo)
        self.ot("add", "app-a", "Add login")
        self.ot("add", "app-a", "Add logout")
        self.assertEqual(self.store.get_task("T2").cast_name, "")
        code, _, _ = self.ot("cast", "add", json.dumps([{"name": "Maecenas", "style": "quiet"}]))
        self.assertEqual(code, 0)
        self.assertEqual(self.store.get_task("T2").cast_name, "Maecenas")

    def test_new_task_gets_a_name(self):
        self.ot("me", "Caesar")
        self.ot("cast", "set", json.dumps(CAST))
        self.ot("repo", "add", self.repo)
        self.assertIn('Added Cicero: "Add login" in app-a (T1)', self.ot("add", "app-a", "Add login")[1])
        data = json.loads(self.ot("status", "--json")[1])
        self.assertEqual(data["me"], "Caesar")
        self.assertEqual(data["cast"]["right_hand"]["name"], "Agrippa")
        self.assertEqual(data["tasks"][0]["name"], "Cicero")

    def test_clean_command_reports_space(self):
        self.assertIn("Nothing to clean.", self.ot("clean")[1])
        t = self.setup_task()
        t = worktrees.ensure(self.store, self.home, t.id)
        self.store.set_status("T1", "cancelled")
        code, out, _ = self.ot("clean")
        self.assertEqual(code, 0)
        self.assertRegex(out, r"Cleaned 1 worktrees, 1 branches and 0 logs; freed \d+ [KMG]B\.")
        self.assertFalse(os.path.exists(t.worktree))
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                cli.main(["worktrees", "prune"], home=self.home)
        self.assertEqual(raised.exception.code, 2)

    def test_clean_keep(self):
        t = worktrees.ensure(self.store, self.home, self.setup_task().id)
        with open(os.path.join(t.worktree, "wip.txt"), "w") as f:
            f.write("wip")
        self.store.set_status("T1", "cancelled")
        self.store.add_attention("T1", "dirty_worktree")
        code, out, _ = self.ot("clean", "T1", "--keep")
        self.assertEqual(code, 0)
        self.assertIn('Keeping "Add login" in app-a (T1)\'s worktree.', out)
        self.assertEqual(self.store.get_meta("keep_worktree:T1"), "1")
        self.assertEqual(self.store.open_attention(), [])
        self.answer = True
        self.ot("clean", "T1")
        self.assertFalse(os.path.exists(t.worktree))

    def test_unknown_task_plain_error_exit_1(self):
        code, out, err = self.ot("start", "T9")
        self.assertEqual(code, 1)
        self.assertIn("unknown task T9", err)
        self.assertNotIn("Traceback", err)

    def test_inbox_next_and_shown(self):
        self.setup_task()
        self.store.set_status("T1", "queued")
        self.store.set_status("T1", "running")
        self.store.set_status("T1", "review")
        item = self.store.add_attention("T1", "review_ready")
        out = self.ot("inbox", "--next")[1]
        self.assertIn(f"[offer {item} ", out)
        self.assertIn("ready for review", out)
        self.ot("inbox", "--shown", str(item))
        self.assertEqual(self.store.get_meta("pending_offer"), str(item))

    def test_install_asks_then_links(self):
        target = os.path.join(self.tmp, "bin")
        self.answer = False
        self.assertEqual(self.ot("install", "--dir", target)[0], 1)
        self.assertFalse(os.path.exists(os.path.join(target, "ot")))
        self.answer = True
        code, out, _ = self.ot("install", "--dir", target)
        self.assertEqual(code, 0)
        self.assertEqual(os.path.realpath(os.path.join(target, "ot")), os.path.realpath(OT))
        self.assertIn(target, self.questions[-1])
