# You are the boss, the agents are your people — design

Status: draft for the owner's approval. Builds on
`2026-10-21-agent-window-orchestrator-design.md` (§5 characters), which it
replaces.

## Intent

Today the owner picks a *character* (`ot character king`) and every agent
speaks *as* that character. The owner wants the opposite: they pick **who
they are** ("ot me Caesar", "ot me king", anyone), and every agent becomes
**someone from that person's world** — the orchestrator is their right hand,
each task's agent is a named member of their circle. It is play: the owner
runs their work like the boss of that world.

## What the owner sees

- First `./ot` ever: the start screen asks "Who are you?" with a few
  examples (king, gangster boss, Roman emperor, pirate captain, plain). Enter = plain.
- `ot me Caesar` (or telling the orchestrator "now I'm Caesar") changes it
  any time. Everyone is recast into the new world, open tasks included.
- The orchestrator speaks as the right hand (Caesar → Agrippa; king → the
  chancellor; gangster boss → the consigliere) and addresses the owner the
  way that world would ("Imperator", "Your Majesty", "Boss").
- Each task gets a cast member's name when created and keeps it until done:
  `Cicero · "Medication reminders" · pillbox · running (T4)`.
- In a task tab, that member greets the owner: "Imperator, I
  was fixing the reminders. What do you need?"
- "Let me talk to Cicero" switches to Cicero's task, like any other words.
- When the circle runs out of people, the orchestrator invents more names
  that fit the same world.
- `plain`: no roleplay, no cast names anywhere.

## Goals

1. `me` replaces `character` everywhere (meta key, command, prompts).
2. A cast: right hand + members, each with a name and a one-line style,
   plus how the owner is addressed.
3. Every task carries its cast name; status, inbox, cards, review headers
   and offers show it; task lookup matches it.
4. Every agent prompt (orchestrator, workers, task tabs) tells that agent
   who it is, who the owner is, and how to address them.
5. First-start question and later changes, from the terminal or by asking
   the orchestrator.

## Non-goals

- No preset catalogue of worlds. The orchestrator (an LLM) builds any cast.
- No per-task voice choice; one world at a time.
- No AI inside `ot` itself. `ot` stores and hands out names; the
  orchestrator invents them.

## Anti-goals (the roleplay must never cost facts)

- `ot`'s own output stays plain and exact: ids, statuses, errors,
  confirmations, commands, file paths, numbers. The cast name is added,
  never substituted for the title, repo or id.
- Worker JSON results stay machine-readable; only `summary` is in voice.
- Real people are a light parody of style, never a claim to be them, and
  never invented real-world claims about them or anyone in their circle.
  The existing persona guardrails stay.
- A missing or broken cast never blocks work: tasks run without a name and
  agents speak plainly until a cast exists.

## How it works

### Data

- meta `me`: who the owner is; `"plain"` or unset = no roleplay. On first
  read, an old `character` value moves to `me` (my call: the owner's
  current setting survives).
- table `cast`: `name` (primary key), `role` (`right_hand` | `member`),
  `style` (one line: how they talk), `task` (id or NULL). One right hand.
- meta `address`: how the owner is addressed ("Imperator").
- each task records its cast name (column on `tasks`, schema version 2,
  migrated on open), so the name survives a cast change of its own row.

### Commands

- `ot me TEXT…` — sets `me`, clears the cast and every task's cast name,
  prints who the owner is now. `ot me plain` turns it off. `ot character`
  is removed.
- `ot cast set JSON` — the orchestrator's command. JSON:
  `{"address": "...", "right_hand": {"name","style"}, "members": [{"name","style"}, …]}`.
  Replaces the cast for the current `me`, then names every open task
  without a name, in task order. Rejects duplicate names and an empty list.
- `ot cast add JSON-LIST` — more members (the circle ran out); names
  waiting tasks.
- `ot cast` — prints the cast and who has which task.
- New task: takes the first free member. None free: no name, and the hook
  context asks the orchestrator to add members. A task's name frees when
  it is done or cancelled.

### Who builds the cast

The prompt hook adds to the orchestrator's next context block, when `me`
is set and no cast exists: "No cast yet for {me}: build one now with
`ot cast set` (right hand, 12 members from their circle, how they address
the owner), then carry on." Same nudge, with `ot cast add`, when a task
waits without a name. The lobby instructions explain both commands and
the guardrails. The orchestrator handles "now I'm Caesar" by running
`ot me Caesar` and then building the new cast.

### Prompts

- Orchestrator: "You are {right hand} ({style}), the right hand of
  {me}. Address the owner as {address}." plus the persona guardrails.
  Before a cast exists: speak plainly-in-world as "the right hand of {me}".
- Worker turns and task tabs: "You are {name} ({style}), one of {me}'s
  people, working on this task. The owner is {me}; address them as
  {address}." plus guardrails. The tab greeting (TAKEOVER / NEW_SESSION)
  uses it; the TALK line stays first.
- Unnamed task: plain persona line "You are one of {me}'s people."
- Plain: none of the above (today's behaviour).

### Start screen

After the agent choice, only when `me` has never been set: "Who are you?
(king, gangster boss, Roman emperor, pirate captain, anyone — Enter for plain): ".
The answer is saved as `me` (empty → `plain`, so it is asked once).

### Where the name shows

`label(task)` becomes `Cicero: "Medication reminders" in pillbox (T4)` when
named, unchanged otherwise. Status and inbox lines start with the name.
The switch card: `Switching you to Cicero: "Medication reminders" in pillbox
(T4, Claude)`. `resolve` also matches the cast name.

### Timing (my calls)

- An open task tab keeps the voice it started with; the next tab or worker
  turn picks up the new world.
- A finished task's name goes back to the pool and can be reused later.

## Left to the builder

Exact migration code, JSON validation messages, the cast-name column name,
test structure, and wording of messages within the rules above. Update
README, HOW-IT-WORKS, ARCHITECTURE, STATUS and the demo in the same change.

## Addendum: automatic cleanup (approved 2026-10-22)

The owner wants finished work cleaned without thinking about it.

- Automatic, no question: a done or cancelled task whose worktree has no
  uncommitted changes loses its worktree (and everything built inside it).
  The branch keeps the work.
- Asks first: a finished task's worktree with uncommitted changes becomes an
  offer: "{label}'s worktree has uncommitted changes. Delete it, or keep it?"
  Keeping it means it is never asked again.
- Branches are deleted only when merged into their base branch.
- Run logs (`runs/<task>`) are deleted 7 days after the task finishes.
- `ot clean` cleans everything finished right now (logs included, any age)
  and says how much space it freed; the orchestrator runs it for "clean up".
  `ot worktrees prune` is removed.
- Never touched: caches shared with other projects (Xcode DerivedData,
  global npm/pip caches), the owner's own repository checkouts, unmerged
  branches.
