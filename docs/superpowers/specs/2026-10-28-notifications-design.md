# Notifications — design

Status: for the owner's approval with its plan
(`docs/superpowers/plans/2026-10-28-notifications.md`).

## Intent

The owner gave the orchestrator a request, a worker finished, and nothing
told them: the orchestrator only speaks when the owner writes, and offers
waited for their next message. The owner wants both: a banner on the
Mac, and the orchestrator speaking up in the chat on its own. From the
notification they want to reach the task's agent in a terminal tab, or
be reminded in 5, 10, 30 minutes or 1 hour.

No build step, no app to sign, nothing installed. The owner did not want
a popup that blocks: they want the soft banner from the top of the
screen, like the one iTerm shows when an agent asks for permission.

## What the owner sees

- A worker finishes (or gets stuck, fails, stops, can't merge, or is
  merged). A banner slides in from the top right, from iTerm2:
  'Cicero: "Medication reminders" in pillbox (T4) is ready for review.'
  It goes away by itself; it never takes focus.
- Clicking the banner jumps to the orchestrator's tab. There the
  orchestrator has already said it in the chat and asks what to do (the
  owner approved this table, 2026-10-28):

  | What happened | Choices |
  |---|---|
  | Ready for review | Review it · Merge it · Open its session · Remind me… |
  | Needs your answer | Answer it · Open its session · Remind me… |
  | Failed | Look at it · Try again · Open its session · Remind me… |
  | Worker stopped | Resume it · Open its session · Remind me… |
  | Urgent task waiting for a worker | Pause the other task to make room · Remind me… |
  | Lease held too long | Open its session · Remind me… |
  | Can't merge: repo state | Merge now · Remind me… |
  | Can't merge: conflict | Look at it · Cancel it · Open its session · Remind me… |
  | Merged | told, no question |

  "Remind me…" then asks 5 min · 10 min · 30 min · 1 hour. There is no
  "Later" button: anything else, or no answer, means later (Claude's
  question holds 4 choices). "Merge it" still runs the review check and
  the confirmation popup. Codex shows the same choices as a numbered list.
- A reminder comes back as a new banner and a new line in the chat when
  its time is up.
- `ot config set notify 0` turns banners off; the chat keeps speaking up.

## How it works

- **What counts:** a new attention item of kind review_ready, blocked,
  failed, interrupted, preempt, lease_overdue, merge_blocked or
  merge_failed, a timed reminder whose time is up, and a task merged by
  the engine. Not: next_task, dirty_worktree, parked (the owner just left
  that tab themselves) (my call).
- **The engine notices** each tick (`notify.tick`), records a `notified`
  event per notice, and posts the banner. Things that were already
  waiting when notifications first run are not announced (my call: no
  flood on the first start).
- **Banner:** the lobby records its terminal (tty path, e.g.
  `/dev/ttys004`) and `TERM_PROGRAM` when it starts. While the lobby lives
  in iTerm2, the engine writes iTerm2's notification escape code
  (`ESC ] 9 ; text BEL`) to that terminal: iTerm2 shows its own banner,
  and clicking it opens that tab. Under tmux inside iTerm2 it goes to the
  tab attached to the lobby's tmux session. Otherwise (lobby closed,
  another terminal) there is no banner. (Changed 2026-10-28: the owner
  disliked the macOS `osascript` banner, which has no choices and opens
  Script Editor, so it was dropped.)
  In Apple's Terminal the engine also rings the orchestrator tab's bell
  (a lone BEL): Terminal marks that tab and badges its Dock icon (the
  owner's choice, 2026-10-28).
- **Banners cannot carry buttons** without a signed app, so the choices
  live in the chat (my call, from the owner's "no build").
- **Chat:** `ot wait` blocks until there is a new notice and prints it.
  The Claude orchestrator keeps one running in the background; Claude
  Code wakes it when the command ends. One waiter at a time (a lock); it
  ends when the orchestrator that started it is gone. Codex has no such
  wake-up: it gets the banner, and the offer on the next message as
  today; `ot wait` refuses to run inside Codex's sandbox (my call: Codex
  would otherwise sit blocked on it).
- **Reminders:** "remind me in 10 min" is `ot later T in 10 minutes`
  (exists today). The engine stays running while a reminder is pending,
  so the reminder fires even with the orchestrator closed.

## Anti-goals

- Never put anything but plain text in the terminal: control characters
  are stripped from the banner text, and the engine writes only to a
  path that looks like a terminal (`/dev/ttysN`).
- No sound, no focus stealing, no modal dialog for notices.
- Nothing installed or built; standard library and macOS's own tools only.
- Never read Claude or Codex files to find their windows.
