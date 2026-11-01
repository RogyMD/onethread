"""Every report and offer the user reads comes from these templates.

These are onethread's own words and always stay plain: the chosen character
is played by the agents (see cast.py and prompts.py), never by these templates.
Facts are always filled in from the task database.
"""

from .model import label

PLAIN = {
    "lead": "Before we go on: ",
    "review_ready": "{label} is ready for review. Review it now, or later?",
    "blocked": "{label} is blocked and needs your answer: {note} Answer now, or later?",
    "failed": "{label} failed: {note} Look at it now, or later?",
    "interrupted": "The worker for {label} stopped without finishing. Resume it now, or later?",
    "preempt": "Urgent {label} is waiting because every worker is busy. Pause {note} to make room, or later?",
    "lease_overdue": "{label} has held the {note} lease for a long time. Check on it now, or later?",
    "parked": "You left {label}. Hand it back to the agent, send it to review, or leave it?",
    "next_task": "Next up: {label}. Switch to it?",
    "dirty_worktree": "{label}'s worktree has uncommitted changes. Delete it, or keep it?",
    "merge_blocked": "{label} is ready to merge, but {note} Fix that and I'll merge it, or later?",
    "merge_failed": "{label} can't merge: {note} Look at it, or cancel it?",
}

def _fill(key, task, note=""):
    return PLAIN[key].format(label=label(task), task=task.id, title=task.title, repo=task.repo,
                       note=note, agent=task.agent, status=task.status)


def offer(kind, task, note="", lead=False):
    text = _fill(kind, task, note)
    if lead and kind != "next_task":
        text = PLAIN["lead"] + text
    return text


def ago(seconds):
    seconds = max(0, int(seconds))
    if seconds < 90:
        return f"{seconds}s"
    if seconds < 5400:
        return f"{seconds // 60} min"
    return f"{seconds // 3600} h"
