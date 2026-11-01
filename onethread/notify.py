"""Notices: what the owner should hear about without asking.

Each engine tick looks for news (a task ready for review, stuck, failed,
stopped, unable to merge, or merged, and reminders whose time is up),
records a `notified` event per notice and posts it as a banner. The chat
reads the same events through `ot wait`.

The banner is iTerm2's own (its OSC 9 escape code, written to the
orchestrator's terminal, or under tmux to the iTerm2 tab showing its
session), so clicking it opens that tab. Apple's Terminal gets the tab's
bell instead, which marks the tab and badges its Dock icon. Anywhere else
there is no banner: the chat still speaks up.
"""
import json
import os
import re
import subprocess
import time

from . import attention
from .model import label
from .procs import alive

KINDS = frozenset({"review_ready", "blocked", "failed", "interrupted", "preempt",
                   "lease_overdue", "merge_blocked", "merge_failed"})


TTY = re.compile(r"/dev/ttys\d+")
MAX_TEXT = 200


def clean(text):
    """Plain text only: nothing in it can act as a terminal escape code."""
    text = re.sub(r"[\n\r\t]", " ", text)
    text = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", text)
    return " ".join(text.split())[:MAX_TEXT]


def _write_tty(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)


def _lobby_tty(store):
    """The orchestrator's terminal, while it runs there; "" otherwise."""
    tty = store.get_meta("lobby_tty", "")
    if not TTY.fullmatch(tty) or not alive(store.get_meta("lobby_pid", "")):
        return ""
    return tty


def _screen(store, run):
    """The tty the owner sees the orchestrator on: its own, or under tmux
    the tty of a terminal tab attached to its session; "" when none."""
    tty = _lobby_tty(store)
    pane = store.get_meta("lobby_pane", "")
    if not tty or not pane:
        return tty
    socket = store.get_meta("lobby_tmux", "").split(",")[0]
    if not re.fullmatch(r"%\d+", pane) or not socket:
        return ""
    try:
        res = run(["tmux", "-S", socket, "list-clients", "-t", pane, "-F", "#{client_tty}"],
                  capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return ""
    if res.returncode != 0:
        return ""
    return next((t for t in res.stdout.split() if TTY.fullmatch(t)), "")


def banner(store, text, write=None, run=subprocess.run):
    """Post `text` as a banner: "iterm", "bell" (Apple's Terminal), or ""
    when the orchestrator has no terminal that can show one."""
    write = write or _write_tty
    term = store.get_meta("lobby_term", "")
    if term not in ("iTerm.app", "Apple_Terminal"):
        return ""
    tty = _screen(store, run)
    if not tty:
        return ""
    iterm = term == "iTerm.app"
    try:
        write(tty, b"\x1b]9;" + clean(text).encode() + b"\x07" if iterm else b"\x07")
    except OSError:
        return ""
    return "iterm" if iterm else "bell"


def banner_hint(store, own):
    """Once per orchestrator: why its banners can't reach the owner, or ""."""
    if store.get_meta("notify", "1") == "0":
        return ""
    if not own:
        why = ("Banners can't reach this chat: it was opened before onethread had banners. "
               "Tell the owner once that reopening the orchestrator with `ot` turns them on.")
    elif own == store.get_meta("lobby_pid", "") and \
            store.get_meta("lobby_term", "") not in ("iTerm.app", "Apple_Terminal"):
        why = ("Banners need iTerm2 (or Apple's Terminal for a tab mark); this chat runs elsewhere. "
               "Tell the owner once; you still speak up here.")
    else:
        return ""
    if store.get_meta("banner_hint", "") == (own or "old"):
        return ""
    store.set_meta("banner_hint", own or "old")
    return " " + why


def _newest_event(store):
    rows = store.q("SELECT MAX(id) AS id FROM events")
    return rows[0]["id"] or 0


def _due(store, item, since, now):
    if item["kind"] not in KINDS or item["snooze_until"] > now:
        return False
    notified = store.get_meta(f"notified:{item['id']}")
    if notified is None:
        if item["created"] > since:
            return True
        notified = since                    # it waited before notices began
    return 0 < item["snooze_until"] and float(notified) < item["snooze_until"]


def _merges(store, upto):
    """Merges the engine made: the owner wasn't there. Those they asked for
    were already told in the chat."""
    rows = store.q("SELECT * FROM events WHERE kind='merged' AND id>? ORDER BY id", (upto,))
    return [dict(r) for r in rows]


def tick(store, post=None, now=None):
    """Record and post what is news since the last tick. The first tick
    ever only starts the clock: what already waits is not announced."""
    now = store.clock() if now is None else now
    since = store.get_meta("notify_since")
    if since is None:
        store.set_meta("notify_since", str(now))
        store.set_meta("notify_event_upto", str(_newest_event(store)))
        return []
    notices = []
    for item in store.open_attention():
        if _due(store, item, float(since), now):
            task = store.get_task(item["task"])
            notices.append({"task": task.id, "item": item["id"], "kind": item["kind"],
                            "text": attention.offer_text(store, item, task)})
            store.set_meta(f"notified:{item['id']}", str(now))
    upto = int(store.get_meta("notify_event_upto", "0"))
    for event in _merges(store, upto):
        upto = event["id"]
        data = json.loads(event["data"] or "{}")
        if data.get("by") != "engine":
            continue
        task = store.get_task(event["task"])
        into = data.get("into", "")
        notices.append({"task": task.id, "item": None, "kind": "merged",
                        "text": f"{label(task)} was merged into {into}."})
    store.set_meta("notify_event_upto", str(upto))
    for n in notices:
        store.log(n["task"], "notified", {"item": n["item"], "kind": n["kind"], "text": n["text"]})
        if post and store.get_meta("notify", "1") != "0":
            try:
                post(store, n["text"])
            except Exception:               # a banner that fails never stops the engine
                pass
    return notices


def seen(store, tid, now=None):
    """The orchestrator just dealt with this task: what it has open now,
    the owner hears in the chat, not as news."""
    now = store.clock() if now is None else now
    for item in store.open_attention():
        if item["task"] == tid and item["kind"] in KINDS:
            store.set_meta(f"notified:{item['id']}", str(now))


def offered(store, item_id):
    """The hook (or `ot inbox --next`) put this offer before the orchestrator."""
    store.set_meta(f"offered_at:{item_id}", str(store.clock()))


def reminder_pending(store, now):
    """True while a notice waits for its reminder time."""
    return any(i["kind"] in KINDS and i["snooze_until"] > now for i in store.open_attention())


# The chat side: `ot wait` -------------------------------------------------
def reset_wait(store):
    """A new orchestrator starts fresh: what happened before, the hook tells."""
    store.set_meta("waited_upto", str(_newest_event(store)))


def since(store, now=None):
    """Notices the chat hasn't reported yet, skipping offers already handled
    or snoozed. Lines: "[offer ID KIND T] TEXT", or TEXT for a merge."""
    now = store.clock() if now is None else now
    upto = store.get_meta("waited_upto")
    if upto is None:
        reset_wait(store)
        return []
    rows = store.q("SELECT * FROM events WHERE kind='notified' AND id>? ORDER BY id", (int(upto),))
    lines, items = [], set()
    for row in rows:
        data = json.loads(row["data"] or "{}")
        item = data.get("item")
        if item is None:
            lines.append(data.get("text", ""))
        elif item not in items and _still_news(store, item, row, now):
            items.add(item)
            lines.append(f"[offer {item} {data.get('kind', '')} {row['task']}] {data.get('text', '')}")
    if rows:
        store.set_meta("waited_upto", str(rows[-1]["id"]))
    return lines


def _still_news(store, item, row, now):
    """Not closed, snoozed, offered by the hook, or put off since its notice."""
    current = store.get_attention(item)
    if not current or not current["open"] or current["snooze_until"] > now:
        return False
    if float(store.get_meta(f"offered_at:{item}", "0")) >= row["ts"]:
        return False
    later = store.q("SELECT data FROM events WHERE task=? AND id>? AND kind IN "
                    "('attention_postponed', 'attention_accepted')", (row["task"], row["id"]))
    return not any(json.loads(r["data"] or "{}").get("kind") == current["kind"] for r in later)


def wait(store, lobby, sleep=time.sleep, interval=2):
    """Block until there is news for the chat. Ends with nothing once the
    orchestrator that started it is gone (closed, or replaced by a new one)."""
    while True:
        if store.get_meta("lobby_pid", "") != lobby or not alive(lobby):
            return []
        lines = since(store)
        if lines:
            return lines
        sleep(interval)
