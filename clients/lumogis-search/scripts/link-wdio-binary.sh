#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# WDIO @wdio/tauri-service resolves debug binaries by tauri.conf.json productName
# ("Lumogis Search") while `cargo build` emits the Cargo package name (lumogis-search).
set -euo pipefail
dir="src-tauri/target/debug"
bin="$dir/lumogis-search"
if [[ ! -f "$bin" ]]; then
  echo "link-wdio-binary: missing $bin — run cargo build --features wdio-e2e first" >&2
  exit 1
fi
ln -sf lumogis-search "$dir/lumogis search"
ln -sf lumogis-search "$dir/Lumogis Search"
