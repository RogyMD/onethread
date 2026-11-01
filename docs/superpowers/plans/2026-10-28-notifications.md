# Notifications Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a worker finishes, gets stuck or is merged, the owner gets a soft iTerm2 banner and the Claude orchestrator speaks up in the chat with Open / Remind me / Later choices.

**Architecture:** A new `onethread/notify.py` decides what is news each engine tick, records a `notified` event per notice and posts a banner (iTerm2's own escape code to the lobby's terminal, else `osascript display notification`). `ot wait` blocks until a `notified` event the chat hasn't reported yet and prints it; the Claude orchestrator keeps one running in the background, so Claude Code wakes it.

**Tech Stack:** Python 3.10+ standard library, SQLite, unittest, macOS `osascript`, iTerm2 OSC 9.

**Spec:** `docs/superpowers/specs/2026-10-28-notifications-design.md`

## Global Constraints

- Standard library only; nothing installed, nothing built.
- Notice kinds: review_ready, blocked, failed, interrupted, preempt, lease_overdue, merge_blocked, merge_failed; plus engine merges. Never next_task, dirty_worktree, parked.
- Banner text: control characters stripped (0x00–0x1f, 0x7f–0x9f), whitespace collapsed, at most 200 characters.
- The engine writes only to a path matching `/dev/ttys\d+`, and only while the lobby that recorded it lives: OSC 9 when `TERM_PROGRAM` was `iTerm.app`; a lone BEL (`\x07`, the tab's bell mark) plus the macOS banner when it was `Apple_Terminal`.
- `ot config set notify 0|1` (default 1) turns banners off/on; chat notices stay.
- Remind choices are exactly 5 min · 10 min · 30 min · 1 hour, via `ot later T in N minutes|hour`.
- Never read `~/.claude` or `~/.codex`. The engine never fails a tick because of a notice.
- Run the suite as `python3 -m unittest discover -s tests > /tmp/ot-suite.log 2>&1; tail -3 /tmp/ot-suite.log` and look for `OK`.

## Review Focus

- A task title holding ESC, BEL or newlines must reach the terminal as plain text, never as an escape sequence (Task 2 test).
- A tampered `lobby_tty` meta (`/tmp/x`, `/dev/disk0`) must never be written; the banner falls back to macOS (Task 2 test).
- An old `ot wait` left over from a closed orchestrator must end when a new lobby starts, so the new one isn't told "Already waiting." forever (Task 3 test).
- An offer the owner already handled in the chat, or snoozed, before `ot wait` read it must not be reported again (Task 3 test).
- A reminder set just before the owner closes the orchestrator, with nothing running, must still fire: the engine stays up until it does (Task 1 test).

---

### Task 1: What is news, recorded by the engine

**Files:**
- Create: `onethread/notify.py`
- Modify: `onethread/procs.py` (add `alive`), `onethread/engine.py` (use `procs.alive`, call `notify.tick`, idle), `onethread/attention.py` (extract `offer_text`)
- Test: `tests/test_notify.py`, `tests/test_engine.py`

**Interfaces:**
- Produces:
  - `procs.alive(raw: str) -> bool`: the body of today's `engine._alive` ("pid:start" record names a live process); `engine._alive` becomes an alias.
  - `attention.offer_text(store, item: dict, task, lead=False) -> str`: the text `pick` builds today (preempt note resolved to the paused task's label); `pick` calls it.
  - `notify.KINDS: frozenset[str]`.
  - `notify.tick(store, post=None, now=None) -> list[dict]`: each notice `{"task": str, "item": int|None, "kind": str, "text": str}` (`kind` "merged", `item` None for merges). Records meta `notified:<item id>` = now, event `notified` with data `{"item", "kind", "text"}` per notice, advances meta `notify_event_upto`; calls `post(store, text)` per notice unless meta `notify` == "0"; an exception from `post` is swallowed. `post=None` posts nothing (Task 2 passes the banner).
  - `notify.reminder_pending(store, now) -> bool`.

- [ ] **Step 1: Write the failing tests** in `tests/test_notify.py` (`NotifyTest(TempHomeCase)`, repo `app-a`, task "Fix login"; pass `now` explicitly; prime first with `notify.tick(store, now=100)`):

```python
def test_first_tick_announces_nothing_already_waiting(self):
    self.store.add_attention(self.t.id, "review_ready")
    self.assertEqual(notify.tick(self.store, now=100), [])
    self.assertEqual(notify.tick(self.store, now=102), [])

def test_new_review_is_announced_once(self):
    notify.tick(self.store, now=100)
    self.store.add_attention(self.t.id, "review_ready")
    got = notify.tick(self.store, now=102)
    self.assertEqual([(n["task"], n["kind"]) for n in got], [(self.t.id, "review_ready")])
    self.assertIn("is ready for review", got[0]["text"])
    self.assertEqual(notify.tick(self.store, now=104), [])
    self.assertEqual([e["kind"] for e in self.store.events() if e["kind"] == "notified"], ["notified"])

def test_quiet_kinds_are_not_announced(self):     # next_task, dirty_worktree, parked
def test_reminder_fires_when_its_time_is_up(self):
    # notified at 102; postpone(item, until=700); tick(now=600) → []; tick(now=701) → one notice; tick(now=703) → []
def test_engine_merge_is_announced(self):
    # after priming, store.log(t.id, "merged", {"into": "main"}) → one notice, kind "merged", item None,
    # text == f'{label(t)} was merged into main.'
def test_post_gets_each_text_unless_notify_is_off(self):
    # post=list.append-like collector: one call; set_meta("notify","0") → notice returned, post not called
def test_failing_post_does_not_raise(self):
def test_reminder_pending(self):
    # False with none; True after postpone(until=now+600); False once closed (update_attention open=0)
```

In `tests/test_engine.py` add:

```python
def test_engine_stays_for_a_pending_reminder(self):
    t = self.store.add_task("app-a", "Auth")
    item = self.store.add_attention(t.id, "review_ready")
    attention.postpone(self.store, item, until=self.store.clock() + 600)
    self.assertFalse(self.engine.idle())

def test_tick_records_notices(self):
    self.engine.tick()                                   # primes
    t = self.store.add_task("app-a", "Auth")
    self.store.add_attention(t.id, "blocked", "Which key?")
    self.engine.tick()
    self.assertTrue(any(e["kind"] == "notified" for e in self.store.events(t.id)))
```

- [ ] **Step 2: Run** `python3 -m unittest tests.test_notify tests.test_engine -v` — Expected: FAIL/ERROR, `No module named 'onethread.notify'`.

- [ ] **Step 3: Implement.**
  - `procs.alive`, `attention.offer_text` as in Interfaces.
  - `notify.tick`: on first run (meta `notify_since` None) set `notify_since` = now and `notify_event_upto` = newest event id, return []. An open item is due when its kind is in KINDS, `snooze_until <= now`, and either it has no `notified:<id>` and `created >= notify_since`, or `0 < snooze_until` and its `notified:<id>` < `snooze_until`. Merges: `merged` events with id > `notify_event_upto`, text `f"{label(task)} was merged into {into}."`. Item text: `attention.offer_text(store, item, task)`.
  - `reminder_pending`: an open item of a KINDS kind with `snooze_until > now`.
  - Engine: `self.post = None` (Task 2 sets the banner), `notify.tick(self.store, self.post)` at the end of `tick`'s try block; `idle()` is also false while `notify.reminder_pending(self.store, self.store.clock())`. Update the engine docstring: it no longer "never talks to the owner".

- [ ] **Step 4: Run** the Step 2 command, then the suite — Expected: `OK`.

- [ ] **Step 5: Commit** `git add onethread tests && git commit -m "Engine notices what the owner should hear about"`

---

### Task 2: The banner

**Files:**
- Modify: `onethread/notify.py`, `onethread/engine.py`, `onethread/cli.py` (`start`, `CONFIG_KEYS`, `cmd_config`)
- Test: `tests/test_notify.py`, `tests/test_lobby.py` (StartScreenTest), `tests/test_cli.py`

**Interfaces:**
- Consumes: `notify.tick(store, post)`, `procs.alive`.
- Produces:
  - `notify.clean(text: str) -> str` (Global Constraints rule).
  - `notify.banner(store, text, write=None, run=subprocess.run) -> str`: "iterm", "macos" or "" (both failed). In Apple's Terminal it also rings the lobby tab's bell (a failed bell write is ignored). `write(path, data: bytes)` defaults to `_write_tty` (`os.open(path, O_WRONLY|O_NOCTTY|O_NONBLOCK)`, one `os.write`, close).
  - Meta written by `cli.start`: `lobby_tty` (from `cli._lobby_tty() -> str`: `os.ttyname` of stdin, "" when not a tty), `lobby_term` (`TERM_PROGRAM`, "" if unset).
  - Engine: `self.post = notify.banner`.

- [ ] **Step 1: Write the failing tests** in `tests/test_notify.py` (`BannerTest(TempHomeCase)`; a live lobby is `store.set_meta("lobby_pid", f"{me}:{process_start(me)}")` with `me = os.getpid()`; `write` and `run` are recorders):

```python
def test_clean_strips_escapes_and_newlines(self):
    self.assertEqual(notify.clean('Fix \x1b]9;x\x07 log\nin\x9b'), "Fix ]9;x log in")
    self.assertEqual(len(notify.clean("x" * 500)), 200)
def test_iterm_lobby_gets_osc9(self):
    # lobby_tty "/dev/ttys004", lobby_term "iTerm.app", live lobby_pid →
    # banner(...) == "iterm"; write called with ("/dev/ttys004", b"\x1b]9;T4 is ready\x07"); run not called
def test_text_reaches_tty_without_control_characters(self):
    # banner(store, "a\x1b[2Jb\x07c") → written bytes == b"\x1b]9;a[2Jbc\x07"
def test_non_tty_path_is_never_written(self):
    # for path in ("/tmp/x", "/dev/disk0", "/dev/ttys004/../disk0"): write not called, result "macos"
def test_closed_lobby_uses_macos(self):
    # lobby_pid "" → "macos"; run argv == ["osascript", "-e", "on run argv", "-e",
    #   "display notification (item 2 of argv) with title (item 1 of argv)", "-e", "end run", "onethread", TEXT]
def test_apple_terminal_rings_the_tab_bell_and_uses_macos(self):
    # lobby_term "Apple_Terminal", live lobby → write called once with ("/dev/ttys004", b"\x07"); run called; result "macos"
def test_other_terminal_uses_macos_only(self):   # lobby_term "WezTerm" → write not called, "macos"
def test_tty_write_error_falls_back_to_macos(self):  # write raises OSError → "macos"
def test_macos_failure_returns_empty(self):     # run raises OSError → ""
```

In `tests/test_lobby.py` StartScreenTest:

```python
def test_start_records_the_lobby_terminal(self):
    with mock.patch("onethread.cli._lobby_tty", return_value="/dev/ttys009"), \
         mock.patch.dict(os.environ, {"TERM_PROGRAM": "iTerm.app"}):
        self.start(["1", ""])
    self.assertEqual(self.reopen().get_meta("lobby_tty"), "/dev/ttys009")
    self.assertEqual(self.store.get_meta("lobby_term"), "iTerm.app")
```

In `tests/test_cli.py`: `ot config set notify 0` stores "0"; `ot config set notify maybe` exits 1 with "notify must be 1 (on) or 0 (off)".

- [ ] **Step 2: Run** `python3 -m unittest tests.test_notify tests.test_lobby tests.test_cli -v` — Expected: FAIL (`clean`/`banner`/`_lobby_tty` missing, `notify` not a config key).

- [ ] **Step 3: Implement** as in Interfaces. `clean`: newlines and tabs become a space before the other control characters are dropped, then whitespace collapses and the result is cut to 200 characters. The tty path check is `re.fullmatch(r"/dev/ttys\d+", path)`. OSC 9 bytes: `b"\x1b]9;" + clean(text).encode() + b"\x07"`. `run` gets `capture_output=True, timeout=10`; a non-zero return code counts as failure.

- [ ] **Step 4: Run** the Step 2 command, then the suite — Expected: `OK`.

- [ ] **Step 5: Manual check** (owner's Mac, iTerm2): `python3 -c "import os;fd=os.open(os.ttyname(1),os.O_WRONLY);os.write(fd,b'\x1b]9;onethread test\x07')"` from another app focused → an iTerm2 banner appears. Record in the ledger whether it was seen; not a gate.

- [ ] **Step 6: Commit** `git add onethread tests && git commit -m "Post a soft banner: iTerm2's own in the orchestrator's tab, else macOS (with the tab bell in Terminal)"`

---

### Task 3: `ot wait`, the orchestrator's rules, docs

**Files:**
- Modify: `onethread/notify.py`, `onethread/cli.py` (`cmd_wait`, parser, `start`, hook context), `onethread/tips.py` (`SKIP`), `onethread/lobby.py` (INSTRUCTIONS)
- Modify docs: `README.md`, `HOW-IT-WORKS.md`, `docs/ARCHITECTURE.md`, `docs/STATUS.md`
- Test: `tests/test_notify.py`, `tests/test_notify_cli.py`, `tests/test_lobby.py`

**Interfaces:**
- Consumes: `notified` events from Task 1 (data `{"item", "kind", "text"}`), `procs.alive`.
- Produces:
  - `notify.since(store, now=None) -> list[str]`: `notified` events with id > meta `waited_upto` (unset → newest id, return []), skipping items now closed or with `snooze_until > now`; lines `f"[offer {item} {kind} {task}] {text}"`, or `text` for merges; advances `waited_upto`.
  - `notify.reset_wait(store)`: `waited_upto` = newest event id. `cli.start` calls it.
  - `notify.wait(store, lobby: str, sleep=time.sleep, interval=2) -> list[str]`: loops `since`; returns [] once meta `lobby_pid` != `lobby` or `procs.alive(lobby)` is false.
  - `ot wait`: CODEX_SANDBOX set → exit 1, "ot wait is for the Claude orchestrator; Codex gets the banner." Lock `<home>/wait.lock` busy → prints "Already waiting.", exit 0. Otherwise `ctx.ensure(home)`, prints `notify.wait(...)`'s lines, exit 0.
  - Hook context, when meta `orchestrator` is "claude" and `wait.lock` is free: `" No `ot wait` is running: start one in the background now."`
  - `tips.SKIP` gains "wait".
  - The hook's offer line becomes `[offer ID KIND T] TEXT` too, so the orchestrator knows which choices to ask.

- [ ] **Step 1: Write the failing tests.** In `tests/test_notify.py` (`WaitTest`):

```python
def test_since_reports_new_notices_once(self):
    notify.since(self.store)                                   # sets the cursor
    item = self.store.add_attention(self.t.id, "review_ready")
    self.store.log(self.t.id, "notified", {"item": item, "kind": "review_ready", "text": "T ready"})
    self.assertEqual(notify.since(self.store), [f"[offer {item} review_ready {self.t.id}] T ready"])
    self.assertEqual(notify.since(self.store), [])

def test_since_skips_handled_and_snoozed_offers(self):
    # one item closed (update_attention open=0), one postponed until now+600 → since == []
def test_merge_line_has_no_offer_id(self):
def test_wait_returns_news(self):
    # live lobby; sleep side effect logs a notified event on first call → wait returns that line
def test_wait_ends_when_a_new_lobby_starts(self):
    # sleep side effect sets meta lobby_pid to another value → wait returns []
def test_wait_ends_when_the_lobby_dies(self):
    # lobby "999999:Thu Jan  1 00:00:00 1970" → returns [] without sleeping
```

In `tests/test_notify_cli.py` (`CliCase` from `tests.test_tips_cli`):

```python
def test_wait_refuses_inside_codex_sandbox(self):         # env CODEX_SANDBOX=seatbelt → code 1, message
def test_second_wait_says_already_waiting(self):          # hold fcntl lock on home/wait.lock → out "Already waiting.\n", code 0
def test_wait_prints_news(self):                          # patch notify.wait → ["[offer 3] x"] → out "[offer 3] x\n"
def test_wait_is_not_journaled(self):
def test_hook_asks_for_a_waiter_when_none_runs(self):     # orchestrator "claude" → context contains "No `ot wait` is running"
def test_hook_quiet_about_waiting_for_codex(self):        # orchestrator "codex" → no such text
def test_start_resets_the_wait_cursor(self):              # put in tests/test_lobby.py StartScreenTest
def test_hook_offer_names_kind_and_task(self):            # tests/test_lobby.py HookTest: "[offer N review_ready T1]" in context
```

In `tests/test_lobby.py` InstructionsTest:

```python
def test_instructions_cover_speaking_up(self):
    for text in ("ot wait", "Already waiting.", "Open its session", "Remind me…",
                 "5 min", "10 min", "30 min", "1 hour", "ot later T in", "Codex never runs `ot wait`",
                 "Review it", "Merge it", "Answer it", "Try again", "Resume it", "to make room",
                 "Merge now", "Cancel it", "no answer, means later"):
        self.assertIn(text, lobby.INSTRUCTIONS)
```

- [ ] **Step 2: Run** `python3 -m unittest tests.test_notify tests.test_notify_cli tests.test_lobby -v` — Expected: FAIL (`since`, `wait`, `ot wait` missing).

- [ ] **Step 3: Implement** as in Interfaces. Lobby INSTRUCTIONS: tools line `ot wait` waits for news (Claude, in the background only); and this rule, verbatim:

```
- Offer choices: whenever you make an offer (from the context block or
  from `ot wait`), ask one question with the choices for its KIND
  (Claude: your question tool; Codex: a numbered list). There is no
  "Later" choice: anything else, or no answer, means later.
  review_ready: Review it (the review list, as before a merge) · Merge it
    (`ot finish T`) · Open its session (`ot open T`) · Remind me…
  blocked: Answer it (they type it; `ot answer T TEXT`) · Open its session · Remind me…
  failed: Look at it (`ot review T`) · Try again (`ot resume T`) · Open its session · Remind me…
  interrupted: Resume it (`ot resume T`) · Open its session · Remind me…
  preempt: Pause OTHER to make room (`ot pause OTHER`) · Remind me…
  lease_overdue: Open its session · Remind me…
  merge_blocked: Merge now (`ot finish T`) · Remind me…
  merge_failed: Look at it (`ot review T`) · Cancel it (`ot cancel T`) · Open its session · Remind me…
  "Remind me…" then asks: 5 min · 10 min · 30 min · 1 hour →
  `ot later T in 5 minutes` … `ot later T in 1 hour`.
- Speaking up (Claude only; Codex never runs `ot wait`): keep one `ot wait`
  running in the background (your Bash tool's background mode). Start one
  when the context block says none is running. When it ends with lines,
  first start the next `ot wait`, then tell the owner each line in your
  own words. A line "[offer ID KIND T] …" is an offer: run
  `ot inbox --shown ID` and ask its choices. A line without "[offer" (a
  merge) is news: say it, ask nothing. If it prints "Already waiting." or
  nothing, say nothing and start nothing.
```

  Docs: README and HOW-IT-WORKS gain a short "Notifications" part (banner, chat, choices, `notify 0`); ARCHITECTURE a "Notifications" section (notify.tick in the engine, `notified` events, OSC 9 / osascript, `ot wait` lock and cursor, hook line); STATUS removes "Notifications while you're idle…" from Not supported, and adds "iTerm2 banners and `ot wait` waking a real Claude orchestrator" to Untested.

- [ ] **Step 4: Run** the Step 2 command, then the suite — Expected: `OK`.

- [ ] **Step 5: Commit** `git add onethread tests README.md HOW-IT-WORKS.md docs && git commit -m "Orchestrator speaks up with ot wait: open, remind me, or later"`
