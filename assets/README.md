# Agent Wiki

This directory is a machine-local content store. The installed bugwiki package
holds scripts and templates elsewhere; do not put package code or a public
remote in this content repository. See the package's README and PROTOCOL.md for
installation, platform support and the full contract.

## Layout

```text
solutions/<domain>/<slug>.md   costly bugs, fixes and failed attempts
facts/<slug>.md                sourced facts with scope and validity
README.md                     this guide and the generated index
LESSONS.md                    short lessons from repeated occurrences
templates/entry.md             template for new solutions
reports/<date>.md              housekeeping report
reports/<date>.dream.md        dream commit log
archive/<domain>/             obsolete solutions, retained on disk
.raw/                         transcript exports; ignored by Git and search
.dream/                       source checkpoints; ignored by Git
.git/                         local knowledge history
```

## Search and reuse

Run `wiki find "<exact error or identifier>"`. Start with one or two matching
pages; check environment, status and verification dates. Follow supersession to
its current endpoint, inspecting extra nodes when needed. An obsolete, conflicting,
expired or ended fact is not current knowledge; an open fact needs verification.
`wiki show` has a ten-hop display cap and does not resolve branching for you.

When a page answers a question, update `last_seen` and `recurrence`, counting
occurrences on distinct dates. Reuse is not subject to the cost gate below.

## Write

- `wiki new <domain> <slug>` creates a solution. Use it for a fix that took 30+
  minutes, three failed attempts, or a cause the error did not explain. Keep the
  exact symptom, environment, failed attempts, working fix and verification.
- `wiki new fact <slug>` creates a fact. Supply a real source and scope. Use
  `verified` for a claim backed by a source tool result; narration or inference
  remains `open`. Set an explicit expiry for volatile state, normally 14 days.
- When a fact changes, retain the old node with `status: obsolete` and `valid_to`,
  and link the new node with `supersedes`. Do not create duplicate accounts of
  the same knowledge. Never write credentials.

Use flat frontmatter and inline edge lists. Dates are `YYYY-MM-DD`; preserve
original timestamps and timezones in the body when needed. Run `wiki verify`
and `wiki lint` before a content commit. These check structure, not source truth.
Graph commands and fact creation require the Bash helper; native Windows covers
solution pages only. Date-dependent Bash commands require BSD-style `date`.

## Maintenance

Nothing is scheduled automatically. Run or schedule `wiki housekeep --apply`
when wanted. It archives obsolete solutions after 90 days from `obsolete_since`,
never overwrites an archived page, and retains the newest 30 report entries.
Missing `obsolete_since` is reported rather than guessed. `wiki stale` reports
fact and solution staleness; an agent must perform the verification.

Default re-verification ages are 180 days for solutions and 90 for stable or
promoted facts. Explicit expiry takes priority. The package documents the
`WIKI_*` threshold overrides.

The promotion policy is first costly occurrence → solution, second → a line in
`LESSONS.md`, third → a separately approved agent rule. Housekeeping reports
candidates; it does not promote them. Dream analysis may write facts and qualifying
solutions, but not unconditional agent instructions or `maturity: promoted`.

For dream work, export explicit input with `wiki dream export --input FILE` (or
explicitly use `--source alma`), have an agent analyze the transcripts, verify the
pages, and run `wiki dream commit`. Prepare local Git history and identity before
analysis writes. The commit stages all content-root changes; inspect them first.
Raw exports and checkpoints stay untracked. Reports use the `.dream.md` suffix so
housekeeping does not overwrite that day's dream log.

## Index

<!-- index:start -->

| Page | Domain | Status | Last seen |
| --- | --- | --- | --- |

<!-- index:end -->
