#!/usr/bin/env python3
"""Explicit Alma CLI adapter emitting canonical dream thread JSON."""
import argparse
import datetime as dt
import json
import subprocess
import sys


def parse_stamp(text):
    try:
        value = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None
    if value.tzinfo is None:
        return None
    return value.astimezone(dt.timezone.utc)


def call(args):
    p = subprocess.run(["alma", *args], capture_output=True, text=True)
    if p.returncode:
        raise RuntimeError(f"alma {' '.join(args[:3])} failed: {p.stderr.strip()[:300]}")
    try:
        return json.loads(p.stdout or "{}")
    except json.JSONDecodeError as e:
        raise RuntimeError(f"alma returned invalid JSON: {e}") from e


def fetch(tid, page=100, cap=2000):
    items, pages, offset = [], [], 0
    while offset < cap:
        data = call(["thread", "messages", tid, str(page), "--full", "--json", "--offset", str(offset)])
        batch = data if isinstance(data, list) else data.get("messages", data.get("data", []))
        if not isinstance(batch, list): raise RuntimeError(f"Alma returned invalid messages for {tid}")
        if not batch and (data.get("hasMore") if isinstance(data, dict) else False):
            raise RuntimeError(f"Alma pagination made no progress for {tid} at offset {offset}")
        items.extend(batch)
        page_info = {"requestedOffset": offset, "returnedCount": len(batch)}
        if isinstance(data, dict):
            page_info.update({key: data[key] for key in ("offset", "limit", "total", "hasMore", "nextOffset", "scope", "missingMessageIds", "contentMode", "sourceTextTruncated", "coverageNote") if key in data})
        pages.append(page_info)
        if not isinstance(data, dict) or not data.get("hasMore"): return items, pages
        nxt = data.get("nextOffset", offset + len(batch))
        if not isinstance(nxt, int) or nxt <= offset: raise RuntimeError(f"Alma pagination did not advance for {tid}")
        offset = nxt
    raise RuntimeError(f"Alma pagination exceeded {cap} messages for {tid} while hasMore remained true")


def message(raw):
    msg = raw.get("message", raw)
    role = msg.get("role", "assistant")
    if role not in ("user", "assistant", "tool"): role = "tool" if role == "tool" else "assistant"
    out = {"id": msg.get("id", raw.get("id", "")), "role": role}
    stamp = msg.get("timestamp", msg.get("createdAt", raw.get("timestamp", raw.get("createdAt"))))
    if stamp is not None: out["timestamp"] = stamp
    timezone = raw.get("metadata", {}).get("sentAtTimeZone")
    if timezone is None:
        timezone = msg.get("timezone", msg.get("timeZone", raw.get("timezone", raw.get("timeZone"))))
    if timezone is not None: out["timezone"] = timezone
    content = []
    for part in msg.get("parts", msg.get("content", [])) or []:
        kind = part.get("type", "")
        if kind == "text": content.append({"type": "text", "text": part.get("text", "")})
        elif kind in ("reasoning", "step-start"): continue
        elif kind.startswith("tool-"):
            name = kind[5:]
            identity = {"toolCallId": part["toolCallId"]} if part.get("toolCallId") else {}
            content.append({"type": "tool_call", "name": name, "input": part.get("input"), **identity})
            if "output" in part:
                value = part["output"]
                if isinstance(value, dict):
                    if "output" in value: value = value["output"]
                    elif "text" in value: value = value["text"]
                content.append({"type": "tool_result", "name": name, "output": value, **identity})
    out["content"] = content
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--thread", action="append")
    ap.add_argument("--exclude-thread", action="append", default=[])
    ap.add_argument("--since")
    args = ap.parse_args()
    try:
        if args.thread:
            ids = args.thread
            listing = []
        else:
            data = call(["threads", "200", "--json"])
            listing = data if isinstance(data, list) else data.get("threads", [])
            if any(not t.get("id") for t in listing): raise RuntimeError("Alma thread listing contains an item without id")
            if args.since:
                cutoff = parse_stamp(args.since)
                if cutoff is None: raise RuntimeError("invalid --since timestamp")
                filtered = []
                for t in listing:
                    stamp = parse_stamp(t.get("updatedAt") or t.get("createdAt"))
                    if stamp is None: raise RuntimeError(f"thread {t['id']}: missing or invalid updatedAt for automatic --since filtering")
                    if stamp >= cutoff: filtered.append(t)
                listing = filtered
            ids = [t["id"] for t in listing]
        threads = []
        for tid in ids:
            meta = next((t for t in listing if t.get("id") == tid), {"id": tid, "title": tid})
            messages, pages = ([], []) if tid in args.exclude_thread else fetch(tid)
            row = {"id": tid, "title": meta.get("title") or tid, "messages": [message(m) for m in messages], "source_pages": pages}
            if tid in args.exclude_thread:
                row["exclusion"] = {"id": tid, "reason": "Explicit Dream-generated source ID excluded by caller; content not fetched or reviewed"}
            if meta.get("updatedAt") is not None: row["updatedAt"] = meta["updatedAt"]
            elif meta.get("createdAt") is not None: row["updatedAt"] = meta["createdAt"]
            threads.append(row)
        print(json.dumps({"threads": threads}, ensure_ascii=False))
    except (RuntimeError, TypeError, AttributeError) as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0

if __name__ == "__main__": sys.exit(main())
