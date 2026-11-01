import json
import os
import subprocess
import sys
import unittest

from onethread import schemas
from onethread.agents import get_agent
from onethread.agents.claude import ClaudeAgent
from onethread.agents.codex import CodexAgent
from tests.helpers import TempHomeCase, git, make_repo

DANGEROUS = ("--dangerously-bypass-approvals-and-sandbox", "bypassPermissions",
             "danger-full-access", "--dangerously-skip-permissions")

GOOD_RESULT = {"status": "done", "summary": "Added login.", "question": "",
               "tests": "3 passed", "commits": ["abc123"]}
GOOD_REPLY = {"reply": "Plan ready.", "boundary": True, "resources": [],
              "operations": [{"op": "approve_implementation", "task": "T1",
                              "repo": "", "title": "", "description": "",
                              "priority": "", "depends_on": [], "when": "",
                              "agent": "", "text": ""}]}


def inv(agent, mode, session_id="", **kw):
    args = dict(mode=mode, cwd="/w", prompt="hello", session_id=session_id,
                schema_path="/s.json", out_path="/o.json",
                extra_dirs=["/home/.onethread"], test_cmd="make test")
    args.update(kw)
    return agent.invocation(**args)


class CodexTest(unittest.TestCase):
    def test_new_planning_turn_is_read_only_json_with_schema(self):
        i = inv(CodexAgent(), "plan")
        self.assertEqual(i.argv[:2], ["codex", "exec"])
        self.assertIn("--json", i.argv)
        self.assertIn('sandbox_mode="read-only"', i.argv)
        self.assertEqual(i.argv[i.argv.index("--output-schema") + 1], "/s.json")
        self.assertEqual(i.argv[i.argv.index("-o") + 1], "/o.json")
        self.assertEqual(i.argv[-1], "-")
        self.assertEqual(i.stdin, "hello")
        self.assertIsNone(i.session_id)

    def test_resumed_work_turn_writes_only_workspace_and_lease_dir(self):
        i = inv(CodexAgent(), "work", session_id="thread-1")
        self.assertEqual(i.argv[:3], ["codex", "exec", "resume"])
        self.assertEqual(i.argv[-2:], ["thread-1", "-"])
        self.assertIn('sandbox_mode="workspace-write"', i.argv)
        self.assertIn('sandbox_workspace_write.writable_roots=["/home/.onethread"]', i.argv)
        self.assertEqual(i.session_id, "thread-1")

    def test_parse_reads_thread_id_and_final_message(self):
        log = "\n".join([json.dumps({"type": "thread.started", "thread_id": "th-9"}),
                         json.dumps({"type": "turn.completed"})])
        sid, payload = CodexAgent().parse(log, json.dumps(GOOD_RESULT))
        self.assertEqual((sid, payload), ("th-9", GOOD_RESULT))

    def test_parse_garbage_gives_no_payload(self):
        self.assertEqual(CodexAgent().parse("not json\n", "{oops"), (None, None))



class ClaudeTest(unittest.TestCase):
    def test_new_session_gets_a_known_uuid_before_launch(self):
        i = inv(ClaudeAgent(), "plan")
        sid = i.argv[i.argv.index("--session-id") + 1]
        self.assertEqual(i.session_id, sid)
        self.assertEqual(len(sid), 36)
        self.assertIn("--permission-prompts", i.argv)
        self.assertEqual(i.argv[i.argv.index("--permission-mode") + 1], "dontAsk")
        tools = i.argv[i.argv.index("--allowedTools") + 1:]
        self.assertNotIn("Edit", tools)

    def test_work_turn_resumes_and_allows_edits_git_tests_and_leases(self):
        i = inv(ClaudeAgent(), "work", session_id="uuid-1",
                leased=("xcodebuild", "xcrun simctl"))
        self.assertEqual(i.argv[i.argv.index("--resume") + 1], "uuid-1")
        self.assertEqual(i.argv[i.argv.index("--permission-mode") + 1], "acceptEdits")
        for tool in ("Edit", "Bash(git commit:*)", "Bash(make test:*)", "Bash(xcodebuild:*)"):
            self.assertIn(tool, i.argv)
        self.assertNotIn("Bash(ot-lease:*)", i.argv)        # it runs any command
        self.assertIn("BASH_MAX_TIMEOUT_MS", i.env)

    def test_plain_text_errors_are_explained(self):
        note = ClaudeAgent().explain("error: unknown option '--permission-prompts'\n")
        self.assertEqual(note, "Claude failed: error: unknown option '--permission-prompts'")

    def test_lost_session_is_recognised(self):
        self.assertTrue(ClaudeAgent().lost_session("No conversation found with session ID: u-1"))
        self.assertFalse(ClaudeAgent().lost_session("error: rate limited"))

    def test_parse_structured_output_or_json_result(self):
        out = json.dumps({"type": "result", "session_id": "u-2",
                          "structured_output": GOOD_RESULT})
        self.assertEqual(ClaudeAgent().parse(out, ""), ("u-2", GOOD_RESULT))
        out = json.dumps({"type": "result", "session_id": "u-2",
                          "result": json.dumps(GOOD_RESULT)})
        self.assertEqual(ClaudeAgent().parse(out, ""), ("u-2", GOOD_RESULT))



class SafetyTest(unittest.TestCase):
    def test_no_invocation_ever_bypasses_sandbox_or_permissions(self):
        for agent in (CodexAgent(), ClaudeAgent()):
            for mode in ("lobby", "plan", "work"):
                for sid in ("", "s-1"):
                    argv = " ".join(inv(agent, mode, session_id=sid).argv)
                    for bad in DANGEROUS:
                        self.assertNotIn(bad, argv)


class SchemaTest(unittest.TestCase):
    def test_valid_payloads_pass(self):
        self.assertEqual(schemas.validate(GOOD_RESULT, schemas.RESULT), [])
        self.assertEqual(schemas.validate(GOOD_REPLY, schemas.REPLY), [])

    def test_invalid_payloads_report_problems(self):
        self.assertTrue(schemas.validate({"status": "done"}, schemas.RESULT))
        bad = dict(GOOD_RESULT, status="finished")
        self.assertTrue(schemas.validate(bad, schemas.RESULT))
        reply = json.loads(json.dumps(GOOD_REPLY))
        reply["operations"][0]["op"] = "rm_rf"
        self.assertTrue(schemas.validate(reply, schemas.REPLY))
        self.assertTrue(schemas.validate("text", schemas.REPLY))


class FakeWorkerTest(TempHomeCase):
    def test_fake_worker_commits_and_writes_valid_result(self):
        repo = make_repo(self.tmp, "app-a")
        agent = get_agent("fake")
        out = os.path.join(self.tmp, "out.json")
        i = agent.invocation(mode="work", cwd=repo, prompt="Task T1: Add login",
                             session_id="", schema_path="", out_path=out,
                             extra_dirs=[], test_cmd="")
        log = subprocess.run(i.argv, cwd=repo, input=i.stdin, text=True,
                             capture_output=True, check=True).stdout
        with open(out) as f:
            sid, payload = agent.parse(log, f.read())
        self.assertTrue(sid)
        self.assertEqual(payload["status"], "done")
        self.assertEqual(schemas.validate(payload, schemas.RESULT), [])
        self.assertIn(payload["commits"][0], git(repo, "log", "--format=%h"))


class SandboxWidthTest(unittest.TestCase):
    def test_claude_worker_gets_no_extra_dirs_and_only_exact_leased_commands(self):
        i = ClaudeAgent().invocation(mode="work", cwd="/w", prompt="p", session_id="",
                                     schema_path="", out_path="", extra_dirs=["/home/leases"],
                                     test_cmd="", leased=["xcodebuild", "npm run build"])
        self.assertNotIn("--add-dir", i.argv)
        self.assertIn("Bash(npm run build:*)", i.argv)
        self.assertNotIn("Bash(npm:*)", i.argv)

    def test_codex_worker_can_write_only_the_lease_dir(self):
        i = CodexAgent().invocation(mode="work", cwd="/w", prompt="p", session_id="",
                                    schema_path="/s", out_path="/o", extra_dirs=["/h/leases"])
        self.assertIn('sandbox_workspace_write.writable_roots=["/h/leases"]', i.argv)

# Synthetic copy of what Codex printed for the owner when its configured
# model was not available on their account.
CODEX_MODEL_ERROR = "\n".join([
    '{"type":"thread.started","thread_id":"01a1-synthetic"}',
    '{"type":"item.completed","item":{"id":"item_0","type":"error","message":"Model metadata for `gpt-6-luna` not found."}}',
    '{"type":"turn.started"}',
    '{"type":"error","message":"{\\"type\\":\\"error\\",\\"status\\":400,\\"error\\":{\\"type\\":\\"invalid_request_error\\",\\"message\\":\\"The \'gpt-6-luna\' model is not supported when using Codex with a ChatGPT account.\\"}}"}',
    '{"type":"turn.failed","error":{"message":"{\\"type\\":\\"error\\",\\"status\\":400,\\"error\\":{\\"type\\":\\"invalid_request_error\\",\\"message\\":\\"The \'gpt-6-luna\' model is not supported when using Codex with a ChatGPT account.\\"}}"}}',
    "2026-10-20T23:28:14Z ERROR codex_models_manager::manager: failed to load models cache",
])


class ModelAndErrorsTest(unittest.TestCase):
    def test_codex_model_flag(self):
        self.assertNotIn('model="gpt-5"', inv(CodexAgent(), "plan").argv)
        i = inv(get_agent("codex", model="gpt-5"), "work", session_id="th")
        self.assertIn('model="gpt-5"', i.argv)
        self.assertEqual(i.argv[i.argv.index('model="gpt-5"') - 1], "-c")

    def test_claude_model_flag(self):
        self.assertNotIn("--model", inv(ClaudeAgent(), "plan").argv)
        i = inv(get_agent("claude", model="opus"), "plan")
        self.assertEqual(i.argv[i.argv.index("--model") + 1], "opus")

    def test_codex_explain_model_rejected(self):
        line = CodexAgent().explain(CODEX_MODEL_ERROR)
        self.assertIn("model is not supported", line)
        self.assertTrue(line.startswith("Codex rejected the request:"), line)
        self.assertNotIn('{"type"', line)
        self.assertNotIn("\n", line)

    def test_claude_explain_error_result(self):
        raw = json.dumps({"type": "result", "is_error": True, "result": "Credit balance is too low"})
        self.assertEqual(ClaudeAgent().explain(raw), "Claude failed: Credit balance is too low")

    def test_explain_fallback_names_the_log(self):
        self.assertEqual(CodexAgent().explain("garbage", log_path="/r/log.jsonl"),
                         "Codex gave no readable reply (log: /r/log.jsonl)")


class InteractiveArgvTest(unittest.TestCase):
    def test_claude_interactive_resume_with_persona_and_prompt(self):
        argv = ClaudeAgent().interactive_argv("uuid-1", "Greet me", "Speak as a king.")
        self.assertEqual(argv, ["claude", "--resume", "uuid-1",
                                "--append-system-prompt", "Speak as a king.", "Greet me"])
        self.assertEqual(ClaudeAgent().interactive_argv("uuid-1", "Hi", ""),
                         ["claude", "--resume", "uuid-1", "Hi"])
        new = ClaudeAgent().interactive_argv("uuid-2", "Hi", "", new=True)
        self.assertEqual(new[:3], ["claude", "--session-id", "uuid-2"])

    def test_codex_interactive_resume_with_persona_and_prompt(self):
        argv = get_agent("codex", model="gpt-5").interactive_argv("th-1", "Greet me", "Speak as a king.")
        self.assertEqual(argv[:3], ["codex", "resume", "th-1"])
        self.assertIn('developer_instructions="Speak as a king."', argv)
        self.assertIn('model="gpt-5"', argv)
        self.assertEqual(argv[-1], "Greet me")

    def test_lobby_argv_resume_and_new(self):
        c = ClaudeAgent()
        self.assertEqual(c.lobby_argv(resume=True, persona="", settings_path="/l/s.json"),
                         ["claude", "--continue", "--settings", "/l/s.json"])
        self.assertEqual(c.lobby_argv(resume=False, persona="P", settings_path=None),
                         ["claude", "--append-system-prompt", "P"])
        x = CodexAgent()
        self.assertEqual(x.lobby_argv(resume=True, persona="", settings_path=None),
                         ["codex", "resume", "--last"])
        self.assertEqual(x.lobby_argv(resume=False, persona="P", settings_path=None),
                         ["codex", "-c", 'developer_instructions="P"'])


if __name__ == "__main__":
    unittest.main()
