# AGENTS.md

Instructions for any AI agent working in this repository. Read fully before changing anything.

## What this is

`onethread` (`ot`): a local-first toolset that turns the owner's own Claude Code or Codex window into an **orchestrator** while many agents work in the background across several git repos, one session and worktree per task, and switches the owner into any task's session in a new iTerm2 tab. Two schedulers: the **worker scheduler** (which task runs, capacity, priority, dependencies, resource leases) and the **conversation scheduler** (what deserves the owner's attention next, offered at natural pauses).

## Rules

- **Python 3.9+ standard library only.** No third-party packages, no package manager, no build system. External programs: `git`, `codex`, `claude`, and `gh` (only after the user confirms).
- **Core has no terminal I/O.** Only `onethread/cli.py` prints or reads; everything else returns data and takes a `confirm(question) -> bool` callback.
- **The LLM proposes, code decides.** The orchestrator acts only through `ot` commands, which validate everything. Scheduling never comes from model output.
- **Destructive actions are confirmed by the owner** (`confirm.py`: terminal y/N or a macOS dialog), never by the model: cancel, merge, push/PR, accept, resume, pausing a running worker, removing a worktree. Taking over a task never asks.
- **The owner is the boss.** Switching into a task stops its worker; the scheduler never touches `with_you` or `parked` tasks.
- **A task is `done` only by the user accepting a review.** A worker process exiting is not completion: only a schema-valid result moves `running` forward. Anything else is `interrupted`.
- **Never pass `--dangerously-bypass-approvals-and-sandbox` or `bypassPermissions`** to an agent.
- **`ot`'s own words stay plain** (`voice.py`, `views.py`). The owner's world (`ot me`, the cast) is played by the agents through `cast.py`/`prompts.py` persona lines, never by templates; templates add the cast name, never replace title, repo or id.
- **No network code and no telemetry in `ot` itself.** Agents talk to their own providers. Never read Claude or Codex conversation files.
- **The hook must never fail or block** the owner's message.
- **Never commit real data:** no `~/.onethread` contents, logs, or transcripts. Tests and the demo use temporary directories and synthetic repos.

## Layout

```
ot                      entry script
onethread/cli.py        start screen, subcommands, hook
onethread/engine.py     background engine (ticks, recovery, parking left tabs)
onethread/lobby.py      orchestrator instructions and settings
onethread/terminal.py   task lookup by words; iTerm2 tabs
onethread/usage.py      usage limits left
onethread/confirm.py    y/N or macOS dialog
onethread/views.py      status, inbox, cards, review text
onethread/model.py      statuses, priorities, lifecycle transitions
onethread/store.py      SQLite persistence
onethread/worktrees.py  git worktree per task
onethread/scheduler.py  worker scheduler
onethread/attention.py  conversation scheduler
onethread/voice.py      offer text (plain)
onethread/leases.py     resource leases; lease_cli.py runs the PATH stand-ins
onethread/procs.py      pid liveness that survives pid reuse
onethread/agents/       claude, codex, fake adapters + fake_worker.py
onethread/schemas.py    JSON shapes agents return + validator
onethread/prompts.py    what agents are told; persona lines
onethread/cast.py       who the owner is, their people, name assignment
onethread/supervisor.py launch, watch and recover detached worker processes
onethread/ops.py        validate + apply operations
demo/run_demo.py        scripted end-to-end demonstration (fake agents)
tests/                  unittest suite
docs/                   ARCHITECTURE.md, STATUS.md, superpowers/ (design and plan)
```

## Testing

Run before finishing any change:

```sh
python3 -m unittest discover -s tests -t .
```

Tests use fake agents and temporary git repos. They never call real Codex or Claude. Real-agent checks are manual and documented in `docs/STATUS.md`. Update `docs/STATUS.md` and `README.md` in the same change as any behavior change.

## Git

Small focused commits, imperative subjects. Check `git status` and the staged diff before each commit.
