Paths in commands are relative to the skill package root unless stated otherwise.

## Install

Follow [README.md](../README.md#install) for dependencies and the platform matrix.
Use a checkout or installed skill as PKG and a **separate** content store. For
macOS, once this package exists at the chosen path:

```sh
export BUGWIKI_PKG="$HOME/.local/share/bugwiki"  # actual installed package path
export WIKI_HOME="$HOME/.agents/wiki"
mkdir -p "$HOME/.local/bin"
bash "$BUGWIKI_PKG/scripts/wiki" init "$WIKI_HOME"  # new store only
ln -sf "$BUGWIKI_PKG/scripts/wiki" "$HOME/.local/bin/wiki"
export PATH="$HOME/.local/bin:$PATH"
git -C "$WIKI_HOME" init
wiki verify --migration
wiki list
```

Configure Git identity before the first dream commit. New Bash stores ignore raw
exports and checkpoints, while facts, solutions and reports remain trackable.
Persist PATH and any non-default WIKI_HOME. Code upgrades do not reinitialize
content. Legacy clone-as-store installations are not the supported dream setup.
Date-dependent Bash commands currently use BSD `date`; GNU/Linux is not fully
supported. For the repository Windows port, see the smaller command surface in [read/write contracts](read-write.md#windows). Separately installed native adapters follow their own README.

The existing pre-schema pages need no edit: `kind` is derived from the path,
relative-path edges remain supported, and new fields remain optional for legacy
pages. [PROTOCOL.md](../PROTOCOL.md) distinguishes CLI checks from agent policies.
