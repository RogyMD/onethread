"""Yes/no questions for destructive steps, answered by the owner only.

On a terminal the owner types y/N. Without one (an agent ran the command)
a native macOS dialog asks, so a model can never answer for the owner.
Anything unexpected counts as "no".
"""
import os
import subprocess
import sys

SCRIPT = ['-e', 'on run argv',
          '-e', 'display dialog (item 1 of argv) buttons {"No", "Yes"} default button "No" '
                'with title "onethread"',
          '-e', 'end run']


def _dialog(question, run=subprocess.run):
    try:
        res = run(["osascript", *SCRIPT, question], capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.SubprocessError):
        return False
    return res.returncode == 0 and "button returned:Yes" in (res.stdout or "")


def make_confirm(isatty=sys.stdin.isatty, ask=input, dialog=_dialog):
    def confirm(question):
        # Inside the orchestrator a terminal may belong to the model (Codex
        # can type into commands), so only the dialog counts there.
        if os.environ.get("ONETHREAD_LOBBY") or not isatty():
            return dialog(question)
        try:
            return ask(f"{question} [y/N] ").strip().lower() in ("y", "yes")
        except EOFError:
            return False
    return confirm
