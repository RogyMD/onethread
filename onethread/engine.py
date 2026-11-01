"""The background engine: starts queued workers, finalizes finished ones,
and notices when the owner leaves a task's tab. It runs detached, one at a
time. It never asks the owner anything; it only posts notices (notify.py).
"""
import fcntl
import os
import subprocess
import sys
import time
import traceback

from . import cleanup, finish, leases, notify, scheduler
from .procs import alive
from .store import Store
from .supervisor import Supervisor

TAB_GRACE = 60          # seconds a switched-to task may wait for its tab to start
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def code_stamp(root=ROOT):
    """Newest modification time of `ot`'s own code: it moves when `ot` is updated."""
    newest = 0.0
    for folder, _, files in os.walk(os.path.join(root, "onethread")):
        for name in files:
            if name.endswith(".py"):
                newest = max(newest, os.path.getmtime(os.path.join(folder, name)))
    return newest


def lock_path(home):
    return os.path.join(home, "engine.lock")


_alive = alive


def tab_alive(store, tid):
    return _alive(store.get_meta(f"tab_pid:{tid}", ""))


def park(store, tid):
    """The owner left the task's tab: it waits for their decision."""
    store.set_status(tid, "parked")
    store.set_meta(f"tab_pid:{tid}", "")
    store.add_attention(tid, "parked")


class Engine:
    def __init__(self, store, home, agents):
        self.store = store
        self.home = home
        self.agents = agents
        self.classes = leases.load_classes(store)
        shims = leases.install_shims(home, self.classes)
        self.lease_store = Store(leases.lease_db(home))
        self.broker = leases.Broker(self.lease_store, self.classes)
        self.sup = Supervisor(store, home, agents,
                              env_for=lambda t: leases.worker_env(
                                  home, t.id, t.rank, leases.install_shims(home, self.classes)),
                              leased=leases.leased_commands(self.classes),
                              extra_dirs=[leases.lease_dir(home)])
        self.shims = shims
        self.cleaned_at = None              # last automatic cleanup (store clock)
        self.code_stamp = code_stamp        # replaceable in tests
        self.stamp = self.code_stamp()
        self.post = notify.banner           # how a notice reaches the owner

    def close(self):
        self.lease_store.close()

    def _launch(self, task):
        try:
            self.sup.start_work(task)
        except Exception as exc:            # missing CLI, git error, or the owner took over
            if self.store.get_task(task.id).status in ("queued", "running"):
                self.store.set_status(task.id, "paused")
                self.store.add_attention(task.id, "failed", f"The worker could not start: {exc}.")
            else:
                self.store.log(task.id, "launch_skipped", {"reason": str(exc)})

    def tick(self):
        """One round. An error is logged, never fatal: the engine keeps going."""
        try:
            self.sup.poll()
            scheduler.tick(self.store, self._launch, busy_classes=self.broker.busy_classes)
            finish.tick(self.store)
            leases.check_overdue(self.store, self.lease_store)
            self.reap_tabs()
            now = self.store.clock()
            if self.cleaned_at is None or now - self.cleaned_at >= cleanup.EVERY:
                self.cleaned_at = now
                cleanup.clean(self.store, self.home)
            notify.tick(self.store, self.post)
        except Exception:
            try:
                self.store.log(None, "engine_error", {"traceback": traceback.format_exc()})
            except Exception:
                pass

    def recover(self):
        return self.sup.recover()

    def reap_tabs(self):
        """Park tasks whose tab launcher is gone (or never started)."""
        now = self.store.clock()
        for task in self.store.list_tasks({"with_you"}):
            raw = self.store.get_meta(f"tab_pid:{task.id}", "")
            if raw:
                if not _alive(raw):
                    park(self.store, task.id)
            elif now - task.updated > TAB_GRACE:
                park(self.store, task.id)

    def idle(self):
        # While the orchestrator runs, stay: an engine it started would be
        # inside its sandbox (Codex), and workers need the real environment.
        # A pending reminder needs the engine to fire it.
        return (not self.store.list_tasks({"running", "queued", "with_you"})
                and not _alive(self.store.get_meta("lobby_pid", ""))
                and not notify.reminder_pending(self.store, self.store.clock()))

    def run(self, stop_after_idle=1800, interval=2, sleep=time.sleep):
        """Tick until idle for `stop_after_idle` seconds. False if another
        engine already runs; "restart" when `ot`'s code changed, so a fresh
        engine runs the new code (workers keep running and are reattached)."""
        lock = open(lock_path(self.home), "a+")   # "w" would wipe a running engine's pid
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close()
            return False
        lock.truncate(0)
        lock.write(str(os.getpid()))        # `ot engine stop` reads it
        lock.flush()
        try:
            self.recover()
            idle_since = None
            while True:
                self.tick()
                if self.code_stamp() != self.stamp:
                    return "restart"
                if not self.idle():
                    idle_since = None
                elif idle_since is None:
                    idle_since = time.time()
                elif time.time() - idle_since >= stop_after_idle:
                    return True
                sleep(interval)
        finally:
            lock.close()


def _spawn(home):
    log = open(os.path.join(home, "engine.log"), "a")
    subprocess.Popen([sys.executable, "-m", "onethread.cli", "engine"], cwd=ROOT,
                     stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                     env=dict(os.environ, ONETHREAD_HOME=home), start_new_session=True)
    log.close()


def ensure_running(home, spawn=None):
    """Start the engine in the background unless one runs. True if started.
    Never from inside a Codex sandbox: workers launched there would inherit it."""
    if os.environ.get("CODEX_SANDBOX"):
        return False
    with open(lock_path(home), "a") as probe:
        try:
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
    (spawn or (lambda: _spawn(home)))()
    return True
