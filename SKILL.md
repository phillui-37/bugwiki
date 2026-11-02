---
name: bugwiki
description: A bug/solution wiki for agents — markdown pages that stop the same failure from being debugged twice. Use before debugging a familiar error, when a provider/config/tool failure needs a known root cause, or after a costly fix that should be written down. Ships the store layout, the `wiki` helper, and the promotion ladder.
---

# bugwiki

One markdown page per costly bug: exact symptom, cause, the fix that worked, the
attempts that failed, and the check that proves it. The point is that the second
agent does not repeat the search.

Everything lives in a plain directory (`~/.agents/wiki` by default). No database,
no daemon. `rg` does the retrieval.

## Quick start (new machine)

```sh
# from this skill directory
bash scripts/wiki init                 # scaffolds ~/.agents/wiki (refuses if not empty)
cp ~/.agents/wiki/bin/wiki ~/.local/bin/wiki   # or: ln -s ~/.agents/wiki/bin/wiki ~/.local/bin/wiki
cat ~/.agents/wiki/AGENTS-snippet.md   # append this block to your agents instruction file
```

The block is the real hook: it tells every agent to search the wiki before
debugging and to write to it after a costly fix. Without it, the store is dead
weight.

## Daily use

```sh
wiki find "429 Too Many Requests"    # literal search; WIKI_REGEX=1 for a regex
wiki list                            # domain/slug, every page
wiki new dsh config-editor-loss      # new page from the template, reindexes
wiki lint                            # bad fields, stale pages, promotion candidates
wiki housekeep                       # report: archive queue, promotions, duplicates
wiki housekeep --apply               # also move obsolete pages into archive/
```

## The two gates

**A page that answered your search is evidence.** Bump it — never gated on cost:

```yaml
recurrence: 2
last_seen: 2026-11-19
```

**A new page is gated on cost.** 30+ min on the fix, or 3+ failed attempts, or
the error text did not explain the cause. A typo or a flag the error already named
gets no page — noise makes search worthless.

## Promotion ladder

| Occurrence | Where |
| --- | --- |
| 1st, costly | `solutions/<domain>/<slug>.md` |
| 2nd | one line in `LESSONS.md` |
| 3rd | a standing rule in the agents instruction file, drop the LESSONS line |

`recurrence:` is the counter. Writing a second page for the same bug resets it to
1 and stalls the ladder — search first.

## What is in the box

```text
SKILL.md              this file
scripts/wiki          the helper: init / find / new / index / lint / housekeep / list
assets/README.md      the store's router and write protocol (copied into the store)
assets/LESSONS.md     the promoted-rules file, empty with its format
assets/templates/entry.md   page template with every field explained
assets/AGENTS-block.md      the 10-line hook for the agents instruction file
```

The store keeps its own copy of `bin/wiki`, so it works with any host and any
agent, not only the one that installed it.

## Rules that keep it honest

- No page for an unverified fix. The whole value is that entries are true.
- `Verify` must show output that changes. A command printing a constant proves
  nothing.
- Keep the failed attempts, with the reason each could not work — that is the
  expensive part to rediscover.
- Never record credentials, keys or tokens in a page.
- Never delete a page: mark `status: obsolete`, set `obsolete_since:`, and let
  housekeeping move it to `archive/`.
