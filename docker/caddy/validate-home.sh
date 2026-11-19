#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
#
# LUM-331 — check the household's pending secure-address site before it is put
# in place. stack-control runs this (`docker compose exec caddy`) after Core has
# written /etc/caddy/home.d/home.caddy.pending; only on success does Core rename
# it to home.caddy and ask for a reload.
#
# It validates the real Caddyfile with the household import pointed at the
# pending file, so the site is checked together with the snippet it imports.
# It prints nothing: the site carries the acme-dns password, and Caddy can echo
# config lines in its errors. The exit code is the whole answer (0 valid,
# 1 invalid or no pending file).

set -u
# Fixed in the image; the tests point them at a temporary folder.
PENDING=${LUMOGIS_HOME_PENDING:-/etc/caddy/home.d/home.caddy.pending}
CADDYFILE=${LUMOGIS_CADDYFILE:-/etc/caddy/Caddyfile}
CHECK=${LUMOGIS_CADDYFILE_CHECK:-/tmp/Caddyfile.check}
GLOB_IMPORT='import /etc/caddy/home.d/*.caddy'

[ -f "$PENDING" ] || exit 1
# Copy the Caddyfile with the one import line swapped (fixed strings, no regex).
# A Caddyfile without that line (an older bind-mounted copy) cannot import the
# site at all, so it fails here rather than validating something else.
awk -v glob="$GLOB_IMPORT" -v pending="import $PENDING" \
	'$0 == glob { print pending; found = 1; next } { print } END { exit found ? 0 : 1 }' \
	"$CADDYFILE" >"$CHECK" || exit 1
caddy validate --adapter caddyfile --config "$CHECK" >/dev/null 2>&1
