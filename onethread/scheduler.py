"""Worker scheduler: decides which queued tasks start, and when.

Pure policy. It never starts processes itself; it calls `launch(task)`, which
the supervisor provides and which moves the task to `running`.
"""
from .model import PRIORITY_RANK

DEFAULT_CAPACITY = 10


def capacity(store):
    return int(store.get_meta("capacity", str(DEFAULT_CAPACITY)))


def runnable(store, now, busy=frozenset()):
    """Queued tasks whose deps are done and whose start time has come,
    in start order: priority, then free resources first, then queue time."""
    done = {t.id for t in store.list_tasks({"done"})}
    ready = [t for t in store.list_tasks({"queued"})
             if t.not_before <= now and set(store.deps_of(t.id)) <= done]
    return sorted(ready, key=lambda t: (t.rank, bool(set(t.resources) & busy),
                                        t.queued_at, t.num))


def tick(store, launch, now=None, busy_classes=lambda: set(), skip=frozenset()):
    """Start as many runnable tasks as capacity allows. Returns started ids.
    `skip` holds tasks whose session is busy with a conversation turn."""
    now = store.clock() if now is None else now
    running = store.list_tasks({"running"})
    free = capacity(store) - len(running)
    ready = [t for t in runnable(store, now, frozenset(busy_classes())) if t.id not in skip]
    started = []
    for task in ready[:max(free, 0)]:
        launch(task)
        started.append(task.id)
    for task in ready[max(free, 0):]:
        if task.priority == "urgent" and running:
            store.add_attention(task.id, "preempt", _victim(running).id)
    return started


def _victim(running):
    """The running task to suggest pausing: lowest priority, newest first."""
    return max(running, key=lambda t: (PRIORITY_RANK[t.priority], t.updated))
