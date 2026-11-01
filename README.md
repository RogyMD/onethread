<img src="docs/icon.svg" width="128" height="128" alt="onethread icon: a river with tributaries flowing into it">

# onethread

**You talk to one agent. Many coding agents work behind it.**

Version 1.0.0: used every day by its author with both Claude Code and Codex.
Some behaviors are covered by simulated tests, and some real-tool checks are
still outstanding; see [docs/STATUS.md](docs/STATUS.md) for the evidence.

Run `ot`, pick Claude Code or Codex, and talk in that window the way you
always do: "add auth to app-a", "the settings bug in app-b is urgent",
"what's running?". That window is the **orchestrator**. It keeps a queue of
tasks across your repos, runs each task's own agent session in the
background (up to 10 at once, each in its own git worktree), and tells you
when something is ready, at a natural pause, never mid-thought.

Say **"let me talk to the medication task in pillbox"** and it switches you:

```
Switching you to "Medication reminders" in pillbox (T4, Claude)
```

A new iTerm2 tab opens inside that task's real session. If its agent was
working, it stops (you're the boss) and greets you with what it was doing.
When you close the tab, the task waits for you to hand it back, review it,
or leave it.

- **You are the boss:** `ot me king` (or Batman, Spider-Man, a Roman emperor: anyone at all;
  real people are played as a light parody). The orchestrator becomes your
  right hand and builds your people from that world: each task gets one of
  them ("Cicero: \"Medication reminders\" in pillbox (T4)"), and "let me talk to
  Cicero" switches to it. The first `./ot` asks who you are; `ot me plain`
  turns it off. Code, ids, errors and confirmations stay plain.
- **Usage at a glance:** the start screen shows how much of each
  provider's usage limit is left (5-hour and weekly).
- **Costly commands take turns:** two Xcode builds never fight over your Mac.
- **Nothing destructive without you:** cancel, merge, push and accept ask you
  in the terminal or a macOS dialog. The model can't answer for you.
- **It tells you when something happens:** a worker finishes, gets stuck,
  fails or stops, a merge can't go ahead, or a task is merged. iTerm2 shows
  its regular notification (also through tmux); it has no buttons. The
  Claude orchestrator gives you the choices in chat: review, merge, answer,
  resume, open the task's session, or remind you in 5, 10, 30 minutes or 1
  hour. In Apple's Terminal the tab's bell marks the tab instead; other
  terminals get no notification. Codex says it on your next message.
  `ot config set notify 0` turns these notifications off.
- **Tips from what you repeat:** at most once a day the orchestrator may
  notice something done three or more times and offer one fix: a
  preference it follows from then on, an `ot` task, or a small app made
  with [CraftApps](https://github.com/RogyMD/craftapps). Say yes or no; a no
  is never asked again. "No more tips"
  turns them off. Preferences live in `~/.onethread/preferences.md`, one
  per line, yours to edit.
- **Finishing is one word:** paste the published link or say "merge it",
  and `ot` records the link, accepts, merges and cleans up. A merge conflict
  goes back to the task's own worker; your repo is never touched while it
  has uncommitted changes or another branch checked out.
- **Survives restarts.** A crashed worker is never mistaken for finished work.

Local-first: Python 3 standard library, one SQLite file, and the `claude` /
`codex` CLIs you already use. No server, no account, no telemetry.

## Quick start

You need macOS, Python 3.9 or newer (the `python3` macOS ships is fine), git, and at least one of
[Claude Code](https://docs.anthropic.com/en/docs/claude-code) (`claude`) or
[Codex CLI](https://github.com/openai/codex) (`codex`), installed and logged
in. [iTerm2](https://iterm2.com) is recommended: it opens task tabs and
shows its notifications. Without iTerm2, `ot` prints the command to run in a
new terminal tab; the choices still appear in the orchestrator's chat.

```sh
git clone https://github.com/RogyMD/onethread.git
cd onethread
./ot install           # links ot into ~/.local/bin (asks first)
ot                     # start screen: pick Claude or Codex
```

If your shell says `ot: command not found`, add `~/.local/bin` to your `PATH`
(for zsh: `echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc`, then
open a new tab).

```
onethread · 0 running · 0 waiting for you

  1  Claude   5h: 55% left · week: 94% left
  2  Codex    5h: 92% left · week: 99% left

Start the orchestrator with [1]:
```

Then just talk: *"Add ~/code/app-a. Add a task there: passkey login, high
priority."* New tasks use the agent you started with, unless you say "run it
in codex". `ot claude` / `ot codex` skip the question; `--new` starts a fresh
orchestrator conversation.

**iTerm2 notifications:** allow iTerm2 in System Settings → Notifications, and
in iTerm2 → Settings → Profiles → Terminal turn on "Notification Center
Alerts", then under "Filter Alerts…" check "Send escape sequence-generated
alerts". Without them, the orchestrator still tells you in the chat.

Claude's usage numbers on the start screen come from
[agent-deck](https://github.com/asheshgoplani/agent-deck)'s cache when it's
installed; without it, that line says "limits unknown".

**Remove it:** To remove only the command and keep your tasks, run
`rm ~/.local/bin/ot`. To delete onethread and all its data safely, first save
any work you want to keep, cancel unfinished tasks, close task tabs and the
orchestrator, then follow the full steps in [HOW-IT-WORKS.md](HOW-IT-WORKS.md).

## See it without spending quota

```sh
python3 demo/run_demo.py      # the full scenario: simulated agents and tabs
```

## Commands (the orchestrator runs these for you)

| | |
|---|---|
| `ot status [--json]`, `ot inbox` | what's running · what waits for you |
| `ot repo add PATH [--name N]`, `ot repo new PATH`, `ot repo base REPO BRANCH`, `ot add REPO TITLE [-p P] [--after T1] [--agent A]` | repos (`new` makes an empty one, asking first; `base` is the branch tasks start from and merge into: the remote's default, else main or master) and tasks |
| `ot start T` | hand a task to a background worker |
| `ot open WORDS-OR-ID` | switch into a task's own session in a new tab |
| `ot handback T`, `ot review T`, `ot accept T`, `ot changes T TEXT`, `ot answer T TEXT` | after you've looked |
| `ot priority T P`, `ot after T DEP`, `ot later T [WHEN]` | arrange |
| `ot pause T`, `ot resume T`, `ot cancel T` | stop and restart (stopping asks) |
| `ot merge T`, `ot pr T` | merge locally, or push and open a PR (both ask) |
| `ot finish T [--link URL]` | accept and merge now, or as soon as its worker is done; with a published link, the worker records it first. You never type it: paste the link or say "merge it when it's done". |
| `ot look T`, `ot show T ITEM…` | before accept or merge: if you haven't seen the work yet, the orchestrator asks what you want to review (a list in the chat) and opens your picks. You never type these: just say "merge it". |
| `ot me TEXT`, `ot cast [set\|add JSON]` | who you are, your people |
| `ot clean [T] [--keep]` | clean finished work now (also automatic) |
| `ot journal`, `ot tip offered\|yes\|no KEY`, `ot tips on\|off`, `ot prefs [remove N]`, `ot repo new PATH` | tips and preferences. You never type these: say "yes", "no more tips" or "forget that preference". |
| `ot wait` | the Claude orchestrator keeps one running in the background, so it can speak up when something happens. You never type it. |
| `ot config set capacity\|claude.model\|codex.model\|merge_on_accept\|notify V` | settings (`merge_on_accept 0`: accept no longer merges; `notify 0`: no notifications) |

## More

- [HOW-IT-WORKS.md](HOW-IT-WORKS.md): where data lives, safety, changing things
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): lifecycle, schedulers, leases, recovery
- [docs/STATUS.md](docs/STATUS.md): what's implemented, simulated, and unverified
- [AGENTS.md](AGENTS.md): rules for AI agents working on this code

## License

MIT. See [LICENSE](LICENSE).

## Welcome to my garden

Onethread is one of the projects grown in [AI Garden](https://aigarden.uk/). Welcome to my garden!
