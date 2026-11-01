"""SQLite persistence for repos, tasks, runs, attention items and leases.

One Store is shared by the REPL thread and the background ticker, so every
call takes the same lock. WAL mode lets lease shims in worker processes write
at the same time.
"""
import json
import os
import sqlite3
import subprocess
import threading
import time

from .model import AGENTS, PRIORITIES, Task, TransitionError, check_transition

SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS repos (
  name TEXT PRIMARY KEY, path TEXT UNIQUE, base_branch TEXT,
  agent TEXT, test_cmd TEXT);
CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY, num INTEGER, repo TEXT, title TEXT, description TEXT,
  priority TEXT, status TEXT, agent TEXT, not_before REAL, session_id TEXT,
  worktree TEXT, branch TEXT, base_branch TEXT, resources TEXT, summary TEXT,
  result TEXT, followups TEXT, created REAL, updated REAL, queued_at REAL,
  cast_name TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS deps (task TEXT, depends_on TEXT,
  PRIMARY KEY (task, depends_on));
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, task TEXT, kind TEXT, pid INTEGER,
  pid_start TEXT, started REAL, ended REAL, log_path TEXT, out_path TEXT,
  open INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS attention (
  id INTEGER PRIMARY KEY AUTOINCREMENT, task TEXT, kind TEXT, note TEXT,
  created REAL, snooze_boundaries INTEGER DEFAULT 0, snooze_until REAL DEFAULT 0,
  open INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, task TEXT, kind TEXT, data TEXT);
CREATE TABLE IF NOT EXISTS leases (
  id INTEGER PRIMARY KEY AUTOINCREMENT, cls TEXT, task TEXT, pid INTEGER,
  pid_start TEXT, command TEXT, rank INTEGER, requested REAL, granted REAL,
  state TEXT);
CREATE TABLE IF NOT EXISTS cast (
  name TEXT PRIMARY KEY, role TEXT, style TEXT, pos INTEGER);
"""

_JSON_FIELDS = ("resources", "result", "followups")
_TASK_FIELDS = ("id", "num", "repo", "title", "description", "priority",
                "status", "agent", "not_before", "session_id", "worktree",
                "branch", "base_branch", "resources", "summary", "result",
                "followups", "created", "updated", "queued_at", "cast_name")


def _git(path, *args):
    return subprocess.run(["git", "-C", path, *args], capture_output=True,
                          text=True)


def main_branch(path):
    """The branch a repo's work merges into: the remote's default when there is
    one, else main or master, else whatever is checked out. Never just the
    branch that happens to be checked out while another one is the main one."""
    def exists(branch):
        return _git(path, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}").returncode == 0
    remote = _git(path, "symbolic-ref", "--short", "refs/remotes/origin/HEAD").stdout.strip()
    for branch in (remote.split("/", 1)[1] if "/" in remote else "", "main", "master"):
        if branch and exists(branch):
            return branch
    current = _git(path, "symbolic-ref", "--short", "HEAD").stdout.strip()
    if current:
        return current
    raise ValueError(f"{path} has no main or master branch and nothing checked out; "
                     "check out the branch work should merge into, then add it again")


class Store:
    def __init__(self, path, clock=time.time, timeout=30):
        self.path = path
        self.clock = clock
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False, timeout=timeout,
                                  isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        columns = {r["name"] for r in self.q("PRAGMA table_info(tasks)")}
        if "cast_name" not in columns:          # schema 1 → 2
            self.x("ALTER TABLE tasks ADD COLUMN cast_name TEXT DEFAULT ''")
        if self.get_meta("schema_version") != str(SCHEMA_VERSION):
            self.set_meta("schema_version", str(SCHEMA_VERSION))

    def close(self):
        with self.lock:
            self.db.close()

    def q(self, sql, args=()):
        with self.lock:
            return self.db.execute(sql, args).fetchall()

    def x(self, sql, args=()):
        with self.lock:
            return self.db.execute(sql, args)

    # meta -----------------------------------------------------------------
    def get_meta(self, key, default=None):
        rows = self.q("SELECT value FROM meta WHERE key=?", (key,))
        return rows[0]["value"] if rows else default

    def set_meta(self, key, value):
        self.x("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, value))

    # events ---------------------------------------------------------------
    def log(self, task, kind, data=None):
        self.x("INSERT INTO events (ts, task, kind, data) VALUES (?,?,?,?)",
               (self.clock(), task, kind, json.dumps(data or {})))

    def events(self, task=None):
        if task:
            rows = self.q("SELECT * FROM events WHERE task=? ORDER BY id", (task,))
        else:
            rows = self.q("SELECT * FROM events ORDER BY id")
        return [dict(r) for r in rows]

    # repos ----------------------------------------------------------------
    def add_repo(self, path, agent="codex", test_cmd="", name=""):
        path = os.path.realpath(os.path.expanduser(path))
        top = _git(path, "rev-parse", "--show-toplevel")
        if top.returncode != 0 or os.path.realpath(top.stdout.strip()) != path:
            raise ValueError(f"{path} is not the top folder of a git repository")
        if agent not in AGENTS:
            raise ValueError(f"unknown agent {agent!r}")
        branch = main_branch(path)
        name = name or os.path.basename(path)
        if self.get_repo(name):
            raise ValueError(f"a repo named {name} is already registered; "
                             "give this one another name with --name")
        self.x("INSERT INTO repos VALUES (?,?,?,?,?)", (name, path, branch, agent, test_cmd))
        self.log(None, "repo_added", {"name": name, "path": path})
        return self.get_repo(name)

    def set_base(self, name, branch):
        """The branch tasks start from and merge into."""
        repo = self.get_repo(name)
        if not repo:
            raise ValueError(f"no repo named {name}; see `ot repo list`")
        if _git(repo["path"], "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}").returncode:
            raise ValueError(f"{name} has no branch {branch}")
        self.x("UPDATE repos SET base_branch=? WHERE name=?", (branch, name))
        return self.get_repo(name)

    def get_repo(self, name):
        rows = self.q("SELECT * FROM repos WHERE name=?", (name,))
        return dict(rows[0]) if rows else None

    def list_repos(self):
        return [dict(r) for r in self.q("SELECT * FROM repos ORDER BY name")]

    # tasks ----------------------------------------------------------------
    def _row_to_task(self, row):
        data = dict(row)
        for f in _JSON_FIELDS:
            data[f] = json.loads(data[f] or ("[]" if f != "result" else "{}"))
        data["cast_name"] = data.get("cast_name") or ""
        return Task(**data)

    def add_task(self, repo, title, description="", priority="normal",
                 agent=None, not_before=0.0):
        r = self.get_repo(repo)
        if not r:
            raise ValueError(f"unknown repo {repo!r}")
        if priority not in PRIORITIES:
            raise ValueError(f"unknown priority {priority!r}")
        agent = agent or self.default_agent()
        if agent not in AGENTS:
            raise ValueError(f"unknown agent {agent!r}")
        with self.lock:
            num = int(self.get_meta("next_task_num", "1"))
            self.set_meta("next_task_num", str(num + 1))
            now = self.clock()
            tid = f"T{num}"
            self.x("""INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                   (tid, num, repo, title, description, priority, "draft",
                    agent, not_before, "", "", "", "", "[]", "", "{}", "[]",
                    now, now, 0.0, ""))
            self.log(tid, "task_added", {"title": title, "repo": repo})
        return self.get_task(tid)

    def default_agent(self):
        """New tasks use the orchestrator's agent unless told otherwise."""
        return self.get_meta("orchestrator", "claude")

    def get_task(self, tid):
        rows = self.q("SELECT * FROM tasks WHERE id=?", (str(tid).upper(),))
        return self._row_to_task(rows[0]) if rows else None

    def list_tasks(self, statuses=None):
        rows = self.q("SELECT * FROM tasks ORDER BY num")
        tasks = [self._row_to_task(r) for r in rows]
        return [t for t in tasks if statuses is None or t.status in statuses]

    def update_task(self, tid, **fields):
        bad = set(fields) - set(_TASK_FIELDS) | ({"id", "num", "status"} & set(fields))
        if bad:
            raise ValueError(f"cannot update fields {sorted(bad)}")
        for f in _JSON_FIELDS:
            if f in fields:
                fields[f] = json.dumps(fields[f])
        fields["updated"] = self.clock()
        cols = ", ".join(f"{k}=?" for k in fields)
        self.x(f"UPDATE tasks SET {cols} WHERE id=?", (*fields.values(), tid))
        return self.get_task(tid)

    def set_status(self, tid, new):
        with self.lock:
            task = self.get_task(tid)
            if not task:
                raise ValueError(f"unknown task {tid}")
            check_transition(task.status, new)
            extra = ", queued_at=?" if new == "queued" else ""
            args = (new, self.clock()) + ((self.clock(),) if extra else ()) + (tid, task.status)
            # Compare-and-set: another process (engine or CLI) may have moved it meanwhile.
            cur = self.x(f"UPDATE tasks SET status=?, updated=?{extra} WHERE id=? AND status=?", args)
            if cur.rowcount != 1:
                raise TransitionError(f"{tid} changed while moving it from {task.status} to {new}")
            self.log(tid, f"status:{task.status}->{new}")
        return self.get_task(tid)

    # dependencies -----------------------------------------------------------
    def deps_of(self, tid):
        return [r["depends_on"] for r in
                self.q("SELECT depends_on FROM deps WHERE task=? ORDER BY depends_on", (tid,))]

    def add_dep(self, tid, depends_on):
        if not self.get_task(tid) or not self.get_task(depends_on):
            raise ValueError(f"unknown task in dependency {tid} -> {depends_on}")
        if tid == depends_on or self._reaches(depends_on, tid):
            raise ValueError(f"{tid} -> {depends_on} would create a cycle")
        self.x("INSERT OR IGNORE INTO deps VALUES (?,?)", (tid, depends_on))
        self.log(tid, "dependency_added", {"depends_on": depends_on})

    def _reaches(self, start, target):
        seen, stack = set(), [start]
        while stack:
            cur = stack.pop()
            if cur == target:
                return True
            if cur not in seen:
                seen.add(cur)
                stack.extend(self.deps_of(cur))
        return False

    # attention items (read by the conversation scheduler) ------------------
    def add_attention(self, task, kind, note=""):
        """Open an item unless the same task already has one of this kind."""
        with self.lock:
            rows = self.q("SELECT id FROM attention WHERE open=1 AND task=? AND kind=?",
                          (task, kind))
            if rows:
                return rows[0]["id"]
            cur = self.x("INSERT INTO attention (task, kind, note, created) VALUES (?,?,?,?)",
                         (task, kind, note, self.clock()))
            self.log(task, f"attention:{kind}", {"note": note})
            return cur.lastrowid

    def open_attention(self):
        return [dict(r) for r in self.q("SELECT * FROM attention WHERE open=1 ORDER BY id")]

    def get_attention(self, item_id):
        rows = self.q("SELECT * FROM attention WHERE id=?", (item_id,))
        return dict(rows[0]) if rows else None

    def update_attention(self, item_id, **fields):
        cols = ", ".join(f"{k}=?" for k in fields)
        self.x(f"UPDATE attention SET {cols} WHERE id=?", (*fields.values(), item_id))

    def close_attention(self, task, kinds=None):
        """Close open items for a task (all kinds, or only the given ones)."""
        for item in self.open_attention():
            if item["task"] == task and (kinds is None or item["kind"] in kinds):
                self.update_attention(item["id"], open=0)
