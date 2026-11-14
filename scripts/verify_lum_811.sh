#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
#
# LUM-811 — discharge the four pre-main verification obligations for LUM-810.
#
# 1. Live IMAP scenarios (Greenmail by default, or LUM811_IMAP_* for a real mailbox)
# 2. p95 < 2.5 s on bounded body fetch
# 3. 1 MiB adversarial html.parser benchmark
# 4. Playwright email_body.spec.ts (desktop + mobile)
#
# Usage:
#   ./scripts/verify_lum_811.sh
#   LUM811_IMAP_HOST=imap.gmail.com LUM811_IMAP_PORT=993 ... ./scripts/verify_lum_811.sh
#
# Requires: docker (for Greenmail auto-start), running lumogis stack for Playwright,
# LUMOGIS_WEB_SMOKE_EMAIL / LUMOGIS_WEB_SMOKE_PASSWORD for e2e.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

GREENMAIL_NAME="lum811-greenmail"
GREENMAIL_IMAGE="${LUM811_GREENMAIL_IMAGE:-greenmail/standalone:2.1.0}"

start_greenmail() {
  if [[ -n "${LUM811_IMAP_HOST:-}" ]]; then
    echo "LUM-811: using configured IMAP host ${LUM811_IMAP_HOST}"
    return 0
  fi
  NET="$(docker inspect -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' "$(docker compose ps -q orchestrator | head -1)" 2>/dev/null | tr ' ' '\n' | grep -E 'lumogis|default' | head -1)"
  if [[ -z "$NET" ]]; then
    NET="$(docker inspect -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' "$(docker compose ps -q orchestrator | head -1)" 2>/dev/null | awk '{print $NF}')"
  fi
  if [[ -z "$NET" ]]; then
    echo "ERROR: lumogis orchestrator container not running — start the stack first (docker compose up -d)"
    exit 1
  fi
  if docker ps --format '{{.Names}}' | grep -qx "$GREENMAIL_NAME"; then
    echo "LUM-811: reusing existing $GREENMAIL_NAME"
  else
    echo "LUM-811: starting $GREENMAIL_NAME on network $NET ($GREENMAIL_IMAGE)"
    docker rm -f "$GREENMAIL_NAME" >/dev/null 2>&1 || true
    docker run -d --name "$GREENMAIL_NAME" --network "$NET" \
      "$GREENMAIL_IMAGE" >/dev/null
    sleep 3
  fi
  export LUM811_IMAP_HOST="${LUM811_IMAP_HOST:-$GREENMAIL_NAME}"
  export LUM811_IMAP_PORT="${LUM811_IMAP_PORT:-3143}"
  export LUM811_SMTP_HOST="${LUM811_SMTP_HOST:-$GREENMAIL_NAME}"
  export LUM811_SMTP_PORT="${LUM811_SMTP_PORT:-3025}"
  echo "LUM-811: Greenmail IMAP at ${LUM811_IMAP_HOST}:${LUM811_IMAP_PORT} (network $NET)"
}

echo "=== LUM-811 verification (email body read view) ==="

start_greenmail

echo ""
echo "--- pytest: live IMAP + p95 + adversarial parse ---"
export LUM811_REQUIRE=1
docker compose run --rm --no-deps --entrypoint "" \
  -v "$ROOT/orchestrator:/app" \
  -e LUM811_REQUIRE \
  -e LUM811_IMAP_HOST \
  -e LUM811_IMAP_PORT \
  -e LUM811_SMTP_HOST \
  -e LUM811_SMTP_PORT \
  -e LUM811_IMAP_USERNAME="${LUM811_IMAP_USERNAME:-user@localhost}" \
  -e LUM811_IMAP_PASSWORD="${LUM811_IMAP_PASSWORD:-user}" \
  -e LUMOGIS_ALLOW_PRIVATE_OUTBOUND_URLS=true \
  orchestrator \
  sh -c 'pip install -q -r requirements-dev.txt && python -m pytest tests/integration/test_lum811_verification.py -v --tb=short'

echo ""
echo "--- Playwright: email_body.spec.ts (desktop + mobile) ---"
if [[ -z "${LUMOGIS_WEB_SMOKE_EMAIL:-}" || -z "${LUMOGIS_WEB_SMOKE_PASSWORD:-}" ]]; then
  if [[ -f .env ]]; then
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
  fi
fi
if [[ -z "${LUMOGIS_WEB_SMOKE_EMAIL:-}" || -z "${LUMOGIS_WEB_SMOKE_PASSWORD:-}" || ${#LUMOGIS_WEB_SMOKE_PASSWORD} -lt 12 ]]; then
  echo "ERROR: LUMOGIS_WEB_SMOKE_EMAIL and LUMOGIS_WEB_SMOKE_PASSWORD (>=12 chars) required for Playwright"
  exit 1
fi
export PLAYWRIGHT_BASE_URL="${PLAYWRIGHT_BASE_URL:-http://127.0.0.1}"
cd clients/lumogis-web
E2E_REQUIRE_CREDS=1 npx playwright test tests/e2e/email_body.spec.ts --workers=1

echo ""
echo "=== LUM-811 verification PASSED ==="
