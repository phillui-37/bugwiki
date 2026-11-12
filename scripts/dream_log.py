"""Content-free JSONL execution events for dream commands (UTC timestamps)."""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import stat
from pathlib import Path
import sys
import time
import uuid

_context = None
_LOG_NAME = re.compile(r"verbose-\d{4}-\d{2}-\d{2}\.jsonl\Z")
_RETENTION_SECONDS = 7 * 24 * 60 * 60
_FORMAT = "bugwiki-dream-verbose-v1"


def owned(fd: int) -> bool:
    os.lseek(fd, 0, os.SEEK_SET)
    first = os.read(fd, 65536).split(b"\n", 1)[0]
    try:
        return json.loads(first).get("format") == _FORMAT
    except (ValueError, AttributeError):
        return False


def append(directory: Path, record: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    now = dt.datetime.now(dt.timezone.utc)
    path = directory / f"verbose-{now.date().isoformat()}.jsonl"
    fd = os.open(path, os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        if os.fstat(fd).st_size and not owned(fd):
            raise OSError("Refusing to append to an unowned log")
        record["format"] = _FORMAT
        data = (json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        os.write(fd, data)
    finally:
        os.close(fd)


def rotate(directory: Path, now: float) -> None:
    if not directory.is_dir():
        return
    dream = directory.parent
    if (dream / "pending-batch.json").exists():
        return
    state = dream / "workflow.json"
    if state.exists():
        try:
            if json.loads(state.read_text())["phase"] not in {"success", "reviewed_with_gaps"}:
                return
        except (OSError, ValueError, KeyError, TypeError):
            return
    for path in directory.iterdir():
        if not _LOG_NAME.fullmatch(path.name):
            continue
        try:
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_mtime >= now - _RETENTION_SECONDS:
                continue
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                if owned(fd):
                    path.unlink()
            finally:
                os.close(fd)
        except (FileNotFoundError, OSError):
            pass


def event(stage: str, status: str, description: str, **fields) -> None:
    if _context is None:
        return
    now = dt.datetime.now(dt.timezone.utc)
    numeric_fields = {"transcript_count", "duration_ms", "characters", "start_character", "end_character",
                      "through_character", "unresolved_gaps", "owned_count", "run_elapsed_ms", "exit_code"}
    boolean_fields = {"source_complete", "source_watermark_eligible"}
    safe_fields = {key: value for key, value in fields.items()
                   if (key in numeric_fields and type(value) is int and -2147483648 <= value <= 2147483647)
                   or (key in boolean_fields and type(value) is bool)}
    run_id = _context["ids"].get("run_id")
    if isinstance(run_id, str) and re.fullmatch(r"[0-9]{8}T[0-9]{12}Z", run_id):
        safe_fields["run_id"] = run_id
    commit = fields.get("commit")
    if isinstance(commit, str) and re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit):
        safe_fields["commit"] = commit
    record = {"timestamp": now.isoformat(timespec="milliseconds"),
              "invocation_id": _context["invocation_id"],
              "component": _context["component"], "action": _context["action"],
              "stage": stage, "status": status if status in {"started", "succeeded", "failed", "resumed", "published", "emitted", "acknowledged", "recorded", "registered", "prepared", "success", "partial", "reviewed_with_gaps"} else "other", "description": description,
              "elapsed_ms": round((time.monotonic() - _context["started"]) * 1000),
              "details": "restricted", **safe_fields}
    append(_context["root"] / ".dream" / "logs", record)


def bind(**identifiers) -> None:
    if _context is not None:
        _context["ids"].update(identifiers)


def run_logged(main, component: str) -> int:
    global _context
    arguments = sys.argv[1:]
    action = "export" if component == "export" else arguments[arguments.index("--root") + 2]
    if action not in {"export", "begin", "status", "read", "source", "ack", "complete", "own", "amend", "resolve", "commit"}:
        action = "other"
    if "--dry-run" in arguments or action == "status":
        return main()
    flag = "--content-root" if component == "export" else "--root"
    positions = [i for i, value in enumerate(arguments) if value == flag]
    root = Path(arguments[positions[-1] + 1]) if positions else Path(os.environ.get("WIKI_HOME", "~/.agents/wiki"))
    _context = {"root": root.expanduser().resolve(), "component": component, "action": action,
                "invocation_id": uuid.uuid4().hex, "started": time.monotonic(), "ids": {}}
    rotate(_context["root"] / ".dream" / "logs", time.time())
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
