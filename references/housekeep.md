Paths in commands are relative to the skill package root unless stated otherwise.

# Lightweight Housekeeping & Consolidation

Keep the wiki lean so future sessions spend minimal context when searching it. Never ingest raw session transcripts for housekeeping.

## Layer 1 — Deterministic CLI pass (zero LLM tokens)

Works on **macOS, Linux/WSL (`scripts/wiki`)**, and **Windows (`wiki.ps1` / `wiki.cmd`)**:

```bash
# macOS / Linux / WSL
WIKI_HOME="$HOME/.agents/wiki" wiki stale
WIKI_HOME="$HOME/.agents/wiki" wiki housekeep --apply
WIKI_HOME="$HOME/.agents/wiki" wiki verify && WIKI_HOME="$HOME/.agents/wiki" wiki lint
```

```powershell
# Windows PowerShell 7
$env:WIKI_HOME = Join-Path $env:USERPROFILE '.agents\wiki'
wiki stale
wiki housekeep --apply
wiki verify; wiki lint
```

What `housekeep --apply` does deterministically:
1. Moves `status: obsolete` pages whose `obsolete_since` is at least `ARCHIVE_DAYS` (default 30d) old into `archive/<domain>/`.
2. Rebuilds `README.md` (`wiki index`) and trims old `reports/`.
3. Reports pages with `recurrence >= 2` (review candidates for `LESSONS.md`), stale pages, and duplicate `oneliner` candidates.

## Layer 2 — Periodic consolidation (low-token, on demand)

Run occasionally (e.g. weekly or when `wiki stale` / `facts/unsorted/` grows) instead of nightly transcript dreaming:

1. **Orient**: Run `wiki stale` and list `facts/unsorted/`.
2. **Retire expired snapshots**: Mark expired point-in-time status notes as `status: obsolete` with `obsolete_since: YYYY-MM-DD` (never delete history).
3. **Resolve conflicts & promote invariants**: Close resolved `status: conflict` pairs; remove 14-day `expires` from durable technical invariants.
4. **Organize & re-index**: Move surviving `facts/unsorted/` pages into `facts/<domain>/`, update any `related`/`supersedes`/`contradicts` paths, then run `wiki index && wiki verify && wiki lint && wiki stale`.
