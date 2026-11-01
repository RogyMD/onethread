"""Who the owner is, and their people.

The owner picks who they are (`ot me Caesar`). The orchestrator, an AI,
builds the cast from that person's world: a right hand for itself and
members for tasks. onethread only stores the cast and hands out names; it
never invents them. Which task holds a name is read from the tasks
themselves, so the two can't disagree.
"""
from . import prompts

FINISHED = ("done", "cancelled")


def me(store):
    """Who the owner is; "plain" means no roleplay."""
    value = store.get_meta("me")
    if value is None:
        old = store.get_meta("character")        # before `ot me`, it was the character
        if old is None:
            return "plain"
        store.set_meta("me", old)
        value = old
    return value or "plain"


def is_plain(store):
    return me(store).strip().lower() == "plain"


def set_me(store, text):
    """The owner is someone new: forget the old world's cast and names."""
    text = " ".join((text or "").split()) or "plain"
    with store.lock:
        store.set_meta("me", text)
        store.x("DELETE FROM cast")
        store.x("DELETE FROM meta WHERE key='address'")
        store.x("UPDATE tasks SET cast_name=''")
    return text


def right_hand(store):
    rows = store.q("SELECT name, style FROM cast WHERE role='right_hand'")
    return dict(rows[0]) if rows else None


def members(store):
    return [dict(r) for r in store.q("SELECT name, style FROM cast WHERE role='member' ORDER BY pos")]


def member(store, name):
    rows = store.q("SELECT name, style FROM cast WHERE role='member' AND name=?", (name,))
    return dict(rows[0]) if rows else None


def address(store):
    return store.get_meta("address", "")


def unnamed(store):
    return [t for t in store.list_tasks() if t.status not in FINISHED and not t.cast_name]


def _person(item, what):
    if not isinstance(item, dict):
        raise ValueError(f"{what} must be an object with a name and a style")
    name = " ".join(str(item.get("name") or "").split())
    if not name:
        raise ValueError(f"{what} needs a name")
    return name, " ".join(str(item.get("style") or "").split())


def _people(items, taken):
    if not isinstance(items, list) or not items:
        raise ValueError("members must be a non-empty list")
    people, seen = [], {n.lower() for n in taken}
    for item in items:
        name, style = _person(item, "each member")
        if name.lower() in seen:
            raise ValueError(f"{name} appears twice in the cast")
        seen.add(name.lower())
        people.append((name, style))
    return people


def set_cast(store, data):
    """Replace the cast; checked in full before anything changes."""
    if not isinstance(data, dict):
        raise ValueError("the cast must be a JSON object")
    if "right_hand" not in data:
        raise ValueError("the cast needs a right_hand")
    hand = _person(data["right_hand"], "right_hand")
    people = _people(data.get("members"), [hand[0]])
    with store.lock:
        store.x("BEGIN")
        try:
            store.x("DELETE FROM cast")
            store.x("INSERT INTO cast VALUES (?, 'right_hand', ?, 0)", hand)
            for pos, (name, style) in enumerate(people, 1):
                store.x("INSERT INTO cast VALUES (?, 'member', ?, ?)", (name, style, pos))
            store.x("INSERT OR REPLACE INTO meta VALUES ('address', ?)",
                    (" ".join(str(data.get("address") or "").split()),))
            store.x("COMMIT")
        except Exception:
            store.x("ROLLBACK")
            raise
    return fill(store)


def add_members(store, items):
    """More people when the circle runs out."""
    taken = [r["name"] for r in store.q("SELECT name FROM cast")]
    people = _people(items, taken)
    with store.lock:
        start = store.q("SELECT COALESCE(MAX(pos), 0) AS p FROM cast")[0]["p"]
        for pos, (name, style) in enumerate(people, start + 1):
            store.x("INSERT INTO cast VALUES (?, 'member', ?, ?)", (name, style, pos))
    return fill(store)


def fill(store):
    """Give every open task without a name the first free member's name."""
    if is_plain(store):
        return []
    named = []
    with store.lock:
        held = {t.cast_name for t in store.list_tasks()
                if t.status not in FINISHED and t.cast_name}
        free = [m["name"] for m in members(store) if m["name"] not in held]
        for task in sorted(unnamed(store), key=lambda t: t.num):
            if not free:
                break
            store.update_task(task.id, cast_name=free.pop(0))
            named.append(task.id)
    return named


def orchestrator_persona(store):
    return prompts.orchestrator_persona(me(store), right_hand(store), address(store))


def task_persona(store, task):
    who = member(store, task.cast_name) if task.cast_name else None
    return prompts.task_persona(me(store), who, address(store))
