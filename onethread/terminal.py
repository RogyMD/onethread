"""Putting the owner into a task: find the task they mean, and open a new
iTerm2 tab running its session."""
import os
import re
import subprocess

STOPWORDS = {"the", "a", "an", "task", "tasks", "in", "about", "to", "on", "of", "for",
             "switch", "lets", "let", "me", "talk", "with", "agent", "one", "that", "this",
             "go", "open", "now", "please", "and", "repo"}

ITERM = ['-e', 'on run argv',
         '-e', 'tell application "iTerm2"',
         '-e', 'activate',
         '-e', 'if (count of windows) = 0 then',
         '-e', 'create window with default profile',
         '-e', 'else',
         '-e', 'tell current window to create tab with default profile',
         '-e', 'end if',
         '-e', 'tell current session of current window to write text (item 1 of argv)',
         '-e', 'end tell',
         '-e', 'end run']


def resolve(store, ref):
    """The task `ref` names: an id like T4, or words matched against repo,
    title and description. Returns the Task, or a list of candidates
    (empty when nothing matches, several when it's ambiguous)."""
    ref = ref.strip()
    if re.fullmatch(r"[Tt]\d+", ref):
        task = store.get_task(ref)
        return task if task else []
    words = [w for w in re.findall(r"[^\W_]+", ref.casefold()) if w not in STOPWORDS]
    scored = []
    for t in store.list_tasks():
        if t.status in ("done", "cancelled"):
            continue
        hay = re.findall(r"[^\W_]+", f"{t.cast_name} {t.repo} {t.title} {t.description}".casefold())
        score = sum(any(h == w or (len(w) >= 3 and h.startswith(w)) for h in hay) for w in words)
        if score:
            scored.append((score, t))
    if not scored:
        return []
    full = [t for s, t in scored if s == len(words)]     # every word matched
    if len(full) == 1:
        return full[0]
    if full:
        return full
    best = max(s for s, _ in scored)
    return [t for s, t in scored if s == best]            # close, but not a clear match


def iterm_installed():
    return any(os.path.isdir(os.path.join(folder, "iTerm.app"))
               for folder in ("/Applications", os.path.expanduser("~/Applications")))


def open_tab(command, run=subprocess.run, installed=iterm_installed):
    """Run `command` in a new iTerm2 tab. False if that isn't possible. Without
    iTerm2, AppleScript would ask the owner where it is: never try."""
    if not installed():
        return False
    try:
        res = run(["osascript", *ITERM, command], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return res.returncode == 0
