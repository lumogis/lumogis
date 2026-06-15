#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Vend redis-server from the FalkorDB image as redis-check-rdb-v13 plus donor
# runtime libs (postgres:16.8 glibc is older than FalkorDB's redis-server build).
set -euo pipefail

DONOR_ROOT="${1:?donor root required}"
BIN="/usr/local/bin/redis-check-rdb-v13"
LIBDIR="/opt/falkordb-redis-check/lib"
LOADER="/opt/falkordb-redis-check/ld-linux-x86-64.so.2"

mkdir -p "$LIBDIR" "$(dirname "$LOADER")"
cp "${DONOR_ROOT}/usr/local/bin/redis-server" "$BIN"
chmod 755 "$BIN"

cp "${DONOR_ROOT}/lib64/ld-linux-x86-64.so.2" "$LOADER"
chmod 755 "$LOADER"

resolve_donor_path() {
  local abs="$1"
  case "$abs" in
    /lib/x86_64-linux-gnu/* | /lib64/* | /usr/lib/x86_64-linux-gnu/*)
      echo "${DONOR_ROOT}${abs}"
      ;;
    *)
      echo ""
      ;;
  esac
}

copy_dep() {
  local abs="$1"
  local donor src dest_name
  [[ "$abs" == "${LIBDIR}/"* ]] && return 0
  donor="$(resolve_donor_path "$abs")"
  [[ -n "$donor" && -e "$donor" ]] || return 0
  dest_name="$(basename "$abs")"
  src="$donor"
  if [[ -L "$donor" ]]; then
    src="$(readlink -f "$donor")"
  fi
  if [[ -f "${LIBDIR}/${dest_name}" ]]; then
    return 0
  fi
  cp -L "$src" "${LIBDIR}/${dest_name}"
}

collect_deps() {
  "$LOADER" --library-path "$LIBDIR" --list "$BIN" 2>/dev/null \
    || "$LOADER" --list "$BIN" 2>/dev/null \
    || true
}

# Seed direct deps from donor loader listing, then iterate for transitive libs.
collect_deps | while IFS= read -r line; do
  case "$line" in
    *'=> '*) ;;
    *) continue ;;
  esac
  dep="${line#*=> }"
  dep="${dep%% *}"
  case "$dep" in
    linux-vdso.so.1 | /lib64/ld-linux-x86-64.so.2*) continue ;;
  esac
  copy_dep "$dep"
done

for _ in $(seq 1 12); do
  added=0
  while IFS= read -r line; do
    case "$line" in
      *'=> '*) ;;
      *) continue ;;
    esac
    dep="${line#*=> }"
    dep="${dep%% *}"
    case "$dep" in
      linux-vdso.so.1 | /lib64/ld-linux-x86-64.so.2*) continue ;;
      "${LIBDIR}/"*) continue ;;
    esac
    if [[ ! -f "${LIBDIR}/$(basename "$dep")" ]]; then
      copy_dep "$dep"
      added=1
    fi
  done < <(collect_deps)
  (( added == 0 )) && break
done

ldd_out="$(collect_deps 2>&1)" || true
if grep -q 'not found' <<<"$ldd_out"; then
  echo "install-redis-check-rdb-v13: unresolved shared libraries after vendoring:" >&2
  echo "$ldd_out" >&2
  exit 1
fi

if grep -qE 'GLIBC_|GLIBCXX_' <<<"$ldd_out"; then
  echo "install-redis-check-rdb-v13: glibc/libstdc++ symbol mismatch after vendoring:" >&2
  echo "$ldd_out" >&2
  exit 1
fi

# Smoke: argv[0] basename must contain redis-check-rdb for check-rdb mode.
smoke_out="$("$LOADER" --library-path "$LIBDIR" "$BIN" 2>&1)" || true
if ! grep -qi 'redis-check-rdb' <<<"$smoke_out"; then
  echo "install-redis-check-rdb-v13: redis-check-rdb-v13 smoke check failed" >&2
  echo "$smoke_out" >&2
  exit 1
fi

echo "install-redis-check-rdb-v13: vendored $(basename "$BIN") with $(find "$LIBDIR" -maxdepth 1 -type f | wc -l) libs"
