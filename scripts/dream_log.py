"""Content-free JSONL execution events for dream commands (UTC timestamps)."""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
import sys
import time
import uuid

_context = None


def event(stage: str, status: str, description: str, **fields) -> None:
    if _context is None:
        return
    now = dt.datetime.now(dt.timezone.utc)
    record = {"timestamp": now.isoformat(timespec="milliseconds"),
              "invocation_id": _context["invocation_id"],
              "component": _context["component"], "action": _context["action"],
              "stage": stage, "status": status, "description": description,
              "elapsed_ms": round((time.monotonic() - _context["started"]) * 1000),
              **_context["ids"], **fields}
    directory = _context["root"] / ".dream" / "logs"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / f"events-{now.date().isoformat()}.jsonl").open("a", encoding="utf-8") as output:
        output.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        output.flush()


def bind(**identifiers) -> None:
    if _context is not None:
        _context["ids"].update(identifiers)


def run_logged(main, component: str) -> int:
    global _context
    arguments = sys.argv[1:]
    action = "export" if component == "export" else arguments[arguments.index("--root") + 2]
    if "--dry-run" in arguments or action == "status":
        return main()
    flag = "--content-root" if component == "export" else "--root"
    positions = [i for i, value in enumerate(arguments) if value == flag]
    root = Path(arguments[positions[-1] + 1]) if positions else Path(os.environ.get("WIKI_HOME", "~/.agents/wiki"))
    _context = {"root": root.expanduser().resolve(), "component": component, "action": action,
                "invocation_id": uuid.uuid4().hex, "started": time.monotonic(), "ids": {}}
    event("command", "started", f"Started dream {action}")
    try:
        code = main()
    except BaseException as error:
        event("command", "failed", f"Dream {action} exited unexpectedly", error_type=type(error).__name__)
        raise
    else:
        event("command", "succeeded" if code == 0 else "failed", f"Dream {action} finished", exit_code=code)
        return code
    finally:
        _context = None
