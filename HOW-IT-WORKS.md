# How it works (owner's guide)

## What it does

`ot` opens Claude Code or Codex as your **orchestrator**, in `~/.onethread/lobby/`
where its instruction files live. You talk to it; it runs `ot` commands. Each
**task** has its own agent session in its own git worktree. The background
**engine** starts queued tasks, finishes them, and notices when you leave a
task's tab. Two schedulers decide what happens:

- the **worker scheduler** starts queued tasks by priority and dependencies, up to the capacity;
- the **conversation scheduler** picks at most one thing to bring up with each of your
  messages (through a prompt hook), and the orchestrator says it only at a natural pause.

**Notifications.** When a task is ready for review, needs your answer, failed,
stopped, can't merge or was merged, iTerm2 shows its regular notification for
the orchestrator's tab (also when it runs in tmux inside iTerm2). It has no
buttons; the Claude orchestrator gives you the choices in chat. In Apple's
Terminal the tab gets a bell mark instead.
Anywhere else, or with the orchestrator closed, there is no notification, and an
orchestrator opened before an update that can't post one says so once. The Claude orchestrator
keeps `ot wait` running in the background, so it speaks up in the chat at
once and asks what to do: the task's own action, "Open its session", or
"Remind me…" (5, 10, 30 minutes, 1 hour). Anything else means later. A
reminder comes back as a new notification and message; with the orchestrator closed,
it waits in the inbox. Codex has no way to be woken, so it tells you on your next message.

## Where your data lives

Everything `ot` stores is in `~/.onethread/` (or `$ONETHREAD_HOME`):

| Path | What |
|---|---|
| `state.db` | tasks, repos, runs, attention items, settings, event log (SQLite); the journal of `ot` commands (30 days) |
| `preferences.md` | your preferences, one per line; edit or delete lines freely |
| `lobby/` | the orchestrator's `AGENTS.md`, `CLAUDE.md`, `.claude/settings.json` |
| `worktrees/<repo>/<task>/` | one git worktree per task, on branch `ot/<task>-<slug>` |
| `runs/<task>/<n>/` | each worker run's prompt, output log and result |
| `shims/`, `leases/leases.db` | stand-ins for costly tools, and their queue |
| `engine.lock`, `engine.log` | the background engine (one at a time) |

Conversations stay where Claude Code (`~/.claude`) and Codex (`~/.codex`) keep
them; `ot` never reads them. Your repos only gain worktree registrations and
`ot/...` branches.

**Back up:** copy `~/.onethread/state.db`. The work itself lives on git branches.
**Cleanup:** automatic. Once a task is done or cancelled, the engine removes
its worktree when everything in it is committed (the branch keeps the work),
deletes its branch once merged, and deletes its run logs after 7 days. A
worktree with uncommitted changes, or with ignored files that aren't build
output (a `.env` you made there), becomes an offer ("delete it, or keep
it?"). `ot clean` does it all now and says how much space it freed; caches
shared with your other projects (Xcode DerivedData, npm, pip) are left alone.

**Remove onethread and its data:** First run `ot status` and quit any task
agent sessions in their tabs. Run `ot cancel T` for each unfinished task; this
asks before stopping a running worker and keeps its branch and worktree. For
work you want to keep, commit it to the task branch or copy the files out of
`~/.onethread/worktrees/<repo>/<task>` before running cleanup. Then run
`ot clean`; it asks before deleting uncommitted changes and ignored files.
Answer yes only when you want those files discarded. If you answer no, copy the
worktree somewhere outside `~/.onethread` before continuing, because deleting
that folder removes it. Quit the orchestrator, then from a new terminal run
`ot engine stop`, remove the command with `rm ~/.local/bin/ot`, and delete
`~/.onethread` with `rm -rf ~/.onethread`. Task branches live in their
repositories; delete `ot/*` branches separately only if you no longer need them.

## Safety

- Background workers run sandboxed in their worktree: Codex `workspace-write`
  (plus the lease folder), Claude `acceptEdits` with an exact allowlist (file
  edits, git, the repo's test command and the costly build commands). The
  bypass-everything flags are never used. Know that a test or build command
  (`npm run build`, `make test`, `xcodebuild`) runs the project's own scripts,
  which the Claude worker can edit: a worker misled by text in the repo could
  use that to run other commands. Only give tasks repos you trust.
- `ot` never runs git in a worktree whose `.git` no longer points into its
  repo, and turns off git's fsmonitor hook, so a worker can't make `ot` run
  commands outside its sandbox. Changed files picked for review open as
  plain text (pictures and PDFs in their viewer), never as programs.
- A task tab is your normal interactive session with your normal permissions;
  the lease stand-ins are still first on `PATH`.
- Cancel, merge, push/PR, accept, resume and pausing a running worker always
  ask **you**: y/N in your own terminal, and always a macOS dialog (default
  "No") when the orchestrator runs the command, since an agent can type into
  a terminal. Switching into a task never asks.
- The orchestrator is a normal Claude Code or Codex session: what it may do
  outside `ot` is governed by that agent's own permissions. The Codex
  orchestrator gets write access to `~/.onethread` so `ot` works from its
  sandbox, which means the model could also edit those files directly. The
  folder of lease stand-ins first on workers' `PATH` is emptied and rewritten
  before every worker and tab, so nothing planted there runs.
- `ot` itself only talks to your local tools. Usage numbers come from Codex's
  app server (an account query, no conversation) and agent-deck's local cache.
  The agents send what they read to their providers, as usual.

## Changing common things

- Parallel workers: `ot config set capacity 4`.
- Notifications: `ot config set notify 0` (off) or `1` (on); the chat still speaks up.
- Models: `ot config set codex.model <model>` / `claude.model <model>` (empty = the CLI's default).
- Who you are: `ot me Roman emperor`, `ot me plain`. The orchestrator then
  builds your people with `ot cast set` (see `ot cast`); `onethread/cast.py`
  stores them and gives each new task the next free name.
- Costly commands: set the `lease_classes` meta key to JSON shaped like
  `leases.DEFAULT_CLASSES` (xcode 1 at a time, build 2, heavy 2).
- The orchestrator's instructions: `onethread/lobby.py` (rewritten into the lobby at every start).

## Code map (for developers)

| File | Role |
|---|---|
| `onethread/cli.py`, `ot` | start screen, every subcommand, the hook |
| `onethread/engine.py` | background engine: ticks, recovery, parking left tabs |
| `onethread/lobby.py` | orchestrator instructions, settings and hook |
| `onethread/terminal.py` | find the task you mean; open an iTerm2 tab |
| `onethread/usage.py` | usage limits left |
| `onethread/confirm.py` | y/N or macOS dialog |
| `onethread/views.py` | status, inbox, cards, review text and JSON |
| `onethread/notify.py` | notices: what's news each tick, banners, `ot wait` |
| `onethread/tips.py` | the journal, the one-tip-a-day gate, declined tips, preferences |
| `onethread/finish.py` | merging into the base branch; standing orders ("finish T when it's ready") |
| `onethread/review.py` | has the owner looked at a task's latest work; what they can review |
| `onethread/model.py`, `store.py` | lifecycle and SQLite |
| `onethread/scheduler.py`, `attention.py` | the two schedulers |
| `onethread/supervisor.py`, `procs.py` | worker processes and recovery |
| `onethread/leases.py`, `lease_cli.py` | resource leases |
| `onethread/agents/` | one adapter per agent CLI (`claude`, `codex`, `fake`) |
| `onethread/ops.py`, `schemas.py`, `prompts.py`, `voice.py` | operations, JSON shapes, prompts, offer text |

**Adding an agent:** write `onethread/agents/<name>.py` with `invocation()`,
`parse()`, `explain()`, `interactive_argv()` and `lobby_argv()` (see
`claude.py`), register it in `agents/__init__.py` and `cli.make_agents()`, and
add it to `model.AGENTS`.

Run the tests with `python3 -m unittest discover -s tests -t .`.
