"""JSON shapes the agents must return, plus a tiny validator (stdlib only).

Both shapes follow OpenAI strict structured-output rules (every property
required, no extra properties) so the same schema works for Codex and Claude.
Unused string fields are sent as "".
"""
import json

OPS = ("add_task", "set_priority", "add_dependency", "approve_implementation",
       "pause", "resume", "cancel", "focus", "accept_offer", "postpone_offer",
       "review_accept", "request_changes", "answer")


def _obj(props):
    return {"type": "object", "properties": props, "required": list(props),
            "additionalProperties": False}


_S = {"type": "string"}
OPERATION = _obj({
    "op": {"type": "string", "enum": list(OPS)},
    "task": _S, "repo": _S, "title": _S, "description": _S,
    "priority": {"type": "string", "enum": ["", "urgent", "high", "normal", "low"]},
    "depends_on": {"type": "array", "items": _S},
    "when": _S,          # "", "tomorrow", or an ISO date/time
    "agent": {"type": "string", "enum": ["", "codex", "claude"]},
    "text": _S,          # feedback, answer, or note
})

REPLY = _obj({
    "reply": _S,
    "operations": {"type": "array", "items": OPERATION},
    "boundary": {"type": "boolean"},
    "resources": {"type": "array", "items": _S},
})

RESULT = _obj({
    "status": {"type": "string", "enum": ["done", "needs_input", "failed"]},
    "summary": _S,
    "question": _S,
    "tests": _S,
    "commits": {"type": "array", "items": _S},
})

_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool}


def validate(value, schema, path="$"):
    """Return a list of problems; empty means valid."""
    want = _TYPES[schema["type"]]
    if not isinstance(value, want) or (want is not bool and isinstance(value, bool)):
        return [f"{path}: expected {schema['type']}"]
    problems = []
    if "enum" in schema and value not in schema["enum"]:
        problems.append(f"{path}: {value!r} is not one of {schema['enum']}")
    if want is dict:
        for key in schema["required"]:
            if key not in value:
                problems.append(f"{path}.{key}: missing")
        for key in value:
            if key not in schema["properties"]:
                problems.append(f"{path}.{key}: not allowed")
            else:
                problems += validate(value[key], schema["properties"][key], f"{path}.{key}")
    if want is list:
        for i, item in enumerate(value):
            problems += validate(item, schema["items"], f"{path}[{i}]")
    return problems


def write_schema_files(folder):
    """Write reply.json and result.json for --output-schema. Returns paths."""
    paths = {}
    for name, schema in (("reply", REPLY), ("result", RESULT)):
        paths[name] = f"{folder}/{name}.schema.json"
        with open(paths[name], "w") as f:
            json.dump(schema, f)
    return paths
