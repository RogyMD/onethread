"""Fake worker process: reads the prompt on stdin, waits, commits a file in
the current directory and writes a result JSON. Used by tests and the demo."""
import argparse
import json
import re
import subprocess
import sys
import time


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--session", required=True)
    ap.add_argument("--delay", type=float, default=0.2)
    args = ap.parse_args()
    prompt = sys.stdin.read()
    print(json.dumps({"type": "thread.started", "thread_id": args.session}), flush=True)
    m = re.search(r"\[fake:delay=([\d.]+)\]", prompt)
    time.sleep(float(m.group(1)) if m else args.delay)
    if "[fake:crash]" in prompt:
        sys.exit(1)
    if "[fake:lost]" in prompt:
        print(f"No conversation found with session ID: {args.session}", file=sys.stderr)
        sys.exit(1)
    if "[fake:noresult]" in prompt:
        sys.exit(0)
    task = (re.search(r"\b(T\d+)\b", prompt) or [None, "task"])[1]
    result = {"status": "done", "summary": f"Fake work for {task} committed.",
              "question": "", "tests": "fake: 1 passed", "commits": []}
    if "[fake:needs_input]" in prompt:
        result.update(status="needs_input", summary="Need a decision.",
                      question="Which option should I use?")
    elif "[fake:fail]" in prompt:
        result.update(status="failed", summary="Tests failed: 1 failing.")
    elif "[fake:nochange]" in prompt:
        pass
    elif "[fake:nocommit]" in prompt:          # e.g. the sandbox blocked git commit
        with open(f"ot-fake-{task}.txt", "a") as f:
            f.write(f"uncommitted work for {task}\n")
    else:
        with open(f"ot-fake-{task}.txt", "a") as f:
            f.write(f"work for {task}\n")
        subprocess.run(["git", "add", "."], check=True)
        subprocess.run(["git", "-c", "user.name=onethread-fake",
                        "-c", "user.email=fake@example.invalid",
                        "commit", "-q", "-m", f"fake: work for {task}"], check=True)
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], check=True,
                             capture_output=True, text=True).stdout.strip()
        result["commits"] = [sha]
    with open(args.out, "w") as f:
        json.dump(result, f)
    print(json.dumps({"type": "turn.completed"}), flush=True)


if __name__ == "__main__":
    main()
