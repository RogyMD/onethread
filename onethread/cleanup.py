"""Clean up after finished tasks: worktrees, merged branches, old run logs.

Only onethread's own things are touched: worktrees under the onethread
home, branches it created once they are merged, and its run logs. Work is
never lost: a worktree goes only when everything in it is committed on its
branch (or the owner said yes), and `git branch -d` refuses unmerged ones.
Caches shared with other projects (Xcode DerivedData, npm, pip) are not ours.
"""
import os
import shutil

from .model import label
from .worktrees import _git, check, is_dirty, is_merged, keeps_ignored

LOG_DAYS = 7
EVERY = 60                          # seconds between automatic rounds
FINISHED = ("done", "cancelled")


def size(path):
    """Bytes under `path`, not following links; 0 if it is missing."""
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                pass
    return total


def human(n):
    if n >= 1024 ** 3:
        return f"{n / 1024 ** 3:.1f} GB"
    if n >= 1024 ** 2:
        return f"{round(n / 1024 ** 2)} MB"
    return f"{round(n / 1024)} KB"


def finished_at(store, task):
    """When the task became done or cancelled (its row's `updated` moves later)."""
    rows = store.q("SELECT MAX(ts) AS ts FROM events WHERE task=? AND "
                   "(kind LIKE 'status:%->done' OR kind LIKE 'status:%->cancelled')", (task.id,))
    return rows[0]["ts"] or task.updated


def _remove_worktree(store, task, repo):
    freed = size(task.worktree)
    _git(repo["path"], "worktree", "remove", "--force", task.worktree, check=False)
    if os.path.isdir(task.worktree):            # git refused: leave it for next time
        return None
    store.log(task.id, "worktree_removed", {"path": task.worktree})
    store.update_task(task.id, worktree="")
    return freed


def clean(store, home, confirm=None, now=None, only=None):
    """One cleanup round. `confirm` given = the owner asked (`ot clean`):
    dirty worktrees are asked about and logs go at once. Without it this is
    the engine's automatic round: dirty worktrees become an offer instead.
    A task that fails (a moved repo, a git error) is logged and skipped."""
    now = store.clock() if now is None else now
    result = {"worktrees": [], "branches": [], "logs": [], "asked": [], "freed": 0}
    for task in store.list_tasks(set(FINISHED)):
        if only and task.id != only:
            continue
        try:
            _clean_one(store, home, task, confirm, now, only, result)
        except Exception as exc:
            store.log(task.id, "cleanup_failed", {"error": str(exc)})
    return result


def _in_use(store, task):
    """An unfinished task of the same repo builds on this branch."""
    return any(t.repo == task.repo and t.base_branch == task.branch
               and t.status not in FINISHED for t in store.list_tasks())


def _clean_one(store, home, task, confirm, now, only, result):
    repo = store.get_repo(task.repo)
    if task.worktree and not os.path.isdir(task.worktree):
        # Already gone: forget just this one (never a repo-wide prune).
        _git(repo["path"], "worktree", "remove", "--force", task.worktree, check=False)
        task = store.update_task(task.id, worktree="")
    if task.worktree:
        check(task.worktree, repo["path"])
        remove = not is_dirty(task.worktree) and not keeps_ignored(task.worktree)
        kept = store.get_meta(f"keep_worktree:{task.id}", "") == "1"
        if not remove and kept and only != task.id:
            pass                                    # the owner chose to keep it
        elif not remove and confirm is None:
            store.add_attention(task.id, "dirty_worktree")
            result["asked"].append(task.id)
        elif not remove:
            remove = confirm(f"{label(task)}'s worktree has uncommitted changes or ignored "
                             "files (like .env) that will be lost. Delete it?")
        if remove:
            freed = _remove_worktree(store, task, repo)
            if freed is not None:
                result["worktrees"].append(task.id)
                result["freed"] += freed
                store.close_attention(task.id, {"dirty_worktree"})
                task = store.get_task(task.id)
    if (not task.worktree and task.branch and task.base_branch and not _in_use(store, task)
            and is_merged(repo["path"], task.branch, task.base_branch)
            and _git(repo["path"], "branch", "-d", task.branch, check=False).returncode == 0):
        store.log(task.id, "branch_deleted", {"branch": task.branch})
        result["branches"].append(task.branch)
        store.update_task(task.id, branch="")
    logs = os.path.join(home, "runs", task.id)
    if os.path.isdir(logs) and (confirm is not None
                                or now - finished_at(store, task) > LOG_DAYS * 86400):
        result["freed"] += size(logs)
        shutil.rmtree(logs, ignore_errors=True)
        result["logs"].append(task.id)
