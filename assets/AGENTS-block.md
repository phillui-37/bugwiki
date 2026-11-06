# Hook into ~/.agents/AGENTS.md

The only block that should mention the wiki. Replace the older "bug/solution wiki" block with this one; keep the total under a dozen lines, because this file is read every turn.

```md
- memory wiki at ~/.agents/wiki (CLI: ~/.config/alma/skills/bugwiki/scripts/wiki); first place to look, not the last. One graph, two kinds: `solutions/` costly bugs, `facts/` things now true
    - before acting on remembered state: `wiki find "<exact term>"`, open at most 2 nodes, follow `supersedes` to the chain tail; `obsolete`/`conflict`/expired means "I don't know", never an answer
    - before debugging: same search (do not grep the tree by hand); check `env:`/`status:`/`last_verified:` before applying
    - a page that answered a search is evidence: bump `recurrence:` and `last_seen:`, never gated on cost
    - new page gated on cost: 30+ min, 3+ failed attempts, or an error the text did not explain -> `wiki new <domain> <slug>`, keep the failed attempts; a cheap typo/flag/error-named fix gets no page and no bump
    - a fact needs `source:` pointing at a real thread/message id; `status: verified` only when it traces to a tool result, else `open`; a changed fact gets `valid_to` + `status: obsolete` on the old node and `supersedes: [old]` on the new one, never a deletion
    - volatile facts (branch/config/version) carry `expires` (14d default); `wiki stale` lists what is past its limit, `wiki verify` and `wiki lint` must pass before a commit
    - one fact one place: 2nd occurrence -> a line in `LESSONS.md`, 3rd -> a rule here; update an existing page, never duplicate
    - the dream pass (`wiki dream export` -> write `facts/` -> `wiki dream commit`) may write `facts/` and `solutions/` only; never `maturity: promoted`, never this file
```

Why these lines exist:

- "follow `supersedes` to the chain tail" is not decoration: A<-B<-C is three
  nodes and the current answer is C. One hop returns a stale answer.
- `obsolete`/`conflict`/expired meaning "I do not know" is the only rule that stops
  an old record from being read as a current one; a stale record does not look stale.
- The dream may write `facts/` because git makes that reversible. It may not write
  `maturity: promoted` or this file, because those reach every session.
