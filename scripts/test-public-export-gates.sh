#!/usr/bin/env bash
# Regression tests for the export gates inside check-public-export.sh.
#
# Why this exists: those gates were rewritten three times and failed OPEN twice during
# review — a `grep | head` returning 141 under pipefail silently skipped tokens, and a
# hand-rolled YAML walk missed a same-indentation block sequence. Every case below was a
# real reported bypass, a real false positive, or a real false alarm.
#
# The gates are extracted and run in isolation (the surrounding script enforces unrelated
# export contracts that a minimal fixture tree cannot satisfy). Run: make test-export-gates
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
pass=0; fail=0

# Extract the python gate block, stripping the bash heredoc wrapper.
sed -n '/^python3 - "\$TARGET"/,/^PYGATES$/p' "$ROOT/scripts/check-public-export.sh" \
  | sed '1d;$d' > "$TMP/gates.py"
[[ -s "$TMP/gates.py" ]] || { echo "could not extract gate block"; exit 1; }

_gate() { python3 "$TMP/gates.py" "$1" "$2" >/dev/null 2>&1; }

wf() { # <name> <expect: reject|accept> <yaml>
  local d="$TMP/w$RANDOM$RANDOM"; mkdir -p "$d/.github/workflows"
  printf '%s' "$3" > "$d/.github/workflows/w.yml"
  : > "$TMP/empty-tokens.txt"
  if _gate "$d" "$TMP/empty-tokens.txt"; then got=accept; else got=reject; fi
  if [[ "$got" == "$2" ]]; then echo "  ok  $2: $1"; pass=$((pass+1));
  else echo "  FAIL expected $2 got $got: $1"; fail=$((fail+1)); fi
}

tok() { # <name> <expect> <token-file-bytes> <file content>
  local d="$TMP/p$RANDOM$RANDOM"; mkdir -p "$d"
  printf '%s' "$4" > "$d/n.md"; printf '%s' "$3" > "$TMP/tok.txt"
  if _gate "$d" "$TMP/tok.txt"; then got=accept; else got=reject; fi
  if [[ "$got" == "$2" ]]; then echo "  ok  $2: $1"; pass=$((pass+1));
  else echo "  FAIL expected $2 got $got: $1"; fail=$((fail+1)); fi
}

echo "== runs-on allowlist =="
wf "flow sequence"             reject $'jobs:\n  b:\n    runs-on: [self-hosted, laptop]\n'
wf "block seq, same indent"    reject $'jobs:\n  b:\n    runs-on:\n    - "self-hosted"\n    - laptop\n'
wf "block seq, deeper indent"  reject $'jobs:\n  b:\n    runs-on:\n      - self-hosted\n'
wf "block seq + comment"       reject $'jobs:\n  b:\n    runs-on:\n      - self-hosted   # runner\n'
wf "scalar"                    reject $'jobs:\n  b:\n    runs-on: self-hosted\n'
wf "casing"                    reject $'jobs:\n  b:\n    runs-on: Self-Hosted\n'
wf "labels mapping"            reject $'jobs:\n  b:\n    runs-on:\n      labels: [self-hosted]\n'
wf "runner group"              reject $'jobs:\n  b:\n    runs-on:\n      group: lumogis\n'
wf "anchor/alias"              reject $'x: &r\n  - self-hosted\njobs:\n  b:\n    runs-on: *r\n'
wf "multi-document"            reject $'---\njobs:\n  a:\n    runs-on: ubuntu-latest\n---\njobs:\n  b:\n    runs-on:\n    - self-hosted\n'
wf "dynamic matrix"            reject $'jobs:\n  b:\n    strategy:\n      matrix:\n        include: ${{ fromJSON(needs.x.outputs.i) }}\n    runs-on: ${{ matrix.os }}\n'
wf "matrix masking (round 5)"  reject $'jobs:\n  a:\n    strategy:\n      matrix:\n        os: [ubuntu-latest]\n    runs-on: ${{ matrix.os }}\n  b:\n    strategy:\n      matrix:\n        include: ${{ fromJSON(needs.x.outputs.i) }}\n    runs-on: ${{ matrix.os }}\n'
wf "unparseable yaml"          reject $'jobs:\n  b:\n   runs-on: [\n'
wf "github-hosted scalar"      accept $'jobs:\n  b:\n    runs-on: ubuntu-latest\n'
wf "static matrix include"     accept $'jobs:\n  b:\n    strategy:\n      matrix:\n        include:\n          - platform: macos-latest\n          - platform: ubuntu-22.04\n    runs-on: ${{ matrix.platform }}\n'
wf "labels mapping, hosted"    accept $'jobs:\n  b:\n    runs-on:\n      labels: [ubuntu-latest]\n'
wf "hosted arm64 matrix"       accept $'jobs:\n  b:\n    strategy:\n      matrix:\n        runner: [ubuntu-24.04, ubuntu-24.04-arm]\n    runs-on: ${{ matrix.runner }}\n'
wf "arm64 lookalike"           reject $'jobs:\n  b:\n    runs-on: ubuntu-24.04-arm-large\n'
wf "static runner, dynamic suite" accept $'jobs:\n  b:\n    strategy:\n      matrix:\n        runner: [ubuntu-24.04, ubuntu-24.04-arm]\n        suite: ${{ fromJSON(needs.f.outputs.s) }}\n    runs-on: ${{ matrix.runner }}\n'
wf "no runs-on at all"         accept $'jobs:\n  b:\n    uses: ./.github/workflows/other.yml\n'

echo "== personal-token content gate =="
tok "regex metacharacters" reject $'project (aurora)\n'    'the project (aurora) codename'
tok "BOM on token list"    reject $'\xef\xbb\xbfzzprobe\n' 'contains zzprobe here'
tok "many matches"         reject $'zzprobe\n'             "$(python3 -c 'print("zzprobe line\n"*5000)')"
tok "comment-only list"    accept $'# only a comment\n'    'nothing sensitive here'
tok "empty list"           accept ''                       'nothing sensitive here'
tok "substring not word"   accept $'mia\n'                 'binomial and SimpleNamespace'

srv() { # <name> <expect> <relpath> <file content>
  local d="$TMP/s$RANDOM$RANDOM"; mkdir -p "$d/$(dirname "$3")"
  printf '%s' "$4" > "$d/$3"; : > "$TMP/empty-tokens.txt"
  if _gate "$d" "$TMP/empty-tokens.txt"; then got=accept; else got=reject; fi
  if [[ "$got" == "$2" ]]; then echo "  ok  $2: $1"; pass=$((pass+1));
  else echo "  FAIL expected $2 got $got: $1"; fail=$((fail+1)); fi
}

# Assembled from parts: this file ships in the public tree, and the gate it tests rejects
# the literals anywhere in that tree (LUM-898).
S="lumogis-""server"; MK="Makefile.""server"".mk"

echo "== appliance path gate, names and content (LUM-898) =="
srv "tree path in an ADR"          reject docs/decisions/1-x.md "see apps/$S/README.md"
srv "tree path in a code comment"  reject clients/a/build.rs "// externalBin under apps/$S/src"
srv "tree path in a non-UTF-8 file" reject assets/x.bin $'\xff\xfe'"apps/$S"$'\x01'
srv "make include name"            reject Makefile "-include $MK"
srv "strip list may not name it"   reject scripts/public-export-strip-list.txt "apps/$S/"
srv "local list may not ship"      reject scripts/public-export-strip-list.local.txt "apps/$S/"
srv "same basename elsewhere"      reject docs/public-export-strip-list.txt "apps/$S/"
srv "tree path in a file path"     reject "vendor/apps/$S/notes.txt" "nothing to see"
srv "make include as a file name"  reject "$MK" "nothing to see"
srv "bare product name is fine"    accept docs/a.md "the $S binary"
srv "no mention at all"            accept docs/a.md "nothing to see"

echo
echo "passed=$pass failed=$fail"
[[ "$fail" -eq 0 ]]
