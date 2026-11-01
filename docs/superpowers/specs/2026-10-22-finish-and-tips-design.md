# Finishing tasks, and tips from what you repeat — design

Status: draft for the owner's approval. Builds on
`2026-10-21-agent-window-orchestrator-design.md` and
`2026-10-21-cast-design.md`.

## Intent

The owner's first two days in the lobby (2026-10-21/22) showed the
orchestrator doing the same chores by hand, again and again:

- Seven times the owner pasted a published link, and the orchestrator ran
  "record this link" → waited for the worker → `ot accept` → `ot merge`.
- Accept and merge always went together; the owner never accepted without
  merging.
- Four times "merge it when done" became a hand-written `sleep`/`ot status`
  loop in the orchestrator.
- Twice a merge conflict between sibling tasks became a whole new task (T9,
  T13) just to resolve it.
- After an update, the old engine kept running against the new database and
  crashed (`TypeError: … 'cast_name'`).

The owner wants two things: (1) these chores handled by `ot` itself, and
(2) the orchestrator to notice such repetitions on its own in future, offer
a fix, and remember the owner's answer.

## Part 1: finishing tasks

### What the owner sees

- They paste a published link ("here's the r/space post: …"). The
  orchestrator runs `ot finish T10 --link URL`. The task records the link,
  then is accepted, merged into its base branch, and cleaned up, with no
  more words from the owner.
- "Merge T9 when it's done" → `ot finish T9`. If T9 is still running, the
  engine finishes it the moment it reaches review. No waiting loop.
- `ot accept T5` also merges (preference `merge_on_accept`, default on).
- A merge conflict sends the task back to its own worker ("the base branch
  moved; rebase onto it and resolve the conflict"); when it returns to
  review, the finish runs again. If the worker cannot resolve it, the
  owner gets one offer: "{label} can't merge: {reason}. Look at it, or
  cancel?"
- The engine restarts itself when `ot`'s code changes.

### How it works

- **`ot finish T [--link URL]`** sets a standing order on the task (meta
  `finish:<id>`, holding the link if any). The orchestrator uses it for
  "posted, merge it", "merge when done", "close it".
  - With a link: the link is added as a followup ("The owner published it:
    URL. Record the published link with the work, then stop.") and the task
    is queued for one more worker turn if it is in review (my call: the
    link lands in the repo, as the owner's records did by hand before;
    `ot` also stores it in the task's result as `published`).
  - The engine checks standing orders each tick: a task in `review` with a
    standing order is accepted and merged; the order is then removed.
    A `done` task with an order is merged at once.
  - Statuses other than review/done (blocked, parked, with_you) wait; the
    order survives. Cancel removes it.
- **Merge from the engine** uses the same checks as `ot merge` (base branch
  checked out in the owner's repo, no uncommitted changes there, never
  `--force`, abort on failure). When the owner's repo has a different
  branch checked out or uncommitted changes, the engine does not touch it:
  the task stays `done` with its order kept, and the owner gets one offer
  explaining what blocks the merge. The order retries when it next ticks
  and the repo is clean.
  - The owner approves once, in the popup shown when the order is given
    (Part 3); the engine's later merge asks nothing more.
- **Conflict:** the merge is aborted (owner's repo left exactly as
  before), the task goes `done → queued` (new transition, my call: reuses
  the existing requeue path) with a followup to rebase onto the current
  base and resolve conflicts, keeping the standing order. Two failed
  rounds in a row → the offer above, order removed.
- **Engine restart:** at start the engine records a fingerprint of its own
  code (newest modification time of `onethread/*.py`). Each tick it
  compares; when changed and no worker launch is mid-flight, it exits
  cleanly; the next `ot` command or hook starts the new code (existing
  `ensure`). Running workers are reattached as today.

## Part 2: tips from what the owner repeats

### What the owner sees

- At most once every 24 hours, at a natural pause, the orchestrator may
  offer one tip in the cast's voice: "Your Majesty, three times today you
  pasted a post link and I merged and closed the task. Shall I do that by
  myself from now on?"
- **Yes** → saved as a preference; every orchestrator window follows it
  from then on, `--new` included.
- **No** → that pattern is never offered again.
- `ot tips off` (or "no more tips") stops tips; `ot tips on` brings them
  back. `ot prefs` lists preferences; the owner can remove any, or edit
  the file by hand.
- Three kinds of fix the orchestrator may suggest:
  1. a preference (most cases): "from now on, I'll …";
  2. a missing `ot` feature: offered as a task in the onethread repo,
     only if the owner registered it as a repo;
  3. a chore outside `ot` that a small app or script would remove:
     offered as a CraftApps project (`discover-app` → `build-app`),
     started only after the owner's yes, in a folder the owner names.

### How it works

- **Who notices:** the orchestrator (an LLM). `ot` has no AI; it supplies
  the gate, the data and the memory.
- **Gate:** the prompt hook adds "A tip slot is open …" to the context only
  when tips are on and no tip was offered in the last 24 hours. Otherwise
  it says nothing about tips. The slot text asks the orchestrator to look at
  its own conversation and `ot journal` for something the owner or it did
  three or more times that no preference or declined tip covers, and to
  offer at most one tip, or none.
- **Journal:** `ot` records every `ot` command it runs (command name, task
  id, and the first 80 characters of any text argument) in its existing
  events table. `ot journal [--days 7]` prints it. Local only, kept 30 days
  (my call).
- **Memory:** `~/.onethread/preferences.md`, one preference per line, plain
  text the owner can read and edit. Declined tips and the time of the last
  tip live in `ot`'s database.
  - `ot tip offered KEY` (KEY: a short slug the orchestrator chooses for
    the pattern) starts the 24-hour clock.
  - `ot tip yes KEY "preference text"` saves the preference.
  - `ot tip no KEY` records the decline.
- **Preferences reach the orchestrator:** the hook includes every
  preference line in each message's context (one line each). The lobby
  instructions say preferences override the default way of working, never
  the safety rules.

## Part 3: review in the chat before merging

The owner's request: before an agent merges (or similar), let them pick
what to review, from a list, in the chat. The popup itself stays a plain
**No · Yes** and still confirms every merge, accept and finish.

### What the owner sees

- The owner says "merge T7", "accept it" or "finish it". If they have not
  looked at T7's latest work yet, the orchestrator first asks in the chat,
  as a multi-select list: "What do you want to review before I merge?"
  - ☐ What the worker says (summary, shown in the chat)
  - ☐ All changes (full diff against the base, new iTerm2 tab, paged)
  - ☐ Changed files (opened with their usual app)
  - ☐ Talk to the agent (its tab, as `ot open`)
  - ☐ Pull request page (browser; only when one exists)
  - ☐ Nothing, go ahead
  Only items that exist for the task are listed.
- Claude: its native multi-select question. Codex (no such list in normal
  chat): a numbered list; the owner answers "1 3".
- It shows or opens what was picked, waits for the owner's "ok", then runs
  the command; the usual popup confirms.
- If the owner has already looked, no list: the command runs and the
  popup confirms.

### "Already looked" (the owner's rule: ask only when they haven't)

The owner has looked at a task's latest work when, after the worker's last
turn ended, any of these happened (all recorded by `ot` as a `reviewed`
event):
- they opened the task's tab (`ot open`, `ot session`);
- they picked anything from the review list, "Nothing, go ahead"
  included;
- they pasted the published link for it (`ot finish --link`): they read
  it to post it (my call).
A new worker turn makes the work new again, except a turn that only
records a published link (my call: the owner already saw that work).

### How it works

- `ot look T` prints, as JSON, whether the owner has looked at the latest
  work and which review items exist (summary, diff, files, agent, pr).
- `ot show T ITEM…` opens the picked items (`summary` prints in the
  chat; `diff` opens a tab; `files` opens each changed file; `agent`
  opens the task's tab; `pr` opens the browser) and records `reviewed`.
  Without iTerm2, `ot` prints the diff command to run, as `ot open` does.
- The lobby INSTRUCTIONS: before `ot accept`, `ot merge` or `ot finish`,
  run `ot look T`; if not looked, ask the list first.
- `ot finish` asks its popup once, when the order is given. The engine
  then carries the order out without asking again. With
  `merge_on_accept`, the accept popup says "Accept and merge …" and asks
  nothing more.
- The dirty-worktree cleanup question stays as today.

## Goals

1. `ot finish` with standing orders, engine-run accept and merge.
2. `merge_on_accept`, on by default.
3. Conflicts handled by the task's own worker, then a retry.
4. Engine restarts on code change.
5. Tips: 24-hour gate, journal, preferences file, declines, on/off.
6. Review in the chat, only when the owner hasn't looked yet.

## Non-goals

- Owner facts for workers (the owner's name, app links), opening files in the
  owner's editor, richer `ot status` errors: not now.
- No automatic detection of a published post: the owner pastes the link.
- No pattern detection inside `ot` itself.

## Anti-goals

- Never push, open a pull request, post anywhere, or send anything off
  this machine.
- Never merge into a repo with uncommitted changes or a different branch
  checked out; never leave the owner's repo mid-merge; never delete an
  unmerged branch.
- Never act on a tip without the owner's yes; never more than one tip per
  24 hours; never repeat a declined tip.
- Never read Claude or Codex history files; the journal holds only `ot`'s
  own commands.
- A preference never overrides a safety rule.

## Calls settled after the builder check (my calls)

- `ot finish T --link URL` on a task already `done`: the link is stored in
  the task's result only (`published`), no new worker turn; if not yet
  merged, it merges. In `review`, `running` or `queued`: the worker records
  it, as above.
- A task stacked on another task's branch merges only after that
  dependency is merged; its target then becomes the repo's own base
  branch (the one the dependency merged into).
- On a conflict the worker **merges the current base into its branch**
  and resolves there, never rebases: other open tasks may build on it.
- `ot accept` that cannot merge (dirty repo, other branch checked out,
  conflict) turns into a standing order, so the merge follows later.
- A CraftApps tip, after the owner's yes: the orchestrator asks for a
  folder, then (with the owner's confirmation dialog) creates it with
  `git init`, registers it (`ot repo add`), and adds a task whose
  description tells the worker to use the CraftApps `discover-app` skill.
  If the worker's agent has no CraftApps skills installed, the task
  blocks with that message; nothing is installed automatically.
- "Safety rules" a preference can never override: the native
  confirmation dialogs, no push or publish, no reading Claude/Codex
  history, never deleting unmerged branches or dirty worktrees.

## Left to the builder

Exact command names and flags beyond those above, the standing-order
storage shape, offer wording, the journal format, test structure, and the
fingerprint mechanism. Update README, HOW-IT-WORKS, docs/ARCHITECTURE,
docs/STATUS, the lobby INSTRUCTIONS and the demo in the same change.
