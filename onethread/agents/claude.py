"""Claude Code adapter: `claude -p --output-format json --json-schema`.
We choose the session id up front (--session-id), so it is stored before the
process starts and a crash can never lose it."""
import json
import uuid

from .base import GIT_TOOLS, Agent, Invocation, loads

READ_TOOLS = ["Read", "Grep", "Glob"]
EDIT_TOOLS = ["Read", "Edit", "Write", "Grep", "Glob"]


class ClaudeAgent(Agent):
    name = "claude"
    label = "Claude"

    def _model(self):
        return ["--model", self.model] if self.model else []

    def invocation(self, *, mode, cwd, prompt, session_id, schema_path,
                   out_path, extra_dirs, test_cmd="", leased=()):
        from .. import schemas
        schema = schemas.RESULT if mode == "work" else schemas.REPLY
        argv = ["claude", "-p", "--output-format", "json",
                "--json-schema", json.dumps(schema),
                "--permission-prompts", "none", *self._model()]
        if session_id:
            argv += ["--resume", session_id]
        else:
            session_id = str(uuid.uuid4())
            argv += ["--session-id", session_id]
        env = {}
        if mode == "work":
            tools = EDIT_TOOLS + [f"Bash({c}:*)" for c in GIT_TOOLS]
            # Exact leased patterns only (`npm run build`, not all of `npm`). Never
            # `ot-lease`: it runs any command it is given.
            tools += [f"Bash({c}:*)" for c in leased if "/" not in c]
            if test_cmd:
                tools.append(f"Bash({test_cmd}:*)")
            # No --add-dir: lease stand-ins are subprocesses, not Claude file tools.
            argv += ["--permission-mode", "acceptEdits", "--allowedTools", *tools]
            # Lease waits block a command; give them room before Bash times out.
            env = {"BASH_DEFAULT_TIMEOUT_MS": "600000", "BASH_MAX_TIMEOUT_MS": "900000"}
        else:
            argv += ["--permission-mode", "dontAsk", "--allowedTools", *READ_TOOLS]
        return Invocation(argv, cwd, prompt, session_id, env, mode=mode)

    def parse(self, log_text, out_text):
        text = (log_text or "").strip()
        data = loads(text) or (loads(text.splitlines()[-1]) if text else None)
        if not data:
            return None, None
        payload = data.get("structured_output")
        if not isinstance(payload, dict):
            payload = loads(data.get("result", ""))
        return data.get("session_id"), payload

    def _explain(self, raw):
        text = raw.strip()
        data = loads(text) or (loads(text.splitlines()[-1]) if text else None)
        if data and data.get("is_error"):
            return f"Claude failed: {data.get('result') or data.get('subtype') or 'unknown error'}"
        lines = [line for line in text.splitlines() if line.strip() and not loads(line)]
        return f"Claude failed: {lines[-1].strip()[:300]}" if lines else ""

    def interactive_argv(self, session_id, prompt, persona, new=False):
        argv = ["claude", "--session-id" if new else "--resume", session_id, *self._model()]
        if persona:
            argv += ["--append-system-prompt", persona]
        return argv + [prompt]

    def lobby_argv(self, resume, persona, settings_path):
        argv = ["claude"] + (["--continue"] if resume else []) + self._model()
        if persona:
            argv += ["--append-system-prompt", persona]
        if settings_path:
            argv += ["--settings", settings_path]
        return argv

