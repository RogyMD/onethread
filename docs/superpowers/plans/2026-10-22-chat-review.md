# Review in the chat Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Before an accept or merge, the orchestrator asks the owner in the chat (multi-select) what to review, but only when they haven't looked at the task's latest work yet.

**Architecture:** A new `onethread/review.py` decides "has the owner looked?" from two timestamps `ot` already keeps: the end of the task's latest run (`runs.ended`) and the newest `reviewed` event. Two commands expose it: `ot look T` (JSON: looked + reviewable items) and `ot show T ITEM…` (opens the picks, records `reviewed`). The owner's own task tab (`ot session`) also records `reviewed`. The lobby INSTRUCTIONS tell the orchestrator to run `ot look` before `ot accept`/`ot merge` and ask the list when needed.

**Tech Stack:** Python 3.10+ standard library, unittest, SQLite, git, macOS `open`, iTerm2 via existing `terminal.open_tab`.

**Spec:** `docs/superpowers/specs/2026-10-22-finish-and-tips-design.md`, Part 3 only. Parts 1 (finishing) and 2 (tips) get their own plans; `ot finish` does not exist yet, so its hooks into this part (finish runs `ot look`; `--link` counts as looked; the link-only turn exception) are left to the finishing plan.

## Global Constraints

- Standard library only; no new dependencies.
- The popup stays a plain **No · Yes**; this plan changes no confirmation.
- Item ids and labels, exactly: `summary` "What the worker says", `diff` "All changes", `files` "Changed files", `agent` "Talk to the agent", `pr` "Pull request page", `nothing` "Nothing, go ahead".
- Only items that exist for the task are listed; `nothing` is always last.
- `ot` never sends anything off the machine; `pr` only opens a URL already stored in the task's result.
- Never read Claude/Codex history files.
- The owner never types `ot look`/`ot show`: only the orchestrator runs them; the owner just talks.
- Update README, HOW-IT-WORKS, docs/ARCHITECTURE, docs/STATUS and the lobby INSTRUCTIONS in the same change.

## Review Focus

1. A task whose worktree was cleaned up (done, merged later): `diff` must still work from the repo using the branch; `files` and `agent` must not be offered.
2. A task with no runs at all (owner did it in their own tab, or never started): `looked` is true, so no list is asked.
3. Many changed files (a big refactor): `files` opens at most 20 and says how many more, instead of flooding the screen.
4. A changed file that was deleted on the branch: it is skipped by `files`, not an error.
5. An unknown or unavailable item in `ot show`: exit 1 naming it, nothing opened, nothing recorded.

---

### Task 1: `review.py` — has the owner looked, and what can they review

**Files:**
- Create: `onethread/review.py`
- Test: `tests/test_review.py`

**Interfaces:**
- Consumes: `Store.log(task, kind, data)`, `Store.events(task)`, `Store.q(sql, args)`, `Store.clock`; task fields `result`, `summary`, `worktree`, `branch`, `base_branch`, `status`, `repo`; `Store.get_repo(name)["path"]`.
- Produces:
  - `LABELS: dict[str, str]` (ids → labels above, insertion order = list order)
  - `latest_work(store, tid: str) -> float | None` — newest `runs.ended` for the task, None when it has no ended run.
  - `looked(store, task) -> bool` — True when `latest_work` is None, or a `reviewed` event for the task has `ts >= latest_work`.
  - `mark(store, tid: str, how: str) -> None` — logs event kind `reviewed` with `{"how": how}`.
  - `changed_files(store, task) -> list[str]` — paths changed on `branch` against `base_branch` (`git diff --name-only base...branch`, run in the repo's path), empty when no branch.
  - `items(store, task) -> list[dict]` — `[{"id": ..., "label": ...}, ...]`.

- [ ] **Step 1: Write the failing tests**

Fixture: `TempHomeCase`, `make_repo(self.tmp, "app-a")`, `store.add_repo`, `store.add_task("app-a", "Add login")`, `worktrees.ensure(...)`, one committed file `a.txt` in the worktree. Helper `run(ended)` inserts a closed run: `INSERT INTO runs (task, kind, started, ended, open) VALUES ('T1','work',?,?,0)`. Set time with `self.store.clock = lambda: NOW`.

```python
def test_no_runs_counts_as_looked(self):            # Review Focus 2
    self.assertTrue(review.looked(self.store, self.task()))

def test_new_work_is_not_looked(self):
    self.run(ended=100)
    self.assertFalse(review.looked(self.store, self.task()))

def test_mark_after_work_is_looked(self):
    self.run(ended=100); self.at(150); review.mark(self.store, "T1", "show")
    self.assertTrue(review.looked(self.store, self.task()))

def test_newer_work_after_mark_is_not_looked(self):
    self.run(ended=100); self.at(150); review.mark(self.store, "T1", "show")
    self.run(ended=200)
    self.assertFalse(review.looked(self.store, self.task()))

def test_items_for_task_in_review(self):
    self.store.update_task("T1", result={"summary": "Added login", "commits": [], "tests": ""})
    for s in ("queued", "running", "review"):
        self.store.set_status("T1", s)
    self.assertEqual([i["id"] for i in review.items(self.store, self.task())],
                     ["summary", "diff", "files", "agent", "nothing"])
    self.assertEqual(review.items(self.store, self.task())[-1]["label"], "Nothing, go ahead")

def test_pr_item_only_with_stored_pr(self):
    self.store.update_task("T1", result={"summary": "x", "pr": "https://example.invalid/pr/1"})
    self.assertIn("pr", [i["id"] for i in review.items(self.store, self.task())])

def test_cleaned_task_offers_diff_but_not_files_or_agent(self):   # Review Focus 1
    # status done, then worktree removed (git worktree remove) and task.worktree = ""
    self.assertEqual([i["id"] for i in review.items(self.store, self.task())],
                     ["summary", "diff", "nothing"])

def test_no_changes_no_diff_or_files(self):
    # fresh task: branch exists but no commits
    self.assertNotIn("diff", [i["id"] for i in review.items(self.store, self.task(commit=False))])

def test_changed_files_lists_branch_changes(self):
    self.assertEqual(review.changed_files(self.store, self.task()), ["a.txt"])
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python3 -m unittest tests.test_review -v`
Expected: FAIL/ERROR with `ImportError: cannot import name 'review'`

- [ ] **Step 3: Implement `onethread/review.py`**

Rules the tests pin: `summary` when `task.result.get("summary")` or `task.summary`; `diff` when `changed_files` is non-empty; `files` when that holds **and** `task.worktree` is an existing folder; `agent` when status is not `done`/`cancelled` and the task has a worktree; `pr` when `task.result.get("pr")`; `nothing` always, last. `changed_files` runs git in the repo path (works after cleanup, since the branch survives) and returns `[]` on any git failure.

- [ ] **Step 4: Run them to verify they pass**

Run: `python3 -m unittest tests.test_review -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add onethread/review.py tests/test_review.py
git commit -m "Add review: has the owner looked, and what can they review"
```

---

### Task 2: `ot look`, `ot show`, and the owner's tab counts as looking

**Files:**
- Modify: `onethread/cli.py` (`Ctx.__init__` gains `open_path`; new `cmd_look`, `cmd_show`; `cmd_session` marks; parser; dispatch table; `main(..., open_path=None)`)
- Modify: `docs/superpowers/specs/2026-10-22-finish-and-tips-design.md` (Part 3: "Without iTerm2, `ot` prints the command to run, as `ot open` does." replaces the Terminal line — the existing fallback pattern, ruled here)
- Test: `tests/test_review_cli.py`

**Interfaces:**
- Consumes: Task 1's `review.items`, `review.looked`, `review.mark`, `review.changed_files`, `review.LABELS`; existing `Ctx.open_tab(command) -> bool`, `cmd_open(ctx, a)`, `views.review_lines`.
- Produces:
  - `ot look T` → stdout one JSON line `{"task": "T1", "looked": bool, "items": [{"id","label"}, ...]}`, exit 0.
  - `ot show T ITEM…` → opens each pick, then `review.mark(store, T, "show")`; exit 0. `summary` prints `views.review_lines` without the diffstat lines (the worker's words, tests, commits). `diff` opens a tab running `git -C <repo> -c color.ui=always diff <base>...<branch> | less -R`. `files` calls `ctx.open_path(path)` for each existing changed file in the worktree, at most 20, then prints `…and N more changed files.` when cut. `agent` runs `cmd_open` for the task. `pr` calls `ctx.open_path(url)`. `nothing` opens nothing.
  - `Ctx(..., open_path=...)`: default `lambda p: subprocess.run(["open", p], check=False)`.

- [ ] **Step 1: Write the failing tests**

Harness like `tests/test_open.py`: `cli.main(argv, home=..., confirm=no_confirm, ensure=lambda h: None, open_tab=lambda c: self.tabs.append(c) or self.tab_ok, open_path=self.opened.append, run_session=self.fake_session)`. One task in review with a committed `a.txt` and one closed run (ended at 100), clock 50 before / 150 after.

```python
def test_look_reports_not_looked_and_items(self):
    code, out, _ = self.ot("look", "T1")
    data = json.loads(out)
    self.assertEqual((code, data["task"], data["looked"]), (0, "T1", False))
    self.assertEqual(data["items"][0], {"id": "summary", "label": "What the worker says"})

def test_show_nothing_marks_looked(self):
    self.assertEqual(self.ot("show", "T1", "nothing")[0], 0)
    self.assertTrue(json.loads(self.ot("look", "T1")[1])["looked"])

def test_show_summary_prints_worker_words(self):
    self.assertIn("Added login", self.ot("show", "T1", "summary")[1])

def test_show_diff_opens_a_tab(self):
    self.ot("show", "T1", "diff")
    self.assertIn("diff main...", self.tabs[0]); self.assertIn("less -R", self.tabs[0])

def test_show_diff_without_iterm_prints_command(self):
    self.tab_ok = False                  # the fake open_tab returns self.tab_ok
    code, out, _ = self.ot("show", "T1", "diff")
    self.assertEqual(code, 0); self.assertIn("less -R", out)

def test_show_files_opens_existing_files_only(self):          # Review Focus 4
    # commit a.txt and delete-on-branch README.md
    self.ot("show", "T1", "files")
    self.assertEqual([os.path.basename(p) for p in self.opened], ["a.txt"])

def test_show_files_caps_at_twenty(self):                     # Review Focus 3
    # 25 committed files
    code, out, _ = self.ot("show", "T1", "files")
    self.assertEqual(len(self.opened), 20); self.assertIn("…and 5 more changed files.", out)

def test_show_unavailable_item_fails_and_records_nothing(self):  # Review Focus 5
    code, _, err = self.ot("show", "T1", "pr")
    self.assertEqual(code, 1); self.assertIn("pr", err)
    self.assertFalse(json.loads(self.ot("look", "T1")[1])["looked"])
    self.assertEqual(self.opened + self.tabs, [])

def test_show_agent_opens_task_tab(self):
    self.ot("show", "T1", "agent")
    self.assertIn("session T1", self.tabs[0])

def test_own_tab_counts_as_looked(self):
    self.ot("session", "T1")             # fake_session returns at once
    self.assertTrue(json.loads(self.ot("look", "T1")[1])["looked"])
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python3 -m unittest tests.test_review_cli -v`
Expected: FAIL — `argparse` error "invalid choice: 'look'" (exit 2 / SystemExit) or `TypeError: main() got an unexpected keyword argument 'open_path'`

- [ ] **Step 3: Implement**

Validate every requested item against `review.items` **before** opening anything (unknown or unavailable → `ValueError("T1 has nothing to review as 'pr'; it has: summary, diff, …")`, which `main` turns into exit 1). Mark only after all picks opened. In `cmd_session`, call `review.mark(ctx.store, t.id, "tab")` in the `finally` block, before parking. Add `look` and `show` to the parser (`show`: `task` upper-cased, `items` nargs="+") and the dispatch table. Edit the spec line named under Files.

- [ ] **Step 4: Run them to verify they pass, then the full suite**

Run: `python3 -m unittest tests.test_review_cli -v && python3 -m unittest discover -s tests 2>&1 | tail -3`
Expected: all PASS; full suite `OK`

- [ ] **Step 5: Commit**

```bash
git add onethread/cli.py tests/test_review_cli.py docs/superpowers/specs/2026-10-22-finish-and-tips-design.md
git commit -m "Add ot look and ot show; the owner's own tab counts as looking"
```

---

### Task 3: The orchestrator asks before accept and merge; docs

**Files:**
- Modify: `onethread/lobby.py` (INSTRUCTIONS: tools line and one rule)
- Modify: `README.md`, `HOW-IT-WORKS.md`, `docs/ARCHITECTURE.md`, `docs/STATUS.md`
- Test: `tests/test_lobby.py`

**Interfaces:**
- Consumes: Task 2's `ot look T` JSON and `ot show T ITEM…`.
- Produces: INSTRUCTIONS text later plans extend (the finishing plan adds `ot finish` to the same rule).

- [ ] **Step 1: Write the failing test**

```python
def test_instructions_ask_what_to_review_before_accept_and_merge(self):
    text = lobby.INSTRUCTIONS
    self.assertIn("`ot look T`", text)
    self.assertIn("`ot show T ITEM…`", text)
    self.assertIn("Before `ot accept T` or `ot merge T`, run `ot look T`", text)
    self.assertIn("multiple selection", text)
    self.assertIn("numbered list", text)
    self.assertIn('"looked": true', text)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m unittest tests.test_lobby -v`
Expected: FAIL on the first `assertIn`

- [ ] **Step 3: Write the INSTRUCTIONS**

Tools line: `` - `ot look T` says if the owner has seen T's latest work and what they can review · `ot show T ITEM…` opens their picks ``. Rule (wording free within this content): Before `ot accept T` or `ot merge T`, run `ot look T`. With `"looked": true`, go ahead. Otherwise first ask the owner what they want to review, offering exactly the items' labels as one question with multiple selection (Claude: your question tool with multiple selection; Codex: a numbered list, they answer with numbers). Run `ot show T` with the picked ids, put the summary text in your reply, wait for their ok, then run the command. The popup still confirms.

Docs: README says the owner never types or remembers these: they say "merge it" and the orchestrator runs `ot look`/`ot show` itself (list them under the orchestrator's commands, not the owner's); HOW-IT-WORKS file table gains `onethread/review.py`; ARCHITECTURE describes "looked" (latest run end vs newest `reviewed` event; tab and `ot show` record it); STATUS dated 2026-10-22 lists the feature as built, not yet tried with real tools.

- [ ] **Step 4: Run lobby tests, then the full suite**

Run: `python3 -m unittest tests.test_lobby -v && python3 -m unittest discover -s tests 2>&1 | tail -3`
Expected: PASS; full suite `OK`

- [ ] **Step 5: Commit**

```bash
git add onethread/lobby.py tests/test_lobby.py README.md HOW-IT-WORKS.md docs/ARCHITECTURE.md docs/STATUS.md
git commit -m "Orchestrator asks what to review before accept and merge"
```
