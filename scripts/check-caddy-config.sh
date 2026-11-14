#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Static proof that docker/caddy/Caddyfile adapts and that
# LUMOGIS_CADDY_TRUSTED_PROXIES reaches trusted_proxies (LUM-887). Needs Docker
# only; no running stack. Three cases: knob unset, two space-separated ranges,
# and a comma value. `caddy adapt` happily builds a single bogus range from a
# comma value; only `caddy validate` (which provisions the config) refuses it,
# so case 3 uses validate. That refusal is the outage mode the docs warn of.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="${CADDY_IMAGE:-caddy:2-alpine}"

adapt() { # adapt [VALUE]  -> prints JSON on stdout, returns caddy's exit code
	local args=(-e CADDY_DOMAIN=:80)
	if [ "$#" -ge 1 ]; then args+=(-e "LUMOGIS_CADDY_TRUSTED_PROXIES=$1"); fi
	docker run --rm -v "$REPO/docker/caddy:/c:ro" "${args[@]}" "$IMAGE" \
		caddy "${CADDY_CMD:-adapt}" --config /c/Caddyfile
}

ranges() { # reads adapt JSON on stdin, prints "strict=<bool> <ranges...>"
	python3 -c '
import json, sys
cfg = json.load(sys.stdin)
for srv in cfg["apps"]["http"]["servers"].values():
    tp = srv.get("trusted_proxies") or {}
    print("strict=%s %s" % (bool(srv.get("trusted_proxies_strict", False)), " ".join(tp.get("ranges", []))))
    break
'
}

fail=0

out="$(adapt 2>/dev/null)" || { echo "case 1 (unset): adapt failed"; exit 1; }
got="$(printf '%s' "$out" | ranges)"
if [ "$got" = "strict=True 127.0.0.1/32" ]; then echo "case 1 (unset): ok ($got)"; else echo "case 1 (unset): FAIL ($got)"; fail=1; fi

out="$(adapt "172.18.0.1/32 100.64.0.0/10" 2>/dev/null)" || { echo "case 2 (two ranges): adapt failed"; exit 1; }
got="$(printf '%s' "$out" | ranges)"
case "$got" in
	*172.18.0.1/32*100.64.0.0/10*) echo "case 2 (two ranges): ok ($got)" ;;
	*) echo "case 2 (two ranges): FAIL ($got)"; fail=1 ;;
esac

if CADDY_CMD=validate adapt "172.18.0.1/32,100.64.0.0/10" >/dev/null 2>&1; then
	echo "case 3 (comma): Caddy now ACCEPTS commas; drop the 'commas break it' warning from .env.example and the docs, then update this case"
	fail=1
else
	echo "case 3 (comma): ok (validate refused it, as documented)"
fi

exit "$fail"
