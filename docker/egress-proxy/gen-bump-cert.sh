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
#   * This key NEVER signs anything: `generate-host-certificates=off` means
#     Squid cannot mint per-host certs, so it can never impersonate an upstream.
#     That is the no-MITM property, and it holds for ALLOWED traffic.
#
#   * It is NOT true that this cert is never presented, or that it never sees
#     plaintext (LUM-764 corrected both claims). On a DENIED CONNECT, Squid
#     terminates rather than splicing: it presents THIS cert to the client and
#     decrypts the in-tunnel bytes to answer with an error — a probe's plaintext
#     was read and answered `X-Squid-Error: ERR_PROTOCOL_UNKNOWN`. That is the
#     intended deny behaviour, and it is why a denied host fails loudly instead
#     of hanging. It is not MITM of allowed traffic; it is refusal of disallowed
#     traffic, and the distinction is worth stating precisely because the old
#     wording overclaimed and would have reassured the wrong reader.
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

# 2048-bit self-signed, 10-year. No SANs needed.
#
# THE CN IS LOAD-BEARING — do not change it casually (LUM-764). It was once
# documented as "irrelevant (never presented to clients)"; both halves were
# wrong. It IS presented on every denied CONNECT, and since LUM-763 the
# containment tests use `CN=lumogis-egress-proxy-bump` as the observable that
# distinguishes a spliced connection (genuine upstream leaf) from a terminated
# one (this cert). Renaming it silently breaks those assertions.
openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout "$TMPKEY" -out "$TMPCRT" \
  -days 3650 -subj "/CN=lumogis-egress-proxy-bump" >/dev/null 2>&1

# Squid tls-cert= expects the cert and key concatenated in one PEM.
cat "$TMPCRT" "$TMPKEY" > "$OUT"
chmod 600 "$OUT"
echo "gen-bump-cert: wrote $OUT (never used to sign; presented only on deny)." >&2
