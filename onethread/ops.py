"""Validates and applies task operations, whether an agent proposed them or
the user typed a slash command. The model proposes; this code decides.

Destructive steps (stopping a worker, cancelling, merging, pushing) always
go through `confirm(question) -> bool`, which the terminal answers, never
the model.
"""
import datetime as dt
import re
import subprocess
from dataclasses import dataclass

from . import attention, cast, finish, schemas
from .model import AGENTS, PRIORITIES, TransitionError, label


@dataclass
class OpResult:
    ok: bool
    message: str
    focus: str = None          # None: no change; "": lobby; "T3": that task
    postponed: bool = False
    boundary: bool = False     # the op itself marks a conversational boundary


def parse_when(when, now):
    """'' -> 0; 'tomorrow' -> 09:00 tomorrow; 'in N minutes|hours|days'; ISO."""
    when = (when or "").strip().lower()
    if when in ("", "later", "not now"):
        return 0.0
    if when == "tomorrow":
        day = dt.datetime.fromtimestamp(now) + dt.timedelta(days=1)
        return day.replace(hour=9, minute=0, second=0, microsecond=0).timestamp()
    m = re.fullmatch(r"in (\d+) (minute|hour|day)s?", when)
    if m:
        return now + int(m.group(1)) * {"minute": 60, "hour": 3600, "day": 86400}[m.group(2)]
    try:
        return dt.datetime.fromisoformat(when).timestamp()
    except ValueError:
        raise ValueError(f"cannot understand the time {when!r}") from None


class Ops:
    def __init__(self, store, supervisor, confirm, home):
        self.store = store
        self.sup = supervisor
        self.confirm = confirm
        self.home = home

    def apply(self, op, origin="user", focus=None, offer=None):
        name = op.get("op")
        if name not in schemas.OPS:
            return OpResult(False, f"Rejected unknown operation {name!r}.")
        if name != "add_task" and not op.get("task") and focus and name != "focus":
            op = dict(op, task=focus)
        try:
            return getattr(self, f"_{name}")(op, origin=origin, focus=focus, offer=offer)
        except (ValueError, TransitionError) as exc:
            return OpResult(False, f"Rejected {name}: {exc}")

    def _task(self, tid):
        task = self.store.get_task(tid) if tid else None
        if not task:
            raise ValueError(f"unknown task {tid or '(none given)'}")
        return task

    def _label(self, t):
        return label(t)

    # adding and arranging --------------------------------------------------
    def _add_task(self, op, focus=None, **_):
        repo = op.get("repo") or (self._task(focus).repo if focus else "")
        if not repo:
            raise ValueError("which repo? none given and no task in focus")
        if not op.get("title"):
            raise ValueError("a task needs a title")
        for dep in op.get("depends_on") or []:
            self._task(dep)
        agent = op.get("agent") or None
        if agent and agent not in AGENTS:
            raise ValueError(f"unknown agent {agent!r}")
        not_before = parse_when(op.get("when"), self.store.clock())
        t = self.store.add_task(repo, op["title"], op.get("description", ""),
                                op.get("priority") or "normal", agent, not_before)
        for dep in op.get("depends_on") or []:
            self.store.add_dep(t.id, dep)
        cast.fill(self.store)
        t = self.store.get_task(t.id)
        extra = f", after {', '.join(op['depends_on'])}" if op.get("depends_on") else ""
        return OpResult(True, f"Added {self._label(t)}, {t.priority}{extra}.")

    def _set_priority(self, op, **_):
        t = self._task(op.get("task"))
        if op.get("priority") not in PRIORITIES:
            raise ValueError(f"unknown priority {op.get('priority')!r}")
        self.store.update_task(t.id, priority=op["priority"])
        self.store.log(t.id, "priority", {"priority": op["priority"]})
        return OpResult(True, f"{self._label(t)} is now {op['priority']}.")

    def _add_dependency(self, op, **_):
        t = self._task(op.get("task"))
        for dep in op.get("depends_on") or []:
            self.store.add_dep(t.id, self._task(dep).id)
        return OpResult(True, f"{self._label(t)} now waits for {', '.join(op.get('depends_on') or [])}.")

    def _approve_implementation(self, op, **_):
        t = self._task(op.get("task"))
        if t.status != "draft":
            raise ValueError(f"{self._label(t)} is {t.status}, not a draft waiting for approval")
        self.store.set_status(t.id, "queued")
        return OpResult(True, f"{self._label(t)} approved: queued for a background worker ({t.agent}).",
                        boundary=True)

    # stopping and restarting -----------------------------------------------
    def _pause(self, op, **_):
        t = self._task(op.get("task"))
        if t.status == "queued":
            self.store.set_status(t.id, "paused")
            return OpResult(True, f"{self._label(t)} paused before it started.")
        if t.status != "running":
            raise ValueError(f"{self._label(t)} is {t.status}; only queued or running tasks can pause")
        if not self.confirm(f"Pause {self._label(t)}? Its worker will be stopped; "
                            "work so far stays in its worktree."):
            return OpResult(False, f"{self._label(t)} keeps running.")
        self.sup.pause(t.id)
        return OpResult(True, f"{self._label(t)} paused. Resume with: ot resume {t.id}.")

    def _resume(self, op, origin="user", **_):
        t = self._task(op.get("task"))
        if t.status not in ("paused", "interrupted", "failed"):
            raise ValueError(f"{self._label(t)} is {t.status}; nothing to resume")
        if origin == "agent" and not self.confirm(f"Resume {self._label(t)}'s worker?"):
            return OpResult(False, f"{self._label(t)} stays {t.status}.")
        self.store.set_status(t.id, "queued")
        self.store.close_attention(t.id, {"interrupted", "failed"})
        return OpResult(True, f"{self._label(t)} queued again; its worker will continue the same session.")

    def _cancel(self, op, **_):
        t = self._task(op.get("task"))
        running = t.status == "running"
        question = f"Cancel {self._label(t)} for good?"
        if running:
            question += " Its worker will be stopped."
        if not self.confirm(question):
            return OpResult(False, f"{self._label(t)} not cancelled.")
        if running:
            self.sup.stop(t.id, "cancelled")
        else:
            self.store.set_status(t.id, "cancelled")
        self.store.close_attention(t.id)
        return OpResult(True, f"{self._label(t)} cancelled. Its branch and worktree are kept.")

    # focus and offers ------------------------------------------------------
    def _focus(self, op, origin="user", **_):
        tid = op.get("task") or ""
        target = self._task(tid) if tid else None
        where = self._label(target) if target else "the lobby"
        if origin == "agent" and not self.confirm(f"Switch to {where}?"):
            return OpResult(True, "Staying here.")
        return OpResult(True, "", focus=target.id if target else "", boundary=True)

    def _accept_offer(self, op, offer=None, **_):
        if not offer:
            raise ValueError("there is no pending offer")
        if offer.kind == "preempt":
            item = self.store.get_attention(offer.item_id)
            attention.accept(self.store, offer.item_id)
            return self._pause({"task": item["note"]})
        attention.accept(self.store, offer.item_id)
        return OpResult(True, "", focus=offer.task, boundary=True)

    def _postpone_offer(self, op, offer=None, **_):
        if not offer:
            raise ValueError("there is no pending offer")
        attention.postpone(self.store, offer.item_id,
                           until=parse_when(op.get("when"), self.store.clock()))
        when = op.get("when") or "later"
        return OpResult(True, f"OK, {offer.task} waits ({when}). It stays in /inbox.",
                        postponed=True)

    # reviews and answers ---------------------------------------------------
    def _review_accept(self, op, origin="user", **_):
        t = self._task(op.get("task"))
        if t.status != "review":
            raise ValueError(f"{self._label(t)} is {t.status}, not waiting for review")
        merging = self.store.get_meta("merge_on_accept", "1") != "0"
        base = self.store.get_repo(t.repo)["base_branch"]
        question = (f"Accept {self._label(t)} and merge it into {base}? This can't be undone."
                    if merging else f"Accept {self._label(t)} as done? This can't be undone.")
        if (origin == "agent" or merging) and not self.confirm(question):
            return OpResult(False, f"{self._label(t)} stays in review.")
        self.store.set_status(t.id, "done")
        self.store.close_attention(t.id)
        if merging:                         # an order: a merge that can't run now runs later
            finish.order(self.store, t.id)
            outcome = finish.step(self.store, self.store.get_task(t.id))
            return OpResult(True, finish.say(self.store, t, outcome), boundary=True)
        return OpResult(True, f"{self._label(t)} accepted. Its work is on branch {t.branch}: "
                              f"`ot merge {t.id}` merges it locally, `ot pr {t.id}` opens a pull request.",
                        boundary=True)

    def _requeue_with(self, t, text, from_status):
        if t.status != from_status:
            raise ValueError(f"{self._label(t)} is {t.status}, not {from_status}")
        self.store.update_task(t.id, followups=t.followups + [text])
        self.store.set_status(t.id, "queued")
        self.store.close_attention(t.id)

    def _request_changes(self, op, **_):
        t = self._task(op.get("task"))
        self._requeue_with(t, op.get("text") or "Please revise.", "review")
        return OpResult(True, f"{self._label(t)} goes back to its worker with your changes.")

    def _answer(self, op, **_):
        t = self._task(op.get("task"))
        self._requeue_with(t, op.get("text") or "", "blocked")
        return OpResult(True, f"{self._label(t)} has your answer and is queued again.")

    # merge and pull request (slash commands only) --------------------------
    def _git(self, path, *args):
        return subprocess.run(["git", "-C", path, *args], capture_output=True, text=True)

    def merge(self, tid):
        t = self._task(tid)
        if t.status != "done":
            return OpResult(False, f"{self._label(t)} is {t.status}; accept the review before merging.")
        into = finish.target(self.store, t)
        if into is None:
            return OpResult(False, f"{self._label(t)} {finish.merge(self.store, t)[1]} first.")
        path = self.store.get_repo(t.repo)["path"]
        why = finish.blocker(path, into)
        if why:
            return OpResult(False, f"{why} Switch the branch or commit first.")
        if not self.confirm(f"Merge branch {t.branch} into {into} in {path}?"):
            return OpResult(False, "Not merged.")
        outcome, detail = finish.merge(self.store, t)
        if outcome in ("conflict", "error"):
            return OpResult(False, f"Merge failed and was undone: {detail}")
        return OpResult(outcome == "merged", detail)

    def open_pr(self, tid):
        t = self._task(tid)
        if t.status not in ("review", "done"):
            return OpResult(False, f"{self._label(t)} is {t.status}; nothing to open a pull request for.")
        if not self.confirm(f"push branch {t.branch} to the 'origin' remote and open a pull "
                            f"request with gh? This publishes the code to that remote."):
            return OpResult(False, "No pull request opened.")
        path = t.worktree or self.store.get_repo(t.repo)["path"]
        push = self._git(path, "push", "-u", "origin", t.branch)
        if push.returncode != 0:
            return OpResult(False, f"Push failed: {push.stderr.strip()}")
        pr = subprocess.run(["gh", "pr", "create", "--fill", "--head", t.branch,
                             "--base", t.base_branch], cwd=path, capture_output=True, text=True)
        if pr.returncode != 0:
            return OpResult(False, f"gh failed: {pr.stderr.strip()}")
        url = pr.stdout.strip().splitlines()[-1]
        self.store.update_task(t.id, result=dict(t.result, pr=url))
        return OpResult(True, f"Pull request: {url}")
