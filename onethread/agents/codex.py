"""Codex CLI adapter: `codex exec --json` for new sessions, `codex exec
resume` for follow-ups. `exec resume` has no -s/-C/--add-dir flags, so the
sandbox is set with -c config overrides in both cases."""
import json

from .base import Agent, Invocation, loads

SANDBOX = {"lobby": "read-only", "plan": "read-only", "work": "workspace-write"}


def _toml(text):
    return json.dumps(text)         # a JSON string is a valid TOML basic string


class CodexAgent(Agent):
    name = "codex"
    label = "Codex"
    presets_session_id = False

    def _config(self, persona=""):
        opts = ["-c", f"model={_toml(self.model)}"] if self.model else []
        if persona:
            opts += ["-c", f"developer_instructions={_toml(persona)}"]
        return opts

    def invocation(self, *, mode, cwd, prompt, session_id, schema_path,
                   out_path, extra_dirs, test_cmd="", leased=()):
        opts = ["--json", "--skip-git-repo-check", "--output-schema", schema_path,
                "-o", out_path, "-c", f'sandbox_mode="{SANDBOX[mode]}"', *self._config()]
        if mode == "work" and extra_dirs:
            opts += ["-c", "sandbox_workspace_write.writable_roots="
                     + json.dumps(list(extra_dirs))]
        if session_id:
            argv = ["codex", "exec", "resume", *opts, session_id, "-"]
        else:
            argv = ["codex", "exec", *opts, "-"]
        return Invocation(argv, cwd, prompt, session_id or None,
                          out_path=out_path, mode=mode)

    def parse(self, log_text, out_text):
        sid = None
        last_message = ""
        for line in (log_text or "").splitlines():
            event = loads(line)
            if not event:
                continue
            if event.get("type") == "thread.started":
                sid = event.get("thread_id")
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") == "agent_message":
                last_message = item.get("text", "")
        return sid, loads(out_text) or loads(last_message)

    def _explain(self, raw):
        message = ""
        for line in raw.splitlines():
            event = loads(line)
            if not event:
                continue
            if event.get("type") == "turn.failed":
                message = (event.get("error") or {}).get("message", "")
            elif event.get("type") == "error":
                message = event.get("message", "")
        inner = loads(message)              # the provider's error, wrapped as JSON text
        if inner:
            message = (inner.get("error") or {}).get("message") or message
        return f"Codex rejected the request: {message}" if message else ""

    def interactive_argv(self, session_id, prompt, persona, new=False):
        if new:
            raise ValueError("Codex can't start an interactive session with a chosen id")
        return ["codex", "resume", session_id, *self._config(persona), prompt]

    def lobby_argv(self, resume, persona, settings_path):
        head = ["codex", "resume", "--last"] if resume else ["codex"]
        return head + self._config(persona)

