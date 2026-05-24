#!/usr/bin/env bash
# Lumogis doctor — config category (LUM-199). Prints TSV rows to stdout; always exits 0.
set -euo pipefail
ROOT="${LUMOGIS_REPO_ROOT:?}"
export LUMOGIS_REPO_ROOT
exec python3 - "$ROOT" <<'PY'
import re
import sys
from pathlib import Path

ROOT = Path(sys.argv[1])
ENVF = ROOT / ".env"
KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
PLACEHOLDERS = frozenset({"", "change-me-in-production", "__GENERATE_ME__"})
WATCH_KEYS = frozenset(
    {
        "AUTH_SECRET",
        "LUMOGIS_CREDENTIAL_KEY",
        "LUMOGIS_CREDENTIAL_KEYS",
        "JWT_SECRET",
        "JWT_REFRESH_SECRET",
        "RESTART_SECRET",
    }
)


def row(category: str, name: str, status: str, message: str, remediation: str) -> None:
    def clean(s: str) -> str:
        return s.replace("\t", " ").replace("\n", " ").replace("\r", " ")

    print(f"{category}\t{name}\t{status}\t{clean(message)}\t{clean(remediation)}")


def main() -> int:
    if not ENVF.is_file():
        row(
            "config",
            "dotenv",
            "error",
            ".env file is missing at repo root",
            "Copy .env.example to .env and configure required keys (see docs/deployment/quickstart.md).",
        )
        return 0

    raw = ENVF.read_text(encoding="utf-8", errors="replace")
    if raw.startswith("\ufeff"):
        raw = raw[1:]
    keys_found: dict[str, str] = {}
    for line in raw.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("export "):
            s = s[7:].lstrip()
        if "=" not in s:
            continue
        key, val = s.split("=", 1)
        key = key.strip()
        if not KEY.match(key):
            continue
        keys_found[key] = val.strip()

    required = ["COMPOSE_PROJECT_NAME", "COMPOSE_FILE"]
    for rk in required:
        if rk not in keys_found:
            row(
                "config",
                rk,
                "error",
                f"Required key {rk} missing from .env",
                "Add the key to .env (see .env.example).",
            )

    auth_raw = keys_found.get("AUTH_ENABLED", "false").strip().lower()
    if auth_raw == "true":
        if "LUMOGIS_PUBLIC_ORIGIN" not in keys_found:
            row(
                "config",
                "LUMOGIS_PUBLIC_ORIGIN",
                "error",
                "AUTH_ENABLED=true requires LUMOGIS_PUBLIC_ORIGIN in .env",
                "Set LUMOGIS_PUBLIC_ORIGIN to the exact browser origin (scheme + host + optional port).",
            )

    for wk in sorted(WATCH_KEYS):
        if wk not in keys_found:
            continue
        v = keys_found[wk].strip().strip('"').strip("'")
        if v in PLACEHOLDERS:
            row(
                "config",
                wk,
                "warn",
                f"Key {wk} appears unset or placeholder",
                "Replace placeholder values before production; see orchestrator/docker-entrypoint.sh and .env.example.",
            )

    if "AUTH_ENABLED" in keys_found:
        row("config", "AUTH_ENABLED", "ok", "AUTH_ENABLED is set (informational)", "")
    if "GRAPH_MODE" in keys_found:
        row("config", "GRAPH_MODE", "ok", "GRAPH_MODE is set (informational)", "")
    ok_required = all(rk in keys_found for rk in required)
    auth_ok = auth_raw != "true" or "LUMOGIS_PUBLIC_ORIGIN" in keys_found
    if ok_required and auth_ok:
        row("config", "dotenv", "ok", ".env present with parseable assignments", "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
PY
