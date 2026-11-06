# Syncing this package across machines

`PKG` (this repo) and `WIKI` (the content root, default `~/.agents/wiki`) are
deliberately separate, so a dotfile manager can sync one without dragging the
other along.

| What | Sync? | Why |
| --- | --- | --- |
| `bin/wiki`, `bin/dream-export.py` | yes | code |
| `SKILL.md`, `assets/` | yes | setup and templates |
| `WIKI/solutions/**`, `WIKI/facts/**` | **no** | machine-local knowledge; it references threads and paths that only exist here |
| `WIKI/.raw/**` | **no** | transcripts, large, and the only poisonable input |
| `WIKI/reports/**` | **no** | per-machine history |
| `WIKI/.git` | **no** | each content root has its own history |

## State observed on this machine (2026-11-06)

- `chezmoi` v2.74.0 installed; `~/.config/chezmoi/chezmoi.toml` sets
  `encryption = "age"` with identity `~/.config/alma/ai-env-age-key.txt`.
- `chezmoi managed` returns **0 files** and `chezmoi source-path` points at
  `~/.local/share/chezmoi`, which is empty.
- Chat `mw1shbu6jrf0s1zm69n` describes a populated source at
  `--source ~/Syncthing/ai-env` (age-encrypted `mcp.json`, platform-split
  templates, `scripts/sync-magpie-routing-groups.cjs`). **That directory does not
  exist on this machine right now** (`ls ~/Syncthing` → no such directory).

So chezmoi is configured but currently syncing nothing. Nothing here depends on
it being enabled; the layout above is what makes enabling it a one-line add
instead of a refactor.

## Enabling it (code only)

```bash
chezmoi --source ~/Syncthing/ai-env add ~/poc/agent-wiki
```

Then keep content out of the source tree. Because the two roots never overlap,
no `.chezmoiignore` entry is needed for a default `WIKI_HOME`. If a machine sets
`WIKI_HOME` inside the synced tree, ignore it explicitly:

```
# .chezmoiignore
/poc/agent-wiki/.raw
/poc/agent-wiki/facts
/poc/agent-wiki/solutions
/poc/agent-wiki/reports
/poc/agent-wiki/.git
```

## Things this package must keep true

1. **No absolute paths.** The binary resolves `PKG` from its own location and
   `WIKI` from `WIKI_HOME`, so a different home directory needs no template.
2. **No secrets.** `.raw/` and `facts/` can quote a transcript; neither is synced,
   but `lint` refuses nothing today, so never copy a credential into a fact.
3. **`bin/*` stays executable.** chezmoi's `private_` prefix is for 0600 files;
   these are 0755.

## Per-machine setup after `chezmoi apply`

```bash
ln -sf ~/poc/agent-wiki/bin/wiki ~/.local/bin/wiki
[ -d ~/.agents/wiki ] || wiki init
cd ~/.agents/wiki && git init      # required: dream writes must be revertable
wiki verify --migration
```

Content is *not* restored by the sync — that is the point. A new machine starts
with an empty, schema-valid wiki.
