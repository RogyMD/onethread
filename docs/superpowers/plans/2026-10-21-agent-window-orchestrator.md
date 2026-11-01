# Orchestrator in the Agent Window — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the `ot chat` terminal with an orchestrator that runs inside the owner's own Claude Code or Codex window, switches the owner into task sessions in new iTerm2 tabs, and speaks in a chosen character.

**Architecture:** `ot` becomes a CLI toolset plus a detached background engine. The orchestrator is a normal interactive agent session started by `ot` in `~/.onethread/lobby/` with instruction files and a prompt hook; task sessions open in iTerm2 tabs via a small launcher (`ot session T`). Store, schedulers, worktrees, supervisor and leases stay.

**Tech Stack:** Python 3.10+ standard library; Claude Code 2.1.304, Codex CLI 0.147, iTerm2 AppleScript via `osascript`.

**Spec:** `docs/superpowers/specs/2026-10-21-agent-window-orchestrator-design.md`

## Global Constraints

- Python standard library only; no new dependencies.
- Workers never get `--dangerously-bypass-approvals-and-sandbox` or `bypassPermissions`.
- Taking over a task (open/switch) never asks; destructive steps (cancel, merge, PR/push, accept, pausing from the orchestrator, removing worktrees) always need the owner's confirmation that the model cannot give.
- Errors shown to the owner are one plain line; full logs stay under `~/.onethread/runs/`.
- `ot` never reads Codex or Claude session/history files.
- Character affects wording only: code, commands, paths, errors and confirmation questions stay plain.
- Test command for every task: `python3 -m unittest discover -s tests -t .` (all green, no warnings).

## Plan-time rulings (verified facts and decisions)

- Verified 2026-10-21: `codex resume [SESSION_ID] [PROMPT]` and `-c key=value` exist; `claude --resume ID` accepts `--append-system-prompt` and a positional prompt; Codex feature `hooks` is stable; `codex app-server` answers JSON-RPC `account/rateLimits/read` with `rateLimits.primary/secondary.usedPercent` (`windowDurationMins` 300 / 10080) after `initialize` + `initialized`; `agent-deck usage --json` gives Claude `five_hour`/`seven_day` `used_percentage`.
- Ruling: confirmations use one mechanism for both agents — terminal y/N when stdin is a TTY, otherwise a native macOS dialog (`osascript display dialog … default button "No"`). Claude's lobby settings therefore allow `Bash(ot:*)`, so the owner is not asked twice. Deviates from spec §6 (Claude permission prompt) — reason: the permission prompt is skipped in auto/bypass modes, a dialog is not; cost if wrong: one extra click.
- Ruling: CLI commands don't tick the scheduler themselves; they call `engine.ensure_running()`. Reason: one process launching workers avoids double launches; cost: up to one tick (2 s) latency.
- Ruling: a Codex task with no session yet gets its session from one short read-only headless turn before the tab opens (Codex can't preset an interactive session id). Cost: one small turn.

## Review Focus

1. Owner switches into a task whose worker is mid-turn: worker must be stopped (killpg escalation) before the tab starts, and the scheduler must never relaunch it while `with_you`.
2. Tab closed by killing iTerm (launcher killed by SIGHUP): task must still end `parked`, not stay `with_you` forever — engine marks `with_you` tasks whose launcher pid is dead as `parked`.
3. Two `ot` processes (engine + CLI command) writing the store at once: SQLite WAL + `BEGIN IMMEDIATE`; no lost status changes.
4. Hook called when the store is busy or missing: `ot hook prompt` must exit 0 with empty context, never block or fail the owner's message.
5. Ambiguous or unknown task in `ot open`: a plain error listing candidates, no tab opened.

---

### Task 1: Statuses `with_you` / `parked`, default agent, character meta

**Files:** Modify `onethread/model.py`, `onethread/store.py`, `onethread/ops.py`; Test `tests/test_store.py`, `tests/test_ops.py`

**Interfaces — Produces:** statuses `with_you`, `parked`; attention kind `parked`; `Store.default_agent() -> str` (meta `orchestrator`, default `"claude"`); `Ops._add_task` uses `op["agent"] or store.default_agent()`; meta `character` (default `"plain"`).

- [ ] Write failing tests: `test_any_open_status_can_go_with_you` (draft, queued, running, review, blocked, paused, failed, interrupted, parked → `with_you` allowed; done/cancelled not); `test_with_you_only_to_parked`; `test_parked_transitions` (→ queued, review, with_you, cancelled); `test_new_task_uses_orchestrator_agent` (meta orchestrator=codex → task.agent codex; explicit `agent="claude"` wins); `test_runnable_ignores_with_you_and_parked`.
- [ ] Run; expect FAIL (unknown status / wrong agent).
- [ ] Implement: extend `TRANSITIONS`; `add_task` default agent from `default_agent()`; repo-level agent no longer used for tasks.
- [ ] Run full suite; PASS. Commit `Add with_you and parked statuses; orchestrator's agent is the default`.

### Task 2: Agent adapters — model setting, plain errors, interactive argv

**Files:** Modify `onethread/agents/base.py`, `claude.py`, `codex.py`, `fake.py`; Test `tests/test_agents.py`

**Interfaces — Produces:**
- `Agent.model` attribute (str, `""` = CLI default); `get_agent(name, model="")`. Claude adds `--model M`, Codex adds `-c model="M"` to every invocation when set. Meta keys `claude.model`, `codex.model`.
- `Agent.explain(raw: str) -> str`: one plain line. Codex: last `turn.failed`/`error` event's inner `error.message` → `"Codex rejected the request: <message>"`; Claude: JSON `is_error`/`result` text → `"Claude failed: <text>"`; fallback `"<Agent> gave no readable reply (log: <path>)"`.
- `Agent.interactive_argv(session_id: str, prompt: str, persona: str) -> list`: Claude `["claude", "--resume", sid, "--append-system-prompt", persona, prompt]` (persona omitted when empty); Codex `["codex", "resume", sid, "-c", "developer_instructions=<toml string>", prompt]`; Fake `["sh", "-c", "echo fake session"]`. Replaces `attach_argv`.
- `Agent.lobby_argv(resume: bool, persona: str, settings_path: str|None) -> list`: Claude `claude [--continue] [--append-system-prompt P] [--settings S]`; Codex `codex [resume --last] [-c developer_instructions=…]`.
- [ ] Failing tests: `test_codex_model_flag`, `test_claude_model_flag`, `test_codex_explain_model_rejected` (fixture: the owner's real error lines with `gpt-6-luna`, synthetic copy) → contains `"model is not supported"` and no `{"type"`; `test_claude_explain_error_result`; `test_interactive_argv_resume_with_persona_and_prompt` for both; `test_lobby_argv_resume_and_new`.
- [ ] Run; FAIL. Implement. Run suite; PASS. Commit `Agents: model setting, plain errors, interactive and lobby commands`.

### Task 3: Characters

**Files:** Modify `onethread/prompts.py`, `onethread/voice.py`, `onethread/supervisor.py`; Test `tests/test_voice.py`

**Interfaces — Produces:** `prompts.persona(character: str) -> str` (`""` for `plain`/empty; otherwise a line: speak as {character} — in parody style when it is a real person — for the whole conversation; keep code, commands, paths, errors and confirmation questions plain and exact; facts only from `ot` output or the repo; propose, the owner decides). `work_prompt` appends it and asks for the `summary` in character. `TAKEOVER = "The owner has just taken over this session in a terminal tab. Greet them in character in two or three sentences: what you were doing, where it stands, and ask what they want."` `RETURN_NOTE` (used on hand-back): `"The owner worked in this session directly. Check git status and log, then continue the task from the current state."` Remove `KING`, `KING_CARD`, `KING_PERSONA` and the `voice` parameter from `voice.py`.
- [ ] Failing tests: `test_plain_has_no_persona`; `test_persona_keeps_guardrails` (contains "plain", "exact", the character text); `test_real_person_marked_parody`; `test_work_prompt_includes_persona`.
- [ ] Run; FAIL. Implement; drop king templates and their tests. Suite PASS. Commit `Characters: any character as a persona line; drop hand-written king templates`.

### Task 4: Background engine

**Files:** Create `onethread/engine.py`; Modify `onethread/cli.py`; Delete `onethread/conversation.py`, `onethread/repl.py` (after Task 6 moves `status_lines`/`inbox` out); Test `tests/test_engine.py`

**Interfaces — Produces:** `Engine(store, home, agents)` with `.sup`, `.broker`, `.lease_store`, `tick()` (sup.poll → scheduler.tick → check_overdue → `reap_tabs()`), `recover()`, `close()`; `reap_tabs()` moves `with_you` tasks whose meta `tab_pid:<T>` is dead (pid + start time) to `parked` + attention `parked`; `run(stop_after_idle=1800, interval=2)` holds `<home>/engine.lock` (fcntl), runs `recover()` once then ticks, exits after `stop_after_idle` s with nothing running/queued/with_you; `ensure_running(home) -> bool` spawns `python3 -m onethread.cli engine` detached (`start_new_session`) if the lock is free.
- [ ] Failing tests: `test_tick_starts_queued_worker` (fake agent); `test_reap_tabs_parks_dead_launcher`; `test_engine_exits_when_idle` (`stop_after_idle=0.2`); `test_ensure_running_is_single_instance`.
- [ ] Run; FAIL. Implement by moving `Conversation.__init__`/`_launch`/`background_tick` logic here. Suite PASS. Commit `Add the background engine`.

### Task 5: Confirmations

**Files:** Create `onethread/confirm.py`; Test `tests/test_confirm.py`

**Interfaces — Produces:** `make_confirm(isatty=sys.stdin.isatty, ask=input, dialog=_dialog) -> callable(question) -> bool`; `_dialog(question) -> bool` runs `osascript -e 'display dialog Q buttons {"No","Yes"} default button "No" with title "onethread"'`, true only on `button returned:Yes`; any error → False.
- [ ] Failing tests: `test_tty_uses_terminal_answer`; `test_no_tty_uses_dialog`; `test_dialog_error_means_no` (osascript missing → False).
- [ ] Run; FAIL. Implement. Suite PASS. Commit `Confirmations: terminal y/N or a native dialog the model cannot answer`.

### Task 6: CLI toolset

**Files:** Rewrite `onethread/cli.py`; Create `onethread/views.py` (`status_lines(store, lease_store)`, `inbox_lines(store)`, `task_card(task, now)`, `status_json(store)`); Test `tests/test_cli.py`

**Interfaces — Produces:** subcommands, each printing short plain text (`--json` on `status`): `repo add PATH… | list`, `add REPO TITLE [-p P] [--after T,..] [--agent A] [--desc TEXT]`, `status`, `inbox [--next] [--shown ID]`, `start T` (approve), `priority T P`, `after T DEP`, `later T [WHEN]`, `review T` (worker result, or for parked tasks the branch diffstat + last summary), `accept T`, `changes T TEXT`, `answer T TEXT`, `handback T`, `pause T`, `resume T`, `cancel T`, `merge T`, `pr T`, `open T`, `session T`, `character TEXT`, `config set KEY VALUE` (keys: capacity, orchestrator, character, claude.model, codex.model), `engine [stop]`, `hook prompt`, `install`, `worktrees prune`. Ops errors exit 1 with the message on stderr. Every state-changing command ends with `engine.ensure_running(home)`. `handback`: parked→queued with `RETURN_NOTE` follow-up; `review` on parked: parked→review.
- [ ] Failing tests (in-process `main(argv)` with temp `ONETHREAD_HOME`, fake agent, `ensure_running` stubbed): `test_add_and_status_json`; `test_destructive_commands_confirm` (cancel with confirm False → unchanged); `test_handback_queues_with_note`; `test_review_parked_moves_to_review`; `test_config_rejects_bad_values`; `test_unknown_task_plain_error_exit_1`.
- [ ] Run; FAIL. Implement; delete `conversation.py`, `repl.py` and their tests. Suite PASS. Commit `ot becomes a toolset: subcommands for every operation`.

### Task 7: Switching into a task — `ot open` and `ot session`

**Files:** Create `onethread/terminal.py`; Modify `onethread/cli.py`; Test `tests/test_open.py`

**Interfaces — Produces:**
- `terminal.open_tab(command: str, run=subprocess.run) -> bool`: osascript `tell application "iTerm2" … create tab with default profile … tell current session of current window to write text <command>`; False if iTerm2 missing or osascript fails (caller prints the command instead).
- `resolve(store, ref: str) -> Task | list[Task]`: exact id (`T4`, case-insensitive) or words matched against repo + title of open tasks; returns the single match or the candidate list.
- `ot open REF`: resolve (ambiguous/unknown → exit 1 listing candidates, no tab); if running → `sup.stop(T, "with_you")` (no confirm); else `set_status(T, "with_you")`; print card `Switching you to T4 · pillbox · medication reminders (Claude)`; `open_tab("ot session T4")`.
- `ot session T`: ensure worktree; if status not `with_you`, set it (owner opened it by hand); if no session: Claude → new uuid with `--session-id`, Codex → one headless read-only plan turn first; store `tab_pid:<T>` = own pid + start; run `interactive_argv(sid, TAKEOVER, persona)` in the worktree with `leases.worker_env` PATH (foreground, inherits TTY); on exit → `parked` + attention `parked` ("You left T4 …: hand back, review, or leave?"), clear `tab_pid`.
- [ ] Failing tests: `test_resolve_by_id_and_words`; `test_resolve_ambiguous_lists_candidates`; `test_open_running_task_stops_worker_without_asking` (fake delay worker; confirm callback asserts never called); `test_open_prints_card_and_tab_command` (open_tab stubbed); `test_session_exit_parks_task` (fake interactive argv exits 0); `test_open_tab_false_when_osascript_fails`.
- [ ] Run; FAIL. Implement. Suite PASS. Commit `Switch into a task: new iTerm2 tab, worker stops, task parks when you leave`.

### Task 8: Start screen, usage limits, lobby, prompt hook

**Files:** Create `onethread/usage.py`, `onethread/lobby.py`; Modify `onethread/cli.py`, `onethread/attention.py`; Test `tests/test_usage.py`, `tests/test_lobby.py`

**Interfaces — Produces:**
- `usage.claude_limits(run=subprocess.run) -> dict|None` from `agent-deck usage --json` (provider `claude`, windows `five_hour`/`seven_day` → `{"5h": used, "week": used, "updated": ts}`), None if agent-deck missing/fails; `usage.codex_limits(popen=subprocess.Popen, timeout=10) -> dict|None` via `codex app-server` JSON-RPC (`initialize` with clientInfo `{"name":"onethread"}`, `initialized`, `account/rateLimits/read`; primary→5h, secondary→week); `usage.line(name, limits, now) -> str` → `Claude   5h: 55% left · week: 94% left` / `limits unknown` / `(2 h old)` when older than 1 h.
- `lobby.prepare(home, agent: str, character: str) -> str` writes `<home>/lobby/AGENTS.md` (orchestrator instructions: coordinate, never code; only `ot` commands; announce switches with the card `ot open` prints; one offer per reply at a natural pause and then `ot inbox --shown ID`; speak as the character; never approve/merge/cancel/push on your own — `ot` asks the owner), `CLAUDE.md` (`@AGENTS.md`), `.claude/settings.json` (`permissions.allow: ["Bash(ot:*)"]`, `hooks.UserPromptSubmit` → `ot hook prompt`); for Codex also passes the hook via `-c` when supported — if the 0.147 hook config is not accepted, AGENTS.md instructs `ot inbox --next` at the start of each turn (verify by running `codex -c <hook config> --help`-level checks only; no model turn).
- `ot hook prompt`: reads stdin JSON (ignored), postpones the previous `pending_offer` if it was shown and not acted on (ignored = later), picks via `attention.pick(store, boundary=True, focus=None, suggest_next=True)`, prints Claude hook JSON `{"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": "<counts · character · offer text with item id>"}}`; any exception → prints `{}` and exits 0.
- `ot` (no args) / `ot claude` / `ot codex` [`--new`]: prints `onethread · N running · M waiting for you`, the two usage lines, asks `Start the orchestrator with [1]:` (Enter = last choice, meta `orchestrator`), saves the choice, `engine.ensure_running`, `lobby.prepare`, then `os.execvp` the agent's `lobby_argv(resume=not new, persona, settings)` in `<home>/lobby`.
- [ ] Failing tests: `test_claude_limits_from_agent_deck_json` (synthetic JSON); `test_codex_limits_from_app_server` (stub Popen speaking the recorded synthetic protocol); `test_usage_line_unknown_and_stale`; `test_prepare_writes_instructions_settings_hook`; `test_hook_prints_offer_context`; `test_hook_never_fails` (store path unwritable → `{}` exit 0); `test_hook_ignored_offer_is_postponed`; `test_start_screen_choice_and_exec` (execvp stubbed, input "2" → codex argv, meta saved).
- [ ] Run; FAIL. Implement. Suite PASS. Commit `Start screen with usage limits; orchestrator lobby and prompt hook`.

### Task 9: Demo, install, docs

**Files:** Rewrite `demo/run_demo.py`, `tests/test_demo.py`; Modify `README.md`, `HOW-IT-WORKS.md`, `docs/ARCHITECTURE.md`, `docs/STATUS.md`, `AGENTS.md`

**Interfaces — Produces:** demo drives `cli.main([...])` in a temp home with fake agents: add repos and five tasks; `start`; urgent sixth task with preempt offer via `ot hook prompt`; `open T2` while running (worker stopped, card printed, tab command printed instead of opened); simulated session exit → parked; `handback`; engine restart recovery; `review`/`accept`; final states consistent. `ot install` symlinks `<repo>/ot` → `~/.local/bin/ot` after `confirm`.
- [ ] Failing test: `test_demo_scenario` asserts the logged steps (card text, parked/handback, recovery report, final statuses, one run for T1).
- [ ] Run; FAIL. Implement demo; update docs (what's implemented / simulated / unverified). Suite PASS; `python3 demo/run_demo.py` runs. Commit `Demo and docs for the agent-window orchestrator`.

### Task 10: Real-tools check (owner present; uses a little quota)

Not automated. With the owner's go-ahead: `ot` → pick Claude → "add ~/some-repo", "add a task there: fix a README typo", "switch to it" (tab opens, greeting in character), close tab, "hand it back", wait, review. Same with Codex after `ot config set codex.model <model>`. Check: hook context arrives, dialog confirmation appears for `ot cancel`, lease shims first on PATH in the tab, usage lines. Record results in `docs/STATUS.md`.
