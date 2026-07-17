#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
#
# LUM-618 — generate the self-signed cert/key the Squid `http_port ... ssl-bump`
# listener requires to initialise its SslBump machinery.
#
# IMPORTANT (custody rule):
#   * Generated PER-DEPLOY (at image build / install time), NEVER committed.
#     The output path is .gitignore'd.
#   * Because the proxy SPLICES (never bumps/decrypts), this key NEVER signs
#     anything and NEVER sees plaintext. It is not a MITM CA and not a shared
#     secret — it only exists so Squid will start the ssl-bump port.
#
# Usage: gen-bump-cert.sh [output.pem]   (default: /etc/squid/bump.pem)
set -eu

OUT="${1:-/etc/squid/bump.pem}"

if [ -f "$OUT" ]; then
  echo "gen-bump-cert: $OUT already exists — leaving it in place." >&2
  exit 0
fi

TMPKEY="$(mktemp)"
TMPCRT="$(mktemp)"
trap 'rm -f "$TMPKEY" "$TMPCRT"' EXIT

# 2048-bit self-signed, 10-year, CN is irrelevant (never presented to clients —
# we splice). No SANs needed.
openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout "$TMPKEY" -out "$TMPCRT" \
  -days 3650 -subj "/CN=lumogis-egress-proxy-bump" >/dev/null 2>&1

# Squid tls-cert= expects the cert and key concatenated in one PEM.
cat "$TMPCRT" "$TMPKEY" > "$OUT"
chmod 600 "$OUT"
echo "gen-bump-cert: wrote $OUT (splice-only; never used to sign)." >&2
