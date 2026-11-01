# Orchestrator in your agent window — design

Status: draft for approval · 2026-10-21 · replaces the `ot chat` terminal of the MVP

## Intent

The MVP built the engine right but put the human in the wrong place: a
separate `ot chat` prompt (`[lobby] you>`). The owner wants to stay in the
window they already use — Claude Code or Codex, whichever they pick — and just
talk: "I want this", "get this too, it's urgent", "let's switch to the
medication task in pillbox". That window is the **orchestrator**. It keeps all
state in SQLite (as today), runs one agent session per task in the background,
and, when asked, puts the owner straight into a task's own session.

Philosophy, in the owner's words: **"I'm the boss."** Taking over always wins;
nothing restarts, merges, or ships without the owner's word.

## Goals

1. **Talk to the orchestrator in a normal agent window.** `ot` (no arguments)
   shows a short start screen — a one-line summary of the queue and, for
   Claude and Codex, how much of each usage limit is left — and asks which
   one to start as the orchestrator. It then opens that agent set up as the
   orchestrator. Everything is done by talking, including adding
   repos ("add ~/Projects/pillbox").
2. **One real session per task.** Each task has its own Claude or Codex session
   (as assigned) living in the task's git worktree, used both by background
   work and by the owner directly.
3. **Switching by talking.** "Switch to the task in pillbox about medication" →
   the orchestrator finds the task, says
   `Switching you to T4 · pillbox · medication reminders (Claude)`, and opens a
   new **iTerm2** tab running that task's session.
4. **Taking over pauses the agent.** If the task's agent is working, it is
   stopped at once — no y/N, the switch request *is* the consent. The session
   continues where it stopped; the owner sees its history in the tab.
5. **Greeting in character.** On takeover the agent greets with a short report
   of what it was doing, in the chosen character, e.g.
   *"Milord, I was adding the reminder schedule. Two of three screens are done,
   tests not yet written. What is your command?"*
6. **Leaving the tab parks the task.** When the owner closes the tab, the task
   waits (`with you` → `parked`). Next time the owner is in the orchestrator it
   asks: hand it back to the agent, send it to review, or leave it. Nothing
   restarts on its own.
7. **Characters.** The owner picks any character — plain, king, gangster,
   baron, Roman emperor, or free text including real people in parody style.
   Every agent (orchestrator and all task agents) speaks as that character
   for the whole conversation. Code, commands, file paths, errors, and
   confirmation questions stay plain and exact.
8. **Errors in plain words.** A failed agent call is reported as one readable
   line ("Codex rejected the request: model gpt-6-luna isn't available on your
   account. Fix: ot config set codex.model <model>"), never as a raw JSONL dump.

## Kept from the MVP (unchanged behavior)

Store and lifecycle, worker scheduler (priority, dependencies, capacity 10,
urgent preempt offers), attention scheduler (one offer at natural boundaries,
"later" respected, no "next up" after the owner's own switch), git worktree per
task, resource leases (xcode/build/heavy), detached workers with restart
recovery, explicit approval before destructive steps, schema-checked results.

## What changes

### 1. `ot` becomes a toolset the orchestrator agent calls

- Every operation in `ops.py` is reachable as a CLI subcommand with short,
  plain output and `--json` where the agent needs structure:
  `ot repo add PATH`, `ot add REPO TITLE [-p] [--after] [--agent]`,
  `ot status`, `ot inbox [--next]`, `ot plan T`, `ot start T` (approve),
  `ot priority T P`, `ot after T DEP`, `ot later T [WHEN]`, `ot review T`,
  `ot accept T`, `ot changes T TEXT`, `ot answer T TEXT`, `ot pause T`,
  `ot resume T`, `ot cancel T`, `ot merge T`, `ot pr T`, `ot open T`,
  `ot character TEXT`, `ot config set KEY VALUE`.
- `ot` with no arguments: ensures the background engine is running (below),
  then `exec`s the chosen agent CLI in `~/.onethread/lobby/`, a folder holding
  the orchestrator's instructions (`AGENTS.md`, plus `CLAUDE.md` pointing to
  it). Claude Code also gets a settings file there (permissions, hook).
- The orchestrator's instructions say: you coordinate, you don't code; use only
  `ot` commands; announce every switch with the task card; at most one offer
  per reply, only at a natural pause; speak as the current character; never
  approve, merge, cancel, or push on your own.
- The `ot chat` REPL and its slash commands are removed (remove before you
  add). `conversation.py` shrinks to what the CLI and engine need.

### 1b. Start screen, usage limits, default agent

```
$ ot
onethread · 3 running · 2 waiting for you

  1  Claude   5h: 55% left · week: 94% left
  2  Codex    limits unknown

Start the orchestrator with: 1
```

- Enter repeats the last choice. `ot claude` / `ot codex` skip the question.
- **Default agent = the orchestrator's agent for that session.** New tasks
  use it unless the owner names the other one ("run it in codex"). A task
  keeps its agent for life (its session belongs to that tool); the repo-level
  default agent and `ot repo add --agent` are removed.
- **Claude limits:** Claude Code reports rate limits only to its status line.
  The owner's agent-deck already caches them (`agent-deck usage --json`:
  `five_hour` / `seven_day` `used_percentage`); `ot` reads that cache when
  agent-deck is present and shows "unknown" otherwise. Changing the owner's
  status line to feed `ot` directly needs their approval first.
- **Codex limits:** only from a source Codex itself offers for this (for
  example its app-server's rate-limit request); builder verifies. `ot` must
  not parse Codex session files, which contain the owner's conversations.
  Unknown → "limits unknown".
- Stale numbers (older than 1 h) are shown with their age.

### 2. A small background engine

Workers must start and finish while the owner is idle or in a task tab, so the
scheduler can no longer live in a chat loop.

- `ot engine` runs the existing tick loop (`scheduler.tick`, `supervisor.poll`,
  `check_overdue`) every ~2 s. Single instance via the existing lock file.
  Started by `ot` on demand, detached; exits after ~30 min with nothing running,
  queued, or open in a tab. `ot engine stop` stops it.
- Every `ot` command also does a quick tick, so state is fresh even if the
  engine was stopped.
- It never talks to the owner. Offers wait for the owner's next message.

### 3. Offers reach the orchestrator at the right time

- **Claude Code:** a `UserPromptSubmit` hook runs `ot hook prompt`, which adds
  to each owner message a few lines of context: the current pending offer (if
  the attention scheduler picks one), counts (running/ready/blocked), and the
  active character. The orchestrator decides whether this reply is a natural
  pause; if it offers, it runs `ot inbox --shown ID` so "ignored = later"
  keeps working.
- **Codex:** if Codex 0.147 supports an equivalent prompt hook, use it;
  otherwise the instructions tell it to run `ot inbox --next` at the start of
  every turn. Builder verifies which.

### 4. Switching into a task (`ot open T`)

1. Resolve the task (the orchestrator matches "pillbox medication" against
   `ot status --json`; ambiguous → it asks).
2. If running: stop the worker the way `pause` does today, without a y/N.
   Leftover uncommitted changes stay in the worktree.
3. Set status `with_you`. The scheduler never touches `with_you` or `parked`
   tasks; queued follow-ups are kept for later.
4. Open a new iTerm2 tab (AppleScript via `osascript`) running
   `ot session T` — a small launcher that `cd`s into the worktree and runs the
   agent's interactive resume (`claude --resume SID …` / `codex resume SID …`)
   with an opening prompt asking it to greet in character and report
   what it was doing. A task with no session yet (a fresh draft) starts a new
   interactive session in the worktree, so planning happens here too.
5. When the agent process exits (tab closed or the owner quits), the launcher
   sets status `parked` and adds an attention item `parked` ("You left T4
   medication — hand back, review, or leave?").
6. "Hand back" queues the task; the background worker continues the same
   session with a prompt saying the owner worked in it and to continue from
   the current state. This replaces the old "approve implementation" step:
   plan with the agent in its tab, close it, say "hand it back".

New statuses: `with_you`, `parked`. Transitions: any non-terminal status →
`with_you`; `with_you` → `parked`; `parked` → `queued | review | with_you |
cancelled`.

### 5. Characters

- Stored as `meta.character` (free text, default `plain`); set by talking
  ("be a Roman emperor") → `ot character "Roman emperor"`.
- One persona line, generated from the text, goes into: the orchestrator's
  hook context, every worker prompt (result `summary` written in character),
  and the interactive task sessions (Claude: `--append-system-prompt`; Codex:
  `-c developer_instructions=…` or equivalent — builder verifies).
- The persona line always carries the guardrails: facts only from `ot`
  output; code, commands, paths, errors, and confirmation questions plain;
  propose, never decide; the owner's choice of character is played as a style,
  not a claim to be that person.
- `ot`'s own fixed output (cards, errors, confirmations) stays plain; the
  hand-written KING templates in `voice.py` are dropped, the king becomes one
  character among many.

### 6. Confirmations the model cannot fake

Destructive steps (cancel, merge, push/PR, pausing a running task from the
orchestrator, accept, removing worktrees) must be confirmed by the owner, not
the model:

- **Claude Code:** the lobby settings allow safe `ot` subcommands without
  asking and leave destructive ones un-allowed, so Claude Code's own
  permission prompt shows the exact command and the owner approves it.
- **Codex:** equivalent via its approval rules if available; otherwise `ot`
  shows a native macOS dialog (`osascript display dialog`) for those steps.
- Taking over a task by switching to it is *not* destructive and never asks.

### 7. Plain errors and setup

- Agent adapters turn a failed turn into one line: what failed, the provider's
  message, and a fix if known. Full logs stay in `~/.onethread/runs/`.
- `ot config set codex.model M` / `claude.model M` pass `-m`/`--model` to
  every call (the owner's Codex default `gpt-6-luna` is rejected for a ChatGPT
  account).
- `ot install` symlinks `ot` into `~/.local/bin` after asking.

### 8. Calls made without asking (owner can overrule)

- `ot` resumes the orchestrator's previous conversation each time
  (`ot --new` starts fresh); the database is the source of truth either way.
- Task tabs open as new tabs in the current iTerm2 window.
- To switch between tasks the owner goes back to the orchestrator tab; a task
  agent does not switch tabs itself.
- Interactive task sessions run with the owner's normal agent permissions (the
  owner is present to approve), but with the lease shims first on `PATH`, so
  an Xcode build in a tab still waits its turn.
- "Send to review" on a parked task with no worker result shows the branch
  diff and the owner's last messages as the review.
- In a task conversation the agent stays in character throughout; only code,
  commands, paths, errors and confirmations are plain (owner answered "ok").

## Non-goals

- No GUI, web page, or menu-bar app. No terminals other than iTerm2 for now
  (others print the command to run instead).
- No notifications while the owner is idle; offers wait for their next message.
- No replacement of agent-deck or tmux; this does not manage arbitrary sessions.
- No multi-user, no remote machines.

## Anti-goals (failure even if it works)

- The orchestrator interrupts mid-thought or chains several offers.
- An agent keeps working in a task the owner has switched into.
- Anything restarts, merges, or pushes without the owner's word.
- A character changes facts, hides an error, or mangles code.
- Raw logs dumped at the owner.

## Constraints

Python 3.10+ standard library only; local-first; workers never get
`--dangerously-bypass-approvals-and-sandbox` / `bypassPermissions`; worktrees
outside the owner's repos; private notes stay out of git.

## Left to the builder

- Exact prompt wording, persona-line template, hook output format.
- Engine idle timeout and tick interval within the bounds above.
- How the orchestrator matches a spoken task description to an ID.
- Verifying on the real CLIs: Claude `--append-system-prompt` and an initial
  prompt with interactive `--resume`; Codex `resume SID PROMPT`, a prompt hook,
  developer instructions, model flag; iTerm2 AppleScript tab creation.
- Migrating existing tests from `ot chat` to the CLI and keeping the scripted
  demo working (it drives `ot` commands with the fake agent).
