"""Has the owner looked at a task's latest work, and what can they review?

"Looked" compares two times `ot` already keeps: when the task's latest run
ended, and the newest `reviewed` event (the owner's own tab, or picks from
the review list). The orchestrator asks what to review only when the owner
hasn't looked yet.
"""
import os
import subprocess

LABELS = {"summary": "What the worker says", "diff": "All changes",
          "files": "Changed files", "agent": "Talk to the agent",
          "pr": "Pull request page", "nothing": "Nothing, go ahead"}


def latest_work(store, tid):
    rows = store.q("SELECT MAX(ended) AS ended FROM runs WHERE task=? AND ended IS NOT NULL", (tid,))
    return rows[0]["ended"] if rows else None


def looked(store, task):
    work = latest_work(store, task.id)
    if work is None:
        return True
    return any(e["kind"] == "reviewed" and e["ts"] >= work for e in store.events(task.id))


def mark(store, tid, how):
    store.log(tid, "reviewed", {"how": how})


def changed_files(store, task):
    """Paths changed on the task's branch against its base. Run in the
    owner's repo, so it still works after the worktree was cleaned up."""
    repo = store.get_repo(task.repo)
    if not (repo and task.branch and task.base_branch):
        return []
    res = subprocess.run(["git", "-C", repo["path"], "diff", "--name-only",
                          f"{task.base_branch}...{task.branch}"], capture_output=True, text=True)
    return res.stdout.split("\n")[:-1] if res.returncode == 0 else []


def items(store, task):
    result = task.result or {}
    has_tree = bool(task.worktree) and os.path.isdir(task.worktree)
    changed = bool(changed_files(store, task))
    present = {"summary": bool(result.get("summary") or task.summary),
               "diff": changed,
               "files": changed and has_tree,
               "agent": has_tree and task.status not in ("done", "cancelled"),
               "pr": bool(result.get("pr")),
               "nothing": True}
    return [{"id": k, "label": v} for k, v in LABELS.items() if present[k]]
