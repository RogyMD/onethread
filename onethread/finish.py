"""Finishing a task: merging its branch into the repo's base branch, and
standing orders ("finish T when it's ready") that the engine carries out.

Merges never touch a repo with uncommitted changes or another branch
checked out, and a failed merge is always aborted, so the owner's repo is
left exactly as before. The owner approved an order when it was given; the
engine asks nothing more.
"""
import json
import traceback

from . import review
from .model import label
from .worktrees import _git, is_merged

LINK_NOTE = "The owner published it: {link}. Record the published link with the work, then stop."
CONFLICT_NOTE = ("{target} moved and your branch no longer merges cleanly. Merge {target} "
                 "into your branch (do not rebase), resolve the conflicts, run the tests, "
                 "and commit.")


def _branch_exists(path, branch):
    """True or False; a repo git can't read (moved, deleted) raises."""
    res = _git(path, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", check=False)
    if res.returncode not in (0, 1):
        raise RuntimeError(f"git can't read {path}: {res.stderr.strip()}")
    return res.returncode == 0


def target(store, task):
    """The branch the task merges into: the repo's base branch. A task stacked
    on another task's branch waits (None) until that branch is merged. A base
    branch that is gone was deleted by cleanup, which only deletes merged ones."""
    repo = store.get_repo(task.repo)
    base = repo["base_branch"]
    if task.base_branch in ("", base) or not _branch_exists(repo["path"], task.base_branch):
        return base
    return base if is_merged(repo["path"], task.base_branch, base) else None


def _dependency(store, task):
    for t in store.list_tasks():
        if t.repo == task.repo and t.branch == task.base_branch and t.id != task.id:
            return label(t)
    return task.base_branch


def blocker(path, into):
    """Why the repo at `path` can't take a merge into `into` now, or None."""
    current = _git(path, "symbolic-ref", "--short", "HEAD", check=False).stdout.strip()
    if current != into:
        return f"{path} has {current!r} checked out, not {into!r}."
    if _git(path, "status", "--porcelain").stdout.strip():
        return f"{path} has uncommitted changes."
    return None


def merge(store, task, by=""):
    """Merge the task's branch. Returns (outcome, detail); outcome is
    "merged", "wait", "blocked", "conflict" or "error" (git refused for another
    reason: a hook, signing, permissions). `by` "engine" marks a merge
    the owner wasn't there for, so it is announced (notify.py)."""
    into = target(store, task)
    if into is None:
        return "wait", f"waits for {_dependency(store, task)} to merge"
    path = store.get_repo(task.repo)["path"]
    if not task.branch or not _branch_exists(path, task.branch):
        return "merged", "Nothing left to merge."       # cleanup deletes merged branches only
    if is_merged(path, task.branch, into):
        return "merged", f"{task.branch} is already in {into}."
    why = blocker(path, into)
    if why:
        return "blocked", why
    res = _git(path, "merge", "--no-ff", "--no-edit", task.branch, check=False)
    if res.returncode != 0:
        conflicted = _git(path, "diff", "--name-only", "--diff-filter=U", check=False).stdout.strip()
        _git(path, "merge", "--abort", check=False)
        return ("conflict" if conflicted else "error"), f"{res.stdout}{res.stderr}".strip()
    store.log(task.id, "merged", {"into": into, "by": by} if by else {"into": into})
    return "merged", f"Merged {task.branch} into {into}."


# standing orders --------------------------------------------------------
def _key(tid):
    return f"finish:{tid}"


def get_order(store, tid):
    raw = store.get_meta(_key(tid), "")
    return json.loads(raw) if raw else None


def _save(store, tid, o):
    store.set_meta(_key(tid), json.dumps(o))


def drop(store, tid):
    store.x("DELETE FROM meta WHERE key=?", (_key(tid),))


def _followup(store, task, text):
    store.update_task(task.id, followups=task.followups + [text])


def order(store, tid, link=""):
    """Finish the task when it's ready: accept it, merge it. A link goes to
    the worker for one more turn first (now, if the task is still queued)."""
    task = store.get_task(tid)
    old = get_order(store, tid) or {}
    if link:
        pending = task.status != "done"                  # done work: the link is only stored
    else:                                                # keep a link given earlier
        link, pending = old.get("link", ""), old.get("link_pending", False)
    o = {"link": link, "link_pending": pending, "conflicts": old.get("conflicts", 0)}
    if link and task.status == "queued":
        _followup(store, task, LINK_NOTE.format(link=link))
        o["link_pending"] = False
    _save(store, tid, o)


def _publish(store, task, o):
    if o["link"] and task.result.get("published") != o["link"]:
        store.update_task(task.id, result=dict(task.result, published=o["link"]))


def step(store, task, by=""):
    """One move for the task's order. Returns what happened."""
    o = get_order(store, task.id)
    if o is None or task.status == "cancelled":
        drop(store, task.id)
        return "dropped"
    if task.status == "review":
        if o["link_pending"]:
            _followup(store, task, LINK_NOTE.format(link=o["link"]))
            store.set_status(task.id, "queued")
            store.close_attention(task.id)
            o["link_pending"] = False
            _save(store, task.id, o)
            return "requeued"
        _publish(store, task, o)
        store.set_status(task.id, "done")
        store.close_attention(task.id)
        review.mark(store, task.id, "finish")
        return "accepted"
    if task.status != "done":
        return "waiting"
    _publish(store, task, o)
    outcome, detail = merge(store, task, by=by)
    if outcome == "wait":
        return "waiting"
    if outcome == "blocked":
        if not o.get("offered"):            # once: a "yes" must not bring it back
            store.add_attention(task.id, "merge_blocked", detail)
            o["offered"] = True
            _save(store, task.id, o)
        return "blocked"
    store.close_attention(task.id, {"merge_blocked"})
    o["offered"] = False
    if outcome == "merged":
        drop(store, task.id)
        return "merged"
    if outcome == "error":                  # nothing for the worker to fix: tell the owner
        store.add_attention(task.id, "merge_failed", detail)
        drop(store, task.id)
        return "error"
    o["conflicts"] += 1
    if o["conflicts"] >= 2:
        store.add_attention(task.id, "merge_failed", detail)
        drop(store, task.id)
        return "failed"
    _save(store, task.id, o)
    _followup(store, task, CONFLICT_NOTE.format(target=target(store, task)))
    store.set_status(task.id, "queued")
    return "conflict"


GIVE_UP = 3            # rounds in a row an order may fail before the owner hears of it


def tick(store):
    """Step every standing order. One task's error never stops the others;
    an order that keeps failing is dropped with one offer."""
    for row in store.q("SELECT key FROM meta WHERE key LIKE 'finish:%'"):
        tid = row["key"].split(":", 1)[1]
        try:
            task = store.get_task(tid)
            if task is None:
                drop(store, tid)
                continue
            step(store, task, by="engine")
            o = get_order(store, tid)
            if o and o.get("errors"):
                o["errors"] = 0
                _save(store, tid, o)
        except Exception as exc:
            store.log(tid, "finish_failed", {"traceback": traceback.format_exc()})
            o = get_order(store, tid)
            if o is None:
                continue
            o["errors"] = o.get("errors", 0) + 1
            if o["errors"] >= GIVE_UP:
                store.add_attention(tid, "merge_failed", f"{str(exc).rstrip('.')}.")
                drop(store, tid)
            else:
                _save(store, tid, o)


def say(store, task, outcome):
    """One line for the owner about what `step` did."""
    t = store.get_task(task.id)
    name, repo = label(t), store.get_repo(t.repo)
    base = repo["base_branch"]
    if outcome == "merged":
        return f"{name} is merged into {base}."
    if outcome == "blocked":
        why = blocker(repo["path"], base) or ""
        return f"{name} is accepted. {why} I'll merge it into {base} once that's fixed."
    if outcome == "conflict":
        return (f"{name} no longer merges cleanly into {base}; its worker merges {base} "
                "in and resolves it, then it merges.")
    if outcome == "failed":
        return f"{name} can't merge into {base}: it conflicts again. Look at it, or cancel it?"
    if outcome == "error":
        note = next((i["note"] for i in store.open_attention()
                     if i["task"] == t.id and i["kind"] == "merge_failed"), "")
        return f"{name} is accepted but can't merge into {base}: git said: {note}"
    if outcome == "requeued":
        return f"{name}'s worker records the link; then it's accepted and merged into {base}."
    if t.status == "done":
        return f"{name} is accepted and merges into {base} after the task it builds on."
    return f"{name} will be accepted and merged when its worker is done."
