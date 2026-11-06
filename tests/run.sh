#!/usr/bin/env bash
# Offline regression suite. No network, no alma calls, no writes outside TMPDIR.
# Run: tests/run.sh
set -uo pipefail

PKG="$(cd -P "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WIKI_BIN="$PKG/scripts/wiki"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
pass=0; fail=0

ok()   { pass=$((pass+1)); printf '  ok   %s\n' "$1"; }
bad()  { fail=$((fail+1)); printf '  FAIL %s\n' "$1"; [ -n "${2:-}" ] && printf '       %s\n' "$2"; }
expect_eq() { # expect_eq <label> <expected> <actual>
  if [ "$2" = "$3" ]; then ok "$1"; else bad "$1" "expected [$2] got [$3]"; fi
}
expect_rc() { # expect_rc <label> <expected-rc> <cmd...>
  local label="$1" want="$2"; shift 2
  "$@" >/dev/null 2>&1
  local got=$?
  if [ "$got" = "$want" ]; then ok "$label"; else bad "$label" "expected rc $want got $got"; fi
}

seed() { # seed <dir> — a content root with the pre-schema page shape
  local d="$1"
  mkdir -p "$d/solutions/demo" "$d/facts" "$d/reports" "$d/.raw" "$d/templates"
  cp "$PKG/assets/README.md" "$d/README.md" 2>/dev/null || printf '# wiki\n' > "$d/README.md"
  cp "$PKG/assets/LESSONS.md" "$d/LESSONS.md" 2>/dev/null || printf '# Lessons\n' > "$d/LESSONS.md"
  cat > "$d/solutions/demo/legacy-page.md" <<'EOF'
---
title: A legacy page that predates the schema
status: active
env: macOS
tags: [demo]
recurrence: 1
oneliner: Legacy pages carry no kind field and must still read correctly.
first_seen: 2026-11-01
last_seen: 2026-11-01
last_verified: 2026-11-01
related: [../demo/legacy-other.md]
---

# A legacy page

Body.
EOF
  cat > "$d/solutions/demo/legacy-other.md" <<'EOF'
---
title: Another legacy page
status: verified
recurrence: 1
oneliner: Second page, target of a relative-path edge.
first_seen: 2026-11-01
last_seen: 2026-11-01
last_verified: 2026-11-01
---

# Another legacy page
EOF
}

echo "1. pre-schema pages read with zero edits"
seed "$TMP/a"
export WIKI_HOME="$TMP/a"
case "$(bash "$WIKI_BIN" show legacy-page | head -1)" in
  *"[solution"*) ok "kind inferred from path (no kind field in the page)" ;;
  *) bad "kind inferred from path (no kind field in the page)" "$(bash "$WIKI_BIN" show legacy-page | head -1)" ;;
esac
expect_eq "list sees both pages" "2" "$(bash "$WIKI_BIN" list | wc -l | tr -d ' ')"
expect_eq "relative-path edge resolves" "1" "$(bash "$WIKI_BIN" show legacy-page | grep -c 'legacy-other.md')"
expect_rc "verify passes on legacy pages" 0 bash "$WIKI_BIN" verify
expect_rc "lint passes with status: active" 0 bash "$WIKI_BIN" lint

echo "2. supersession chain resolves past one hop"
seed "$TMP/b"; export WIKI_HOME="$TMP/b"
mkfact() { # mkfact <slug> <status> <supersedes-csv>
  cat > "$TMP/b/facts/$1.md" <<EOF
---
slug: $1
kind: fact
status: $2
maturity: distilled
scope: repo:demo
source: [thread:t]
valid_from: 2026-11-0${3:-1}
recorded_at: 2026-11-01
verified_at: 2026-11-05
expires: null
supersedes: [$4]
contradicts: []
recurrence: 1
last_seen: 2026-11-05
---

# $1
EOF
}
mkfact f-a obsolete 1 ""
mkfact f-b obsolete 2 "f-a"
mkfact f-c verified 3 "f-b"
out="$(bash "$WIKI_BIN" show f-a)"
case "$out" in
  *"chain 2: f-c"*) ok "chain reaches the tail (A<-B<-C)" ;;
  *) bad "chain reaches the tail (A<-B<-C)" "$(printf '%s' "$out" | tr '\n' '|')" ;;
esac
case "$out" in
  *"superseded/contradicted by"*) ok "inbound edge printed" ;;
  *) bad "inbound edge printed" ;;
esac

echo "3. a dead edge fails lint and verify"
seed "$TMP/c"; export WIKI_HOME="$TMP/c"
printf '\nrelated: [./does-not-exist.md]\n' >> "$TMP/c/solutions/demo/legacy-other.md"
# the appended key lands outside frontmatter on purpose: neither parser may crash
expect_rc "verify tolerates a stray key" 0 bash "$WIKI_BIN" verify

echo "4. dead edge inside frontmatter is caught"
seed "$TMP/d"; export WIKI_HOME="$TMP/d"
python3 - "$TMP/d/solutions/demo/legacy-other.md" <<'PY'
import sys
p = sys.argv[1]
s = open(p).read().replace("last_verified: 2026-11-01", "last_verified: 2026-11-01\nrelated: [nope-not-here]")
open(p, "w").write(s)
PY
expect_rc "lint rejects an unresolvable edge" 1 bash "$WIKI_BIN" lint
expect_rc "verify rejects an unresolvable edge" 1 bash "$WIKI_BIN" verify

echo "5. per-kind status vocabulary"
seed "$TMP/e"; export WIKI_HOME="$TMP/e"
cat > "$TMP/e/facts/bad-status.md" <<'EOF'
---
slug: bad-status
kind: fact
status: workaround
recorded_at: 2026-11-06
verified_at: 2026-11-06
recurrence: 1
last_seen: 2026-11-06
---

# bad
EOF
expect_rc "a solution status on a fact is rejected" 1 bash "$WIKI_BIN" verify

echo "6. staleness is per kind"
seed "$TMP/f"; export WIKI_HOME="$TMP/f"
cat > "$TMP/f/facts/expired.md" <<'EOF'
---
slug: expired
kind: fact
status: verified
maturity: distilled
recorded_at: 2026-10-01
verified_at: 2026-10-01
expires: 2026-10-15
recurrence: 1
last_seen: 2026-10-01
---

# expired fact
EOF
case "$(bash "$WIKI_BIN" stale)" in
  *"expired"*) ok "an expired fact is reported" ;;
  *) bad "an expired fact is reported" ;;
esac

echo "7. index is idempotent"
seed "$TMP/g"; export WIKI_HOME="$TMP/g"
bash "$WIKI_BIN" index >/dev/null; cp "$TMP/g/README.md" "$TMP/g.r1"
bash "$WIKI_BIN" index >/dev/null
if cmp -s "$TMP/g.r1" "$TMP/g/README.md"; then ok "second index run is a no-op"; else bad "second index run is a no-op"; fi

echo "8. dream refuses to write without git"
seed "$TMP/h"; export WIKI_HOME="$TMP/h"
printf 'x\n' > "$TMP/h/facts/x.md"
expect_rc "commit without a repo fails loudly" 1 bash "$WIKI_BIN" dream commit

echo "9. dream commits and records one line"
( cd "$TMP/h" && git init -q && git add -A && git -c user.email=t@t -c user.name=t commit -qm base )
printf 'y\n' > "$TMP/h/facts/y.md"
expect_rc "commit succeeds in a repo" 0 bash "$WIKI_BIN" dream commit
n="$(git -C "$TMP/h" log --oneline | wc -l | tr -d ' ')"
expect_eq "one commit per run" "2" "$n"
mkdir -p "$TMP/h/facts"
printf 'a\n' > "$TMP/h/facts/c1.md"; printf 'b\n' > "$TMP/h/facts/c2.md"
bash "$WIKI_BIN" dream commit >/dev/null
case "$(git -C "$TMP/h" log -1 --pretty=%s)" in
  *"2 new"*) ok "commit message counts files, not directories" ;;
  *) bad "commit message counts files, not directories" "$(git -C "$TMP/h" log -1 --pretty=%s)" ;;
esac
case "$(ls "$TMP/h/reports" 2>/dev/null)" in
  *.dream.md) ok "dream line goes to its own report file" ;;
  *) bad "dream line goes to its own report file" "$(ls "$TMP/h/reports" 2>/dev/null | tr '\n' ' ')" ;;
esac

echo "10. find spans both kinds and resolves a cross-kind edge"
seed "$TMP/j"; export WIKI_HOME="$TMP/j"
cat > "$TMP/j/facts/f-demo.md" <<'EOF'
---
slug: f-demo
kind: fact
status: verified
recorded_at: 2026-11-06
verified_at: 2026-11-06
recurrence: 1
last_seen: 2026-11-06
related: [../solutions/demo/legacy-other.md]
---

# a fact about a zzz-unique-token
EOF
case "$(bash "$WIKI_BIN" find zzz-unique-token)" in
  *"facts/f-demo.md"*) ok "find searches facts, not only solutions" ;;
  *) bad "find searches facts, not only solutions" ;;
esac
expect_rc "a fact may link into solutions/" 0 bash "$WIKI_BIN" verify

echo "11. nothing is written outside the content root"
seed "$TMP/i"; export WIKI_HOME="$TMP/i"
before="$(find "$PKG" -newer "$PKG/scripts/wiki" -not -path "*/.git/*" -type f 2>/dev/null | wc -l | tr -d ' ')"
bash "$WIKI_BIN" index >/dev/null; bash "$WIKI_BIN" verify >/dev/null 2>&1
after="$(find "$PKG" -newer "$PKG/scripts/wiki" -not -path "*/.git/*" -type f 2>/dev/null | wc -l | tr -d ' ')"
expect_eq "package untouched by a run" "$before" "$after"

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
