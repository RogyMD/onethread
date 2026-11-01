"""Conversation scheduler: decides what deserves the human's attention next.

Separate from the worker scheduler on purpose. A task can be finished by a
worker and still wait here until a natural boundary in the conversation.
"""
from dataclasses import dataclass

from . import voice as voices
from .model import label

KIND_WEIGHT = {"preempt": 4, "blocked": 3, "failed": 3, "interrupted": 3,
               "lease_overdue": 3, "parked": 3, "review_ready": 2, "next_task": 1,
               "dirty_worktree": 1, "merge_blocked": 2, "merge_failed": 2}
PRIORITY_WEIGHT = {"urgent": 3, "high": 2, "normal": 1, "low": 0}
SNOOZE_BOUNDARIES = 3


@dataclass
class Offer:
    item_id: int
    task: str
    kind: str
    text: str


def _urgent(item, task):
    return (item["kind"] in ("interrupted", "preempt")
            or (item["kind"] in ("blocked", "failed") and task.priority == "urgent"))


def _preempt_applies(store, item, task):
    victim = store.get_task(item["note"])
    return task.status == "queued" and victim is not None and victim.status == "running"


def pick(store, *, boundary, focus=None, now=None, suggest_next=True):
    """The single best offer for this turn end, or None.

    Off a boundary only urgent items qualify. Snoozed items are skipped.
    At a boundary the next draft task (not the focused one) is a candidate
    too, with the lowest weight.
    """
    now = store.clock() if now is None else now
    candidates = []
    for item in store.open_attention():
        task = store.get_task(item["task"])
        if item["snooze_boundaries"] > 0 or item["snooze_until"] > now:
            continue
        if item["kind"] == "next_task" and (task.status != "draft" or task.id == focus
                                            or not suggest_next):
            continue
        if item["kind"] == "preempt" and not _preempt_applies(store, item, task):
            store.update_attention(item["id"], open=0)
            continue
        if boundary or _urgent(item, task):
            candidates.append((item, task))
    if boundary and suggest_next:
        suggested = {i["task"] for i in store.open_attention() if i["kind"] == "next_task"}
        drafts = sorted((t for t in store.list_tasks({"draft"})
                         if t.id != focus and t.id not in suggested),
                        key=lambda t: (t.rank, t.num))
        if drafts:
            candidates.append(({"id": None, "kind": "next_task", "note": "",
                                "created": now, "task": drafts[0].id}, drafts[0]))
    if not candidates:
        return None
    item, task = max(candidates, key=lambda c: (
        KIND_WEIGHT[c[0]["kind"]] + PRIORITY_WEIGHT[c[1].priority],
        -c[0]["created"], -(c[0]["id"] or 1e18)))
    item_id = item["id"] or store.add_attention(task.id, "next_task")
    return Offer(item_id, task.id, item["kind"], offer_text(store, item, task, lead=boundary))


def offer_text(store, item, task, lead=False):
    note = item["note"] or ""
    if item["kind"] == "preempt" and store.get_task(note):
        note = label(store.get_task(note))       # the task to pause, by name
    return voices.offer(item["kind"], task, note, lead=lead)


def after_boundary(store):
    """Count one boundary down on every snoozed item."""
    for item in store.open_attention():
        if item["snooze_boundaries"] > 0:
            store.update_attention(item["id"],
                                   snooze_boundaries=item["snooze_boundaries"] - 1)


def postpone(store, item_id, until=0.0):
    """'Later' snoozes for a few boundaries; a time snoozes until then."""
    if until:                               # a time replaces any "a few messages" snooze
        store.update_attention(item_id, snooze_until=until, snooze_boundaries=0)
    else:
        store.update_attention(item_id, snooze_boundaries=SNOOZE_BOUNDARIES)
    item = store.get_attention(item_id)
    store.log(item["task"], "attention_postponed", {"kind": item["kind"]})


def accept(store, item_id):
    item = store.get_attention(item_id)
    store.update_attention(item_id, open=0)
    store.log(item["task"], "attention_accepted", {"kind": item["kind"]})
