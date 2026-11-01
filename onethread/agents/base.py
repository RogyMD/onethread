"""What every agent adapter provides. One file per agent keeps CLI details
(flags, output formats) in one place when those tools change."""
import json
import os
import subprocess
from dataclasses import dataclass, field

# Commands a worker may always run. Costly ones go through lease shims.
GIT_TOOLS = ("git add", "git commit", "git status", "git diff", "git log",
             "git restore", "git mv", "git rm")


@dataclass
class Invocation:
    argv: list
    cwd: str
    stdin: str
    session_id: str = None        # known before launch (Claude) or None
    env: dict = field(default_factory=dict)
    out_path: str = None          # where the final message lands, if a file
    mode: str = "plan"


class Agent:
    name = "base"
    label = "Agent"
    presets_session_id = True       # can a new session be started with an id we choose?

    def __init__(self, model=""):
        self.model = model          # "" = the CLI's own default

    def explain(self, raw, log_path=""):
        """One plain line saying why a turn failed, never a raw log dump."""
        line = self._explain(raw or "")
        if line:
            return " ".join(line.split())
        return f"{self.label} gave no readable reply" + (f" (log: {log_path})" if log_path else "")

    def _explain(self, raw):
        return ""

    def lost_session(self, raw):
        """True when a resumed turn failed because its session never existed."""
        return "No conversation found" in (raw or "")

    def interactive_argv(self, session_id, prompt, persona, new=False):
        """The agent's own interactive session, opened in a terminal tab."""
        raise NotImplementedError

    def lobby_argv(self, resume, persona, settings_path):
        """The orchestrator's interactive session, started by `ot`."""
        raise NotImplementedError

    def invocation(self, *, mode, cwd, prompt, session_id, schema_path,
                   out_path, extra_dirs, test_cmd="", leased=()):
        raise NotImplementedError

    def parse(self, log_text, out_text):
        """(session_id or None, payload dict or None) from a finished turn."""
        raise NotImplementedError


    def run_turn(self, inv, timeout=1800):
        """Run a conversation turn to completion (not detached)."""
        env = dict(os.environ, **inv.env)
        try:
            proc = subprocess.run(inv.argv, cwd=inv.cwd, input=inv.stdin, env=env,
                                  capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return inv.session_id, None, f"agent did not run: {exc}"
        out_text = _read(inv.out_path)
        sid, payload = self.parse(proc.stdout, out_text)
        raw = (proc.stdout[-2000:] + proc.stderr[-2000:]).strip()
        return sid or inv.session_id, payload, raw


def _read(path):
    if not path or not os.path.exists(path):
        return ""
    with open(path) as f:
        return f.read()


def loads(text):
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None
