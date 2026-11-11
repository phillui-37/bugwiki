# Resumable dream runs

Each export publishes an immutable, content identified batch directory under `.raw/<date>/<batch-id>/` with a manifest. Publication uses a staging directory and one atomic rename, so interruption cannot expose a partial batch. Repeating the same selection returns the same batch. The manifest stores `since` (the lower selection bound) separately from `source_boundary`/`selected_at` (the UTC upper bound captured immediately before source fetch). Only a fully successful Alma batch with no older unresolved backlog advances its full-source checkpoint to that upper bound; `--thread` manifests set `advances_watermark: false`, and `--input` never advances a checkpoint. Export publishes `.dream/pending-batch.json` after the immutable directory so a crash before `begin` leaves a discoverable batch. Batch IDs include rendering limits, and retries search all date directories and verify exact transcript files, SHA256 hashes, and identity metadata before reuse. Each manifest has `transcript_hashes`, keyed by transcript filename. New batches also store canonical pre-render per-thread source JSON in `.raw` with `source_files` SHA256 hashes; this is lossless relative to the payload received from the source adapter and cannot restore bytes already truncated upstream. `wiki dream source --thread <id> --offset <n>` reads bounded source JSON; it never acknowledges or implies review. Older compact-only manifests stay immutable and report that full source is unavailable. An empty selection is a valid zero-transcript batch.

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

An unacknowledged `read` replays the same chunk after retries or restarts; it never advances the cursor until `ack`. Chunk limits are capped at 12,000 characters, and offsets count Unicode characters. `complete` requires all characters acknowledged. `wiki dream ack --thread <path> --next` acknowledges the outstanding chunk and emits the next bounded chunk in one call; it reserves the next chunk without acknowledging that new text. `--status analyzed --source-complete` records the operator's explicit source coverage attestation; truncation markers alone neither establish nor invalidate that attestation. Use `--status skipped --reason 'reason'` to record an intentional exclusion. `--gaps` records missing evidence and must not be used as a substitute for a skip reason. A skipped transcript appears in the report and does not block subsequent runs. Other incomplete or unread transcripts remain pending. Pending/unread transcripts block finalization unless `--partial` is used to save genuinely unfinished work. A fully reviewed compact transcript with unresolved evidence gaps can finalize as `reviewed_with_gaps`: it is recorded as processed, advances `.dream/last-reviewed-<source>` for selection, leaves `.dream/last-run-<source>` unchanged, and persists gaps under `.dream/unresolved-backlog/`. Gaps carry forward and are never blindly retried. Resolve explicit provenance to reviewed evidence before claiming source-complete coverage. A failed owned file can be repaired only by an explicit amend that names its recorded prior hash; never silently replace it: `wiki dream amend --path facts/known-fact.md --expected-hash <registered-sha256>`. Resolve a gap with provenance to reviewed evidence before claiming complete coverage:

```sh
wiki dream resolve --thread "$WIKI_HOME/.raw/2026-11-10/<batch-id>/thread.txt" --gaps 'reviewed source message msg-123'
wiki dream commit
```

`wiki dream commit --partial` saves outputs while transcripts remain unread and leaves the run resumable. All-unread runs cannot finalize without `--partial`. Finished reviews with gaps use ordinary `wiki dream commit`, yielding `reviewed_with_gaps` without attesting source completion. Both complete reviews and reviews with gaps advance the monotonic reviewed cursor. A later successful batch cannot move the full-source-success frontier past an older unresolved backlog. Retry of an older receipt cannot move either cursor backwards. A reviewed-only cursor keeps later selection moving while the full-source-success cursor waits for evidence.

An existing old-version partial receipt with no pending compact reviews can be finalized by ordinary `wiki dream commit`; its transcripts, original gaps, and historical commit receipts remain intact. Old batches are never rewritten to add snapshots. `wiki dream source` also works after review finalization, returns exact snapshot characters at the requested offset, and rejects limits outside 1..12000 without advancing any review acknowledgement. `progress` exposes both checkpoint types and unresolved backlog counts.

For known self-generated Dream sources, pass explicit repeatable IDs:

```sh
wiki dream export --source alma --exclude-thread "$ALMA_THREAD_ID"
```

Use this only when the scheduler has supplied the actual isolated Dream thread ID; never substitute the original user/source thread ID. If identity is unavailable, omit the option and classify manually. The Alma adapter does not fetch excluded message pages. Their source IDs and reasons remain in snapshot/manifest-backed records and appear as skipped with zero acknowledged characters in the report. An ordinary user thread with the same title is retained. No title substring is an exclusion rule.

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

The package has no runtime or spend stop. A retrieval that returns no new source evidence blocks only its related gap; continue all independent transcript reviews and fact work. Do not retry an unchanged source query. Pause only when no authorized next action can progress, and record the exact missing input so the run can resume without rereading acknowledged chunks. There is no per-night partial-spend quota.
