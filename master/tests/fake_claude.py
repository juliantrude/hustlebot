#!/usr/bin/env python3
"""A stand-in for `claude -p` that replays recorded JSON results, so the runner is tested without real sessions.

FAKE_CLAUDE_SCRIPT names a JSON file with a list of steps; call n plays step n. A step:

    {"result": "results/haiku-ok.json",  # recorded result, relative to the script file (or an inline object)
     "stream": "streams/haiku-tools.jsonl",  # or a recorded `stream-json` run, replayed line by line
     "text": "...",                      # replaces the result's "result" field, e.g. to carry a report block
     "run": "git commit ...",            # shell command run in the session's cwd first, to fake the work
     "sleep": 0.5, "exit": 0, "stderr": ""}

Every call is appended to calls.jsonl next to the script (argv, cwd, stdin), so tests can assert what the
runner passed. More calls than steps exit 3: a test must say how many sessions it expects.
"""

from __future__ import annotations

import fcntl
import json
import os
import select
import subprocess
import sys
import time
from pathlib import Path


def main() -> int:
    script = Path(os.environ["FAKE_CLAUDE_SCRIPT"])
    steps = json.loads(script.read_text())
    # the real claude waits for stdin unless it is closed; a short wait keeps an open pipe from hanging the test
    stdin = sys.stdin.read() if select.select([sys.stdin], [], [], 0.2)[0] else None
    with open(script.parent / "calls.jsonl", "a+") as log:
        # a lock, because the runner may start several sessions at once and each needs its own step
        fcntl.flock(log, fcntl.LOCK_EX)
        log.seek(0)
        n = sum(1 for _ in log)
        log.write(json.dumps({"argv": sys.argv[1:], "cwd": os.getcwd(), "stdin": stdin}) + "\n")
    if n >= len(steps):
        print(f"fake claude: call {n + 1}, but the script has only {len(steps)} steps", file=sys.stderr)
        return 3
    step = steps[n]
    if step.get("run"):
        subprocess.run(step["run"], shell=True, check=True, stdout=subprocess.DEVNULL)
    time.sleep(step.get("sleep", 0))
    if step.get("stream"):
        for line in (script.parent / step["stream"]).read_text().splitlines():
            event = json.loads(line)
            if event.get("type") == "result" and "text" in step:
                event["result"] = step["text"]
            print(json.dumps(event), flush=True)
    result = step.get("result")
    if isinstance(result, str):
        result = json.loads((script.parent / result).read_text())
    if result is not None:
        if "text" in step:
            result["result"] = step["text"]
        print(json.dumps(result))
    if step.get("stderr"):
        print(step["stderr"], file=sys.stderr)
    return step.get("exit", 0)


if __name__ == "__main__":
    sys.exit(main())
