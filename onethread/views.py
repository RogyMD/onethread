"""What `ot` prints: short plain text for the owner and the orchestrator,
JSON where an agent needs structure. Facts come straight from the store."""
import subprocess

from . import cast, scheduler
from .model import label
from .voice import ago

AGENT_NAMES = {"claude": "Claude", "codex": "Codex", "fake": "Fake"}


def _who(t):
    return f"{t.cast_name} · " if t.cast_name else ""


def _deps(store, t):
    deps = [store.get_task(d) for d in store.deps_of(t.id)]
    return f" · after {', '.join(label(d) for d in deps)}" if deps else ""


def status_lines(store, lease_store=None):
    tasks = store.list_tasks()
    by = lambda *s: [t for t in tasks if t.status in s]   # noqa: E731
    now = store.clock()
    lines = [f"Running ({len(by('running'))}/{scheduler.capacity(store)}):"]
    lines += [f"  {_who(t)}\"{t.title}\" · {t.repo} · {t.agent} · {ago(now - t.updated)} · {t.id}"
              for t in by("running")] or ["  nothing"]
    for heading, statuses in (("With you", ("with_you",)), ("Queued", ("queued",)),
                            ("Waiting for you", ("review", "blocked", "failed", "interrupted", "parked")),
                            ("Paused", ("paused",)), ("To plan", ("draft",))):
        group = by(*statuses)
        if group:
            lines.append(f"{heading}:")
            lines += [f"  {_who(t)}\"{t.title}\" · {t.repo} · {t.status} · {t.priority} · {t.agent} · {t.id}"
                      + _deps(store, t) for t in group]
    if lease_store is not None:
        for row in lease_store.q("SELECT * FROM leases WHERE state IN ('held','waiting') ORDER BY id"):
            verb = "holds" if row["state"] == "held" else "waits for"
            lines.append(f"  {row['task']} {verb} {row['cls']} (`{row['command']}`)")
    return lines


def inbox_lines(store):
    items = store.open_attention()
    if not items:
        return ["Inbox empty: nothing waits for you."]
    lines = ["Waiting for you:"]
    for i in items:
        t = store.get_task(i["task"])
        snooze = " (postponed)" if i["snooze_boundaries"] or i["snooze_until"] > store.clock() else ""
        lines.append(f"  {_who(t)}\"{t.title}\" · {t.repo} · {i['kind'].replace('_', ' ')}{snooze} · {t.id}")
    return lines


def status_json(store):
    return {
        "repos": [r["name"] for r in store.list_repos()],
        "capacity": scheduler.capacity(store),
        "me": cast.me(store),
        "cast": {"address": cast.address(store), "right_hand": cast.right_hand(store),
                 "members": cast.members(store)},
        "tasks": [{"id": t.id, "name": t.cast_name, "repo": t.repo, "title": t.title,
                   "status": t.status,
                   "priority": t.priority, "agent": t.agent, "after": store.deps_of(t.id),
                   "summary": t.summary}
                  for t in store.list_tasks() if t.status not in ("done", "cancelled")],
        "inbox": [{"id": i["id"], "task": i["task"], "kind": i["kind"]}
                  for i in store.open_attention()],
    }


def task_card(task):
    """The line the orchestrator repeats when it switches the owner."""
    who = f"{task.cast_name}: " if task.cast_name else ""
    return (f'Switching you to {who}"{task.title}" in {task.repo} '
            f"({task.id}, {AGENT_NAMES.get(task.agent, task.agent)})")


def review_lines(store, task, stat=True):
    lines = [f"{label(task)} · {task.status} · branch {task.branch or 'none'}"]
    r = task.result or {}
    if r:
        lines.append(f"Worker result: {r.get('summary', '')}")
        lines.append(f"Tests: {r.get('tests') or 'none'} · Commits: "
                     f"{', '.join(r.get('commits') or []) or 'none'}")
    elif task.summary:
        lines.append(f"Last: {task.summary}")
    if stat and task.worktree and task.base_branch:
        diff = subprocess.run(["git", "-C", task.worktree, "diff", "--stat",
                               f"{task.base_branch}...HEAD"], capture_output=True, text=True)
        lines += [f"  {line.strip()}" for line in diff.stdout.splitlines()] or ["  no changes"]
    return lines
