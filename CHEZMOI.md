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
- The populated source is the GitHub repo `phillui-37/ai-env` (private, 589 files,
  `.chezmoi.toml.tmpl`, `.chezmoiignore`, `dot_config/alma/skills/bugwiki/`, age-encrypted
  `mcp.json`), last pushed 2026-11-05. **It has never been cloned to this machine**:
  `~/Syncthing/ai-env` does not exist. So the source lives remotely only; restoring
  it is `git clone` plus `sourceDir` in `~/.config/chezmoi/chezmoi.toml`.
- `dot_agents/AGENTS.md` is managed there. A local edit to `~/.agents/AGENTS.md` is
  drift: it survives until someone runs `chezmoi apply`, then it is overwritten.
  Put rule changes in the source repo, not only in the live file.

So chezmoi is configured but currently syncing nothing. Nothing here depends on
it being enabled; the layout above is what makes enabling it a one-line add
instead of a refactor.

## Enabling it

This package **is** the skill directory, and that directory is already managed by
the `ai-env` chezmoi source (`dot_agents/...` and `dot_config/...` entries), so
there is nothing to add — a `chezmoi apply` on another machine places it. Content
stays out because the two roots never overlap; a machine that points `WIKI_HOME`
into a synced tree must ignore it explicitly:

```
# .chezmoiignore
/dot_config/alma/skills/bugwiki/.raw
/dot_config/alma/skills/bugwiki/facts
/dot_config/alma/skills/bugwiki/solutions
/dot_config/alma/skills/bugwiki/reports
/dot_config/alma/skills/bugwiki/.git
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
ln -sf ~/.config/alma/skills/bugwiki/scripts/wiki ~/.local/bin/wiki
[ -d ~/.agents/wiki ] || wiki init
cd ~/.agents/wiki && git init      # required: dream writes must be revertable
wiki verify --migration
```

Content is *not* restored by the sync — that is the point. A new machine starts
with an empty, schema-valid wiki.
