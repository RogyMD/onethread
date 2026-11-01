"""How much of each agent's usage limit is left, from the providers' own
numbers. Claude reports limits only to its status line, which agent-deck
caches; Codex answers through its app server. Neither reads any
conversation. Unknown is a fine answer."""
import json
import os
import queue
import subprocess
import threading
import time

from .voice import ago

STALE = 3600


def claude_limits(run=subprocess.run):
    try:
        res = run(["agent-deck", "usage", "--json"], capture_output=True, text=True, timeout=10,
                  env=dict(os.environ, AGENTDECK_SUPPRESS_TMUX_WARNING="1"))
        data = json.loads(res.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    for provider in data.get("providers", []):
        if provider.get("id") == "claude":
            used = {w.get("kind"): w.get("used_percentage") for w in provider.get("windows", [])}
            if used.get("five_hour") is None:
                return None
            return {"5h": used["five_hour"], "week": used.get("seven_day"),
                    "updated": provider.get("updated_at") or 0}
    return None


def codex_limits(popen=subprocess.Popen, timeout=10, now=time.time):
    try:
        proc = popen(["codex", "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                     stderr=subprocess.DEVNULL, text=True)
    except OSError:
        return None
    lines = queue.Queue()
    reader = threading.Thread(target=lambda: [lines.put(line) for line in iter(proc.stdout.readline, "")],
                              daemon=True)
    try:
        for msg in ({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                     "params": {"clientInfo": {"name": "onethread", "version": "1"}}},
                    {"jsonrpc": "2.0", "method": "initialized"},
                    {"jsonrpc": "2.0", "id": 2, "method": "account/rateLimits/read"}):
            proc.stdin.write(json.dumps(msg) + "\n")
            proc.stdin.flush()
        reader.start()
        end = time.time() + timeout
        while time.time() < end:
            try:
                msg = json.loads(lines.get(timeout=max(0.01, end - time.time())))
            except (queue.Empty, ValueError):
                continue
            if msg.get("id") == 2:
                limits = (msg.get("result") or {}).get("rateLimits") or {}
                primary, secondary = limits.get("primary") or {}, limits.get("secondary") or {}
                if primary.get("usedPercent") is None:
                    return None
                return {"5h": primary["usedPercent"], "week": secondary.get("usedPercent"),
                        "updated": now()}
        return None
    except OSError:
        return None
    finally:
        proc.kill()
        proc.wait(timeout=5)


def _left(used):
    return "?" if used is None else f"{max(0, 100 - round(used))}%"


def line(name, limits, now):
    if not limits:
        return f"{name:<8} limits unknown"
    text = f"{name:<8} 5h: {_left(limits['5h'])} left · week: {_left(limits.get('week'))} left"
    updated = limits.get("updated")
    age = 0 if updated is None else now - updated
    return text + (f" ({ago(age)} old)" if age > STALE else "")
