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
import time

from dream_log import bind, event, run_logged

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
    if t.get("exclusion"):
        return "\n".join(lines + ["EXCLUDED: " + t["exclusion"]["reason"], ""])
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
        adapter_args += sum((["--exclude-thread", x] for x in args.exclude_thread), [])
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
    ap.add_argument("--exclude-thread", action="append", default=[], help="explicit Dream-generated thread ID to exclude (repeatable)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    try:
        root = os.path.realpath(os.path.expanduser(args.content_root))
        workflow_state = os.path.join(root, ".dream", "workflow.json")
        if os.path.isfile(workflow_state):
            with open(workflow_state, encoding="utf-8") as state_file:
                workflow = json.load(state_file)
            if workflow.get("phase") not in ("success", "reviewed_with_gaps"):
                raise ValueError("an incomplete run must be resumed before exporting another batch")
        # Recover publication even if the process stopped before begin/pointer write.
        # Only manifest-backed batches created by this workflow participate.
        from pathlib import Path
        pending = []
        for manifest_path in sorted(Path(root, ".raw").glob("*/*/manifest.json")):
            if manifest_path.parent.name.startswith(".dream-stage-"):
                continue
            metadata = json.loads(manifest_path.read_text(encoding="utf-8"))
            if Path(root, ".dream", "processed-batches", metadata["batch_id"] + ".json").exists():
                continue
            names = metadata["transcripts"]
            valid = (manifest_path.parent.name == metadata["batch_id"]
                     and set(names) == set(metadata["transcript_hashes"])
                     and len(set(names)) == len(names)
                     and set(p.name for p in manifest_path.parent.iterdir()) == set(names) | set(metadata.get("source_files", {})) | {"manifest.json"})
            for name in names:
                path = manifest_path.parent / name
                if Path(name).name != name or path.is_symlink() or not path.is_file():
                    valid = False
                    break
                if hashlib.sha256(path.read_bytes()).hexdigest() != metadata["transcript_hashes"][name]:
                    valid = False
            if valid:
                for name, expected_hash in metadata.get("source_files", {}).items():
                    source_path = manifest_path.parent / name
                    if Path(name).name != name or source_path.is_symlink() or not source_path.is_file() or hashlib.sha256(source_path.read_bytes()).hexdigest() != expected_hash:
                        valid = False
            if not valid:
                raise ValueError(f"existing immutable batch failed verification: {manifest_path.parent}")
            pending.append((manifest_path.parent, metadata))
        if pending:
            if len(pending) != 1:
                raise ValueError("multiple unpublished batches need explicit reconciliation")
            directory, metadata = pending[0]
            if not args.dry_run:
                os.makedirs(os.path.join(root, ".dream"), exist_ok=True)
                atomic_write(os.path.join(root, ".dream", "pending-batch.json"),
                             json.dumps({"batch_id": metadata["batch_id"], "path": os.path.relpath(directory, root)}) + "\n")
            bind(batch_id=metadata["batch_id"])
            event("batch", "resumed", "Reusing published pending batch without fetching sources", transcript_count=len(metadata["transcripts"]))
            print(f"resume {directory} (existing immutable batch; source selection was not rerun)")
            return 0
        source = "input" if args.input else "alma"
        if args.since:
            since_dt = parse_stamp(args.since)
            if since_dt is None: raise ValueError("--since must be a valid ISO date or timestamp")
        else:
            review = os.path.join(root, ".dream", f"last-reviewed-{source}")
            state = os.path.join(root, ".dream", f"last-run-{source}")
            values = [parse_stamp(open(cursor, encoding="utf-8").read()) for cursor in (review, state) if os.path.exists(cursor)]
            values = [value for value in values if value is not None]
            since_dt = max(values) if values else None
            since_dt = since_dt - dt.timedelta(minutes=30) if since_dt else dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=args.days)
        selected_at = dt.datetime.now(dt.timezone.utc)
        fetch_started = time.monotonic()
        event("source_fetch", "started", "Fetching source transcript pages", source=source)
        threads, source = load_threads(args, since_dt)
        event("source_fetch", "succeeded", "Source fetch completed", source=source, transcript_count=len(threads), duration_ms=round((time.monotonic()-fetch_started)*1000))
        ids = [t["id"] for t in threads]
        duplicates = sorted({tid for tid in ids if ids.count(tid) > 1})
        if duplicates:
            raise ValueError("duplicate thread id: " + ", ".join(duplicates))
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
        for t in chosen:
            if t["id"] in args.exclude_thread:
                t["exclusion"] = {"id": t["id"], "reason": "Explicit Dream-generated source ID excluded by caller; content not reviewed"}
        today = dt.date.today().isoformat()
        plan = []
        for t in chosen:
            title = (t.get("title") or "").strip()
            name = f"{t['id']}.txt" if not title or title == t["id"] else f"{t['id']}-{slugify(title)}.txt"
            plan.append((t["id"], name, render(t, args.tool_result_chars)))
        total = sum(len(body) for _, _, body in plan)
        source_boundary = selected_at.isoformat()
        source_hash = hashlib.sha256(json.dumps(chosen, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        render_settings = {"user_chars": USER_CHARS, "agent_chars": AGENT_CHARS,
                           "tool_input_chars": TOOL_INPUT_CHARS,
                           "tool_result_chars": args.tool_result_chars,
                           "max_thread_chars": MAX_THREAD_CHARS}
        advances_watermark = source == "alma" and not args.thread
        identity = json.dumps({"source": source, "source_hash": source_hash,
                               "render_settings": render_settings, "advances_watermark": advances_watermark}, sort_keys=True)
        batch_id = hashlib.sha256(identity.encode()).hexdigest()[:20]
        bind(batch_id=batch_id)
        event("render", "succeeded", "Compact export rendered; original-source coverage still requires review", transcript_count=len(plan), characters=total)
        print(f"threads: {len(plan)}  since: {since_dt.date()}  characters: {total}")
        for _, name, body in plan: print(f"  {name}  {len(body)} characters")
        if args.dry_run: return 0
        raw_root = os.path.join(root, ".raw")
        os.makedirs(raw_root, exist_ok=True)
        out_dir = None
        for parent, dirs, _ in os.walk(raw_root):
            if batch_id in dirs:
                candidate = os.path.join(parent, batch_id)
                manifest_path = os.path.join(candidate, "manifest.json")
                expected_hashes = {name: hashlib.sha256(body.encode("utf-8")).hexdigest()
                                   for _, name, body in plan}
                expected = {"batch_id": batch_id, "source": source,
                            "source_hash": source_hash, "render_settings": render_settings,
                            "transcripts": [name for _, name, _ in plan],
                            "transcript_hashes": expected_hashes,
                            "source_files": {f"{tid}.json": hashlib.sha256((json.dumps(next(t for t in chosen if t['id'] == tid), ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()).hexdigest() for tid, _, _ in plan},
                            "advances_watermark": advances_watermark}
                try:
                    existing = json.loads(open(manifest_path, encoding="utf-8").read())
                    actual_names = set(os.listdir(candidate))
                    legacy = "source_files" not in existing
                    expected_names = set(expected["transcripts"]) | (set() if legacy else set(expected["source_files"])) | {"manifest.json"}
                    metadata_fields = (set(expected) - {"source_files"} if legacy else set(expected)) | {"source_boundary", "since", "selected_at"}
                    valid = (set(existing) == metadata_fields
                             and all(existing.get(k) == v for k, v in expected.items() if k != "source_files")
                             and existing.get("source_boundary") == existing.get("selected_at")
                             and parse_stamp(existing.get("source_boundary")) is not None
                             and parse_stamp(existing.get("since")) is not None
                             and actual_names == expected_names)
                    valid = valid and all(hashlib.sha256(open(os.path.join(candidate, n), "rb").read()).hexdigest() == h for n, h in expected_hashes.items())
                    valid = valid and (legacy or all(hashlib.sha256(open(os.path.join(candidate, n), "rb").read()).hexdigest() == h for n, h in expected.get("source_files", {}).items()))
                except (OSError, ValueError):
                    valid = False
                if not valid:
                    raise ValueError(f"existing immutable batch failed verification: {candidate}")
                out_dir = candidate
                break
        base = os.path.join(raw_root, today)
        os.makedirs(base, exist_ok=True)
        if out_dir is None:
            out_dir = os.path.join(base, batch_id)
            if os.path.exists(out_dir):
                raise ValueError(f"incomplete publication staging exists: {out_dir}")
        if os.path.basename(os.path.dirname(out_dir)) == today and not os.path.exists(os.path.join(out_dir, "manifest.json")):
            staging = tempfile.mkdtemp(prefix=f".dream-stage-{batch_id}-", dir=base)
            try:
                for _, name, body in plan:
                    with open(os.path.join(staging, name), "w", encoding="utf-8") as output:
                        output.write(body)
                source_files = {}
                for tid, _, _ in plan:
                    source_name = f"{tid}.json"
                    source_body = json.dumps(next(t for t in chosen if t["id"] == tid), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
                    with open(os.path.join(staging, source_name), "w", encoding="utf-8") as output:
                        output.write(source_body)
                    source_files[source_name] = hashlib.sha256(source_body.encode()).hexdigest()
                metadata = {"batch_id": batch_id, "source": source, "source_boundary": source_boundary,
                            "since": since_dt.isoformat(), "selected_at": source_boundary,
                            "source_hash": source_hash, "render_settings": render_settings,
                            "transcripts": [name for _, name, _ in plan],
                            "transcript_hashes": {name: hashlib.sha256(body.encode("utf-8")).hexdigest() for _, name, body in plan},
                            "source_files": source_files,
                            "advances_watermark": advances_watermark}
                with open(os.path.join(staging, "manifest.json"), "w", encoding="utf-8") as output:
                    json.dump(metadata, output, indent=2, sort_keys=True)
                    output.write("\n")
                os.replace(staging, out_dir)
            finally:
                if os.path.isdir(staging):
                    import shutil
                    shutil.rmtree(staging)
        if not args.dry_run:
            pointer = os.path.join(root, ".dream", "pending-batch.json")
            os.makedirs(os.path.dirname(pointer), exist_ok=True)
            atomic_write(pointer, json.dumps({"batch_id": batch_id, "path": os.path.relpath(out_dir, root)}, sort_keys=True, indent=2) + "\n")
        event("batch", "published", "Immutable batch and pending pointer available", transcript_count=len(plan))
        print(f"wrote {out_dir}" + (" (existing immutable batch)" if os.path.exists(os.path.join(out_dir, "manifest.json")) else ""))
        return 0
    except (ValueError, OSError, KeyError, json.JSONDecodeError, RuntimeError) as exc:
        event("export", "failed", "Export failed; no processing success recorded", error_type=type(exc).__name__)
        print(f"dream export: {exc}", file=sys.stderr)
        return 1

if __name__ == "__main__": sys.exit(run_logged(main, "export"))
