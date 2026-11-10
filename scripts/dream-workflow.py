#!/usr/bin/env python3
"""Durable, manifest-scoped dream run progress and commit receipt."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

MAX_CHUNK = 12_000


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def git(root: Path, *args: str, env=None, check=True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args], text=True, capture_output=True, env=env, check=check)


def safe_owned_path(root: Path, rel: str, report: str) -> Path:
    candidate = Path(rel)
    if candidate.is_absolute() or ".." in candidate.parts or candidate.as_posix() != rel:
        raise ValueError(f"invalid owned path: {rel}")
    allowed = rel.startswith("facts/") and rel.endswith(".md") or rel.startswith("solutions/") and rel.endswith(".md") or rel == report
    if not allowed:
        raise ValueError(f"owned path is outside facts/, solutions/, and this run report: {rel}")
    path = root / candidate
    if path.is_symlink() or not path.resolve().is_relative_to(root):
        raise ValueError(f"owned path escapes content root: {rel}")
    if any(part.lower() in {"config", "configs", "rules", "prompts"} for part in candidate.parts):
        raise ValueError(f"rule/config paths cannot be owned: {rel}")
    if path.is_file() and "maturity: promoted" in path.read_text(encoding="utf-8").lower():
        raise ValueError(f"promoted pages cannot be owned: {rel}")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("action", choices=["begin", "status", "read", "ack", "complete", "own", "resolve", "commit"])
    parser.add_argument("--batch")
    parser.add_argument("--thread")
    parser.add_argument("--limit", type=int, default=MAX_CHUNK)
    parser.add_argument("--offset", type=int)
    parser.add_argument("--status")
    parser.add_argument("--source-complete", action="store_true")
    parser.add_argument("--gaps", default="")
    parser.add_argument("-m", default="")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    state = root / ".dream" / "workflow.json"
    try:
        run = json.loads(state.read_text(encoding="utf-8")) if state.exists() else None
        if args.action == "begin":
            if run and run["phase"] != "success":
                raise ValueError("pending run exists; resume it before exporting another batch")
            if not args.batch:
                raise ValueError("--batch required")
            batch = Path(args.batch).resolve()
            metadata_path = batch / "manifest.json"
            if not metadata_path.is_file():
                raise ValueError("batch manifest.json is required")
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            processed = root / ".dream" / "processed-batches" / f"{metadata['batch_id']}.json"
            if processed.exists():
                raise ValueError("this immutable batch was already committed")
            if run and run.get("batch_id") == metadata["batch_id"]:
                print(run["id"])
                return 0
            if run and run.get("phase") == "success" and run.get("batch_id") == metadata["batch_id"]:
                raise ValueError("this immutable batch was already committed")
            paths = sorted(batch.glob("*.txt"))
            if not paths:
                raise ValueError("batch contains no transcripts")
            now = dt.datetime.now(dt.timezone.utc)
            ident = now.strftime("%Y%m%dT%H%M%S%fZ")
            baseline = git(root, "status", "--porcelain", "-z", "-uall").stdout
            baseline_paths = [item.decode("utf-8", "surrogateescape") for item in baseline.encode().split(b"\0") if item]
            transcripts = {str(p): {"hash": digest(p), "ack": 0, "outstanding": None, "analysis": "pending", "source_complete": False, "reason": ""} for p in paths}
            run = {"id": ident, "phase": "processing", "batch": str(batch), "batch_id": metadata["batch_id"], "source": metadata["source"], "source_boundary": metadata["source_boundary"], "source_hash": metadata["source_hash"], "report": f"reports/{ident}.dream.md", "transcripts": transcripts, "owned": {}, "baseline_dirty": baseline_paths, "gaps": [], "started": now.isoformat()}
            save(state, run)
            print(ident)
            return 0
        if args.action == "status":
            details = run or {"phase": "idle"}
            details = {**details, "source_checkpoints": {path.name.removeprefix("last-run-"): path.read_text(encoding="utf-8").strip() for path in (root / ".dream").glob("last-run-*")}, "legacy_checkpoint": (root / ".dream" / "last-run").read_text(encoding="utf-8").strip() if (root / ".dream" / "last-run").exists() else None}
            print(json.dumps(details, indent=2, sort_keys=True))
            return 0
        if not run:
            raise ValueError("no active dream run; use dream begin --batch DIR")
        if run["phase"] == "success" and args.action not in ("status", "commit"):
            raise ValueError("run already succeeded; mutation is forbidden")
        if run["phase"] == "partial" and args.action not in ("status", "resolve", "commit"):
            raise ValueError("partially committed run only accepts gap resolution or commit retry")
        if args.action == "resolve":
            if run["phase"] not in ("processing", "partial") or not args.thread or not args.gaps.strip():
                raise ValueError("resolve requires an active run, --thread, and provenance in --gaps")
            matches = [gap for gap in run["gaps"] if gap["thread"] == args.thread and not gap["resolved"]]
            if not matches:
                raise ValueError("no unresolved gap for this transcript")
            for gap in matches:
                gap["resolved"] = True
                gap["resolution"] = args.gaps.strip()
            save(state, run)
            return 0
        if args.action == "commit" and run["phase"] == "partial":
            unresolved = [gap for gap in run["gaps"] if not gap["resolved"]]
            if unresolved:
                partial_receipt = root / ".dream" / "receipts" / f"{run['id']}.json"
                if not partial_receipt.exists():
                    recovered = {"run": run["id"], "commit": run["commit"], "phase": "partial", "owned": run["prepared_owned"], "report": run["report"], "report_hash": run["prepared_report_hash"], "completed": run["completed"]}
                    save(partial_receipt, recovered)
                print(partial_receipt.read_text(encoding="utf-8"))
                return 0
        if args.action in ("read", "ack", "complete"):
            if not args.thread or args.thread not in run["transcripts"]:
                raise ValueError("--thread must be a manifest transcript path")
            record = run["transcripts"][args.thread]
            path = Path(args.thread)
            if digest(path) != record["hash"]:
                raise ValueError("transcript hash changed; run rejected")
            text = path.read_text(encoding="utf-8")
            if args.action == "read":
                if not 1 <= args.limit <= MAX_CHUNK:
                    raise ValueError(f"--limit must be between 1 and {MAX_CHUNK}")
                outstanding = record["outstanding"]
                if outstanding is None:
                    offset = record["ack"]
                    if args.offset is not None and args.offset != offset:
                        raise ValueError(f"non-contiguous offset; expected {offset}")
                    chunk = text[offset:offset + args.limit]
                    record["outstanding"] = {"offset": offset, "end": offset + len(chunk), "text": chunk, "hash": hashlib.sha256(chunk.encode()).hexdigest()}
                    save(state, run)
                    sys.stdout.write(chunk)
                else:
                    chunk = outstanding["text"]
                    if args.offset not in (None, outstanding["offset"]):
                        raise ValueError(f"outstanding chunk starts at {outstanding['offset']}")
                    sys.stdout.write(chunk)
                return 0
            if args.action == "ack":
                outstanding = record["outstanding"]
                if outstanding is None:
                    raise ValueError("no outstanding chunk to acknowledge")
                if args.offset is not None and args.offset != outstanding["offset"]:
                    raise ValueError(f"expected outstanding chunk offset {outstanding['offset']}")
                record["ack"] = outstanding["end"]
                record["outstanding"] = None
                save(state, run)
                print(f"acknowledged through character {record['ack']}")
                return 0
            if args.status not in ("analyzed", "skipped"):
                raise ValueError("--status must be analyzed or skipped")
            if args.status == "analyzed" and (record["ack"] != len(text) or record["outstanding"] is not None):
                raise ValueError("transcript has unread or unacknowledged content")
            record["analysis"] = args.status
            record["source_complete"] = args.status == "analyzed" and args.source_complete
            record["reason"] = args.gaps.strip() if args.gaps else ("operator excluded dream-job transcript" if args.status == "skipped" else "")
            if args.status == "analyzed" and not args.source_complete:
                record["analysis"] = "pending"
                raise ValueError("analyzed completion requires explicit --source-complete acknowledgement")
            if args.gaps:
                run["gaps"].append({"thread": args.thread, "reason": args.gaps.strip(), "resolved": False})
            save(state, run)
            return 0
        if args.action == "own":
            for rel in sys.stdin.read().splitlines():
                path = safe_owned_path(root, rel, run["report"])
                if not path.is_file():
                    raise ValueError(f"owned file must exist: {rel}")
                if any(rel in item for item in run["baseline_dirty"]):
                    raise ValueError(f"cannot adopt pre-existing dirty path: {rel}")
                expected = run["owned"].get(rel)
                actual = digest(path)
                if expected and expected != actual:
                    raise ValueError(f"owned file changed since registration: {rel}")
                if not expected:
                    status = git(root, "status", "--porcelain", "--", rel).stdout
                    if status and status[:2] != "??" and status[0] != " ":
                        raise ValueError(f"cannot adopt pre-staged path: {rel}")
                    run["owned"][rel] = actual
            save(state, run)
            return 0
        if args.action == "commit":
            receipt_path = root / ".dream" / "receipts" / f"{run['id']}.json"
            if run["phase"] == "success":
                if not receipt_path.exists():
                    recovered_receipt = {"run": run["id"], "commit": run["commit"], "phase": "success", "owned": run["prepared_owned"], "report": run["report"], "report_hash": run["prepared_report_hash"], "completed": run["completed"]}
                    save(receipt_path, recovered_receipt)
                    save(root / ".dream" / "processed-batches" / f"{run['batch_id']}.json", recovered_receipt)
                    if run["source"] != "input":
                        (root / ".dream" / f"last-run-{run['source']}").write_text(run["source_boundary"] + "\\n", encoding="utf-8")
                print(receipt_path.read_text(encoding="utf-8"))
                return 0
            if run["phase"] == "committing":
                current = git(root, "rev-parse", "HEAD").stdout.strip()
                if current != run["prepared_commit"]:
                    parents = git(root, "rev-list", "--parents", "-n", "1", current).stdout.split()
                    committed_paths = set(git(root, "diff-tree", "--no-commit-id", "--name-only", "-r", current).stdout.splitlines())
                    if len(parents) > 1 and parents[1] == run["prepared_commit"] and set(run["prepared_owned"]).issubset(committed_paths):
                        unresolved = any(not gap["resolved"] for gap in run["gaps"])
                        run["phase"] = "partial" if unresolved else "success"
                        run["commit"] = current
                        run["completed"] = dt.datetime.now(dt.timezone.utc).isoformat()
                        save(state, run)
                        receipt = {"run": run["id"], "commit": current, "phase": run["phase"], "owned": run["prepared_owned"], "report": run["report"], "report_hash": run["prepared_report_hash"], "completed": run["completed"]}
                        save(receipt_path, receipt)
                        if not unresolved:
                            save(root / ".dream" / "processed-batches" / f"{run['batch_id']}.json", receipt)
                            if run["source"] != "input":
                                (root / ".dream" / f"last-run-{run['source']}").write_text(run["source_boundary"] + "\n", encoding="utf-8")
                        print(json.dumps(receipt))
                        return 0
                    raise ValueError("prepared commit state does not match current Git history; manual reconciliation required")
            incomplete = [name for name, record in run["transcripts"].items() if record["analysis"] == "pending" or record["analysis"] == "analyzed" and not record["source_complete"]]
            if incomplete:
                raise ValueError("unresolved transcript analysis/source coverage: " + ", ".join(incomplete))
            for name, record in run["transcripts"].items():
                if digest(Path(name)) != record["hash"]:
                    raise ValueError(f"transcript hash changed before commit: {name}")
            for rel, expected in run["owned"].items():
                path = safe_owned_path(root, rel, run["report"])
                if not path.is_file() or digest(path) != expected:
                    raise ValueError(f"owned artifact changed or missing: {rel}")
            report = root / run["report"]
            report.parent.mkdir(parents=True, exist_ok=True)
            counts = {"analyzed": 0, "skipped": 0, "gaps": sum(not gap["resolved"] for gap in run["gaps"])}
            for record in run["transcripts"].values():
                counts[record["analysis"]] = counts.get(record["analysis"], 0) + 1
            report.write_text(f"# Dream run {run['id']}\n\n- Batch: `{run['batch_id']}`\n- Source: `{run['source']}`\n- Selection boundary: `{run['source_boundary']}`\n- Source hash: `{run['source_hash']}`\n- Analyzed: {counts['analyzed']}\n- Skipped: {counts['skipped']}\n- Unresolved gaps: {counts['gaps']}\n\n## Transcript outcomes\n\n" + "".join(f"- `{Path(name).name}`: {record['analysis']}" + (f" — {record['reason']}" if record['reason'] else "") + "\n" for name, record in run["transcripts"].items()) + "\n## Source gaps\n\n" + "".join(f"- `{Path(gap['thread']).name}`: {gap['reason']}" + (f" (resolved with: {gap['resolution']})" if gap["resolved"] else " (unresolved)") + "\n" for gap in run["gaps"]), encoding="utf-8")
            report_rel = run["report"]
            run["owned"][report_rel] = digest(report)
            dirty = git(root, "status", "--porcelain", "-z").stdout
            dirty_paths = [entry.decode("utf-8", "surrogateescape") for entry in dirty.encode().split(b"\0") if entry]
            if any(any(rel in item for rel in run["owned"]) and item[:2] in ("MM", "AM", "MD", "AD") for item in dirty_paths):
                raise ValueError("mixed staged and unstaged ownership detected")
            for command in ("verify", "lint"):
                result = subprocess.run(["bash", str(Path(__file__).with_name("wiki")), command], env=dict(os.environ, WIKI_HOME=str(root)))
                if result.returncode:
                    raise ValueError(f"wiki {command} failed")
            run["phase"] = "committing"
            run["prepared_commit"] = git(root, "rev-parse", "HEAD").stdout.strip()
            run["prepared_owned"] = sorted(run["owned"])
            run["prepared_report_hash"] = digest(report)
            save(state, run)
            index = root / ".dream" / f"index-{run['id']}"
            env = dict(os.environ, GIT_INDEX_FILE=str(index))
            try:
                git(root, "read-tree", "HEAD", env=env)
                git(root, "add", "--", *run["prepared_owned"], env=env)
                staged = git(root, "diff", "--cached", "--quiet", check=False, env=env)
                if staged.returncode:
                    git(root, "commit", "-m", args.m or f"dream {run['id']}", env=env)
                commit = git(root, "rev-parse", "HEAD").stdout.strip()
            except subprocess.CalledProcessError:
                raise
            finally:
                index.unlink(missing_ok=True)
            unresolved = any(not gap["resolved"] for gap in run["gaps"])
            run["phase"] = "partial" if unresolved else "success"
            run["commit"] = commit
            run["completed"] = dt.datetime.now(dt.timezone.utc).isoformat()
            save(state, run)
            receipt = {"run": run["id"], "commit": commit, "phase": run["phase"], "owned": run["prepared_owned"], "report": run["report"], "report_hash": digest(report), "completed": run["completed"]}
            save(receipt_path, receipt)
            if not unresolved:
                save(root / ".dream" / "processed-batches" / f"{run['batch_id']}.json", receipt)
                if run["source"] != "input":
                    (root / ".dream" / f"last-run-{run['source']}").write_text(run["source_boundary"] + "\n", encoding="utf-8")
            print(json.dumps(receipt))
            return 0
    except (OSError, ValueError, subprocess.CalledProcessError, json.JSONDecodeError, KeyError) as error:
        print(f"dream {args.action}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
