# Sync code, keep content local

Use two roots:

- **PKG**: a checkout or installed skill containing `scripts/`, `assets/` and `SKILL.md`.
- **WIKI_HOME**: a separate content directory, default `~/.agents/wiki`, with its own local Git history.

Follow the [installation guide](README.md#install) first. Do not clone the package
into WIKI_HOME for a new installation. This guide describes an optional dotfile
manager setup; neither chezmoi nor Alma is required to use bugwiki.

| What | Sync between machines? |
| --- | --- |
| Package scripts, assets and documentation | Yes |
| A PATH entry or machine-appropriate launcher pointing at PKG | Yes, with local paths resolved per machine |
| WIKI_HOME solutions, facts, reports and content index | No; these can contain machine-specific paths and source references |
| WIKI_HOME `.raw/` and `.dream/` | No; transcripts and export checkpoints are local |
| WIKI_HOME `.git/` | No; content history belongs to that machine |

## Registering the package with chezmoi

Choose one owner for package updates: your skill manager, a Git checkout, or
chezmoi. Do not have two managers overwrite the same installed directory.

For chezmoi, add the selected package files to your own source state using your
actual installed path. Keep package `.git/`, virtual environments and test output
out of the managed copy. Preserve executable bits on the shell and Python scripts.
Verify the exact targets with `chezmoi managed` and inspect `chezmoi diff` before
applying. A target path is not the same thing as chezmoi's encoded source path;
check the rendered ignore patterns for your own layout.

Never add WIKI_HOME to the source state. If a broader managed directory contains
it, exclude its rendered target path in `.chezmoiignore`, then confirm that
`chezmoi managed` lists no content files. No private source repository, encryption
identity or pre-existing chezmoi configuration is assumed here.

## New machine after package installation or sync

macOS, with PKG replaced by the actual installed package directory:

```sh
export BUGWIKI_PKG="$HOME/.local/share/bugwiki"
export WIKI_HOME="$HOME/.agents/wiki"
mkdir -p "$HOME/.local/bin"
# Call the installed code directly; the content store does not contain scripts.
bash "$BUGWIKI_PKG/scripts/wiki" init "$WIKI_HOME"
ln -sf "$BUGWIKI_PKG/scripts/wiki" "$HOME/.local/bin/wiki"
export PATH="$HOME/.local/bin:$PATH"
git -C "$WIKI_HOME" init
wiki verify --migration
wiki list
```

Use this only for a new store. An existing store is not reinitialized or restored
by code sync. Set Git identity before the first content commit, and persist the
PATH and optional WIKI_HOME override. The native Windows setup and its smaller
command surface are documented in the [platform matrix](README.md#platform-support).

## History and upgrades

A fresh Bash store ignores `.raw/` and `.dream/` in its local `.gitignore`.
Knowledge pages and reports remain trackable, so `wiki dream commit` can record
them. This is distinct from the package repository's ignore rules, which exclude
content. Keep the content repository local without a remote unless you separately
choose and review a content-sharing policy.

Update PKG through its chosen manager. Run the documented verification commands
against WIKI_HOME after an update; do not copy a new template over existing pages.
Changes to an agent's instruction file belong in whichever source manages that
file, so the next dotfile sync does not replace them. Do not store credentials in
pages, raw exports or documentation; the helper is not a credential scanner.
