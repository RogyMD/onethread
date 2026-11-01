"""A deterministic stand-in agent for tests and the demo. It never calls a
model. Conversation turns come from a `responder` function; worker runs are a
real detached process (fake_worker.py) that commits a file and reports.

Worker behaviour can be steered from the task text: [fake:needs_input],
[fake:fail], [fake:crash], [fake:noresult], [fake:nocommit],
[fake:nochange], [fake:lost], [fake:delay=SECONDS]."""
import os
import sys
import uuid

from .base import Agent, Invocation, loads

WORKER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake_worker.py")


def default_responder(prompt, mode, session_id):
    return {"reply": "(fake agent) Noted.", "operations": [], "boundary": False,
            "resources": []}


class FakeAgent(Agent):
    name = "fake"
    label = "Fake agent"

    def __init__(self, responder=None, delay=0.2):
        super().__init__()
        self.responder = responder or default_responder
        self.delay = delay

    def invocation(self, *, mode, cwd, prompt, session_id, schema_path,
                   out_path, extra_dirs, test_cmd="", leased=()):
        sid = session_id or f"fake-{uuid.uuid4().hex[:8]}"
        argv = [sys.executable, "-I", WORKER, "--out", out_path,
                "--session", sid, "--delay", str(self.delay)]
        return Invocation(argv, cwd, prompt, sid, out_path=out_path, mode=mode)

    def run_turn(self, inv, timeout=1800):
        payload = self.responder(inv.stdin, inv.mode, inv.session_id)
        return inv.session_id, payload, ""

    def parse(self, log_text, out_text):
        sid = None
        for line in (log_text or "").splitlines():
            event = loads(line)
            if event and event.get("type") == "thread.started":
                sid = event["thread_id"]
        return sid, loads(out_text)

    def interactive_argv(self, session_id, prompt, persona, new=False):
        return ["sh", "-c", f"echo 'fake session {session_id}'"]

    def lobby_argv(self, resume, persona, settings_path):
        return ["sh", "-c", "echo 'fake orchestrator'"]

