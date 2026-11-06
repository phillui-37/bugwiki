#!/usr/bin/env python3
"""dream-export — render Alma threads as compact transcripts for the dream pass.

Why compact: the raw `--full` export of one thread is routinely 0.8-1.1 MB on
this machine (measured 2026-11-06), because it carries tool output, compaction
archives and system reminders. Almost none of that is a fact. A fact lives in
the user's question, the agent's conclusion, and the first lines of a tool
result. So we keep those and drop the rest, which is also what keeps the dream
affordable.

The Alma message shape is NOT Anthropic's: parts are `text`, `reasoning`,
`step-start`, and `tool-<Name>` (tool-Bash, tool-Read...), never `tool_use`.
Matching on `tool_use` silently reports zero tool uses.

Usage:
  dream-export.py --content-root DIR [--since ISO] [--days N]
                  [--max-thread-chars N] [--tool-result-chars N] [--dry-run]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys

USER_CHARS = 4000
AGENT_CHARS = 4000
TOOL_INPUT_CHARS = 300
TOOL_RESULT_CHARS = 600
MAX_THREAD_CHARS = 400_000


def sh(args: list[str]) -> str:
    out = subprocess.run(args, capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"{' '.join(args[:3])} failed: {out.stderr.strip()[:200]}")
    return out.stdout


def slugify(text: str, limit: int = 48) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (text[:limit].rstrip("-") or "thread")


def fetch_messages(thread_id: str, page: int = 100, cap: int = 2000) -> list[dict]:
    """Page until hasMore is false. Source page count is not read coverage."""
    items: list[dict] = []
    offset = 0
    while offset < cap:
        raw = sh(["alma", "thread", "messages", thread_id, str(page),
                  "--full", "--json", "--offset", str(offset)])
        data = json.loads(raw or "{}")
        batch = data.get("messages") or data.get("data") or []
        if isinstance(data, list):
            batch = data
        items.extend(batch)
        if not data.get("hasMore"):
            break
        offset = data.get("nextOffset", offset + page)
    return items


def clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + f" …[{len(text) - limit} chars cut]"


def render(items: list[dict], title: str, thread_id: str,
           tool_result_chars: int) -> str:
    lines = [f"SESSION {thread_id}  {title}", ""]
    for item in items:
        msg = item.get("message", item)
        role = msg.get("role", "?")
        for part in msg.get("parts") or []:
            kind = part.get("type", "")
            if kind == "text":
                body = clip(part.get("text", ""), USER_CHARS if role == "user" else AGENT_CHARS)
                if body:
                    lines += [f"{role.upper()}:", body, ""]
            elif kind == "reasoning" or kind == "step-start":
                continue  # not a fact, and the largest part of the payload
            elif kind.startswith("tool-"):
                name = kind[len("tool-"):]
                payload = part.get("input") or {}
                rendered = json.dumps(payload, ensure_ascii=False) if isinstance(payload, dict) else str(payload)
                lines.append(f"TOOL CALL {name}:")
                lines.append(clip(rendered, TOOL_INPUT_CHARS))
                out = part.get("output")
                if isinstance(out, dict):
                    out = out.get("output") or out.get("text") or json.dumps(out, ensure_ascii=False)
                if out:
                    lines.append(f"TOOL RESULT ({name}):")
                    lines.append(clip(str(out), tool_result_chars))
                lines.append("")
    text = "\n".join(lines)
    return text[:MAX_THREAD_CHARS]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--content-root", required=True)
    ap.add_argument("--since", help="ISO date; default = last run minus 1 day, else 7 days")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--tool-result-chars", type=int, default=TOOL_RESULT_CHARS)
    ap.add_argument("--thread", action="append", help="force one thread id (repeatable)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    root = os.path.expanduser(args.content_root)
    state_dir = os.path.join(root, ".dream")
    state_path = os.path.join(state_dir, "last-run")
    today = dt.date.today()

    # Window: keep 6 hours of overlap so a thread touched just before the last
    # run still gets picked up, but do not re-do whole days (a date-based
    # "minus one day" re-exports two days every night).
    since_dt = None
    if args.since:
        since_dt = dt.datetime.fromisoformat(args.since)
    elif os.path.exists(state_path):
        since_dt = dt.datetime.fromisoformat(open(state_path).read().strip()) - dt.timedelta(hours=6)
    else:
        since_dt = dt.datetime.now() - dt.timedelta(days=args.days)
    since = since_dt.date()

    if args.thread:
        threads = [{"id": t, "title": t} for t in args.thread]
    else:
        raw = sh(["alma", "threads", "200", "--json"])
        data = json.loads(raw or "[]")
        threads = data if isinstance(data, list) else data.get("threads", [])
        picked = []
        for t in threads:
            stamp = t.get("updatedAt") or t.get("createdAt") or ""
            if not stamp:
                continue
            try:
                when = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00")).replace(tzinfo=None)
            except ValueError:
                continue
            if when >= since_dt.replace(tzinfo=None):
                picked.append(t)
        threads = picked

    out_dir = os.path.join(root, ".raw", today.isoformat())
    plan = []
    for t in threads:
        tid = t.get("id")
        if not tid:
            continue
        try:
            items = fetch_messages(tid)
        except RuntimeError as exc:
            print(f"skip {tid}: {exc}", file=sys.stderr)
            continue
        if not items:
            continue
        title = (t.get("title") or "").strip()
        # a thread with no title yet would otherwise be named <id>-<id>.txt
        name = f"{tid}.txt" if not title or title == tid else f"{tid}-{slugify(title)}.txt"
        plan.append((tid, name, render(items, t.get("title") or tid, tid, args.tool_result_chars)))

    total = sum(len(body) for _, _, body in plan)
    print(f"threads: {len(plan)}  since: {since}  bytes: {total}")
    for tid, name, body in plan:
        print(f"  {name}  {len(body)}B")
    if args.dry_run:
        return 0

    os.makedirs(out_dir, exist_ok=True)
    for tid, name, body in plan:
        # a thread must have exactly one file per day: the name carries the
        # title, which can be edited, so drop any earlier name for this id
        for stale in os.listdir(out_dir):
            if stale.startswith(f"{tid}-") and stale != name:
                os.remove(os.path.join(out_dir, stale))
        with open(os.path.join(out_dir, name), "w", encoding="utf-8") as fh:
            fh.write(body)
    os.makedirs(state_dir, exist_ok=True)
    with open(state_path, "w", encoding="utf-8") as fh:
        fh.write(dt.datetime.now().isoformat(timespec="seconds"))
    print(f"wrote {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
