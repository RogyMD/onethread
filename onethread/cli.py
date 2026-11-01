"""`ot` subcommands: the toolset the orchestrator agent (and the owner) uses.
Data lives in $ONETHREAD_HOME (default ~/.onethread). Output is short and
plain; errors are one line on stderr with exit code 1."""
import argparse
import fcntl
import io
import json
import os
import select
import shlex
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
import uuid

from . import (attention, cast, cleanup, engine, finish, leases, lobby, notify, prompts, review, schemas, terminal, tips,
               usage, views, worktrees)
from .agents import FakeAgent, get_agent
from .confirm import make_confirm
from .model import PRIORITIES, label
from .ops import Ops, parse_when
from .procs import process_start
from .store import Store

OT = os.path.join(engine.ROOT, "ot")

CONFIG_KEYS = ("capacity", "orchestrator", "claude.model", "codex.model", "merge_on_accept",
               "notify")


def home_dir():
    home = os.path.expanduser(os.environ.get("ONETHREAD_HOME", "~/.onethread"))
    os.makedirs(home, exist_ok=True)
    return home


def make_agents(store):
    return {"claude": get_agent("claude", store.get_meta("claude.model", "")),
            "codex": get_agent("codex", store.get_meta("codex.model", "")),
            "fake": FakeAgent()}


def blank_op(name, **kw):
    op = {k: "" for k in schemas.OPERATION["properties"]}
    op["depends_on"] = []
    op.update(op=name, **kw)
    return op


class Ctx:
    """What one `ot` command works with. The engine (and its supervisor) is
    only built for commands that may stop a worker."""

    def __init__(self, store, home, confirm, ensure, open_tab=terminal.open_tab,
                 run_session=None, open_path=None):
        self.store, self.home, self.confirm, self.ensure = store, home, confirm, ensure
        self.open_tab = open_tab
        self.open_path = open_path or (lambda p, as_text=False: subprocess.run(
            ["open", *(["-t"] if as_text else []), p], check=False))
        self.run_session = run_session or (
            lambda argv, cwd, env: subprocess.call(argv, cwd=cwd, env=env))
        self._engine = None

    @property
    def engine(self):
        if self._engine is None:
            self._engine = engine.Engine(self.store, self.home, make_agents(self.store))
        return self._engine

    @property
    def ops(self):
        return Ops(self.store, self.engine.sup, self.confirm, self.home)

    def close(self):
        if self._engine:
            self._engine.close()

    def say(self, res):
        """Print an OpResult; 0 when it worked, 1 otherwise."""
        if res.message:
            print(res.message, file=sys.stdout if res.ok else sys.stderr)
        if res.ok:
            self.ensure(self.home)
        return 0 if res.ok else 1

    def op(self, name, **kw):
        # Every command may come from the orchestrator agent, so anything
        # that needs the owner's word asks for it.
        return self.say(self.ops.apply(blank_op(name, **kw), origin="agent"))


def _task(ctx, tid):
    t = ctx.store.get_task(tid)
    if not t:
        raise ValueError(f"unknown task {tid}")
    return t


# commands ------------------------------------------------------------------
def _repo_new(ctx, path):
    """A new, empty git repository for a project the owner said yes to.
    Never touches a folder with anything in it; undoes itself on failure."""
    path = os.path.abspath(os.path.expanduser(path))
    name = os.path.basename(path)
    if os.path.exists(path) and (not os.path.isdir(path) or os.listdir(path)):
        raise ValueError(f"{path} already exists and isn't empty.")
    if ctx.store.get_repo(name):
        raise ValueError(f"a repo named {name} is already registered")
    if not ctx.confirm(f"Create a new git repository at {path} and register it with onethread?"):
        print("Not created.", file=sys.stderr)
        return 1
    made = not os.path.exists(path)
    os.makedirs(path, exist_ok=True)
    for args in (["init", "-q", "-b", "main"], ["commit", "-q", "--allow-empty", "-m", "Start"]):
        res = subprocess.run(["git", "-C", path, *args], capture_output=True, text=True)
        if res.returncode != 0:
            shutil.rmtree(path if made else os.path.join(path, ".git"), ignore_errors=True)
            raise ValueError(f"git could not create the repository: {(res.stderr or res.stdout).strip()}")
    repo = ctx.store.add_repo(path)
    print(f"Registered {repo['name']} ({repo['path']}, base {repo['base_branch']}).")
    return 0


def cmd_repo(ctx, a):
    if a.repo_cmd == "new":
        return _repo_new(ctx, a.path)
    if a.repo_cmd == "add":
        if a.name and len(a.paths) > 1:
            raise ValueError("--name names one repo: add the others one at a time")
        for path in a.paths:
            repo = ctx.store.add_repo(path, name=a.name)
            print(f"Registered {repo['name']} ({repo['path']}, base {repo['base_branch']}).")
        return 0
    if a.repo_cmd == "base":
        repo = ctx.store.set_base(a.repo, a.branch)
        print(f"{repo['name']}'s new tasks start from {a.branch} and merge into it.")
        return 0
    repos = ctx.store.list_repos()
    for r in repos:
        print(f"{r['name']}: {r['path']} ({r['base_branch']})")
    if not repos:
        print("No repos yet. Add one with: ot repo add PATH")
    return 0


def cmd_add(ctx, a):
    deps = [d.strip().upper() for d in a.after.split(",") if d.strip()]
    return ctx.op("add_task", repo=a.repo, title=" ".join(a.title), priority=a.priority,
                  agent=a.agent, description=a.desc, depends_on=deps)


def cmd_status(ctx, a):
    if a.json:
        print(json.dumps(views.status_json(ctx.store), indent=1))
    else:
        print("\n".join(views.status_lines(ctx.store, ctx.engine.lease_store)))
    return 0


def cmd_inbox(ctx, a):
    if a.shown:
        ctx.store.set_meta("pending_offer", str(a.shown))
        return 0
    if a.next:
        offer = attention.pick(ctx.store, boundary=True)
        if offer:
            notify.offered(ctx.store, offer.item_id)
        print(f"{_offer_tag(offer)} {offer.text}" if offer else "Nothing to offer.")
        return 0
    print("\n".join(views.inbox_lines(ctx.store)))
    return 0


def _offer_tag(offer):
    """The orchestrator picks the choices it asks by the offer's kind."""
    return f"[offer {offer.item_id} {offer.kind} {offer.task}]"


def _wait_lock(home):
    return os.path.join(home, "wait.lock")


def _waiting(home):
    """True while an `ot wait` holds its lock."""
    with open(_wait_lock(home), "a") as probe:
        try:
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
    return False


def cmd_wait(ctx, a):
    """Block until there's news for the chat (the Claude orchestrator runs
    this in the background, so Claude Code wakes it when it ends)."""
    if os.environ.get("CODEX_SANDBOX"):
        raise ValueError("ot wait is for the Claude orchestrator; Codex gets the banner.")
    lock = open(_wait_lock(ctx.home), "a")
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Already waiting.")
            return 0
        ctx.ensure(ctx.home)
        lobby = os.environ.get("ONETHREAD_LOBBY_PID") or ctx.store.get_meta("lobby_pid", "")
        for line in notify.wait(ctx.store, lobby):
            print(line)
        return 0
    finally:
        lock.close()


def cmd_later(ctx, a):
    t = _task(ctx, a.task)
    until = parse_when(" ".join(a.when), ctx.store.clock())
    items = [i for i in ctx.store.open_attention() if i["task"] == t.id]
    for i in items:
        attention.postpone(ctx.store, i["id"], until=until)
    if until and ctx.store.get_meta("pending_offer", "") in {str(i["id"]) for i in items}:
        ctx.store.set_meta("pending_offer", "")     # a time was picked; not a "later"
    print(f"OK, {label(t)} waits. It stays in `ot inbox`." if items
          else f"Nothing from {label(t)} is waiting.")
    return 0


def cmd_review(ctx, a):
    t = _task(ctx, a.task)
    if t.status == "parked":
        t = ctx.store.set_status(t.id, "review")
    ctx.store.close_attention(t.id, {"review_ready", "parked"})
    print("\n".join(views.review_lines(ctx.store, t)))
    return 0


def cmd_look(ctx, a):
    t = _task(ctx, a.task)
    print(json.dumps({"task": t.id, "looked": review.looked(ctx.store, t),
                      "items": review.items(ctx.store, t)}, ensure_ascii=False))
    return 0


SHOW_FILES = 20
# Opened in their own viewer; every other changed file opens as plain text.
VIEWABLE = {".png", ".jpg", ".jpeg", ".gif", ".heic", ".webp", ".tiff", ".bmp", ".pdf"}


def cmd_show(ctx, a):
    """Open what the owner picked from the review list, then remember they looked."""
    t = _task(ctx, a.task)
    have = [i["id"] for i in review.items(ctx.store, t)]
    missing = [i for i in a.items if i not in have]
    if missing:
        raise ValueError(f"{label(t)} has nothing to review as {', '.join(missing)}; "
                         f"it has: {', '.join(have)}")
    for item in dict.fromkeys(a.items):
        if item == "summary":
            print("\n".join(views.review_lines(ctx.store, t, stat=False)))
        elif item == "diff":
            repo = ctx.store.get_repo(t.repo)["path"]
            command = (f"git -C {shlex.quote(repo)} -c color.ui=always diff "
                       f"{shlex.quote(f'{t.base_branch}...{t.branch}')} | less -R")
            if not ctx.open_tab(command):
                print(f"Couldn't open an iTerm2 tab. Open a new terminal tab and run:\n  {command}")
        elif item == "files":
            paths = [os.path.join(t.worktree, f) for f in review.changed_files(ctx.store, t)]
            paths = [p for p in paths if os.path.isfile(p)]
            for path in paths[:SHOW_FILES]:          # as text: never run what a worker wrote
                ctx.open_path(path, as_text=os.path.splitext(path)[1].lower() not in VIEWABLE)
            if len(paths) > SHOW_FILES:
                print(f"…and {len(paths) - SHOW_FILES} more changed files.")
        elif item == "agent":
            cmd_open(ctx, argparse.Namespace(ref=[t.id]))
        elif item == "pr":
            ctx.open_path(t.result["pr"])
    review.mark(ctx.store, t.id, "show")
    return 0


def cmd_handback(ctx, a):
    t = _task(ctx, a.task)
    if t.status != "parked":
        raise ValueError(f"{label(t)} is {t.status}; only a task you left can be handed back")
    ctx.store.update_task(t.id, followups=t.followups + [prompts.RETURN_NOTE])
    ctx.store.set_status(t.id, "queued")
    ctx.store.close_attention(t.id)
    ctx.ensure(ctx.home)
    print(f"{label(t)} is back with its agent ({t.agent}); it continues the same session.")
    return 0


def _take_over(ctx, t):
    """The owner is the boss: a working agent stops at once, no question."""
    for _ in range(3):                      # the engine may move it meanwhile
        t = ctx.store.get_task(t.id)
        if t.status == "with_you":
            break
        try:
            ctx.store.set_status(t.id, "with_you")
            break
        except ValueError:
            if t.status in ("done", "cancelled"):
                raise
    ctx.engine.sup.kill_runs(t.id)          # any worker, whatever the status said
    ctx.store.close_attention(t.id, {"parked", "next_task"})
    return ctx.store.get_task(t.id)


def cmd_open(ctx, a):
    ref = " ".join(a.ref)
    found = terminal.resolve(ctx.store, ref)
    if isinstance(found, list):
        if not found:
            raise ValueError(f"no open task matches {ref!r}; see `ot status`")
        raise ValueError(f"{ref!r} doesn't name one task clearly. Closest: "
                         + "; ".join(label(t) for t in found)
                         + ". Say which one.")
    if found.status == "with_you" and engine.tab_alive(ctx.store, found.id):
        print(views.task_card(found))
        print(f"{label(found)} is already open in a tab; switch to that tab.")
        return 0
    t = _take_over(ctx, found)
    print(views.task_card(t))
    command = (f"ONETHREAD_HOME={shlex.quote(ctx.home)} {shlex.quote(OT)} session {t.id}")
    if not ctx.open_tab(command):
        print(f"Couldn't open an iTerm2 tab. Open a new terminal tab and run:\n  {command}")
    ctx.ensure(ctx.home)
    return 0


def _session_id(ctx, t, agent):
    """(session id, new) for the task, creating a Codex session first if needed."""
    if t.session_id:
        return t.session_id, False
    if agent.presets_session_id:
        sid = str(uuid.uuid4())
        ctx.store.update_task(t.id, session_id=sid)
        return sid, True
    print(f"Preparing {t.id}'s {agent.label} session…")
    turn_dir = os.path.join(ctx.home, "runs", t.id, "turns")
    os.makedirs(turn_dir, exist_ok=True)
    inv = agent.invocation(mode="plan", cwd=t.worktree, session_id="",
                           prompt=prompts.NEW_SESSION.format(
                               id=t.id, repo=t.repo, title=t.title,
                               description=t.description, branch=t.branch),
                           schema_path=ctx.engine.sup.schemas["reply"],
                           out_path=os.path.join(turn_dir, f"{len(os.listdir(turn_dir)) + 1}.json"),
                           extra_dirs=[])
    sid, _, raw = agent.run_turn(inv)
    if not sid:
        raise ValueError(agent.explain(raw))
    ctx.store.update_task(t.id, session_id=sid)
    return sid, False


NEVER_STARTED = 20   # seconds: a new session that fails this fast never really began


def cmd_session(ctx, a):
    t = _task(ctx, a.task)
    t = worktrees.ensure(ctx.store, ctx.home, t.id)
    t = _take_over(ctx, t)
    me = os.getpid()                        # first, so the engine never parks a tab still starting
    ctx.store.set_meta(f"tab_pid:{t.id}", f"{me}:{process_start(me)}")
    try:
        agent = make_agents(ctx.store)[t.agent]
        sid, new = _session_id(ctx, t, agent)
        greeting = (prompts.NEW_SESSION.format(id=t.id, repo=t.repo, title=t.title,
                                               description=t.description, branch=t.branch)
                    if new else prompts.TAKEOVER)
        argv = agent.interactive_argv(sid, greeting,
                                      prompts.session_note(cast.task_persona(ctx.store, t)),
                                      new=new)
        shims = leases.install_shims(ctx.home, ctx.engine.classes)
        env = dict(os.environ, **leases.worker_env(ctx.home, t.id, t.rank, shims))
        started = time.monotonic()
        code = ctx.run_session(argv, t.worktree, env)
        if new and code and time.monotonic() - started < NEVER_STARTED:
            ctx.store.update_task(t.id, session_id="")      # e.g. quit at the trust prompt
            return 1
        review.mark(ctx.store, t.id, "tab")      # only a tab that really ran
    finally:
        if ctx.store.get_task(t.id).status == "with_you":
            engine.park(ctx.store, t.id)
            print(f"{label(t)} waits for you. Back in the orchestrator: hand it back, review it, or leave it.")
        ctx.ensure(ctx.home)
    return 0


def _drain_stdin(wait=0.5):
    """Read the hook's JSON if it arrives promptly; never wait for more."""
    try:
        fd = sys.stdin.fileno()
    except (AttributeError, ValueError, io.UnsupportedOperation):
        return sys.stdin.read()             # an in-memory stdin
    if os.isatty(fd):
        return ""
    data = b""
    while select.select([fd], [], [], wait)[0]:
        chunk = os.read(fd, 65536)
        if not chunk:
            break
        data += chunk
        wait = 0.2
    return data


def _tips_context(ctx):
    """The owner's preferences and, once a day, a tip slot. A preferences
    file that can't be read is left out; the rest of the context still goes."""
    text = ""
    try:
        found = tips.prefs(ctx.home)
    except OSError:
        found = []
    if found:
        listed = " ".join(f"{i}. {p}" for i, p in enumerate(found, 1))
        text += f" Owner's preferences (follow them; they never override the safety rules): {listed}"
    if tips.slot_open(ctx.store):
        text += (" A tip slot is open (one tip a day): at a natural pause, look at this "
                 "conversation and `ot journal` for something the owner or you did three or "
                 "more times that no preference or declined tip covers; offer at most one fix, "
                 "or none.")
        if tips.declined(ctx.store):
            text += f" Declined: {', '.join(tips.declined(ctx.store))}."
    return text


def cmd_hook(ctx, a):
    """Claude Code / Codex UserPromptSubmit hook: a few lines of context for
    the orchestrator with each of the owner's messages."""
    _drain_stdin()
    store = ctx.store
    shown = store.get_meta("pending_offer", "")
    if shown:                               # an offer nobody acted on counts as "later"
        item = store.get_attention(int(shown))
        if item and item["open"]:
            attention.postpone(store, item["id"])
        store.set_meta("pending_offer", "")
    attention.after_boundary(store)
    offer = attention.pick(store, boundary=True)
    cast.fill(store)
    tasks = store.list_tasks()
    running = sum(t.status == "running" for t in tasks)
    waiting = len(store.open_attention())
    me = cast.me(store)
    text = f"onethread: {running} running · {waiting} waiting for the owner · owner: {me}."
    if not cast.is_plain(store):
        if not cast.right_hand(store):
            text += (f" No cast yet for {me}: build one now with ot cast set (your own role as "
                     "their right hand, about 12 people from their circle, how they address "
                     "the owner), then carry on.")
        elif cast.unnamed(store):
            text += (f" {len(cast.unnamed(store))} task(s) wait for a name: "
                     "add people with ot cast add.")
        text += f" Your role now: {cast.orchestrator_persona(store)}"
    text += _tips_context(ctx)
    own = os.environ.get("ONETHREAD_LOBBY_PID", "")
    if (store.get_meta("orchestrator", "claude") == "claude" and not _waiting(ctx.home)
            and own in ("", store.get_meta("lobby_pid", ""))):
        text += " No `ot wait` is running: start one in the background now."
    text += notify.banner_hint(store, own)
    if offer:
        notify.offered(store, offer.item_id)
        text += (f" Offer for a natural pause: {_offer_tag(offer)} {offer.text} "
                 f"If you make it, run `ot inbox --shown {offer.item_id}`.")
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                             "additionalContext": text}}, ensure_ascii=False))
    ctx.ensure(ctx.home)                    # the engine may have stopped; offers need it
    return 0


def cmd_install(ctx, a):
    target = os.path.join(os.path.expanduser(a.dir), "ot")
    if not ctx.confirm(f"Link {target} to {OT} so `ot` works from any folder?"):
        print("Not installed.", file=sys.stderr)
        return 1
    os.makedirs(os.path.dirname(target), exist_ok=True)
    if os.path.islink(target):
        os.remove(target)
    elif os.path.exists(target):
        raise ValueError(f"{target} exists and is not a link; remove it first")
    os.symlink(OT, target)
    print(f"Installed: {target}. If `ot` isn't found, add {os.path.dirname(target)} to your PATH.")
    return 0


def cmd_merge(ctx, a):
    return ctx.say(ctx.ops.merge(a.task))


def cmd_finish(ctx, a):
    t = _task(ctx, a.task)
    if t.status in ("draft", "cancelled"):
        raise ValueError(f"{label(t)} is {t.status}; nothing to finish.")
    base = ctx.store.get_repo(t.repo)["base_branch"]
    first = "record the published link, then " if a.link else ""
    if not ctx.confirm(f"Finish {label(t)}: {first}accept it and merge it into {base} "
                       "when it's ready?"):
        print("Not finished.", file=sys.stderr)
        return 1
    finish.order(ctx.store, t.id, a.link)
    if a.link:
        review.mark(ctx.store, t.id, "link")        # they read it to post it
    outcome = finish.step(ctx.store, ctx.store.get_task(t.id))
    if outcome == "accepted":
        outcome = finish.step(ctx.store, ctx.store.get_task(t.id))
    print(finish.say(ctx.store, t, outcome))
    ctx.ensure(ctx.home)
    return 0


def cmd_journal(ctx, a):
    lines = tips.journal(ctx.store, a.days)
    print("\n".join(lines) if lines else f"Nothing in the journal for the last {a.days} days.")
    return 0


def cmd_tip(ctx, a):
    k = tips.key(a.key)
    if a.action == "offered":
        tips.offered(ctx.store, k)
        print(f"Tip {k} offered; the next one can come in 24 h.")
    elif a.action == "yes":
        text = " ".join(a.text).strip()
        tips.accept(ctx.store, k, text, home=ctx.home)
        print(f"Saved: {' '.join(text.split())}" if text else f"Noted: yes to {k}.")
    else:
        tips.decline(ctx.store, k)
        print(f"Noted: {k} won't be offered again.")
    return 0


def cmd_tips(ctx, a):
    tips.set_enabled(ctx.store, a.state == "on")
    print(f"Tips are {a.state}.")
    return 0


def cmd_prefs(ctx, a):
    if a.action == "remove":
        print(f"Removed: {tips.remove_pref(ctx.home, a.n)}")
        return 0
    found = tips.prefs(ctx.home)
    print("\n".join(f"{i}. {p}" for i, p in enumerate(found, 1)) if found else "No preferences yet.")
    return 0


def cmd_pr(ctx, a):
    return ctx.say(ctx.ops.open_pr(a.task))


def cmd_me(ctx, a):
    if not a.text:
        print(f"You are {cast.me(ctx.store)}.")
        return 0
    who = cast.set_me(ctx.store, " ".join(a.text))
    print("No roleplay: agents speak plainly." if cast.is_plain(ctx.store) else
          f"You are {who} now. The orchestrator builds your people on your next message.")
    return 0


def _json(text):
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"the cast must be JSON ({exc})") from None


def cmd_cast(ctx, a):
    store = ctx.store
    if a.action == "set":
        named = cast.set_cast(store, _json(a.json))
    elif a.action == "add":
        named = cast.add_members(store, _json(a.json))
    else:
        hand = cast.right_hand(store)
        if not hand:
            print(f"No cast yet for {cast.me(store)}.")
            return 0
        holder = {t.cast_name: t for t in store.list_tasks()
                  if t.status not in cast.FINISHED and t.cast_name}
        print(f"You are {cast.me(store)}. Right hand: {hand['name']} ({hand['style']}).")
        for m in cast.members(store):
            t = holder.get(m["name"])
            print(f"  {m['name']} ({m['style']}) · {label(t) if t else 'free'}")
        return 0
    hand = cast.right_hand(store)
    labels = [label(store.get_task(tid)) for tid in named]
    print(f"Cast saved: {hand['name']} and {len(cast.members(store))} people."
          + (f" Named: {'; '.join(labels)}." if labels else ""))
    ctx.ensure(ctx.home)
    return 0


def cmd_config(ctx, a):
    key, value = a.key, a.value
    if key == "capacity" and not value.isdigit():
        raise ValueError("capacity must be a whole number, like 4")
    if key == "orchestrator" and value not in ("claude", "codex"):
        raise ValueError("orchestrator must be claude or codex")
    if key == "merge_on_accept" and value not in ("0", "1"):
        raise ValueError("merge_on_accept must be 1 (on) or 0 (off)")
    if key == "notify" and value not in ("0", "1"):
        raise ValueError("notify must be 1 (on) or 0 (off)")
    ctx.store.set_meta(key, value)
    print(f"{key} = {value}")
    return 0


def cmd_engine(ctx, a):
    if a.action == "stop":
        with open(engine.lock_path(ctx.home), "a+") as probe:
            try:                            # free lock: no engine, and the pid may be stale
                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
                print("The engine is not running.")
                return 0
            except BlockingIOError:
                probe.seek(0)
                pid = probe.read().strip()
        try:
            os.kill(int(pid), signal.SIGTERM)
        except (OSError, ValueError):
            print("The engine is not running.")
            return 0
        print("Engine stopped. Workers keep running; `ot` restarts the engine.")
        return 0
    result = ctx.engine.run()
    if result == "restart":                 # the lock is free again: start the new code
        ctx.ensure(ctx.home)
    elif not result:
        print("The engine is already running.")
    return 0


def cmd_clean(ctx, a):
    if a.keep:
        t = _task(ctx, a.task or "")
        ctx.store.set_meta(f"keep_worktree:{t.id}", "1")
        ctx.store.close_attention(t.id, {"dirty_worktree"})
        print(f"Keeping {label(t)}'s worktree.")
        return 0
    only = _task(ctx, a.task).id if a.task else None
    r = cleanup.clean(ctx.store, ctx.home, confirm=ctx.confirm, only=only)
    if not (r["worktrees"] or r["branches"] or r["logs"]):
        print("Nothing to clean.")
    else:
        print(f"Cleaned {len(r['worktrees'])} worktrees, {len(r['branches'])} branches and "
              f"{len(r['logs'])} logs; freed {cleanup.human(r['freed'])}.")
    return 0


SIMPLE = {"start": "approve_implementation", "accept": "review_accept",
          "pause": "pause", "resume": "resume", "cancel": "cancel"}
WITH_TEXT = {"changes": "request_changes", "answer": "answer"}


def parser():
    ap = argparse.ArgumentParser(prog="ot", description="One conversation, many coding agents.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("repo", help="register or list git repositories")
    rs = r.add_subparsers(dest="repo_cmd", required=True)
    ra = rs.add_parser("add")
    ra.add_argument("paths", nargs="+")
    ra.add_argument("--name", default="", help="register under this name instead of the folder's")
    rb = rs.add_parser("base", help="the branch tasks start from and merge into")
    rb.add_argument("repo")
    rb.add_argument("branch")
    rs.add_parser("list")
    rs.add_parser("new", help="create and register a new, empty repository").add_argument("path")
    a = sub.add_parser("add", help="add a task")
    a.add_argument("repo")
    a.add_argument("title", nargs="+")
    a.add_argument("-p", "--priority", default="normal", choices=PRIORITIES)
    a.add_argument("--after", default="")
    a.add_argument("--agent", default="", choices=["", "claude", "codex", "fake"])
    a.add_argument("--desc", default="")
    s = sub.add_parser("status", help="what's running, queued and waiting")
    s.add_argument("--json", action="store_true")
    i = sub.add_parser("inbox", help="what waits for you")
    i.add_argument("--next", action="store_true", help="the one offer worth making now")
    i.add_argument("--shown", type=int, default=0, help="record that offer ID was shown")
    for name, help_ in (("start", "hand a planned task to a background worker"),
                        ("accept", "accept reviewed work as done"), ("pause", "pause a task"),
                        ("resume", "queue a paused or stopped task again"),
                        ("cancel", "cancel a task for good"), ("review", "show a task's work"),
                        ("handback", "give a task you left back to its agent"),
                        ("merge", "merge a done task's branch locally"),
                        ("pr", "push a task's branch and open a pull request")):
        sub.add_parser(name, help=help_).add_argument("task", type=str.upper)
    for name in WITH_TEXT:
        p = sub.add_parser(name)
        p.add_argument("task", type=str.upper)
        p.add_argument("text", nargs="+")
    p = sub.add_parser("finish", help="accept and merge a task now or when its worker is done")
    p.add_argument("task", type=str.upper)
    p.add_argument("--link", default="", help="the published link to record first")
    p = sub.add_parser("journal", help="the `ot` commands run lately (for tips)")
    p.add_argument("--days", type=int, default=7)
    p = sub.add_parser("tip", help="record a tip offered to the owner and their answer")
    p.add_argument("action", choices=["offered", "yes", "no"])
    p.add_argument("key")
    p.add_argument("text", nargs="*")
    sub.add_parser("tips", help="turn tips on or off").add_argument("state", choices=["on", "off"])
    p = sub.add_parser("prefs", help="the owner's preferences")
    p.add_argument("action", nargs="?", choices=["remove"])
    p.add_argument("n", nargs="?", type=int, default=0)
    p = sub.add_parser("priority")
    p.add_argument("task", type=str.upper)
    p.add_argument("priority", choices=PRIORITIES)
    p = sub.add_parser("after", help="make a task wait for another")
    p.add_argument("task", type=str.upper)
    p.add_argument("dep", type=str.upper)
    p = sub.add_parser("later", help="postpone what a task wants from you")
    p.add_argument("task", type=str.upper)
    p.add_argument("when", nargs="*")
    sub.add_parser("open", help="switch into a task's own session in a new tab").add_argument(
        "ref", nargs="+")
    sub.add_parser("session", help="run a task's session here (used by `ot open`)").add_argument(
        "task", type=str.upper)
    sub.add_parser("wait", help="wait for news for the orchestrator (Claude, in the background)")
    sub.add_parser("look", help="has the owner seen a task's latest work, and what can they review").add_argument(
        "task", type=str.upper)
    p = sub.add_parser("show", help="open what the owner picked to review")
    p.add_argument("task", type=str.upper)
    p.add_argument("items", nargs="+", type=str.lower)
    sub.add_parser("hook", help="agent hook (used by the orchestrator's settings)").add_argument(
        "event", choices=["prompt"])
    sub.add_parser("install", help="put `ot` on your PATH").add_argument(
        "--dir", default="~/.local/bin")
    sub.add_parser("me", help="who you are; every agent becomes one of your people").add_argument("text", nargs="*")
    c = sub.add_parser("cast", help="your people (the orchestrator builds them)")
    c.add_argument("action", nargs="?", choices=["set", "add"])
    c.add_argument("json", nargs="?", default="")
    c = sub.add_parser("config")
    c.add_argument("action", choices=["set"])
    c.add_argument("key", choices=CONFIG_KEYS)
    c.add_argument("value")
    sub.add_parser("engine", help="run the background engine").add_argument(
        "action", nargs="?", default="run", choices=["run", "stop"])
    cl = sub.add_parser("clean", help="remove finished tasks' worktrees, merged branches and logs")
    cl.add_argument("task", nargs="?", default="")
    cl.add_argument("--keep", action="store_true", help="keep this task's dirty worktree")
    return ap


def dispatch(ctx, a):
    if a.cmd in SIMPLE:
        return ctx.op(SIMPLE[a.cmd], task=a.task)
    if a.cmd in WITH_TEXT:
        return ctx.op(WITH_TEXT[a.cmd], task=a.task, text=" ".join(a.text))
    if a.cmd == "priority":
        return ctx.op("set_priority", task=a.task, priority=a.priority)
    if a.cmd == "after":
        return ctx.op("add_dependency", task=a.task, depends_on=[a.dep])
    return {"repo": cmd_repo, "add": cmd_add, "status": cmd_status, "inbox": cmd_inbox,
            "later": cmd_later, "review": cmd_review, "handback": cmd_handback,
            "merge": cmd_merge, "finish": cmd_finish, "journal": cmd_journal,
            "tip": cmd_tip, "tips": cmd_tips, "prefs": cmd_prefs, "pr": cmd_pr, "open": cmd_open, "session": cmd_session,
            "look": cmd_look, "show": cmd_show, "hook": cmd_hook, "install": cmd_install, "me": cmd_me, "cast": cmd_cast,
            "config": cmd_config, "engine": cmd_engine, "clean": cmd_clean, "wait": cmd_wait}[a.cmd](ctx, a)


def _exec(argv, cwd, env):
    os.chdir(cwd)
    try:
        os.execvpe(argv[0], argv, env)
    except OSError as exc:
        print(f"ot: could not start {argv[0]}: {exc}", file=sys.stderr)
        return 1


LIMITS = {"claude": usage.claude_limits, "codex": usage.codex_limits}


def _lobby_tty():
    try:
        return os.ttyname(sys.stdin.fileno()) if sys.stdin.isatty() else ""
    except (OSError, ValueError):
        return ""


def start(argv, home, ensure, ask, launch, limits):
    """`ot`, `ot claude`, `ot codex` [--new]: pick and open the orchestrator."""
    new = "--new" in argv
    named = next((a for a in argv if a in ("claude", "codex")), "")
    store = Store(os.path.join(home, "state.db"))
    try:
        running = len(store.list_tasks({"running"}))
        print(f"onethread · {running} running · {len(store.open_attention())} waiting for you\n")
        now = time.time()
        print(f"  1  {usage.line('Claude', limits['claude'](), now)}")
        print(f"  2  {usage.line('Codex', limits['codex'](), now)}\n")
        choice = named
        if not choice:
            last = "2" if store.get_meta("orchestrator", "claude") == "codex" else "1"
            try:
                answer = ask(f"Start the orchestrator with [{last}]: ").strip().lower() or last
            except EOFError:                # no terminal to answer from: the default
                answer = last
            choice = {"1": "claude", "2": "codex", "claude": "claude", "codex": "codex"}.get(answer)
            if not choice:
                print("ot: pick 1 (Claude) or 2 (Codex)", file=sys.stderr)
                return 1
        store.set_meta("orchestrator", choice)
        if store.get_meta("me") is None and store.get_meta("character") is None:
            try:
                who = ask("Who are you? Anyone at all: a king, a Roman emperor, Batman, "
                          "Spider-Man… (Enter for plain): ")
            except EOFError:                # no terminal to answer from
                who = ""
            cast.set_me(store, who)
        # Set by the orchestrator's first `ot` command (main), so a launch that
        # failed never leaves a conversation to continue that doesn't exist.
        resume = not new and store.get_meta(f"lobby_used:{choice}", "") == "1"
        me = os.getpid()                    # exec keeps the pid: the engine stays while it lives
        lobby_pid = f"{me}:{process_start(me)}"
        store.set_meta("lobby_pid", lobby_pid)
        store.set_meta("lobby_tty", _lobby_tty())      # where banners for the owner go
        tmux = os.environ.get("TMUX", "")
        term = os.environ.get("TERM_PROGRAM", "")
        if tmux and os.environ.get("LC_TERMINAL") == "iTerm2":
            term = "iTerm.app"              # tmux inside iTerm2: banners go to its tab
        store.set_meta("lobby_term", term)
        store.set_meta("lobby_tmux", tmux)
        store.set_meta("lobby_pane", os.environ.get("TMUX_PANE", "") if tmux else "")
        notify.reset_wait(store)
        ensure(home)
        path = lobby.prepare(home, choice)
        agent = make_agents(store)[choice]
        cmd = agent.lobby_argv(resume=resume, settings_path=None,
                               persona=cast.orchestrator_persona(store))
        if choice == "codex":
            cmd += lobby.codex_options(home)
    finally:
        store.close()
    env = dict(os.environ, ONETHREAD_HOME=home, ONETHREAD_LOBBY="1", ONETHREAD_LOBBY_PID=lobby_pid,
               PATH=engine.ROOT + os.pathsep + os.environ.get("PATH", ""))
    return launch(cmd, path, env) or 0


def main(argv=None, home=None, confirm=None, ensure=None, open_tab=None, run_session=None,
         ask=input, launch=None, limits=None, open_path=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if not argv or argv[0] in ("claude", "codex", "--new"):
        return start(argv, home or home_dir(), ensure or engine.ensure_running, ask,
                     launch or _exec, limits or LIMITS)
    args = parser().parse_args(argv)
    if args.cmd == "hook":                  # must never fail or block the owner's message
        try:
            home = home or home_dir()
            store = Store(os.path.join(home, "state.db"), timeout=1)
            try:
                return cmd_hook(Ctx(store, home, None, ensure or engine.ensure_running), args)
            finally:
                store.close()
        except Exception:
            print("{}")
            return 0
    home = home or home_dir()
    store = Store(os.path.join(home, "state.db"))
    task = getattr(args, "task", None)
    tips.record(store, argv, task=task if isinstance(task, str) and store.get_task(task) else None)
    ctx = Ctx(store, home, confirm or make_confirm(), ensure or engine.ensure_running,
              open_tab or terminal.open_tab, run_session, open_path)
    lobby = os.environ.get("ONETHREAD_LOBBY_PID", "")
    if lobby and lobby == store.get_meta("lobby_pid", ""):     # the orchestrator is talking
        store.set_meta(f"lobby_used:{store.get_meta('orchestrator', 'claude')}", "1")
    try:
        code = dispatch(ctx, args)
        if (args.cmd not in tips.SKIP and isinstance(task, str) and store.get_task(task)):
            notify.seen(store, task)        # the chat is on it: no banner for it
        return code
    except (ValueError, OSError, sqlite3.Error) as exc:   # one plain line, never a traceback
        print(f"ot: {exc}", file=sys.stderr)
        return 1
    finally:
        ctx.close()
        store.close()


if __name__ == "__main__":
    sys.exit(main())
