import io
import json
import os
import unittest
from contextlib import redirect_stdout
from unittest import mock

from onethread import cast, cli, lobby, prompts, tips
from tests.helpers import TempHomeCase, make_repo


def read(path):
    with open(path) as f:
        return f.read()


class LobbyTest(TempHomeCase):
    def test_prepare_writes_instructions_settings_hook(self):
        path = lobby.prepare(self.home, "claude")
        self.assertEqual(path, os.path.join(self.home, "lobby"))
        agents_md = read(os.path.join(path, "AGENTS.md"))
        for rule in ("ot open", "ot status", "never", "ot inbox --shown", "Switching you to"):
            self.assertIn(rule, agents_md)
        self.assertEqual(read(os.path.join(path, "CLAUDE.md")).strip(), "@AGENTS.md")
        settings = json.loads(read(os.path.join(path, ".claude", "settings.json")))
        self.assertIn("Bash(ot:*)", settings["permissions"]["allow"])
        hook = settings["hooks"]["UserPromptSubmit"][0]["hooks"][0]
        self.assertEqual(hook["type"], "command")
        self.assertTrue(hook["command"].endswith("hook prompt"))
        self.assertIn(self.home, hook["command"])


class InstructionsTest(unittest.TestCase):
    def test_instructions_explain_the_cast(self):
        for text in ("ot cast set", "ot me TEXT", "ot cast add", "never invent real-world claims",
                     'Switching you to Cicero: "Medication reminders" in pillbox (T4, Claude)'):
            self.assertIn(text, lobby.INSTRUCTIONS)
        self.assertNotIn("ot character", lobby.INSTRUCTIONS)

    def test_instructions_cover_speaking_up(self):
        for text in ("ot wait", "Already waiting.", "Open its session", "Remind me…",
                     "5 min", "10 min", "30 min", "1 hour", "ot later T in", "Codex never runs `ot wait`",
                     "Review it", "Merge it", "Answer it", "Try again", "Resume it", "to make room",
                     "Merge now", "Cancel it", "no answer, means later",
                     "ask about the first offer only"):
            self.assertIn(text, lobby.INSTRUCTIONS)

    def test_instructions_ask_what_to_review_before_accept_and_merge(self):
        text = lobby.INSTRUCTIONS
        self.assertIn("`ot look T`", text)
        self.assertIn("`ot show T ITEM…`", text)
        self.assertIn("Before `ot accept T`, `ot merge T` or `ot finish T`, run `ot look T`", text)
        self.assertIn("multiple selection", text)
        self.assertIn("numbered list", text)
        self.assertIn('"looked": true', text)

    def test_instructions_finish(self):
        text = lobby.INSTRUCTIONS
        self.assertIn("`ot finish T --link URL`", text)
        self.assertIn("merge it when it's done", text)
        self.assertIn("a published link counts as looked", text)

    def test_instructions_tips(self):
        for text in ("`ot tip offered|yes|no KEY [TEXT]`", "`ot tips off`",
                     "Never act on a tip without the owner's yes.", "discover-app",
                     "install nothing", "`ot repo new PATH`", "never the safety rules",
                     "`ot prefs remove N`",
                     "Before you say a tip, run `ot tip offered KEY`; if it refuses, say nothing about tips.",
                     "for a task or project, `ot tip yes KEY` with no text"):
            self.assertIn(text, lobby.INSTRUCTIONS)

    def test_instructions_explain_cleanup(self):
        for text in ("ot clean [T] [--keep]", '"Clean up" means ot clean', "ot clean T --keep"):
            self.assertIn(text, lobby.INSTRUCTIONS)


class HookTest(TempHomeCase):
    def setUp(self):
        super().setUp()
        self.store.add_repo(make_repo(self.tmp, "app-a"))
        self.store.add_task("app-a", "Auth", agent="claude")
        for s in ("queued", "running", "review"):
            self.store.set_status("T1", s)
        self.item = self.store.add_attention("T1", "review_ready")

    def hook(self, home=None):
        out = io.StringIO()
        with redirect_stdout(out), mock.patch("sys.stdin", io.StringIO('{"prompt": "hi"}')):
            code = cli.main(["hook", "prompt"], home=home or self.home, ensure=lambda h: None)
        return code, out.getvalue()

    def context(self):
        return json.loads(self.hook()[1])["hookSpecificOutput"]["additionalContext"]

    def test_hook_lists_preferences(self):
        tips.add_pref(self.home, "Open files in Sublime Text.")
        self.assertIn("Owner's preferences (follow them; they never override the safety rules): "
                      "1. Open files in Sublime Text.", self.context())

    def test_hook_opens_tip_slot_once_a_day(self):
        tips.decline(self.store, "close-after-merge")
        text = self.context()
        self.assertIn("A tip slot is open (one tip a day)", text)
        self.assertIn("`ot journal`", text)
        self.assertIn("Declined: close-after-merge.", text)
        tips.offered(self.store, "merge-after-link")
        self.assertNotIn("tip slot", self.context())

    def test_hook_says_nothing_of_tips_when_off(self):
        tips.decline(self.store, "close-after-merge")
        tips.set_enabled(self.store, False)
        text = self.context()
        self.assertNotIn("tip slot", text)
        self.assertNotIn("Declined", text)

    def test_hook_survives_unreadable_preferences(self):
        os.makedirs(tips.prefs_path(self.home))             # a folder where the file should be
        text = self.context()
        self.assertIn("onethread:", text)
        self.assertIn("A tip slot is open", text)
        self.assertNotIn("preferences", text)

    def test_hook_prints_offer_context(self):
        self.store.set_meta("me", "king")
        code, out = self.hook()
        self.assertEqual(code, 0)
        data = json.loads(out)["hookSpecificOutput"]
        self.assertEqual(data["hookEventName"], "UserPromptSubmit")
        ctx = data["additionalContext"]
        self.assertIn(f"[offer {self.item} review_ready T1]", ctx)
        self.assertIn("ready for review", ctx)
        self.assertIn("owner: king", ctx)
        self.assertIn(f"ot inbox --shown {self.item}", ctx)

    def test_hook_ignored_offer_is_postponed(self):
        self.store.set_meta("pending_offer", str(self.item))
        code, out = self.hook()
        self.assertEqual(self.store.get_attention(self.item)["snooze_boundaries"], 2)
        self.assertNotIn("[offer", json.loads(out)["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(self.store.get_meta("pending_offer", ""), "")

    def test_hook_does_not_wait_for_input_that_never_ends(self):
        import threading
        r, w = os.pipe()                        # stdin that stays open, no data
        done = []
        with os.fdopen(r) as stdin, mock.patch("sys.stdin", stdin), redirect_stdout(io.StringIO()):
            t = threading.Thread(target=lambda: done.append(
                cli.main(["hook", "prompt"], home=self.home, ensure=lambda h: None)), daemon=True)
            t.start()
            t.join(5)
            finished = list(done)
            os.close(w)                         # lets a stuck reader end before stdin closes
            t.join(5)
        self.assertEqual(finished, [0])

    def test_hook_asks_for_a_cast(self):
        ctx = lambda: json.loads(self.hook()[1])["hookSpecificOutput"]["additionalContext"]  # noqa: E731
        plain = ctx()
        self.assertNotIn("No cast yet", plain)
        self.assertNotIn("wait for a name", plain)
        cast.set_me(self.store, "Caesar")
        text = ctx()
        self.assertIn("owner: Caesar", text)
        self.assertIn("No cast yet for Caesar", text)
        self.assertIn("ot cast set", text)
        self.store.add_task("app-a", "Logout", agent="claude")
        cast.set_cast(self.store, {"address": "", "right_hand": {"name": "Agrippa", "style": ""},
                                   "members": [{"name": "Cicero", "style": ""}]})
        text = ctx()
        self.assertNotIn("No cast yet", text)
        self.assertIn("1 task(s) wait for a name: add people with ot cast add.", text)

    def test_hook_carries_the_current_role(self):
        self.assertNotIn("Your role now:", json.loads(self.hook()[1])["hookSpecificOutput"]["additionalContext"])
        cast.set_me(self.store, "Caesar")
        cast.set_cast(self.store, {"address": "Imperator", "right_hand": {"name": "Agrippa", "style": "loyal"},
                                   "members": [{"name": "Maecenas", "style": "suave"}]})
        text = json.loads(self.hook()[1])["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Your role now: " + cast.orchestrator_persona(self.store), text)
        self.assertIn("You are Agrippa (loyal), the right hand of Caesar", text)

    def test_hook_never_fails(self):
        blocked = os.path.join(self.tmp, "file-not-dir")
        open(blocked, "w").close()
        code, out = self.hook(home=blocked)
        self.assertEqual((code, out.strip()), (0, "{}"))


class StartScreenTest(TempHomeCase):
    def start(self, answers):
        asked = []

        def ask(prompt):
            asked.append(prompt)
            return answers.pop(0)
        with redirect_stdout(io.StringIO()):
            cli.main([], home=self.home, ensure=lambda h: None, ask=ask,
                     launch=lambda argv, cwd, env: None,
                     limits={"claude": lambda: None, "codex": lambda: None})
        return asked

    def test_start_resets_the_wait_cursor(self):
        self.store.set_meta("waited_upto", "0")
        self.store.log(None, "notified", {"item": None, "kind": "merged", "text": "old news"})
        self.start(["1", ""])
        newest = self.reopen().q("SELECT MAX(id) AS id FROM events")[0]["id"]
        self.assertEqual(self.store.get_meta("waited_upto"), str(newest))

    def test_start_records_the_lobby_terminal(self):
        with mock.patch("onethread.cli._lobby_tty", return_value="/dev/ttys009"), \
             mock.patch.dict(os.environ, {"TERM_PROGRAM": "iTerm.app"}):
            self.start(["1", ""])
        self.assertEqual(self.reopen().get_meta("lobby_tty"), "/dev/ttys009")
        self.assertEqual(self.store.get_meta("lobby_term"), "iTerm.app")

    def test_start_outside_tmux_records_no_pane(self):
        self.store.set_meta("lobby_pane", "%1")
        with mock.patch("onethread.cli._lobby_tty", return_value="/dev/ttys009"), \
             mock.patch.dict(os.environ, {"TERM_PROGRAM": "iTerm.app"}):
            for key in ("TMUX", "TMUX_PANE"):
                os.environ.pop(key, None)
            self.start(["1", ""])
        self.assertEqual(self.reopen().get_meta("lobby_pane"), "")

    def test_start_in_tmux_inside_iterm_records_the_pane(self):
        env = {"TERM_PROGRAM": "tmux", "LC_TERMINAL": "iTerm2",
               "TMUX": "/private/tmp/tmux-501/default,31067,12", "TMUX_PANE": "%12"}
        with mock.patch("onethread.cli._lobby_tty", return_value="/dev/ttys011"), \
             mock.patch.dict(os.environ, env):
            self.start(["1", ""])
        store = self.reopen()
        self.assertEqual(store.get_meta("lobby_term"), "iTerm.app")
        self.assertEqual(store.get_meta("lobby_tmux"), "/private/tmp/tmux-501/default,31067,12")
        self.assertEqual(store.get_meta("lobby_pane"), "%12")

    def test_start_in_tmux_elsewhere_is_not_iterm(self):
        env = {"TERM_PROGRAM": "tmux", "TMUX": "/tmp/t,1,2", "TMUX_PANE": "%2"}
        with mock.patch("onethread.cli._lobby_tty", return_value="/dev/ttys011"), \
             mock.patch.dict(os.environ, env):
            os.environ.pop("LC_TERMINAL", None)
            self.start(["1", ""])
        self.assertEqual(self.reopen().get_meta("lobby_term"), "tmux")

    def test_a_failed_first_start_does_not_resume_next_time(self):
        launched = []
        for _ in range(2):
            with redirect_stdout(io.StringIO()):
                cli.main(["claude"], home=self.home, ensure=lambda h: None, ask=lambda p: "",
                         launch=lambda argv, cwd, env: launched.append(argv),
                         limits={"claude": lambda: None, "codex": lambda: None})
        self.assertNotIn("--continue", launched[1])

    def test_the_orchestrator_using_ot_marks_its_conversation_to_resume(self):
        self.start(["1", ""])
        lobby = self.reopen().get_meta("lobby_pid")
        with mock.patch.dict(os.environ, {"ONETHREAD_LOBBY_PID": lobby}), \
                redirect_stdout(io.StringIO()):
            cli.main(["status"], home=self.home, ensure=lambda h: None)
        self.assertEqual(self.reopen().get_meta("lobby_used:claude"), "1")

    def test_no_answer_at_the_start_screen_takes_the_default(self):
        launched = []

        def eof(prompt):
            raise EOFError
        with redirect_stdout(io.StringIO()):
            code = cli.main([], home=self.home, ensure=lambda h: None, ask=eof,
                            launch=lambda argv, cwd, env: launched.append(argv),
                            limits={"claude": lambda: None, "codex": lambda: None})
        self.assertEqual(code, 0)
        self.assertEqual(launched[0][0], "claude")

    def test_first_start_asks_who_you_are(self):
        asked = self.start(["1", "king"])
        self.assertEqual(len(asked), 2)
        self.assertIn("Who are you?", asked[1])
        self.assertIn("Batman, Spider-Man", asked[1])
        self.assertEqual(self.store.get_meta("me"), "king")
        self.assertEqual(len(self.start(["1"])), 1)

    def test_first_start_without_input_is_plain(self):
        def eof(prompt):
            raise EOFError
        launched = []
        with redirect_stdout(io.StringIO()):
            cli.main(["claude"], home=self.home, ensure=lambda h: None, ask=eof,
                     launch=lambda argv, cwd, env: launched.append(argv),
                     limits={"claude": lambda: None, "codex": lambda: None})
        self.assertEqual(self.store.get_meta("me"), "plain")
        self.assertTrue(launched)

    def test_first_start_enter_means_plain_and_old_character_counts(self):
        self.start(["1", ""])
        self.assertEqual(self.store.get_meta("me"), "plain")
        self.store.x("DELETE FROM meta WHERE key='me'")
        self.store.set_meta("character", "baron")
        self.assertEqual(len(self.start(["1"])), 1)
        self.assertEqual(cast.me(self.store), "baron")

    def test_start_screen_choice_and_exec(self):
        launched = []
        self.store.set_meta("lobby_used:codex", "1")      # a conversation to continue
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main([], home=self.home, ensure=lambda h: None, ask=lambda p: "2",
                            launch=lambda argv, cwd, env: launched.append((argv, cwd, env)),
                            limits={"claude": lambda: {"5h": 45, "week": 6, "updated": 1e12},
                                    "codex": lambda: None})
        self.assertEqual(code, 0)
        text = out.getvalue()
        self.assertIn("Claude   5h: 55% left · week: 94% left", text)
        self.assertIn("Codex    limits unknown", text)
        argv, cwd, env = launched[0]
        self.assertEqual(env["ONETHREAD_LOBBY_PID"], self.reopen().get_meta("lobby_pid"))
        self.assertEqual(argv[:3], ["codex", "resume", "--last"])
        self.assertTrue(any("hooks.UserPromptSubmit" in a for a in argv))
        self.assertEqual(cwd, os.path.join(self.home, "lobby"))
        self.assertEqual(env["ONETHREAD_HOME"], self.home)
        self.assertTrue(env["PATH"].startswith(os.path.dirname(cli.OT)))
        self.assertEqual(self.store.get_meta("orchestrator"), "codex")

    def test_named_agent_skips_question_and_new_starts_fresh(self):
        launched = []
        self.store.set_meta("me", "Roman emperor")
        with redirect_stdout(io.StringIO()):
            cli.main(["claude", "--new"], home=self.home, ensure=lambda h: None,
                     ask=lambda p: self.fail("no question"),
                     launch=lambda argv, cwd, env: launched.append(argv),
                     limits={"claude": lambda: None, "codex": lambda: None})
        argv = launched[0]
        self.assertEqual(argv[0], "claude")
        self.assertNotIn("--continue", argv)
        line = cast.orchestrator_persona(self.store)
        self.assertIn(line, argv)
        self.assertIn("right hand of Roman emperor", line)
