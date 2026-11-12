#!/usr/bin/env python3
"""Record safe metadata for Alma's isolated nightly dream tool calls."""
from __future__ import annotations

import datetime as dt
import json
import os
import re
from pathlib import Path
import sys
import time

from dream_log import append, rotate


TOOLS = frozenset({"Bash", "BashOutput", "Read", "Write", "Edit", "Glob", "Grep", "run_script"})


def is_nightly_dream(context: object) -> bool:
    if not isinstance(context, dict) or context.get("source") != "cron":
        return False
    thread = context.get("threadId")
    if not isinstance(thread, str) or not thread:
        return False
    home = Path.home()
    try:
        jobs = json.loads((home / ".config/alma/cron/jobs.json").read_text())
        matches = [job for job in jobs if job.get("name") == "nightly dream" and job.get("executionMode") == "isolated"]
        if len(matches) != 1 or sum(job.get("name") == "nightly dream" for job in jobs) != 1:
            return False
        job_id = matches[0]["id"]
        state = json.loads((home / ".config/alma/personal/state.json").read_text())
        return any(run.get("threadId") == thread and run.get("jobId") == job_id
                   and run.get("kind") == "routine" and run.get("source") == "cron"
                   for run in state["runs"])
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return False


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        data = payload.get("input", {})
        hook = payload.get("hook")
        if hook not in {"tool.didExecute", "tool.onError"} or not is_nightly_dream(data.get("context")):
            return 0
        tool = data.get("tool")
        result = data.get("result")
        record = {
            "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds"),
            "component": "alma-hook",
            "stage": hook,
            "tool": tool if isinstance(tool, str) and tool in TOOLS else "other",
            "command": "restricted",
            "output": "restricted",
            "error_details": "restricted",
        }
        args = data.get("args")
        command = args.get("command") if isinstance(args, dict) else None
        if tool == "Bash" and isinstance(command, str):
            match = re.match(r"^wiki dream (export|begin|progress|read|source|ack|complete|own|amend|resolve|commit)(?:\s|$)", command)
            if match:
                record["command"] = "wiki dream " + match.group(1) + " [arguments restricted]"
        if tool == "Bash" and isinstance(command, str) and command in {"wiki verify", "wiki lint"}:
            record["command"] = args["command"]
            if isinstance(result, dict):
                stdout = result.get("stdout")
                stderr = result.get("stderr")
                if isinstance(stdout, str):
                    lines = stdout.splitlines()
                    safe = [line for line in lines if re.fullmatch(r"verify ok: [0-9]{1,9} pages \(schema\)|verify FAILED", line)]
                    record["stdout"] = safe
                    record["stdout_omitted_lines"] = len(lines) - len(safe)
                if stderr == "":
                    record["stderr"] = "empty"
                elif isinstance(stderr, str):
                    record["stderr"] = "nonempty (restricted)"
        duration = data.get("duration")
        if type(duration) in (int, float) and 0 <= duration <= 2147483647:
            record["durationMs"] = round(duration)
        if isinstance(result, dict):
            for key in ("exitCode", "durationMs"):
                value = result.get(key)
                if type(value) is int and 0 <= value <= 2147483647:
                    record[key] = value
            for key in ("timedOut", "background", "stdoutComplete", "stderrComplete"):
                value = result.get(key)
                if type(value) is bool:
                    record[key] = value
        root = Path(os.environ.get("WIKI_HOME", "~/.agents/wiki")).expanduser()
        directory = root / ".dream" / "logs"
        rotate(directory, time.time())
        append(directory, record)
    except (OSError, ValueError, TypeError, AttributeError):
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
