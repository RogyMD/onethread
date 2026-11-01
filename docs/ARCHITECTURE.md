# Architecture

## Principles

- **The owner stays in their own agent window.** Claude Code or Codex, started by `ot` in the lobby folder, is the orchestrator; it coordinates through `ot` commands only. Each task has its own agent session, in its own git worktree, which the owner can enter in a new iTerm2 tab.
- **Reuse, don't rebuild.** Codex and Claude Code do all the coding, sandboxing and session persistence. Git worktrees isolate files. SQLite stores state. What's new here is only the task queue, the two schedulers, leases, and the operation layer.
- **The model proposes; code decides.** Agents return structured JSON. Scheduling, validation and anything destructive are deterministic code plus your y/N.
- **Core without UI.** Only `cli.py` touches the terminal.
- **The owner is the boss.** Taking over a task stops its worker at once; nothing restarts without the owner's word.

## Components

```
owner ⇄ Claude Code / Codex (orchestrator, ~/.onethread/lobby) ── runs `ot …`; hook adds context to each message
                │
cli.py ───────── subcommands · start screen · hook · open/session (iTerm2 tab) · confirmations (confirm.py)
                │
engine.py ────── detached, one at a time: supervisor.poll → scheduler.tick → finish.tick → leases.check_overdue → reap tabs → cleanup → notify.tick
                │
 ┌──────────┬──────────────┬───────────────┬───────────────┐
ops.py   attention.py   scheduler.py    supervisor.py    leases.py (+ lease_cli.py, shims)
 └──────────┴──────────────┴───────┬───────┴───────────────┘
   store.py (SQLite WAL) · cast.py · worktrees.py · agents/{claude,codex,fake}.py · schemas.py · prompts.py · views.py · usage.py
```

## Sessions and context

| Conversation | Agent session | Working directory | Permissions |
|---|---|---|---|
| Orchestrator | `claude --continue` / `codex resume --last` in the lobby (`--new` starts fresh) | `~/.onethread/lobby` | the owner's own; `ot` allowed |
| Task tab (`ot open`) | the task's session (`tasks.session_id`), interactive | the task's worktree | the owner's own, lease shims first on `PATH` |
| Task worker | **the same session**, resumed headless | the task's worktree | workspace-write / allowlist |

A task's session is created by its first tab (Claude: `--session-id` chosen up front; Codex: one short read-only headless turn, since Codex can't preset an id) or by its first worker. A session never gets two turns at once: the scheduler never starts a `with_you` task, and taking over stops a running worker first.

Who the owner is (meta `me`) and their cast (table `cast`: one right hand, ordered members, meta `address`; schema 2 adds `tasks.cast_name`) live in `cast.py`. `ot` never invents names: the orchestrator writes the cast with `ot cast set`, nudged by the hook. Open tasks without a name take the first free member (on add, on cast changes, before a worker starts, and at each hook); a finished task frees its name; `ot me` clears the cast and every name. Persona lines (`cast.orchestrator_persona`, `cast.task_persona`, built in `prompts.py` with parody guardrails) reach the orchestrator (`--append-system-prompt` / `developer_instructions`), task tabs (same flags, after the talk-naturally line) and workers (their prompt). Workers reply `{status: done|needs_input|failed, summary, question, tests, commits[]}` (strict schema, `onethread/schemas.py`).

## Task lifecycle

`draft → queued → running → review → done`, plus `blocked`, `paused`, `failed`, `interrupted`, `cancelled`, and the owner's two: `with_you` (in the task's tab) and `parked` (left it).

| From | To | Trigger |
|---|---|---|
| draft | queued | you approve implementation |
| queued | running | the worker scheduler starts it |
| running | review / blocked / failed | the worker's schema-valid result says done / needs_input / failed |
| running | interrupted | the process ended without a valid result (crash, kill, dead on restart) |
| running | paused / cancelled | you pause or cancel (confirmed; SIGINT to the process group, then SIGTERM after 10 s) |
| review | done | you accept (with `merge_on_accept`, the merge follows: see Finishing) |
| review | queued | you ask for changes (a follow-up on the same session) |
| blocked | queued | you answer the worker's question |
| paused / interrupted / failed | queued | resume or retry (same session; told to check `git status` first) |
| any open status | with_you | the owner switches in (`ot open`); a running worker is stopped first, without asking |
| with_you | parked | the tab's launcher exits, dies, or never starts within 60 s (`engine.reap_tabs`) |
| done | queued | a standing order's merge conflicts: the worker merges the base in and resolves |
| done | cancelled | the owner gives up on work that can't merge (its branch is kept) |
| parked | queued / review / cancelled | hand back (with a note to check git state) / review the branch / cancel |

Any other move raises `TransitionError` (`model.py`). Every change is written to `events`.

Just before a worker starts, `worktrees.refresh_base` moves a task branch with no commits of its own onto its same-repo dependency's finished branch, so planning early never loses the dependency's work. After a valid `done`, `ot` commits anything the worker left uncommitted and flags a branch with no changes. `cleanup.py` runs from the engine at most once a minute: for done/cancelled tasks it removes clean worktrees, `git branch -d`s branches merged into their base, and deletes `runs/<task>` 7 days after the task finished (from the status event, since `updated` moves on later edits); a dirty worktree becomes a `dirty_worktree` offer unless `keep_worktree:<task>` is set. `ot clean [T] [--keep]` runs the same round with the owner's confirmation for dirty worktrees and logs of any age. 

## Review before accept and merge (`review.py`)

The owner has *looked* at a task when a `reviewed` event is at least as new as the end of its latest run (`runs.ended`); a task with no ended run counts as looked. `ot session` (the owner's own tab) and `ot show T ITEM…` record `reviewed`. Before `ot accept`/`ot merge`/`ot finish`, the orchestrator runs `ot look T` (JSON: `looked`, and the items that exist: `summary`, `diff`, `files`, `agent`, `pr`, `nothing`) and, when not looked, asks the owner a multi-select question with those labels, then `ot show`s the picks. `diff` runs in the owner's repo against the task branch, so it works after cleanup; `files` and `agent` need the worktree. The confirmation popup is unchanged.

## Finishing (`finish.py`)

`finish.merge` is the one way `ot` merges (`ot merge` uses it too). The target is the repo's base branch; a task stacked on another task's branch waits until that branch is merged (or gone: cleanup only deletes merged branches). It never touches a repo with another branch checked out or uncommitted changes (`blocked`), and a failed `git merge --no-ff` is always aborted: `conflict` when it left unmerged paths, else `error` (a hook, signing, permissions), which goes straight to the owner as `merge_failed` instead of back to the worker.

A *standing order* is meta `finish:<task>` = `{"link", "link_pending", "conflicts"}`, given by `ot finish T [--link URL]` (one confirmation popup) or left by `ot accept` when `merge_on_accept` is on (the default). The engine runs `finish.tick` every round; `step` makes one move:

| Task | Does |
|---|---|
| cancelled | drop the order |
| review, link pending | followup "The owner published it: URL…", back to its worker once |
| review | store `published`, accept (done), record `reviewed` |
| done | merge: merged → drop; waiting for a dependency → keep; blocked → one `merge_blocked` offer (once per order, so a "yes" isn't asked again), retried each round; conflict → followup "merge {base} into your branch (do not rebase)…", done → queued; the second conflict → one `merge_failed` offer, drop |
| anything else | wait |

A link for a task already done is only stored in its result. One order's error is logged (`finish_failed`) and never stops the others; after 3 failing rounds in a row (a moved repo, say) the order is dropped with one `merge_failed` offer. A later order without a link keeps a link given earlier.

## Tips (`tips.py`)

The orchestrator notices repetitions; `ot` has no AI and supplies the rest. Every `ot` command except `hook`, `journal`, `engine` and `wait` is written to `events` (kind `cmd`: command, first 80 characters of its arguments, task id), kept 30 days; `ot journal [--days N]` prints it. The hook and the engine never write to it; text arguments (which may carry the owner's words, as in `ot answer`) are kept to 80 characters. Meta: `tips` (`off` disables), `tip_last` (when the last tip was offered), `tip_offered:K`, `tip_yes:K`, `tip_no:K` (K is a slug of the orchestrator's key, so spellings match). The prompt hook adds the owner's preferences (`<home>/preferences.md`, at most 30 lines of 200 characters, unreadable file left out) and, when tips are on and 24 hours have passed since `tip_last`, a tip slot line with the declined keys. `ot tip offered K` refuses while the slot is closed or K was answered before, and the orchestrator runs it before saying a tip, so one tip a day holds across orchestrator windows. `ot tip yes K TEXT` saves a preference only for a tip that was offered. A CraftApps tip uses `ot repo new PATH` (confirmed) and a task whose description asks the worker for the `discover-app` skill, installing nothing.

## Notifications (`notify.py`)

- **What is news:** an open attention item of kind review_ready, blocked, failed, interrupted, preempt, lease_overdue, merge_blocked or merge_failed, created after meta `notify_since` (set by the first tick ever, so nothing already waiting is announced) and not yet notified (meta `notified:<id>`), or one whose timed snooze (`snooze_until`) has passed since it was last notified (or since `notify_since`, for items older than that); and every `merged` event logged by the engine's standing orders (`"by": "engine"`) after meta `notify_event_upto`. next_task, dirty_worktree and parked are never announced. Any `ot` command about a task (except `hook`, `journal`, `engine`, `wait`) marks that task's open items as heard (`notify.seen`): the orchestrator is already telling the owner, so no banner follows.
- **Engine:** `notify.tick` runs last in each tick, logs one `notified` event per notice (`{item, kind, text}`) and posts it unless meta `notify` is `0`. A failing banner never fails the tick. The engine stays up (not idle) while a timed reminder is pending.
- **Banner:** the start screen records the lobby's tty (`lobby_tty`), `TERM_PROGRAM` (`lobby_term`; `iTerm.app` for tmux when `LC_TERMINAL` is `iTerm2`) and, under tmux, `$TMUX` (`lobby_tmux`) and `$TMUX_PANE` (`lobby_pane`). While that lobby lives (`lobby_pid`) and the tty path matches `/dev/ttys\d+`, the screen is that tty, or under tmux the first `/dev/ttys\d+` from `tmux -S <socket> list-clients -t <pane>` (pane must match `%\d+`). iTerm2 gets OSC 9 (`ESC ] 9 ; text BEL`) written to the screen, which iTerm2 shows as its own notification and focuses the tab on click; Apple's Terminal gets a lone BEL (tab bell mark, Dock badge). Anywhere else, with no screen, or if the write fails: no banner. The hook tells the orchestrator once per lobby (meta `banner_hint`) when it can't get banners: opened before they existed (no `ONETHREAD_LOBBY_PID`), or not in iTerm2/Terminal. Text is stripped of control characters (0x00–0x1f, 0x7f–0x9f) and cut to 200 characters.
- **Chat (`ot wait`):** blocks until a `notified` event after meta `waited_upto`, skipping offers since closed or snoozed, and prints `[offer ID KIND T] TEXT` (or the merge text), once per item. It also skips offers the hook or `ot inbox --next` already made (meta `offered_at:<id>`) and offers put off ("later", `attention_postponed`) after their notice. One waiter at a time (`<home>/wait.lock`); it serves the lobby it runs in (`ONETHREAD_LOBBY_PID`, set by the start screen) and ends when meta `lobby_pid` changes or that process dies. A time snooze clears any "few messages" snooze and the pending offer, so the reminder comes back on time. The start screen resets `waited_upto`, so a new orchestrator isn't flooded (the hook offers what waits). Refused inside the Codex sandbox. The hook tells the Claude orchestrator of the current lobby when no waiter runs; the lobby instructions say which choices to ask per kind, and "Remind me…" is `ot later T in N minutes`.

## Worker scheduler (`scheduler.py`)

1. Runnable = `queued`, all dependencies `done` (any repo), `not_before` passed.
2. Order: priority (urgent > high > normal > low), then tasks whose expected lease classes are free, then queue time.
3. Fill free slots up to the capacity (default 10). The engine ticks every 2 seconds.
4. An urgent task with no free slot creates a **preempt** attention item suggesting the lowest-priority running task. Nothing is paused without your y/N.

## Conversation scheduler (`attention.py`, delivered by `ot hook prompt`)

1. Never mid-turn: background events wait for the owner's next message, except notices, which the Claude orchestrator hears through `ot wait` (see Notifications).
2. With each message, the prompt hook (`UserPromptSubmit`, Claude Code and Codex) adds one line of context: counts, who the owner is, a request to build or extend the cast when needed, and at most one offer with its id and kind (`[offer ID KIND T]`). The orchestrator makes the offer only if its reply ends a topic, and records it with `ot inbox --shown ID`. Without hook context it runs `ot inbox --next`.
3. Score = kind weight (preempt 4; blocked, failed, interrupted, lease_overdue, parked 3; review_ready 2; next_task 1) + priority weight (urgent 3 … low 0). Ties go to the oldest.
4. "Later" (`ot later T [WHEN]`) snoozes an item for 3 messages, or until a time. An offer shown and not acted on by the next message counts as "later".
5. Switching never asks; the orchestrator repeats the card `ot open` prints: `Switching you to "Medication reminders" in pillbox (T4, Claude)`.

## Resource leases (`leases.py`)

Workers get `PATH=~/.onethread/shims:…`. The lease queue lives in its own database, `~/.onethread/leases/leases.db`, because that folder is the only one outside its worktree a worker may write. Stand-ins named like costly tools ask the broker (`leases` table) for a lease. A lease is granted when the class is below its limit **and** the request is first in line by task priority, then age (`BEGIN IMMEDIATE`, so it's atomic across processes). Cheap invocations of the same tool (`xcrun --find`) pass straight through. A waiting stand-in prints *"Not now: xcode is in use by T1 … You are #1 in line."* and gives up after 8 minutes with exit 75 and "retry the same command later", which stays below the agents' command timeouts (Claude's are raised for workers). Leases belong to a pid plus its start time, so dead holders are reclaimed. A lease held over 30 minutes becomes an attention item and is never killed.

## Recovery (`supervisor.py`)

Workers run detached (`start_new_session=True`, output to files), so they outlive the engine. Each run stores its pid and start time. `ot` (the start screen), the task commands and the prompt hook make sure the engine runs (never from inside a Codex sandbox, whose limits workers would inherit); it stays while the orchestrator runs and exits after 30 idle minutes otherwise. After each round it compares the newest modification time of `onethread/**/*.py` with the one it started with; when `ot` was updated it exits and starts a fresh engine on the new code (between rounds only, so running workers are reattached, never stopped). Every status change is a compare-and-set, and a worker claims `running` before its process starts, so a takeover can't slip past it. On start, the engine:
- reattaches runs whose process is alive with the same start time (no second launch);
- finalizes runs whose process is gone: a valid result is applied, otherwise the task becomes `interrupted` with an attention item;
- marks any `running` task with no open run as `interrupted`.

Claude session ids are chosen before launch (`--session-id`). Codex thread ids are read from the `thread.started` event as soon as they appear in the log.

## Risks and how they're handled

| Risk | Handling |
|---|---|
| Context bleeding between tasks or files clobbered | one session and one worktree per task; the lobby only sees a snapshot; never two concurrent turns per session |
| "Process exited" taken as "done" | only a schema-valid result moves a task out of `running` |
| `ot` crash or restart | detached workers; pid+start-time reattach; explicit `interrupted` |
| Coarse interruption (no mid-turn steering with `exec`) | pause or take over = stop + resume the same session; partial work stays in the worktree |
| The model answering a confirmation for the owner | confirmations are y/N on a terminal or a macOS dialog, never model input |
| Hook trouble blocking the owner's message | the hook never fails (prints `{}`) and never waits for input |
| Headless agent needing approval it can't ask for | allowlists cover the normal flow; denials surface as `needs_input` → `blocked` |
| Bad operations from the model (hallucinated ids, prompt injection from repo content) | strict validation in `ops.py`; destructive or irreversible operations confirmed by the owner |
| CLI output formats changing | one adapter file per agent, tested with synthetic samples |
| Many parallel agents overloading the Mac | leases for costly tools; capacity limit |
