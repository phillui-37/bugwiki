# Resumable dream runs

Exported transcript batches are immutable. `dream export` never advances a successful source checkpoint; a repeated export refuses collisions instead of replacing files. Treat `.raw/<date>/` as a frozen batch.

Start and inspect a run:

```sh
wiki dream begin --batch "$WIKI_HOME/.raw/2026-11-10"
wiki dream progress
```

For each transcript listed in the manifest, use its exact absolute path. `read` emits at most 12,000 characters by default and resumes at the next contiguous offset. Only acknowledge after reviewing that emitted chunk:

```sh
wiki dream read --thread "$WIKI_HOME/.raw/2026-11-10/thread.txt" --limit 12000
wiki dream ack --thread "$WIKI_HOME/.raw/2026-11-10/thread.txt"
wiki dream complete --thread "$WIKI_HOME/.raw/2026-11-10/thread.txt" --status analyzed --source-complete
```

`complete` requires all characters acknowledged. `--status analyzed` also requires `--source-complete`, an explicit human acknowledgement that the compact transcript covers the source. Known exporter truncation markers reject this claim. Use `--status skipped` to record a skipped thread; skipped threads prevent full success and must be resolved in a follow-up workflow. `--gaps 'reason'` records unresolved source coverage and blocks full success. Acknowledgement records an operator assertion that the chunk was reviewed; it cannot measure model comprehension or establish that compact export covers the original source.

Register exact output files after creating or editing them:

```sh
wiki dream own <<'EOF'
facts/known-fact.md
reports/2026-11-10.dream.md
EOF
wiki verify && wiki lint
wiki dream commit -m 'dream: process 2026-11-10 batch'
```

Commit checks manifest hashes, all transcript statuses, unresolved gaps, `verify`, and `lint`. It stages registered paths through an isolated temporary Git index, preserving unrelated staged and unstaged work. Any registered file changed afterwards blocks commit. A successful run writes `.dream/receipt.json` with the commit hash; the commit cannot include its own hash. A batch with no facts can be explicitly completed without owned paths and records the current commit as its receipt, without creating an empty commit.

To stop before a job timeout, stop beginning new transcripts, finish or leave the current chunk unacknowledged, and retain the content root. Resume with `wiki dream progress`; the workflow does not configure or change scheduler timeouts.
