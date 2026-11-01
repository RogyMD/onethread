"""Task model: priorities, statuses and the allowed lifecycle transitions."""
import re
from dataclasses import dataclass, field

PRIORITIES = ("urgent", "high", "normal", "low")
PRIORITY_RANK = {p: i for i, p in enumerate(PRIORITIES)}
AGENTS = ("codex", "claude", "fake")

TRANSITIONS = {
    "draft": {"queued", "cancelled"},
    "queued": {"running", "paused", "cancelled"},
    "running": {"review", "blocked", "failed", "interrupted", "paused",
                "cancelled"},
    "review": {"done", "queued", "cancelled"},
    "blocked": {"queued", "cancelled"},
    "paused": {"queued", "cancelled"},
    "failed": {"queued", "cancelled"},
    "interrupted": {"queued", "cancelled"},
    "with_you": {"parked"},              # the owner is in the task's own tab
    "parked": {"queued", "review", "cancelled"},   # the owner left the tab
    "done": {"queued", "cancelled"},     # a merge conflict goes back to its worker; or give up
    "cancelled": set(),
}
# The owner can take over any task that isn't finished.
for _status, _targets in TRANSITIONS.items():
    if _status not in ("with_you", "done", "cancelled"):
        _targets.add("with_you")
STATUSES = tuple(TRANSITIONS)


class TransitionError(ValueError):
    pass


def check_transition(old, new):
    if new not in TRANSITIONS.get(old, ()):
        raise TransitionError(f"cannot move a task from {old} to {new}")


@dataclass
class Task:
    id: str
    num: int
    repo: str
    title: str
    description: str = ""
    priority: str = "normal"
    status: str = "draft"
    agent: str = "codex"
    not_before: float = 0.0
    session_id: str = ""
    worktree: str = ""
    branch: str = ""
    base_branch: str = ""
    resources: list = field(default_factory=list)
    summary: str = ""          # first sentence of the last exchange
    result: dict = field(default_factory=dict)
    followups: list = field(default_factory=list)  # messages for next turn
    created: float = 0.0
    updated: float = 0.0
    queued_at: float = 0.0
    cast_name: str = ""        # the owner's person working on it ("Cicero")

    @property
    def rank(self):
        return PRIORITY_RANK[self.priority]


def label(task):
    """How a task is named to the owner: its person and title first, the id in brackets."""
    who = f"{task.cast_name}: " if task.cast_name else ""
    return f'{who}"{task.title}" in {task.repo} ({task.id})'


def first_sentence(text, limit=160):
    """Short stored summary of an exchange: its first sentence, trimmed."""
    text = " ".join((text or "").split())
    m = re.match(r"(.+?[.!?])(\s|$)", text)
    s = m.group(1) if m else text
    return s if len(s) <= limit else s[:limit - 1].rstrip() + "…"
