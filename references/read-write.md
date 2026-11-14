Paths in commands are relative to the skill package root unless stated otherwise.

# bugwiki

One store, two entry doors. A page is either a **solution** (a bug that cost real
time) or a **fact** (something now true, with evidence). Both live in the same
namespace, both are `find`-able, and edges between them are what stop an old
answer from being read as the current one.

Code and content are two roots on purpose: `PKG` (this package) is syncable,
`WIKI` (default `~/.agents/wiki`) is machine-local and never synced.

```
wiki find "<exact term>"     # search, with 1-hop neighbours
wiki show <slug>             # kind, validity, inbound edges, supersession chain
wiki stale                   # what is expired or past its per-kind limit
wiki new <domain> <slug>     |  wiki new fact <slug>
wiki verify [--migration]    # schema + edge + date check
wiki lint                    # policy check (statuses, edges, recurrence)
wiki dream export|begin|progress|read|source|recover|ack|complete|own|amend|resolve|commit
```

## Windows

`scripts/wiki.cmd` and `scripts/wiki.ps1` are a separate PowerShell
implementation, not a shim. This section describes the repository port; independently installed native adapters follow their own README. The repository port covers the older surface only: `init`, `list`,
`find`, `new`, `index`, `lint`, `housekeep`. **`show`, `stale`, `verify` and
`dream` exist only in the bash script**, so on Windows treat the graph features
as unavailable rather than absent — use `rg` against `facts/` and `solutions/`
directly, and never claim an edge was checked by `verify`.

## Read protocol

Trigger: before you act on remembered operational state — a branch, a config
value, a version, a preference, a decision made earlier.

1. `wiki find "<exact term>"` — use the error string, identifier or filename, not
   an abstraction.
2. Start with at most **two** matching nodes; read more only to resolve a supersession chain.
3. Follow the `supersedes` chain to its tail. One hop is not enough: A←B←C is
   three nodes and the answer is C. `show` prints at most ten hops and one
   successor per node; inspect sources if that cap or branching leaves ambiguity.
4. Treat `obsolete`, `conflict`, an expired `expires`, or an ended `valid_to` as
   **"I don't know"** for current state. An `open` fact needs verification.
5. If it answered you, update `recurrence` and `last_seen`; count separate dates.
6. Nothing in 60 seconds → do the work, then write it down. Do not browse.

Step 4 is the whole point. A stale record is not wrong because it was never
written; it is wrong because it looks exactly like a current one. Only `status`,
`expires` and `valid_to` can tell them apart.

## Write protocol

Two doors into one store:

| Door | Bar |
| --- | --- |
| `solutions/<domain>/<slug>.md` | costly: 30+ min, or 3+ failed attempts, or the error did not name the cause |
| `facts/<slug>.md` | has a `source:` that points at a real thread/message id, and is not one assistant's opinion |

`status` follows the **kind of evidence**, not your confidence:

- `verified` — the fact traces to a **tool result** in that transcript (a command's
  output, a file's content, an API response)
- `open` — it only traces to prose (an agent's narration, an inference)

When a fact changes, do not delete the old node: set `valid_to`, `status: obsolete`,
and `supersedes: [old-slug]` on the new one. Deletion loses the history that makes
the graph worth having.

Never write a credential. Refer to it by name ("the deploy token").

## Thresholds

| kind | limit | action |
| --- | --- | --- |
| fact, volatile (branch/config/version) | policy: write `expires`, normally 14d | expired = "I don't know" |
| fact, stable (mechanism/causality) | 90d (`WIKI_FACT_STALE_DAYS`) | queue a re-verify |
| solution | 180d (`WIKI_STALE_DAYS`) | verify or mark obsolete |
| promoted | 90d (`WIKI_PROMOTED_STALE_DAYS`) | verify or demote |

Any verifier may clear a stale node; exactly one sweeper owns the queue. Many
hands, one owner — otherwise the queue is nobody's.
