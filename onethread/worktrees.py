"""One git worktree and branch per task, so parallel agents never share files.

Worktrees live under <home>/worktrees/<repo>/<task>, outside the user's repo
folder. Only `git worktree` commands touch the repo itself.
"""
import os
import re
import subprocess


def _git(cwd, *args, check=True):
    # fsmonitor runs a command from git's settings: never wanted here.
    return subprocess.run(["git", "-C", cwd, "-c", "core.fsmonitor=false", *args],
                          capture_output=True, text=True, check=check)


def check(worktree, repo_path):
    """Raise ValueError unless the worktree's `.git` file still points into the
    repo's own git folder. A worker can edit its worktree; pointed elsewhere,
    git would follow settings there that run commands outside any sandbox."""
    dot_git = os.path.join(worktree, ".git")
    target = ""
    if os.path.isfile(dot_git) and not os.path.islink(dot_git):
        with open(dot_git) as f:
            m = re.fullmatch(r"gitdir: (.+)\n?", f.read(4096))
        target = os.path.realpath(os.path.join(worktree, m.group(1))) if m else ""
    common = _git(repo_path, "rev-parse", "--git-common-dir", check=False).stdout.strip()
    allowed = os.path.realpath(os.path.join(repo_path, common, "worktrees")) if common else ""
    if not target or not allowed or os.path.dirname(target) != allowed:
        raise ValueError(f"{worktree} no longer belongs to {repo_path}; onethread won't run git in it")


def slug(text, limit=30):
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:limit].rstrip("-") or "task"


def is_merged(repo_path, branch, into):
    """True when every commit on `branch` is already in `into`."""
    res = _git(repo_path, "merge-base", "--is-ancestor", branch, into, check=False)
    return res.returncode == 0


def _base_for(store, task, repo):
    """A same-repo dependency's unmerged branch, else the repo's base branch."""
    for dep_id in store.deps_of(task.id):
        dep = store.get_task(dep_id)
        if (dep.repo == task.repo and dep.branch
                and not is_merged(repo["path"], dep.branch, repo["base_branch"])):
            return dep.branch
    return repo["base_branch"]


def ensure(store, home, task_id):
    """Create the task's worktree if it has none. Returns the updated task."""
    task = store.get_task(task_id)
    if task.worktree and os.path.isdir(task.worktree):
        return task
    repo = store.get_repo(task.repo)
    path = os.path.join(home, "worktrees", task.repo, task.id)
    branch = task.branch or f"ot/{task.id}-{slug(task.title)}"
    base = task.base_branch or _base_for(store, task, repo)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    exists = _git(repo["path"], "rev-parse", "--verify", "--quiet",
                  f"refs/heads/{branch}", check=False).returncode == 0
    if exists:
        _git(repo["path"], "worktree", "add", path, branch)
    else:
        _git(repo["path"], "worktree", "add", "-b", branch, path, base)
    store.log(task.id, "worktree_created", {"path": path, "branch": branch, "base": base})
    return store.update_task(task.id, worktree=path, branch=branch, base_branch=base)


def has_commits(path, base):
    """True when HEAD has commits that `base` doesn't. If git can't tell (the
    base branch is gone), assume yes: never treat unknown work as no work."""
    res = _git(path, "rev-list", "--count", f"{base}..HEAD", check=False)
    return res.returncode != 0 or res.stdout.strip() not in ("", "0")


def commit_all(path, message):
    """Commit everything in the worktree. Returns the short sha, or "" on failure."""
    if _git(path, "add", "-A", check=False).returncode != 0:
        return ""
    if _git(path, "commit", "-q", "-m", message, check=False).returncode != 0:
        return ""
    return _git(path, "rev-parse", "--short", "HEAD").stdout.strip()


def refresh_base(store, task_id):
    """Just before a worker starts: if a dependency's work belongs under this
    task and the task has no commits of its own yet (planning is read-only),
    move the branch onto that base."""
    task = store.get_task(task_id)
    repo = store.get_repo(task.repo)
    check(task.worktree, repo["path"])
    wanted = _base_for(store, task, repo)
    if is_merged(task.worktree, wanted, "HEAD"):        # already contains that work
        return task if wanted == task.base_branch else store.update_task(task.id, base_branch=wanted)
    if has_commits(task.worktree, task.base_branch) or is_dirty(task.worktree):
        return task                         # its own work exists: leave it alone
    _git(task.worktree, "reset", "-q", "--hard", wanted)
    store.log(task.id, "base_refreshed", {"base": wanted})
    return store.update_task(task.id, base_branch=wanted)


def is_dirty(path):
    return bool(_git(path, "status", "--porcelain").stdout.strip())


# Ignored folders that tools rebuild; anything else ignored (.env, local
# settings) may be the owner's own and is never deleted without asking.
REBUILT = {"node_modules", ".build", "build", "DerivedData", "dist", "target", ".gradle",
           "Pods", "__pycache__", ".venv", "venv", ".pytest_cache", ".next", ".swiftpm"}


def keeps_ignored(path):
    """True when the worktree holds ignored files that aren't rebuildable output."""
    out = _git(path, "status", "--porcelain", "--ignored", "--untracked-files=normal").stdout
    for line in out.splitlines():
        if line.startswith("!! "):
            parts = line[3:].strip('"').rstrip("/").split("/")
            if not (set(parts) & REBUILT or parts[-1].endswith(".pyc")):
                return True
    return False

