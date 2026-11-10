#!/usr/bin/env python3
"""Render canonical JSON threads as compact transcripts; Alma is opt-in."""
from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile

USER_CHARS = 4000
AGENT_CHARS = 4000
TOOL_INPUT_CHARS = 300
TOOL_RESULT_CHARS = 600
MAX_THREAD_CHARS = 400_000


def parse_stamp(text: str) -> dt.datetime | None:
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        value = text.strip()
        when = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if len(value) == 10 and when.tzinfo is None:
        when = when.replace(tzinfo=dt.timezone.utc)
    if when.tzinfo is None:
        return None
    return when.astimezone(dt.timezone.utc)


def slugify(text: str, limit: int = 48) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return text[:limit].rstrip("-") or "thread"


def clip(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + f" …[{len(text) - limit} chars cut]"


def validate(data: object) -> list[dict]:
    if not isinstance(data, dict) or not isinstance(data.get("threads"), list):
        raise ValueError('input must be an object containing a "threads" array')
    for t in data["threads"]:
        if not isinstance(t, dict) or not isinstance(t.get("id"), str) or not t["id"]:
            raise ValueError("each thread requires a non-empty string id")
        if not re.fullmatch(r"[A-Za-z0-9._-]+", t["id"]):
            raise ValueError(f"unsafe thread id: {t['id']!r}")
        if not isinstance(t.get("title", ""), str):
            raise ValueError(f"thread {t['id']}: title must be a string")
        if not isinstance(t.get("messages"), list):
            raise ValueError(f"thread {t['id']}: messages must be an array")
        for m in t["messages"]:
            if not isinstance(m, dict) or m.get("role") not in ("user", "assistant", "tool"):
                raise ValueError(f"thread {t['id']}: each message requires role user, assistant, or tool")
            if m.get("timestamp") is not None and parse_stamp(m["timestamp"]) is None:
                raise ValueError(f"thread {t['id']}: invalid message timestamp")
            if m.get("timezone") is not None and not isinstance(m["timezone"], str):
                raise ValueError(f"thread {t['id']}: message timezone must be a string")
            content = m.get("content", "")
            if not isinstance(content, (str, list)):
                raise ValueError(f"thread {t['id']}: message content must be a string or array")
            for p in content if isinstance(content, list) else []:
                if not isinstance(p, dict) or p.get("type") not in ("text", "tool_call", "tool_result"):
                    raise ValueError(f"thread {t['id']}: invalid content part")
    return data["threads"]


def render(t: dict, tool_result_chars: int) -> str:
    tid = t["id"]
    lines = [f"SESSION {tid}  {t.get('title') or tid}", ""]
    for msg in t["messages"]:
        role = msg["role"]
        fields = [f"id={msg.get('id') or '(none)'}"]
        stamp = msg.get("timestamp")
        if stamp:
            fields.append(f"timestamp={stamp}")
        timezone = msg.get("timezone")
        if timezone:
            fields.append(f"timezone={timezone}")
        prefix = f"{role.upper()} [{'; '.join(fields)}]:"
        lines.append(prefix)
        content = msg.get("content", "")
        parts = [{"type": "text", "text": content}] if isinstance(content, str) else content
        for p in parts:
            kind = p["type"]
            if kind == "text":
                body = p.get("text", "")
                if not isinstance(body, str):
                    body = str(body)
                body = clip(body, USER_CHARS if role == "user" else AGENT_CHARS)
                if body:
                    lines.extend([body, ""])
            elif kind == "tool_call":
                name = str(p.get("name") or "unknown")
                payload = json.dumps(p.get("input"), ensure_ascii=False, default=str)
                lines.extend([f"TOOL CALL {name}:", clip(payload, TOOL_INPUT_CHARS)])
            elif kind == "tool_result":
                name = str(p.get("name") or "unknown")
                value = p.get("output")
                if value is None:
                    continue
                rendered = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
                lines.extend([f"TOOL RESULT ({name}):", clip(rendered, tool_result_chars), ""])
    text = "\n".join(lines)
    if len(text) > MAX_THREAD_CHARS:
        text = text[:MAX_THREAD_CHARS].rstrip() + f"\n…[thread truncated; {len(text) - MAX_THREAD_CHARS} chars cut]\n"
    return text


def load_threads(args, since: dt.datetime | None) -> tuple[list[dict], str]:
    if args.input:
        if args.source:
            raise ValueError("choose either --input or --source alma")
        raw = sys.stdin.read() if args.input == "-" else open(args.input, encoding="utf-8").read()
        return validate(json.loads(raw)), "input"
    if args.source == "alma":
        adapter_args = [] if not args.thread else sum((["--thread", x] for x in args.thread), [])
        if since is not None and not args.thread:
            adapter_args += ["--since", since.isoformat()]
        p = subprocess.run([sys.executable, os.path.join(os.path.dirname(__file__), "dream-source-alma.py"), *adapter_args], capture_output=True, text=True)
        if p.returncode:
            raise RuntimeError(p.stderr.strip() or "Alma source failed")
        return validate(json.loads(p.stdout)), "alma"
    raise ValueError("choose a source: pass --input FILE (or - for stdin), or explicitly use --source alma")


def atomic_write(path: str, text: str) -> None:
    directory = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(prefix=".dream-", dir=directory, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try: os.unlink(tmp)
        except FileNotFoundError: pass
        raise


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--content-root", required=True)
    ap.add_argument("--input", help="canonical JSON file, or - for stdin")
    ap.add_argument("--source", choices=["alma"], help="explicitly fetch from Alma")
    ap.add_argument("--since", help="ISO date/time; automatic selection requires valid updatedAt")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--tool-result-chars", type=int, default=TOOL_RESULT_CHARS)
    ap.add_argument("--thread", action="append", help="select requested thread id (repeatable)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    try:
        root = os.path.realpath(os.path.expanduser(args.content_root))
        workflow_state = os.path.join(root, ".dream", "workflow.json")
        if os.path.isfile(workflow_state):
            with open(workflow_state, encoding="utf-8") as state_file:
                workflow = json.load(state_file)
            if workflow.get("phase") != "success":
                raise ValueError("an incomplete run must be resumed before exporting another batch")
        source = "input" if args.input else "alma"
        if args.since:
            since_dt = parse_stamp(args.since)
            if since_dt is None: raise ValueError("--since must be a valid ISO date or timestamp")
        else:
            state = os.path.join(root, ".dream", f"last-run-{source}")
            since_dt = parse_stamp(open(state, encoding="utf-8").read()) if os.path.exists(state) else None
            since_dt = since_dt - dt.timedelta(minutes=30) if since_dt else dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=args.days)
        threads, source = load_threads(args, since_dt)
        if args.thread:
            by_id = {t["id"]: t for t in threads}
            missing = [tid for tid in args.thread if tid not in by_id]
            if missing: raise ValueError("requested thread id not found: " + ", ".join(missing))
            chosen = [by_id[tid] for tid in args.thread]
        else:
            chosen = []
            for t in threads:
                stamp = parse_stamp(t.get("updatedAt"))
                if stamp is None: raise ValueError(f"thread {t['id']}: missing or invalid updatedAt for automatic --since filtering")
                if stamp >= since_dt: chosen.append(t)
        today = dt.date.today().isoformat()
        plan = []
        for t in chosen:
            title = (t.get("title") or "").strip()
            name = f"{t['id']}.txt" if not title or title == t["id"] else f"{t['id']}-{slugify(title)}.txt"
            plan.append((t["id"], name, render(t, args.tool_result_chars)))
        total = sum(len(body) for _, _, body in plan)
        source_boundary = since_dt.isoformat()
        source_hash = hashlib.sha256(json.dumps(chosen, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        batch_id = hashlib.sha256(f"{source}\0{source_hash}".encode()).hexdigest()[:20]
        print(f"threads: {len(plan)}  since: {since_dt.date()}  characters: {total}")
        for _, name, body in plan: print(f"  {name}  {len(body)} characters")
        if args.dry_run: return 0
        base = os.path.join(root, ".raw", today)
        os.makedirs(base, exist_ok=True)
        out_dir = os.path.join(base, batch_id)
        if os.path.exists(out_dir):
            manifest = os.path.join(out_dir, "manifest.json")
            if os.path.isfile(manifest):
                print(f"wrote {out_dir} (existing immutable batch)")
                return 0
            raise ValueError(f"incomplete publication staging exists: {out_dir}")
        staging = tempfile.mkdtemp(prefix=f".dream-stage-{batch_id}-", dir=base)
        try:
            for _, name, body in plan:
                with open(os.path.join(staging, name), "w", encoding="utf-8") as output:
                    output.write(body)
            metadata = {"batch_id": batch_id, "source": source, "source_boundary": source_boundary, "source_hash": source_hash, "selected_at": dt.datetime.now(dt.timezone.utc).isoformat(), "transcripts": [name for _, name, _ in plan]}
            with open(os.path.join(staging, "manifest.json"), "w", encoding="utf-8") as output:
                json.dump(metadata, output, indent=2, sort_keys=True)
                output.write("\n")
            os.replace(staging, out_dir)
        finally:
            if os.path.isdir(staging):
                import shutil
                shutil.rmtree(staging)
        print(f"wrote {out_dir}")
        return 0
    except (ValueError, OSError, json.JSONDecodeError, RuntimeError) as exc:
        print(f"dream export: {exc}", file=sys.stderr)
        return 1

if __name__ == "__main__": sys.exit(main())
