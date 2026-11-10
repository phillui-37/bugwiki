# Resumable dream runs

Each export publishes an immutable, content identified batch directory under `.raw/<date>/<batch-id>/` with a manifest. Publication uses a staging directory and one atomic rename, so interruption cannot expose a partial batch. Repeating the same selection returns the same batch. The manifest stores `since` (the lower selection bound) separately from `source_boundary`/`selected_at` (the UTC upper bound captured immediately before source fetch). Only a fully successful Alma batch advances its checkpoint to that upper bound; `--thread` manifests set `advances_watermark: false`, and `--input` never advances a checkpoint. Export publishes `.dream/pending-batch.json` after the immutable directory so a crash before `begin` leaves a discoverable batch. Batch IDs include rendering limits, and retries search all date directories and verify exact transcript files, SHA256 hashes, and identity metadata before reuse. Each manifest has `transcript_hashes`, keyed by transcript filename. An empty selection is a valid zero-transcript batch.

Start and inspect a run:

```sh
wiki dream begin --batch "$WIKI_HOME/.raw/2026-11-10/<batch-id>"
wiki dream progress
```

For each transcript listed in the manifest, use its exact absolute path. `read` emits at most 12,000 characters by default and resumes at the next contiguous offset. Only acknowledge after reviewing that emitted chunk:

```sh
wiki dream read --thread "$WIKI_HOME/.raw/2026-11-10/<batch-id>/thread.txt" --limit 12000
wiki dream ack --thread "$WIKI_HOME/.raw/2026-11-10/<batch-id>/thread.txt"
wiki dream complete --thread "$WIKI_HOME/.raw/2026-11-10/<batch-id>/thread.txt" --status analyzed --source-complete
```

An unacknowledged `read` replays the same chunk after retries or restarts; it never advances the cursor until `ack`. Chunk limits are capped at 12,000 characters, and offsets count Unicode characters. `complete` requires all characters acknowledged. `--status analyzed --source-complete` records the operator's explicit source coverage attestation; truncation markers alone neither establish nor invalidate that attestation. Use `--status skipped --reason 'reason'` to record an intentional exclusion. `--gaps` records missing evidence and must not be used as a substitute for a skip reason. A skipped transcript appears in the report and does not block subsequent runs. Other incomplete or unread transcripts remain pending. Partial work may be saved while keeping the run resumable; the report makes gaps visible. Resolve explicit provenance to reviewed evidence before claiming complete coverage. A failed owned file can be repaired only by an explicit amend that names its recorded prior hash; never silently replace it: `wiki dream amend --path facts/known-fact.md --expected-hash <registered-sha256>`. Resolve a gap with provenance to reviewed evidence before claiming complete coverage:

```sh
wiki dream resolve --thread "$WIKI_HOME/.raw/2026-11-10/<batch-id>/thread.txt" --gaps 'reviewed source message msg-123'
wiki dream commit
```

`wiki dream commit --partial` also saves reviewed outputs while other transcripts remain unread. Continue `read`/`ack`/`complete` afterward; a partial commit never ends the remaining work. Partial commits do not advance the source checkpoint or mark the batch processed. After all gaps are resolved, commit again to finalize the run and advance the checkpoint to the captured selection boundary.

Register exact output files after creating or editing them. Only fact/solution Markdown pages are accepted; the per-run report is generated automatically. Pre-existing dirty or staged paths, promoted pages, symlinks, and rule/config paths are rejected. Files changed after registration require an explicit hash-checked amendment:

```sh
wiki dream own <<'EOF'
facts/known-fact.md
EOF
wiki verify && wiki lint
wiki dream commit -m 'dream: process 2026-11-10 batch'
```

Commit checks source and owned file hashes, transcript outcomes, `verify`, and `lint`. It stages registered paths through a temporary Git index and cleans that index on normal or caught-error exits. A hard process kill can leave scratch state; retry reconstructs the index. The workflow records the exact candidate Git commit before atomically updating HEAD, then reconciles only owned paths in the regular index. It uses Git plumbing after the explicit verify/lint gates; ordinary Git commit hooks are not invoked. Unrelated staged blobs, modes, deletions, and worktree edits remain intact. The run report records batch identity, selection boundary and hash, counts, and per transcript outcomes. Each run has a durable receipt with the report hash, written after commit; this avoids a circular hash. Commit retries reconcile the prepared run with Git and return the same receipt. A batch with no facts still receives a truthful report and receipt.

To stop before a job timeout, stop beginning new transcripts, finish or leave the current chunk unacknowledged, and retain the content root. Resume with `wiki dream progress`; the workflow does not configure or change scheduler timeouts.


Only one dream writer may operate on a wiki at a time. The workflow is not a concurrent scheduler. It records acknowledgements and source-coverage attestations; it cannot prove that a person or model actually read the emitted text. Resolve missing evidence from its original source before using `--source-complete`; seeing the end of a compact export is insufficient.

The new commands require manifest-backed batches. Existing flat `.raw/<date>/*.txt` exports and older checkpoints are not migrated automatically. Deploy the package and update the caller's command sequence together after any existing recovery is reconciled. This source change does not install itself or alter a live nightly schedule.


## Execution events and timeout tracing

Mutating dream commands append JSONL events under `.dream/logs/events-YYYY-MM-DD.jsonl` (UTC date). Every event has an ISO 8601 UTC timestamp, an invocation ID, action, stage, status, a fixed descriptive message and elapsed milliseconds. Batch and run IDs are attached once known. Source fetch, chunk emission/acknowledgement, analysis outcomes, owned-file registration, verify/lint start and end, commit preparation/publication, and partial/success receipts are recorded. Verification events also carry their own duration; receipt events carry the run elapsed time and commit hash. The duration of a resumed run includes time between invocations.

Logs exclude transcript text, fact bodies, commit-message text, gap explanations, command argument dumps and credentials. Operational identifiers such as transcript filenames remain local. Log output never alters stdout, so chunk reading and JSON consumers keep their existing contracts. `status`/`progress` and export `--dry-run` remain read-only. No automatic log deletion or remote upload occurs.

```sh
# Show the most recent execution events; filename date is UTC.
tail -n 30 "$WIKI_HOME/.dream/logs/events-2026-11-10.jsonl"
wiki dream progress  # includes the event log location
```

A `started` event without a terminal event means no completion event was recorded; it does not prove a hang. Compare timestamps with the scheduler deadline and transport errors. A gap between chunk emission and acknowledgement includes review, model and transport time; the log cannot attribute that gap to one cause.

For a 60-minute scheduler limit, stop starting new transcripts at elapsed minute 50 and reserve the final 10 minutes for progress, verification and a partial commit. This is caller guidance, not an internal timer: configure the scheduler's hard deadline and caller instructions together. The package never changes scheduler settings itself.
