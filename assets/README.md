# Agent Wiki

One markdown page per costly bug. The page holds the exact symptom, the cause,
the fix that worked, and the attempts that failed. Read it before you debug,
write it after you verify — but only if the bug cost real time.

Layout borrowed from [magpie](https://github.com/yetone/magpie), with three
deliberate differences. Magpie's `LESSONS.md` takes a lesson on the day a nightly
commit review finds it and promotes it at 3 review days into
`docs/code-standards.md`; its `docs/subsystems/` pages document current contracts,
not bug history. Here:

1. **Entry bar is cost, not first sight** — there is no nightly review writing
   this file, so the writer is the agent that just spent an hour debugging.
2. **`LESSONS.md` starts at the second occurrence** — a personal wiki spans every
   tool on the machine and would flood at magpie's bar.
3. **Promotion lands in `AGENTS.md`** — the file every agent already reads, not a
   separate standards doc. There are no contract pages here; `solutions/` is
   incident history and is not a substitute for them.

## Two gates, not one

**New page — gated on cost.** Not every fix deserves a page:

| Signal | Costly? |
| --- | --- |
| Wall clock 30+ min on the fix | yes |
| 3+ attempts that failed before one worked | yes |
| Root cause is not visible in the error text | yes |
| Typo, wrong flag, missing dependency named by the error | no |

Costly → one page. Cheap → nothing. A wiki with every typo in it is worse than no
wiki, because search returns noise and nobody trusts the results.

**Recurrence bump — not gated at all.** If an existing page answered your search,
bump `recurrence:` and `last_seen:` before you continue. A wiki hit is cheap by
definition, so applying the cost gate here would suppress the only counter the
ladder runs on.

## Fields that carry meaning

| Field | Written when | Read by |
| --- | --- | --- |
| `recurrence:` | the bug bites again | `lint`, `housekeep` → promotion |
| `last_seen:` | the bug bites again | nothing automatic; recency of recurrence |
| `last_verified:` | you re-check the fix still works | `lint`, `housekeep` → staleness |
| `obsolete_since:` | you set `status: obsolete` | `housekeep` → archiving starts here |

## The ladder (one fact, one place)

| Occurrence | Lives in | Moved by |
| --- | --- | --- |
| 1st, costly | `solutions/<domain>/<slug>.md` | `wiki new` |
| 2nd | one line in `LESSONS.md` | `wiki lint` flags the page, you add the line |
| 3rd | `AGENTS.md` standing rule | you, and delete the `LESSONS.md` line |

Recurrence is `recurrence:` on the page, counted in occurrences on separate days.
Bump it when the bug bites again; that counter is the only reason the chain
works. Writing a second page for the same bug resets it to 1 — the failure mode
to avoid.

## Housekeeping

`wiki housekeep` runs daily at 09:00 from a scheduled job. It writes
`reports/<date>.md` and never deletes a page: an obsolete one moves to
`archive/` and stays on disk.

| Threshold | Default | Env override | Applies to |
| --- | --- | --- | --- |
| Stale | 180 d | `WIKI_STALE_DAYS` | `last_verified` older than N days → verify or mark obsolete |
| Archive | 90 d | `WIKI_ARCHIVE_DAYS` | `obsolete_since` older than N days → moved out of `solutions/` |
| LESSONS max | 12 rows | `WIKI_LESSONS_MAX` | above this, promote to `AGENTS.md` or drop |
| Duplicate | 55 % | `WIKI_DUP_JACCARD` | `oneliner:` token overlap that counts as a duplicate |
| Dup scan starts | 8 pages | `WIKI_DUP_MIN_PAGES` | below this the pair scan is noise |
| Reports kept | 30 | `WIKI_REPORTS_KEEP` | newest N daily reports |

`--apply` (what cron runs) moves the archive queue and reindexes. Everything else
is left in the report for a person to decide, because "this is obsolete" is a
judgement about a tool, not a fact about a date.

Two limits are deliberate: a page with `status: obsolete` but no
`obsolete_since:` is reported, never moved — otherwise marking it obsolete would
archive it the same day. And a move never overwrites an existing file in
`archive/`; a collision is reported instead. `reports/` keeps the newest 30 and
deletes the rest, so "nothing is deleted" applies to pages only.

## Layout

```text
~/.agents/wiki/
  README.md              this file: the router, plus the index table
  LESSONS.md             rules that repeated; empty is a valid state
  solutions/<domain>/<slug>.md   one bug per file
  templates/entry.md     copy this for a new entry
  reports/<date>.md      daily housekeeping report (newest 30 kept)
  archive/<domain>/      obsolete pages, out of circulation, still on disk
  scripts/wiki           find / new / index / lint / housekeep / list
  install.sh             copy to ~/.agents/wiki, link `wiki`, install the skill
  AGENTS-snippet.md      the block installed in ~/.agents/AGENTS.md
```

Domains in use: `alma`, `dsh`. Add a domain only when a second entry needs it.
Never nest deeper than `solutions/<domain>/`.

## Read protocol (cheap, do it first)

1. Search the exact string from the error before you form a theory:

   ```sh
   wiki find "429 Too Many Requests"
   # or, no helper installed:
   rg -i -l "429|coding plan" ~/.agents/wiki/solutions
   ```

2. Open the 1–2 matching pages only. Do not load the whole tree.
3. Check `env:`, `status:` and `last_verified:`. A `verified` fix from a different
   macOS major version or provider product line is a lead, not an answer; an old
   `last_verified` means the fix may have rotted with the tool.
4. If it answered you, bump `recurrence:` and `last_seen:` before moving on.

## Write protocol

1. `wiki new alma zai-coding-plan-key-429` — creates the page from the template
   and reindexes.
2. Fill every section. **Keep the failed attempts**: they are the expensive part
   to rediscover.
3. Paste exact error text, not a paraphrase. Search is textual.
4. Set `oneliner:` — the reusable sentence behind the bug. That is the promotion
   candidate, and the thing `wiki find` prints first, because the second
   occurrence usually arrives in different words than the first.
5. Bug again? Edit that page: `recurrence: 2`, new `last_seen:`. Do not create a
   second page — that resets the counter and the ladder stalls.
6. `wiki lint` before you leave. It reports stale pages, missing fields, and
   anything at `recurrence: >= 2` that belongs in `LESSONS.md`.

## Index

<!-- index:start -->

| Page | Domain | Status | Last seen |
| --- | --- | --- | --- |
| [Z.ai Coding Plan key returns 429 for every model](solutions/alma/zai-coding-plan-key-429.md) | alma | workaround | 2026-11-02 |
| [dsh-config-editor silently deletes config blocks it does not model](solutions/dsh/config-editor-rewrites-whole-document.md) | dsh | verified | 2026-11-02 |
| [DeepSeek Harness fails because the continuation session keeps the old provider](solutions/dsh/continuation-session-keeps-old-provider.md) | dsh | verified | 2026-11-02 |
<!-- index:end -->
