"""Agent adapters by name."""
from .claude import ClaudeAgent
from .codex import CodexAgent
from .fake import FakeAgent


def get_agent(name, model=""):
    if name == "fake":
        return FakeAgent()
    return {"codex": CodexAgent, "claude": ClaudeAgent}[name](model=model)
