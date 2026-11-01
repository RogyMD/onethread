"""Tips from what the owner repeats: the journal, the daily gate, and the
owner's preferences.

`ot` has no AI. The orchestrator notices repetitions; `ot` supplies the
journal of its own commands (never the owner's messages), lets one tip
through a day, and remembers the answers: declined and accepted tips in
meta, preferences in a plain file the owner can edit.
"""
import datetime as dt
import json
import os
import re

from .worktrees import slug

DAY = 86400
KEEP = 30 * DAY
PREFS_MAX = 30
PREF_CHARS = 200
SKIP = {"hook", "journal", "engine", "wait"}     # not the orchestrator's doing
HEADER = ("# onethread preferences\n"
          "# One per line. Your orchestrator follows them; edit or delete lines freely.\n")


# journal ------------------------------------------------------------------
def record(store, argv, task=None):
    """Write one `ot` command to the journal; drop entries older than KEEP."""
    if not argv or argv[0] in SKIP:
        return
    args = " ".join(argv[1:])
    if len(args) > 80:
        args = args[:79] + "…"
    store.log(task, "cmd", {"cmd": argv[0], "args": args})
    store.x("DELETE FROM events WHERE kind='cmd' AND ts < ?", (store.clock() - KEEP,))


def journal(store, days=7):
    rows = store.q("SELECT ts, data FROM events WHERE kind='cmd' AND ts >= ? ORDER BY ts, id",
                   (store.clock() - days * DAY,))
    lines = []
    for r in rows:
        d = json.loads(r["data"])
        when = dt.datetime.fromtimestamp(r["ts"]).strftime("%Y-%m-%d %H:%M")
        lines.append(f"{when}  ot {d['cmd']} {d['args']}".rstrip())
    return lines


# the daily gate -----------------------------------------------------------
def key(text):
    """The tip's key, so any spelling matches. Letters outside a-z would all
    become the same slug, so they are refused."""
    if not re.sub(r"[^a-z0-9]+", "", (text or "").lower()):
        raise ValueError("a tip needs a KEY of letters a-z or digits")
    return slug(text, 40)


def enabled(store):
    return store.get_meta("tips", "on") != "off"


def set_enabled(store, on):
    store.set_meta("tips", "on" if on else "off")


def slot_open(store):
    return enabled(store) and store.clock() - float(store.get_meta("tip_last", "") or 0) >= DAY


def declined(store):
    return sorted(r["key"].split(":", 1)[1]
                  for r in store.q("SELECT key FROM meta WHERE key LIKE 'tip_no:%'"))


def offered(store, k):
    if not slot_open(store):
        raise ValueError("no tip slot: one tip a day")
    if store.get_meta(f"tip_no:{k}") is not None:
        raise ValueError(f"you declined {k} before")
    if store.get_meta(f"tip_yes:{k}") is not None:
        raise ValueError(f"{k} was accepted before")
    now = str(store.clock())
    store.set_meta("tip_last", now)
    store.set_meta(f"tip_offered:{k}", now)


def accept(store, k, text="", home=None):
    """A preference is saved only for a tip that was offered: nothing else
    (such as text a worker wrote) can plant one."""
    if text:
        if store.get_meta(f"tip_offered:{k}") is None:
            raise ValueError(f"tip {k} wasn't offered; no preference saved")
        add_pref(home, text)
    store.set_meta(f"tip_yes:{k}", str(store.clock()))


def decline(store, k):
    store.set_meta(f"tip_no:{k}", str(store.clock()))


# preferences --------------------------------------------------------------
def prefs_path(home):
    return os.path.join(home, "preferences.md")


def _pref(line):
    """The preference on a line, or None for blank and comment lines."""
    s = line.strip()
    if not s or s.startswith("#"):
        return None
    if s[:2] in ("- ", "* "):
        s = s[2:].strip()
    return s or None


def _lines(home):
    try:
        with open(prefs_path(home), errors="replace") as f:
            return f.read().splitlines(keepends=True)
    except FileNotFoundError:
        return []


def prefs(home):
    found = [p for p in map(_pref, _lines(home)) if p]
    return [p[:PREF_CHARS] for p in found[:PREFS_MAX]]


def add_pref(home, text):
    text = " ".join(text.split())
    path = prefs_path(home)
    new = not os.path.exists(path)
    with open(path, "a") as f:
        if new:
            f.write(HEADER)
        f.write(f"- {text}\n")


def remove_pref(home, n):
    lines = _lines(home)
    at = [i for i, line in enumerate(lines) if _pref(line)]
    if not 1 <= n <= len(at):
        raise ValueError(f"there is no preference {n}")
    gone = _pref(lines[at[n - 1]])
    del lines[at[n - 1]]
    with open(prefs_path(home), "w") as f:
        f.writelines(lines)
    return gone
