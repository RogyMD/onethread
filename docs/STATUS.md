# Status

Version 1.0.0. Last updated 2026-11-01.

## Implemented and tested (automated, `python3 -m unittest discover -s tests -t .`)

- Start screen: queue summary, Claude and Codex usage left, choice remembered, `ot claude|codex [--new]`.
- Orchestrator lobby: instructions (`AGENTS.md`/`CLAUDE.md`), Claude settings (allow `ot`, prompt hook), Codex hook and writable state folder via `-c`.
- Prompt hook: counts, who the owner is, cast requests and at most one offer per message; an offer nobody acted on counts as "later"; never fails and never waits for input.
- `ot` toolset: every operation as a subcommand, plain one-line errors.
- Switching: task lookup by id or words (ambiguous → asks), takeover stops a running worker without asking, `with_you` → `parked` when the tab's launcher exits (or dies, or never starts), hand back with a note to the same session.
- You are the boss: `ot me` (asked on first start; old `character` carries over), cast built by the orchestrator via `ot cast set|add`, names on tasks in status, offers, cards and lookup; right-hand persona for the orchestrator, member personas for workers and tabs; plain is the default.
- Task tabs talk in plain conversation, not the workers' JSON result format.
- Review in the chat: before accept or merge, the orchestrator runs `ot look`; when the owner hasn't seen the latest work (no `reviewed` since the last run ended), it asks a multi-select list of what to review and opens the picks with `ot show`. The owner's own tab counts as looking.
- Finishing: `ot finish T [--link URL]` (the orchestrator runs it when the owner pastes a link or says "merge it"), standing orders run by the engine, accept merges by itself (`merge_on_accept`), conflicts back to the task's worker (merge, not rebase), one offer when the repo isn't ready or a conflict repeats.
- Tips: journal of `ot` commands (30 days), one tip slot a day in the hook, declined tips never again, `ot tips on|off`, preferences file carried with every message, `ot repo new` for a CraftApps project.
- Notifications: the engine notices reviews, questions, failures, stops, merge problems and merges (never what already waited at the first start), posts an iTerm2 banner to the orchestrator's tab (OSC 9), or to the iTerm2 tab showing its tmux session, a bell mark in Apple's Terminal, nothing elsewhere (the hook says once why); `ot wait` for the Claude orchestrator (one at a time, ends with its orchestrator); choices per kind in the instructions; timed reminders keep the engine up; `ot config set notify 0|1`.
- Cleanup: finished tasks' clean worktrees removed automatically (engine, once a minute), merged branches deleted, run logs after 7 days, dirty worktrees offered; `ot clean [T] [--keep]` replaces `ot worktrees prune`.
- Background engine: single instance, recovery on start, exits when idle, restarts itself on new code after `ot` is updated (workers keep running).
- Release hardening (2026-10-29): Claude workers can't run `ot-lease` (it runs any command); changed files open as text; the shims folder is emptied and rewritten before each use; `ot` refuses to run git in a worktree whose `.git` points elsewhere and turns off fsmonitor; a lost session (resume says "No conversation found", or a new tab that exits within 20 s with an error) is forgotten; the orchestrator's conversation is resumed only after it has run an `ot` command; merge failures that aren't conflicts go to the owner; the base branch is the remote's default, else main/master (`ot repo base` changes it); `ot repo add --name`; cleanup keeps ignored files that aren't build output; no AppleScript without iTerm2; OS and database errors are one line; a new worker result keeps the PR and published links; Python 3.9 works.
- Confirmations: terminal y/N, or a macOS dialog when no terminal is attached (default "No").
- Plain errors from agents (e.g. "Codex rejected the request: The 'gpt-6-luna' model is not supported…"), per-agent model setting.
- Kept from 0.1: lifecycle, worker and conversation schedulers, worktrees, supervisor and recovery, resource leases, merge/PR.
- The demonstration scenario as an automated test (`tests/test_demo.py`).

## Release verification (2026-10-30)

- 405 automated tests passed on macOS Python 3.9.6 and Python 3.12.0.
- `python3 demo/run_demo.py` completed with fake agents and temporary repositories.
- Installed `ot` from a fresh local clone into a temporary home directory using macOS Python 3.9.6; the installed command's `ot --help` worked. Starting an agent and walking through removal were not part of this smoke check.
- The owner reports daily real use with both Claude Code and Codex. No real agent session was started for this release-preparation run.

## Simulated

- In tests and the demo, every agent is the fake agent (workers are local processes that commit one file), the orchestrator is played by the script running `ot` commands, and no iTerm2 tab is opened.

## Verified against the real tools (no model turns)

- `claude --resume ID … PROMPT` with `--append-system-prompt`; `codex resume ID PROMPT` with `-c`.
- Codex accepts the `hooks.UserPromptSubmit` override shape (and rejects wrong ones).
- Usage numbers: Codex via `codex app-server` `account/rateLimits/read`; Claude via `agent-deck usage --json`.
- The iTerm2 AppleScript compiles.

## In daily use (the author's own work, not a checklist)

- The orchestrator with Claude Code and with Codex, running real tasks with real workers; the hook context arriving in both.
- A real Claude orchestrator starting `ot wait` in the background by itself.
- iTerm2 showing an OSC 9 banner (tried by hand, not yet from the engine).

## Not yet checked one by one (some are likely covered by daily use)

- A real iTerm2 tab opening and the agent greeting in character.
- A real orchestrator building a cast from the hook's request (`ot cast set` with valid JSON).
- Codex lobby sandbox: `ot` writing state, opening tabs and dialogs from inside it (may need Codex's approval prompt). Known limit: the Codex orchestrator can write `~/.onethread` directly (needed for `ot` inside its sandbox).
- Background workers with the real CLIs (adapters from 0.1, still unverified): structured results, `git commit` in Codex's sandbox, lease shims first on `PATH` (macOS `path_helper`), Codex's command timeout vs the 8-minute lease wait.
- `ot pr` against a real remote.
- Finishing with a real worker recording a published link and resolving a real conflict; the orchestrator recognising "merge it when it's done" and pasted links.
- A real orchestrator noticing a repetition, offering one tip and following a saved preference.
- Claude's exact "No conversation found" text and exit code when a session id doesn't exist, and its exit code when quitting at the trust prompt.
- A real iTerm2 banner from OSC 9 written by the engine, and clicking it focusing the tab; the Terminal bell mark; the banner through tmux via `list-clients`.
- A real Claude orchestrator keeping `ot wait` in the background, being woken by it, and asking the choices (and the Claude Code background command staying alive for hours).
- The orchestrator asking the review list (Claude's multi-select question, Codex's numbered list) and opening picks with real tabs and apps.

## Not supported (by design, for now)

- Terminals other than iTerm2 (the command is printed instead).
- Buttons on the banner itself (that needs a signed app); the choices are asked in the chat.
- The Codex orchestrator speaking up by itself (nothing wakes it); it gets the banner and says it on your next message.
- Mid-turn steering of a background worker (taking over = stop + resume the same session).
- Automatic preemption, or merging without the owner's word; web UI; remote machines; agents other than Claude Code and Codex.
