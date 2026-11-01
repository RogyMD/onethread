"""Resource leases: costly commands (Xcode builds, simulators, big builds)
take turns instead of all running at once.

Workers get a PATH whose first folder holds small stand-in scripts with the
same names as the costly tools. A stand-in asks the broker for a lease,
waits its turn by task priority, runs the real tool, and releases. Commands
that match no class run straight away. A lease belongs to a process; if that
process dies, the lease is reclaimed.
"""
import json
import os
import shutil
import sys

from .procs import pid_alive, process_start

DEFAULT_CLASSES = {
    "xcode": {"limit": 1, "commands": ["xcodebuild", "xcrun simctl"]},
    "build": {"limit": 2, "commands": ["swift build", "gradle", "./gradlew",
                                       "cargo build", "npm run build"]},
    "heavy": {"limit": 2, "commands": []},
}
DEFAULT_WAIT = 480          # seconds; below the agents' command timeouts
OVERDUE_SECONDS = 1800
EXIT_RETRY = 75             # EX_TEMPFAIL


def lease_dir(home):
    """The only folder workers may write: it holds just the lease database."""
    return os.path.join(home, "leases")


def lease_db(home):
    os.makedirs(lease_dir(home), exist_ok=True)
    return os.path.join(lease_dir(home), "leases.db")


def leased_commands(classes):
    """Command patterns that take a lease, e.g. 'xcodebuild', 'npm run build'."""
    return [p for spec in classes.values() for p in spec["commands"]]


def load_classes(store):
    raw = store.get_meta("lease_classes")
    return json.loads(raw) if raw else DEFAULT_CLASSES


def classify(argv, classes):
    """The class whose command pattern prefixes argv, or None."""
    for name, spec in classes.items():
        for pattern in spec["commands"]:
            words = pattern.split()
            if argv[:len(words)] == words:
                return name
    return None


def shim_names(classes):
    """Command names that get a stand-in on PATH (bare names only)."""
    return sorted({p.split()[0] for spec in classes.values()
                   for p in spec["commands"] if "/" not in p.split()[0]})


class Broker:
    def __init__(self, store, classes=None):
        self.store = store
        self.classes = classes or load_classes(store)

    def request(self, cls, task, pid, command, rank):
        cur = self.store.x(
            "INSERT INTO leases (cls, task, pid, pid_start, command, rank, requested,"
            " granted, state) VALUES (?,?,?,?,?,?,?,0,'waiting')",
            (cls, task, pid, process_start(pid), command, rank, self.store.clock()))
        return cur.lastrowid

    def _reap(self, db):
        for row in db.execute("SELECT * FROM leases WHERE state IN ('waiting','held')").fetchall():
            if not pid_alive(row["pid"], row["pid_start"]):
                db.execute("UPDATE leases SET state='dead' WHERE id=?", (row["id"],))

    def try_grant(self, lease_id):
        """Grant if the class has room and this is first in line. Atomic
        across processes (BEGIN IMMEDIATE)."""
        with self.store.lock:
            db = self.store.db
            db.execute("BEGIN IMMEDIATE")
            try:
                self._reap(db)
                row = db.execute("SELECT * FROM leases WHERE id=?", (lease_id,)).fetchone()
                ok = row["state"] == "held"
                if row["state"] == "waiting":
                    limit = self.classes.get(row["cls"], {"limit": 1})["limit"]
                    held = db.execute("SELECT COUNT(*) FROM leases WHERE cls=? AND state='held'",
                                      (row["cls"],)).fetchone()[0]
                    first = db.execute(
                        "SELECT id FROM leases WHERE cls=? AND state='waiting'"
                        " ORDER BY rank, requested, id LIMIT 1", (row["cls"],)).fetchone()[0]
                    ok = held < limit and first == lease_id
                    if ok:
                        db.execute("UPDATE leases SET state='held', granted=? WHERE id=?",
                                   (self.store.clock(), lease_id))
                db.execute("COMMIT")
                return ok
            except Exception:
                db.execute("ROLLBACK")
                raise

    def release(self, lease_id, state="released"):
        self.store.x("UPDATE leases SET state=? WHERE id=?", (state, lease_id))

    def wait_message(self, lease_id):
        row = self.store.q("SELECT * FROM leases WHERE id=?", (lease_id,))[0]
        holders = self.store.q("SELECT * FROM leases WHERE cls=? AND state='held'", (row["cls"],))
        waiting = [r["id"] for r in self.store.q(
            "SELECT id FROM leases WHERE cls=? AND state='waiting' ORDER BY rank, requested, id",
            (row["cls"],))]
        now = self.store.clock()
        who = ", ".join(f"{h['task']} (`{h['command']}`, {int((now - h['granted']) // 60)}m so far)"
                        for h in holders) or "another task"
        place = waiting.index(lease_id) + 1 if lease_id in waiting else 1
        return (f"Not now: {row['cls']} is in use by {who}. "
                f"You are #{place} in line. Waiting…")

    def acquire(self, cls, task, rank, command, pid, wait, poll=0.5, on_wait=None):
        """Block until granted (returns lease id) or `wait` seconds pass (None)."""
        import time
        lease_id = self.request(cls, task, pid, command, rank)
        end = time.time() + wait
        told = 0.0
        while not self.try_grant(lease_id):
            if time.time() >= end:
                self.release(lease_id, state="gave_up")
                return None
            if on_wait and time.time() - told >= 60:
                on_wait(self.wait_message(lease_id))
                told = time.time()
            time.sleep(poll)
        return lease_id

    def busy_classes(self):
        busy = set()
        for name, spec in self.classes.items():
            held = self.store.q("SELECT pid, pid_start FROM leases WHERE cls=? AND state='held'",
                                (name,))
            if sum(pid_alive(r["pid"], r["pid_start"]) for r in held) >= spec["limit"]:
                busy.add(name)
        return busy


def check_overdue(store, lease_store, max_seconds=OVERDUE_SECONDS):
    """A lease held too long becomes an attention item. Never killed."""
    now = store.clock()
    for row in lease_store.q("SELECT * FROM leases WHERE state='held'"):
        if now - row["granted"] > max_seconds and store.get_task(row["task"]):
            if pid_alive(row["pid"], row["pid_start"]):
                store.add_attention(row["task"], "lease_overdue", row["cls"])


_CLI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lease_cli.py")


def _remove(path):
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path, ignore_errors=True)
    else:
        os.remove(path)


def install_shims(home, classes):
    """Write one stand-in script per costly command, plus `ot-lease`, into an
    emptied folder. Called before every use: the Codex orchestrator may write
    onethread's state folder, so nothing found there is trusted."""
    folder = os.path.join(home, "shims")
    os.makedirs(folder, exist_ok=True)
    names = shim_names(classes) + ["ot-lease"]
    for entry in os.listdir(folder):        # nothing else may sit first on PATH
        path = os.path.join(folder, entry)
        if entry not in names or os.path.islink(path) or not os.path.isfile(path):
            _remove(path)                       # ours are rewritten in place below
    for name in names:
        args = "" if name == "ot-lease" else f"--shim {name} --"
        path = os.path.join(folder, name)
        with open(path, "w") as f:
            f.write(f'#!/bin/sh\nexec "{sys.executable}" -I "{_CLI}" {args} "$@"\n')
        os.chmod(path, 0o755)
    return folder


def worker_env(home, task_id, rank, shims_dir, wait=DEFAULT_WAIT):
    return {"PATH": shims_dir + os.pathsep + os.environ.get("PATH", ""),
            "ONETHREAD_HOME": home, "ONETHREAD_TASK": task_id,
            "ONETHREAD_RANK": str(rank), "ONETHREAD_SHIMS": shims_dir,
            "ONETHREAD_LEASE_WAIT": str(wait)}
