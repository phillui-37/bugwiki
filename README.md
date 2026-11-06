# bugwiki

A bug-and-fix wiki for coding agents. One markdown page per costly bug: the exact
symptom, the cause, the fix, the attempts that failed, and the check that proves
it. Your agent searches it before debugging, so the second agent does not repeat
the first agent's hour.

No database, no daemon, no embeddings service. A directory of markdown files and
`ripgrep`.

## Why

An agent that spends 40 minutes on a provider 429 learns something valuable and
then forgets it, because the lesson lives in a chat log that scrolls away. The
next session pays the same 40 minutes again.

Memory files do not fix this: they hold conclusions, not the reproduction. When
the conclusion is "check the key's product line", the agent still lacks the
symptom to match on and the failed attempts to avoid.

## What it looks like

```text
~/.agents/wiki/
  README.md                      router + index
  INDEX.md                       index instead, when the store is a repo clone
  LESSONS.md                     rules that repeated (empty is a valid state)
  solutions/<domain>/<slug>.md   one bug per file
  templates/entry.md
  reports/<date>.md              daily housekeeping report
  archive/<domain>/              obsolete pages, out of circulation, still on disk
  bin/wiki                       the helper (bash)
  bin/wiki.ps1 / wiki.cmd        the helper (Windows)
```

A page:

```markdown
---
title: Z.ai Coding Plan key returns 429 for every model
status: workaround
recurrence: 1
env: macOS 27.2 arm64, provider zai-coding-plan
last_seen: 2026-11-02
last_verified: 2026-11-02
oneliner: A 429 from a provider with separate key families can be an entitlement
  answer rather than a rate limit; check which product line minted the key.
---

## Symptom
Paste the exact error text — search is textual.

## Cause
What you actually confirmed. Mark a guess as a guess.

## Fix
The commands that worked, in order.

## Failed attempts
What you tried and why it could not work. This is the expensive part to rediscover.

## Verify
A command that shows output which changes. Printing a constant proves nothing.
```

## Install

**macOS / Linux**

```sh
git clone https://github.com/phillui-37/bugwiki ~/.agents/wiki
bash ~/.agents/wiki/scripts/wiki init ~/.agents/wiki
ln -sf ~/.config/alma/skills/bugwiki/scripts/wiki ~/.local/bin/wiki
```

**Windows (PowerShell 5.1 or 7+)**

```powershell
git clone https://github.com/phillui-37/bugwiki $HOME\.agents\wiki
powershell -NoProfile -ExecutionPolicy Bypass -File $HOME\.agents\wiki\scripts\wiki.ps1 init $HOME\.agents\wiki
# then add $HOME\.agents\wiki\bin to your PATH — `wiki.cmd` is the shim, so `wiki find "..."` works
```

Then append the block from `assets/AGENTS-block.md` to your agents instruction
file (`~/.agents/AGENTS.md`, `AGENTS.md`, `CLAUDE.md` — whatever your host reads).
That block is what makes the store live: search before debugging, write after a
costly fix, bump when a page answers you.

Cloning the repo *is* the store. `wiki index` then writes `INDEX.md` instead of
touching this README, and `solutions/`, `reports/`, `archive/`, `bin/`,
`templates/` and `LESSONS.md` are gitignored — so `git status` stays clean while
you use it.

Same store on both platforms: the frontmatter, the reports, the archive rules and
the exit codes match. Only the helper differs — `scripts/wiki` (bash) or
`scripts/wiki.ps1` + `scripts/wiki.cmd` (Windows).

Hosts that scan skill directories can install this repo directly as a skill —
`SKILL.md` is at the root.

## Usage

| Command | Does |
| --- | --- |
| `wiki find "429 Too Many Requests"` | literal search; `WIKI_REGEX=1` for a regex |
| `wiki list` | every page as `domain/slug` |
| `wiki new dsh config-editor-loss` | new page from the template, reindexes |
| `wiki index` | regenerate the README index table |
| `wiki lint` | bad fields, stale pages, promotion candidates; exits 1 on errors |
| `wiki housekeep [--apply]` | archive queue, promotions, duplicate pairs → `reports/<date>.md` |
| `wiki init [target]` | scaffold a new store from the bundled assets |

On Windows the same commands exist as `wiki.cmd` (calls `wiki.ps1` with `pwsh`
when installed, `powershell` otherwise). `WIKI_REGEX=1` works there too —
`$env:WIKI_REGEX=1`.

## Two gates

**New page: gated on cost.** 30+ minutes on the fix, or 3+ failed attempts, or the
error text does not explain the cause. A typo or a missing dependency the error
already named gets no page. Cheap entries turn search into noise.

**Recurrence bump: never gated.** If a page answered your search, bump
`recurrence:` before you move on. A wiki hit is cheap by definition, so gating the
bump would starve the only counter the ladder uses.

## Promotion ladder

| Occurrence | Where it lives |
| --- | --- |
| 1st, costly | `solutions/<domain>/<slug>.md` |
| 2nd | one line in `LESSONS.md` |
| 3rd | a standing rule in your agents instruction file |

`recurrence:` is a number on the page, not a judgement. Housekeeping names pages at
2; you decide when a rule is general enough for 3. Writing a second page for the
same bug resets the counter to 1 — search first.

## Housekeeping

A daily cron (`wiki housekeep --apply`) reindexes and moves pages whose
`obsolete_since` is older than 90 days into `archive/`. It never deletes a page and
never overwrites an archived file; a collision is reported instead.

The report lists what needs a human: pages at `recurrence: >= 2`, pages whose
`last_verified` is older than 180 days (the fix may have rotted with the tool), and
possible duplicates by `oneliner` token overlap.

Thresholds are env-overridable: `WIKI_STALE_DAYS`, `WIKI_ARCHIVE_DAYS`,
`WIKI_LESSONS_MAX`, `WIKI_DUP_JACCARD`, `WIKI_DUP_MIN_PAGES`, `WIKI_REPORTS_KEEP`.

## Prior art and how this differs

| Project | What it does | Difference |
| --- | --- | --- |
| [yetone/magpie](https://github.com/yetone/magpie) | `LESSONS.md` written by a nightly commit review, promoted at 3 review days into `docs/code-standards.md` | There is no nightly review here, so entry is gated on the cost the agent just paid, and promotion starts at the second occurrence |
| [compound-engineering](https://github.com/EveryInc/compound-engineering-plugin) `ce-compound` | captures solved problems under `docs/solutions/` in a repo | This is machine-wide (`~/.agents/`), not per-repo, and spans tools rather than one codebase |
| `CLAUDE.md` / `AGENTS.md` memory | rules and conclusions | A rule without the symptom does not match; a symptom without failed attempts gets re-tried |

The parts borrowed from magpie are named; the parts invented here are the cost
gate, the second-occurrence bar, and the `recurrence:` counter.

## Design notes

- **Two helpers, one contract.** `scripts/wiki` (bash) and `scripts/wiki.ps1`
  produce the same output, exit codes and report layout, so an agent on either
  platform reads the same store. The PowerShell port uses `rg` when it exists and
  falls back to `Select-String`, because Windows machines often lack ripgrep.
- **bash + ripgrep.** Retrieval is `rg -F` over a few dozen files. At 3 pages this
  is enough; at 500 it is still fast, and the failure mode (a paraphrase the
  keywords miss) is visible in the search output rather than hidden in a ranking.
- **`oneliner:` is the join key.** The second occurrence usually arrives in
  different words, so the reusable sentence is what catches it — `wiki find`
  prints it first.
- **`last_seen` and `last_verified` are separate.** Recurrence and validity decay
  at different speeds.
- **Nothing is deleted.** Obsolete pages move to `archive/`; reports keep the
  newest 30.

## License

MIT.

## Facts, edges and the dream

The wiki holds two kinds of node in one namespace: `solutions/<domain>/<slug>.md`
(a bug that cost real time) and `facts/<slug>.md` (something now true, with a
`source:`). `kind` is derived from the path, so pages written before the schema
need no edit.

```bash
wiki show <slug>        # kind, validity, inbound edges, supersession chain
wiki stale              # expired, or past the per-kind limit
wiki verify             # schema, dates, every edge resolves
wiki dream export       # Alma threads -> .raw/<date>/*.txt (compact)
wiki dream commit       # one git commit + one line in reports/<date>.dream.md
```

A changed fact is never deleted: the old node gets `valid_to` + `status:
obsolete`, the new one gets `supersedes: [old-slug]`. `PROTOCOL.md` has the
thresholds and the handler model; `CHEZMOI.md` has the sync split (code syncs,
content does not).

`tests/run.sh` is the offline regression suite (21 checks, no network).
