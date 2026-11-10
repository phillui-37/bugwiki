# Resumable dream runs

Each export publishes an immutable, content identified batch directory under `.raw/<date>/<batch-id>/` with a manifest. Publication uses a staging directory and one atomic rename, so interruption cannot expose a partial batch. Repeating the same selection returns the same batch. A non-input source checkpoint advances to the captured selection boundary only after verified commit.

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

An unacknowledged `read` replays the same chunk after retries or restarts; it never advances the cursor until `ack`. Chunk limits are capped at 12,000 characters, and offsets count Unicode characters. `complete` requires all characters acknowledged. `--status analyzed --source-complete` records the operator's explicit source coverage attestation; truncation markers alone neither establish nor invalidate that attestation. Use `--status skipped --gaps 'reason'` to record an exclusion. A skipped transcript appears in the report and does not block subsequent runs. Other incomplete or unread transcripts remain pending. Gaps may be recorded and committed as partial work; the report makes them visible and the run remains resumable. Resolve a gap with provenance to reviewed evidence before claiming complete coverage:

```sh
wiki dream resolve --thread "$WIKI_HOME/.raw/2026-11-10/<batch-id>/thread.txt" --gaps 'reviewed source message msg-123'
wiki dream commit
```

Partial commits do not advance the source checkpoint or mark the batch processed. After all gaps are resolved, commit again to finalize the run and advance the checkpoint to the captured selection boundary.

Register exact output files after creating or editing them:

```sh
wiki dream own <<'EOF'
facts/known-fact.md
reports/2026-11-10.dream.md
EOF
wiki verify && wiki lint
wiki dream commit -m 'dream: process 2026-11-10 batch'
```

Commit checks source and owned file hashes, transcript outcomes, `verify`, and `lint`. It stages registered paths through a temporary Git index and removes that index on every exit. The run report records batch identity, selection boundary and hash, counts, and per transcript outcomes. Each run has a durable receipt with the report hash, written after commit; this avoids a circular hash. Commit retries reconcile the prepared run with Git and return the same receipt. A batch with no facts still receives a truthful report and receipt.

To stop before a job timeout, stop beginning new transcripts, finish or leave the current chunk unacknowledged, and retain the content root. Resume with `wiki dream progress`; the workflow does not configure or change scheduler timeouts.
