import io
import json
import subprocess
import unittest

from onethread import usage

AGENT_DECK = {"providers": [{"id": "claude", "label": "Claude", "updated_at": 1000, "windows": [
    {"kind": "five_hour", "label": "5h", "used_percentage": 45, "resets_at": 2000},
    {"kind": "seven_day", "label": "7d", "used_percentage": 6, "resets_at": 9000}]}]}


class FakeAppServer:
    """Speaks the recorded shape of `codex app-server` (synthetic values)."""

    def __init__(self, argv, **kw):
        assert argv == ["codex", "app-server"], argv
        self.sent = []
        self.stdin = self
        self.stdout = io.StringIO()

    def write(self, text):
        msg = json.loads(text)
        self.sent.append(msg)
        if msg.get("method") == "account/rateLimits/read":
            self.stdout = io.StringIO(
                json.dumps({"method": "remoteControl/status/changed", "params": {}}) + "\n"
                + json.dumps({"id": msg["id"], "result": {"rateLimits": {
                    "primary": {"usedPercent": 12, "windowDurationMins": 300},
                    "secondary": {"usedPercent": 30, "windowDurationMins": 10080}}}}) + "\n")

    def flush(self):
        pass

    def kill(self):
        pass

    def wait(self, timeout=None):
        return 0


class UsageTest(unittest.TestCase):
    def test_claude_limits_from_agent_deck_json(self):
        def run(argv, **kw):
            self.assertEqual(argv, ["agent-deck", "usage", "--json"])
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(AGENT_DECK))
        self.assertEqual(usage.claude_limits(run=run), {"5h": 45, "week": 6, "updated": 1000})

        def missing(argv, **kw):
            raise FileNotFoundError("agent-deck")
        self.assertIsNone(usage.claude_limits(run=missing))

    def test_codex_limits_from_app_server(self):
        limits = usage.codex_limits(popen=FakeAppServer, now=lambda: 5000)
        self.assertEqual(limits, {"5h": 12, "week": 30, "updated": 5000})

        def missing(argv, **kw):
            raise FileNotFoundError("codex")
        self.assertIsNone(usage.codex_limits(popen=missing))

    def test_usage_line_unknown_and_stale(self):
        self.assertEqual(usage.line("Claude", {"5h": 45, "week": 6, "updated": 1000}, now=1500),
                         "Claude   5h: 55% left · week: 94% left")
        self.assertEqual(usage.line("Codex", None, now=0), "Codex    limits unknown")
        self.assertIn("(2 h old)", usage.line("Claude", {"5h": 45, "week": 6, "updated": 0}, now=7300))
