#!/usr/bin/env bash
# Generate JWT secrets in .env if they are still set to placeholder values.
# Run once after cloning: bash scripts/init-env.sh
# Safe to re-run — only changes placeholder values.

set -euo pipefail

ENV_FILE="${1:-.env}"
[[ ! -f "$ENV_FILE" ]] && { echo "No .env found. Copy .env.example first."; exit 1; }

_gen() {
    if command -v openssl &>/dev/null; then openssl rand -hex 32
    else python3 -c "import secrets; print(secrets.token_hex(32))"; fi
}

_sed_i() {
    if sed --version &>/dev/null 2>&1; then sed -i "$@"
    else sed -i '' "$@"; fi
}

changed=0
if grep -q "^JWT_SECRET=change-me-in-production" "$ENV_FILE"; then
    _sed_i "s|^JWT_SECRET=change-me-in-production|JWT_SECRET=$(_gen)|" "$ENV_FILE"
    echo "  ✓ Generated JWT_SECRET"
    changed=1
fi
if grep -q "^JWT_REFRESH_SECRET=change-me-in-production" "$ENV_FILE"; then
    _sed_i "s|^JWT_REFRESH_SECRET=change-me-in-production|JWT_REFRESH_SECRET=$(_gen)|" "$ENV_FILE"
    echo "  ✓ Generated JWT_REFRESH_SECRET"
    changed=1
fi
if grep -q "^RESTART_SECRET=change-me-to-a-random-secret" "$ENV_FILE"; then
    _sed_i "s|^RESTART_SECRET=change-me-to-a-random-secret|RESTART_SECRET=$(_gen)|" "$ENV_FILE"
    echo "  ✓ Generated RESTART_SECRET"
    changed=1
fi
[[ $changed -eq 0 ]] && echo "  ✓ All secrets already set — nothing changed"
