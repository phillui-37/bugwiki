#!/usr/bin/env python3
"""Durable progress and isolated publication for a single-writer dream run."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

from dream_log import bind, event, run_logged

MAX_CHUNK = 12_000


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path: Path, value: object) -> None:
    atomic_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".dream-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write(text)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def git(root: Path, *args: str, env=None, input=None) -> str:
    return subprocess.run(["git", "-C", str(root), *args], text=True,
                          capture_output=True, env=env, input=input, check=True).stdout


def head_commit(root: Path) -> str | None:
    result = subprocess.run(["git", "-C", str(root), "rev-parse", "--verify", "-q", "HEAD"], text=True, capture_output=True)
    if result.returncode == 1:
        return None  # A newly initialized wiki has an unborn branch.
    result.check_returncode()
    return result.stdout.strip()


def changed_paths(root: Path) -> set[str]:
    # Separate Git path outputs avoid parsing quoted porcelain names/rename pairs.
    paths = set()
    for arguments in (("diff", "--name-only", "-z"),
                      ("diff", "--cached", "--name-only", "-z"),
                      ("ls-files", "--others", "--exclude-standard", "-z")):
        paths.update(filter(None, git(root, *arguments).split("\0")))
    return paths


def promoted(text: str) -> bool:
    if not text.startswith("---\n"):
        return False
    frontmatter = text.split("---", 2)[1]
    return bool(re.search(r'''(?mi)^\s*maturity\s*:\s*["']?promoted(?:["']?\s*(?:#.*)?)$''', frontmatter))


def safe_path(root: Path, rel: str) -> Path:
    candidate = Path(rel)
    if candidate.is_absolute() or ".." in candidate.parts or candidate.as_posix() != rel:
        raise ValueError(f"invalid relative path: {rel}")
    path = root / candidate
    if any(parent.is_symlink() for parent in (path, *path.parents) if parent != root and root in parent.parents):
        raise ValueError(f"symlink path cannot be owned: {rel}")
    if not path.resolve().is_relative_to(root):
        raise ValueError(f"path escapes content root: {rel}")
    return path


def owned_path(root: Path, rel: str) -> Path:
    path = safe_path(root, rel)
    parts = Path(rel).parts
    if len(parts) < 2 or parts[0] not in ("facts", "solutions") or path.suffix != ".md":
        raise ValueError(f"owned output must be a fact or solution Markdown page: {rel}")
    if any(part.lower() in {"config", "configs", "rules", "prompts"} for part in parts):
        raise ValueError(f"rule/config paths cannot be owned: {rel}")
    if path.is_file() and promoted(path.read_text(encoding="utf-8")):
        raise ValueError(f"promoted page cannot be owned: {rel}")
    if head_commit(root) is not None and rel in git(root, "ls-tree", "--name-only", "HEAD", "--", rel).splitlines():
        if promoted(git(root, "show", f"HEAD:{rel}")):
            raise ValueError(f"previously promoted page cannot be owned: {rel}")
    return path


def check_sources(run: dict) -> None:
    if digest(Path(run["batch"]) / "manifest.json") != run["manifest_hash"]:
        raise ValueError("batch manifest changed")
    for name, record in run["transcripts"].items():
        if digest(Path(name)) != record["hash"]:
            raise ValueError(f"transcript hash changed: {name}")


def index_entries(root: Path, paths: list[str]) -> str:
    return git(root, "ls-files", "--stage", "-z", "--", *paths)


def receipt_for(run: dict) -> dict:
    prepared = run["prepared"]
    return {"run": run["id"], "commit": prepared["commit"], "phase": prepared["outcome"],
            "owned": prepared["paths"], "report": run["report"],
            "report_hash": prepared["hashes"][run["report"]], "completed": prepared["completed"]}


def finish_metadata(root: Path, state: Path, run: dict) -> dict:
    receipt = receipt_for(run)
    # Repeat every derived write on retry, even if a receipt already exists.
    save(root / ".dream" / "receipts" / f"{run['id']}-{receipt['commit']}.json", receipt)
    save(root / ".dream" / "receipts" / f"{run['id']}.json", receipt)
    if receipt["phase"] == "success":
        save(root / ".dream" / "processed-batches" / f"{run['batch_id']}.json", receipt)
        pointer = root / ".dream" / "pending-batch.json"
        if pointer.exists() and json.loads(pointer.read_text())["batch_id"] == run["batch_id"]:
            pointer.unlink()
        if run["advances_watermark"]:
            checkpoint = root / ".dream" / f"last-run-{run['source']}"
            boundary = dt.datetime.fromisoformat(run["source_boundary"])
            if checkpoint.exists():
                previous = dt.datetime.fromisoformat(checkpoint.read_text().strip().replace("Z", "+00:00"))
                boundary = max(boundary, previous)
            atomic_text(checkpoint, boundary.isoformat() + "\n")
    run["phase"] = receipt["phase"]
    run["commit"] = receipt["commit"]
    run["last_report_hash"] = receipt["report_hash"]
    save(state, run)
    event("run", receipt["phase"], "Run receipt persisted", commit=receipt["commit"], outcome=receipt["phase"], source_watermark_eligible=run["advances_watermark"], run_elapsed_ms=round((dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(run["started"])).total_seconds() * 1000))
    return receipt


def publish_prepared(root: Path, state: Path, run: dict) -> dict:
    prepared = run["prepared"]
    head = head_commit(root)
    candidate = prepared["commit"]
    if git(root, "rev-parse", f"{candidate}^{{tree}}").strip() != prepared["tree"]:
        raise ValueError("prepared commit tree does not match recorded tree")
    if head not in (prepared["parent"], candidate):
        raise ValueError("HEAD moved outside the prepared dream commit; manual reconciliation required")
    if head == prepared["parent"]:
        check_sources(run)
        for rel, expected in prepared["hashes"].items():
            if digest(safe_path(root, rel)) != expected:
                raise ValueError(f"prepared artifact changed: {rel}")
        if index_entries(root, prepared["paths"]) != prepared["normal_index"]:
            raise ValueError("owned paths changed in the normal Git index")
        git(root, "update-ref", "-m", f"dream {run['id']}", "HEAD", candidate, prepared["parent"] or "0" * len(candidate))
    # Reconcile only owned entries; preserve unrelated staged blobs/modes/deletions.
    actual_index = index_entries(root, prepared["paths"])
    if actual_index != prepared["normal_index"]:
        committed_entries = git(root, "ls-tree", "-r", "-z", candidate, "--", *prepared["paths"])
        expected_index = ""
        for entry in filter(None, committed_entries.split("\0")):
            metadata, path = entry.split("\t", 1)
            mode, _, blob = metadata.split()
            expected_index += f"{mode} {blob} 0\t{path}\0"
        if actual_index != expected_index:
            raise ValueError("normal index changed on owned paths; commit exists, manual reconciliation required")
    git(root, "reset", "-q", candidate, "--", *prepared["paths"])
    prepared.setdefault("completed", dt.datetime.now(dt.timezone.utc).isoformat())
    save(state, run)
    event("commit", "published", "Prepared commit published and owned index entries reconciled", commit=candidate, outcome=prepared["outcome"])
    return finish_metadata(root, state, run)


def write_report(root: Path, run: dict, outcome: str) -> None:
    report = safe_path(root, run["report"])
    if report.exists() and digest(report) != run.get("last_report_hash"):
        raise ValueError("run report contains an unregistered edit")
    records = run["transcripts"]
    unresolved = [gap for gap in run["gaps"] if not gap["resolved"]]
    lines = [f"# Dream run {run['id']}", "", f"- Outcome: {outcome}",
             f"- Batch: `{run['batch_id']}`", f"- Source: `{run['source']}`",
             f"- Selection boundary: `{run['source_boundary']}`", f"- Source hash: `{run['source_hash']}`",
             f"- Analyzed: {sum(r['analysis'] == 'analyzed' for r in records.values())}",
             f"- Skipped: {sum(r['analysis'] == 'skipped' for r in records.values())}",
             f"- Pending: {sum(r['analysis'] == 'pending' for r in records.values())}",
             f"- Unresolved gaps: {len(unresolved)}", f"- Owned fact/solution files: {len(run['owned'])}",
             "- Verification: wiki verify exit 0; wiki lint exit 0",
             f"- Commit receipt: `.dream/receipts/{run['id']}.json` (written after commit)",
             "", "## Transcript outcomes", ""]
    for name, record in records.items():
        lines.append(f"- `{Path(name).name}`: {record['analysis']}; acknowledged {record['ack']} characters; source coverage {'attested' if record['source_complete'] else 'not attested'}" + (f" — {record['reason']}" if record["reason"] else ""))
    lines.extend(["", "## Source gaps", ""])
    for gap in run["gaps"]:
        lines.append(f"- `{Path(gap['thread']).name}`: {gap['reason']}" + (f" (resolved with: {gap['resolution']})" if gap["resolved"] else " (unresolved)"))
    atomic_text(report, "\n".join(lines) + "\n")
    run["last_report_hash"] = digest(report)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("action", choices=["begin", "status", "read", "ack", "complete", "own", "amend", "resolve", "commit"])
    parser.add_argument("--batch")
    parser.add_argument("--thread")
    parser.add_argument("--limit", type=int, default=MAX_CHUNK)
    parser.add_argument("--offset", type=int)
    parser.add_argument("--status", choices=["analyzed", "skipped"])
    parser.add_argument("--source-complete", action="store_true")
    parser.add_argument("--gaps", default="")
    parser.add_argument("--reason", default="")
    parser.add_argument("--path")
    parser.add_argument("--expected-hash")
    parser.add_argument("--partial", action="store_true")
    parser.add_argument("-m", default="")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    state = root / ".dream" / "workflow.json"
    try:
        run = json.loads(state.read_text()) if state.exists() else None
        if run:
            bind(run_id=run["id"], batch_id=run["batch_id"])
        if args.action == "begin":
            if not args.batch:
                raise ValueError("--batch required")
            batch = Path(args.batch).resolve()
            if not batch.is_relative_to(root / ".raw"):
                raise ValueError("batch must be inside this wiki's .raw directory")
            if run and run["phase"] != "success":
                if run["batch"] == str(batch):
                    print(run["id"])
                    return 0
                raise ValueError("pending run exists; resume it")
            metadata = json.loads((batch / "manifest.json").read_text())
            batch_id = metadata["batch_id"]
            if not re.fullmatch(r"[A-Za-z0-9_-]+", batch_id):
                raise ValueError("unsafe batch ID")
            if (root / ".dream" / "processed-batches" / f"{batch_id}.json").exists():
                raise ValueError("this immutable batch was already committed")
            names = metadata["transcripts"]
            if len(set(names)) != len(names) or set(names) != {p.name for p in batch.glob("*.txt")}:
                raise ValueError("batch transcript inventory does not match manifest")
            transcripts = {}
            for name in names:
                if Path(name).name != name or not name.endswith(".txt") or (batch / name).is_symlink():
                    raise ValueError("unsafe transcript filename")
                actual = digest(batch / name)
                if actual != metadata["transcript_hashes"][name]:
                    raise ValueError(f"exported transcript hash changed: {name}")
                transcripts[str(batch / name)] = {"hash": actual, "ack": 0, "outstanding": None,
                                                "analysis": "pending", "source_complete": False, "reason": ""}
            source = metadata["source"]
            if source not in ("alma", "input"):
                raise ValueError("unknown batch source")
            boundary = dt.datetime.fromisoformat(metadata["source_boundary"].replace("Z", "+00:00"))
            if boundary.tzinfo is None:
                raise ValueError("source boundary requires a timezone")
            now = dt.datetime.now(dt.timezone.utc)
            ident = now.strftime("%Y%m%dT%H%M%S%fZ")
            run = {"id": ident, "phase": "processing", "batch": str(batch), "batch_id": batch_id,
                   "manifest_hash": digest(batch / "manifest.json"), "source": source,
                   "source_boundary": boundary.isoformat(), "source_hash": metadata["source_hash"],
                   "advances_watermark": source != "input" and metadata.get("advances_watermark", False),
                   "report": f"reports/{ident}.dream.md", "transcripts": transcripts, "owned": {},
                   "baseline_dirty": sorted(changed_paths(root)), "gaps": [], "started": now.isoformat()}
            save(state, run)
            bind(run_id=ident, batch_id=batch_id)
            event("batch", "started", "Frozen batch ready for transcript review", transcript_count=len(transcripts))
            print(ident)
            return 0
        if args.action == "status":
            details = dict(run or {"phase": "idle"})
            details["event_logs"] = str(root / ".dream" / "logs" / "events-YYYY-MM-DD.jsonl")
            details["source_checkpoints"] = {p.name.removeprefix("last-run-"): p.read_text().strip() for p in (root / ".dream").glob("last-run-*")}
            legacy = root / ".dream" / "last-run"
            details["legacy_checkpoint"] = legacy.read_text().strip() if legacy.exists() else None
            pointer = root / ".dream" / "pending-batch.json"
            details["pending_export"] = json.loads(pointer.read_text()) if pointer.exists() else None
            print(json.dumps(details, indent=2, sort_keys=True))
            return 0
        if not run:
            raise ValueError("no active dream run; use dream begin --batch DIR")
        if run["phase"] in ("success", "committing"):
            if args.action != "commit":
                raise ValueError(f"run is {run['phase']}; only commit retry is allowed")
            receipt = finish_metadata(root, state, run) if run["phase"] == "success" else publish_prepared(root, state, run)
            print(json.dumps(receipt))
            return 0
        if args.action in ("read", "ack", "complete", "resolve"):
            if args.thread not in run["transcripts"]:
                raise ValueError("--thread must be a manifest transcript path")
            record = run["transcripts"][args.thread]
            path = Path(args.thread)
            if digest(path) != record["hash"]:
                raise ValueError("transcript hash changed")
            text = path.read_text(encoding="utf-8")
            if args.action == "read":
                if not 1 <= args.limit <= MAX_CHUNK:
                    raise ValueError(f"--limit must be between 1 and {MAX_CHUNK}")
                outstanding = record["outstanding"]
                start = record["ack"] if outstanding is None else outstanding["start"]
                if args.offset not in (None, start):
                    raise ValueError(f"non-contiguous offset; expected {start}")
                if outstanding is None:
                    outstanding = {"start": start, "end": min(start + args.limit, len(text))}
                    record["outstanding"] = outstanding
                save(state, run)
                event("read", "emitted", "Transcript chunk emitted; awaiting review acknowledgement", transcript=path.name, start_character=start, end_character=outstanding["end"])
                sys.stdout.write(text[start:outstanding["end"]])
                return 0
            if args.action == "ack":
                outstanding = record["outstanding"]
                if outstanding is None or args.offset not in (None, outstanding["start"]):
                    raise ValueError("no matching outstanding chunk to acknowledge")
                record["ack"] = outstanding["end"]
                record["outstanding"] = None
                save(state, run)
                event("read", "acknowledged", "Transcript chunk review acknowledged", transcript=path.name, through_character=record["ack"])
                print(f"acknowledged through character {record['ack']}")
                return 0
            if args.action == "resolve":
                matches = [gap for gap in run["gaps"] if gap["thread"] == args.thread and not gap["resolved"]]
                if not matches or not args.gaps.strip():
                    raise ValueError("resolve needs an unresolved gap and evidence provenance in --gaps")
                for gap in matches:
                    gap.update(resolved=True, resolution=args.gaps.strip())
                if record["analysis"] == "analyzed":
                    record["source_complete"] = True
            else:
                if args.status == "skipped":
                    if not args.reason.strip():
                        raise ValueError("skipped transcript requires an explicit exclusion --reason")
                elif args.status == "analyzed":
                    if record["ack"] != len(text) or record["outstanding"] is not None:
                        raise ValueError("transcript has unread or unacknowledged content")
                    if not args.source_complete and not args.gaps.strip():
                        raise ValueError("supply --source-complete or record unresolved --gaps")
                else:
                    raise ValueError("--status required")
                record.update(analysis=args.status, source_complete=args.status == "analyzed" and args.source_complete and not args.gaps.strip(), reason=args.reason.strip())
                if args.gaps.strip():
                    run["gaps"].append({"thread": args.thread, "reason": args.gaps.strip(), "resolved": False})
            run["phase"] = "processing"
            save(state, run)
            event("analysis", "recorded", "Transcript analysis outcome updated", transcript=path.name, analysis=record["analysis"], source_complete=record["source_complete"], unresolved_gaps=sum(not g["resolved"] for g in run["gaps"]))
            return 0
        if args.action in ("own", "amend"):
            paths = [args.path] if args.action == "amend" else sys.stdin.read().splitlines()
            for rel in paths:
                if not rel:
                    raise ValueError("owned path required")
                path = owned_path(root, rel)
                if not path.is_file() or rel in run["baseline_dirty"]:
                    raise ValueError(f"missing or pre-existing dirty path: {rel}")
                if rel in git(root, "diff", "--cached", "--name-only", "-z").split("\0"):
                    raise ValueError(f"cannot adopt pre-staged path: {rel}")
                previous = run["owned"].get(rel)
                actual = digest(path)
                if args.action == "amend":
                    if previous is None or args.expected_hash != previous:
                        raise ValueError("amend requires the previously registered --expected-hash")
                elif previous is not None and previous != actual:
                    raise ValueError("owned artifact changed; use explicit amend with its registered hash")
                run["owned"][rel] = actual
            run["phase"] = "processing"
            save(state, run)
            event("artifacts", "registered", "Owned artifact hashes recorded", owned_count=len(run["owned"]))
            return 0
        if args.action == "commit":
            if run["phase"] == "partial":
                print(json.dumps(receipt_for(run)))
                return 0
            check_sources(run)
            pending = any(record["analysis"] == "pending" for record in run["transcripts"].values())
            if pending and not args.partial:
                raise ValueError("unread/pending transcripts; use --partial to save an incomplete run")
            unresolved = any(not gap["resolved"] for gap in run["gaps"])
            incomplete_source = any(record["analysis"] == "analyzed" and not record["source_complete"] for record in run["transcripts"].values())
            outcome = "partial" if pending or unresolved or incomplete_source else "success"
            for rel, expected in run["owned"].items():
                if digest(owned_path(root, rel)) != expected:
                    raise ValueError(f"owned artifact changed: {rel}")
            staged_paths = set(git(root, "diff", "--cached", "--name-only", "-z").split("\0"))
            if staged_paths.intersection(set(run["owned"]) | {run["report"]}):
                raise ValueError("owned paths have separately staged edits")
            for command in ("verify", "lint"):
                check_started = time.monotonic()
                event(command, "started", f"Running wiki {command}")
                result = subprocess.run(["bash", str(Path(__file__).with_name("wiki")), command], env=dict(os.environ, WIKI_HOME=str(root)))
                event(command, "succeeded" if result.returncode == 0 else "failed", f"Wiki {command} finished", exit_code=result.returncode, duration_ms=round((time.monotonic() - check_started) * 1000))
                run.setdefault("checks", {})[command] = result.returncode
                save(state, run)
                if result.returncode:
                    raise ValueError(f"wiki {command} failed")
            write_report(root, run, outcome)
            save(state, run)
            paths = sorted([*run["owned"], run["report"]])
            hashes = {rel: digest(root / rel) for rel in paths}
            parent = head_commit(root)
            index = root / ".dream" / f"index-{run['id']}"
            env = dict(os.environ, GIT_INDEX_FILE=str(index))
            try:
                git(root, "read-tree", *([parent] if parent else ["--empty"]), env=env)
                git(root, "add", "--", *paths, env=env)
                tree = git(root, "write-tree", env=env).strip()
                if any(digest(root / rel) != expected for rel, expected in hashes.items()):
                    raise ValueError("owned files changed during staging")
                message = (args.m or f"dream {run['id']}: {outcome}") + f"\n\nDream-Run: {run['id']}\n"
                candidate = git(root, "commit-tree", tree, *(["-p", parent] if parent else []), input=message).strip()
            finally:
                index.unlink(missing_ok=True)
            run["prepared"] = {"parent": parent, "tree": tree, "commit": candidate, "paths": paths,
                               "hashes": hashes,
                               "normal_index": index_entries(root, paths), "outcome": outcome,
                               "prepared_at": dt.datetime.now(dt.timezone.utc).isoformat()}
            run["phase"] = "committing"
            save(state, run)
            event("commit", "prepared", "Exact candidate commit saved before publication", commit=candidate, owned_count=len(paths), outcome=outcome)
            print(json.dumps(publish_prepared(root, state, run)))
            return 0
    except (OSError, ValueError, subprocess.CalledProcessError, json.JSONDecodeError, KeyError) as error:
        event("validation", "failed", "Dream command rejected; inspect command stderr for details", error_type=type(error).__name__)
        print(f"dream {args.action}: {error}", file=sys.stderr)
        if isinstance(error, subprocess.CalledProcessError) and error.stderr:
            print(error.stderr.strip(), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(run_logged(main, "workflow"))
