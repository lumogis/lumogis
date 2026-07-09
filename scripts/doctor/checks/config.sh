#!/usr/bin/env bash
# Lumogis doctor — config category (LUM-199). Prints TSV rows to stdout; always exits 0.
set -euo pipefail
ROOT="${LUMOGIS_REPO_ROOT:?}"
export LUMOGIS_REPO_ROOT
DOCTOR_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." && pwd)"
exec python3 - "$ROOT" "$DOCTOR_DIR" <<'PY'
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(sys.argv[1])
DOCTOR_DIR = Path(sys.argv[2]) if len(sys.argv) > 2 else (ROOT / "scripts" / "doctor")
ENVF = ROOT / ".env"
KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SECRET_KEY_RE = re.compile(r"(_PASSWORD|_SECRET|_KEY|_TOKEN|_DSN|_CREDENTIALS)$|^DEK|^JWT")
ENV_VALUE_RE = re.compile(r"^[A-Za-z0-9_./:@=+,-]+$")
ALLOW_ENV_EDITS = os.environ.get("LUMOGIS_DOCTOR_ALLOW_ENV_EDITS") == "1"
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


def _clean(s: str) -> str:
    return s.replace("\t", " ").replace("\n", " ").replace("\r", " ")


def row(category: str, name: str, status: str, message: str, remediation: str) -> None:
    print(f"{category}\t{name}\t{status}\t{_clean(message)}\t{_clean(remediation)}")


def fixrow(name: str, status: str, message: str, remediation: str, fix_kind: str, fix_target: dict) -> None:
    # 7-field row: cols 1-5 populate checks[]; cols 6-7 carry the repair (LUM-320).
    ft = json.dumps(fix_target, separators=(",", ":"))
    print(f"config\t{name}\t{status}\t{_clean(message)}\t{_clean(remediation)}\t{fix_kind}\t{ft}")


def _load_env_safelist() -> dict:
    """Editable-key safelist for set_env_key detection (LUM-341). Override via
    LUMOGIS_DOCTOR_ENV_SAFELIST_FILE, else shipped manifest, else built-in."""
    fallback = {
        "EMBEDDING_MODEL": "BAAI/bge-small-en-v1.5",
        "LUMOGIS_DEFAULT_LLM": "llama3.1:8b",
        "GRAPH_MODE": "off",
        "ORCHESTRATOR_HOST_PORT": "8000",
        "RERANKER_BACKEND": "bge",
    }
    candidates = []
    override = os.environ.get("LUMOGIS_DOCTOR_ENV_SAFELIST_FILE", "").strip()
    if override:
        candidates.append(Path(override))
    candidates.append(DOCTOR_DIR / "env-safelist.json")
    for path in candidates:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        keys = doc.get("keys") if isinstance(doc, dict) else None
        if not isinstance(keys, dict):
            continue
        out = {}
        for k, spec in keys.items():
            if not (isinstance(k, str) and KEY.match(k) and not SECRET_KEY_RE.search(k)):
                continue
            default = spec.get("default") if isinstance(spec, dict) else None
            if isinstance(default, str) and ENV_VALUE_RE.match(default):
                out[k] = default
        if out:
            return out
    return fallback


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
    # LUM-341 — slice-2 .env edits are fully opt-in: only surface missing
    # safelisted keys (as set_env_key repair rows) when the operator enabled it.
    if ALLOW_ENV_EDITS:
        safelist = _load_env_safelist()
        for k in sorted(safelist):
            if k in keys_found:
                continue
            fixrow(
                k,
                "warn",
                f"Safelisted key {k} is absent from .env",
                'Append a default with LUMOGIS_DOCTOR_ALLOW_ENV_EDITS=1 make doctor ARGS="--fix --apply --yes" (append-only).',
                "set_env_key",
                {"key": k, "value": safelist[k]},
            )

    ok_required = all(rk in keys_found for rk in required)
    auth_ok = auth_raw != "true" or "LUMOGIS_PUBLIC_ORIGIN" in keys_found
    if ok_required and auth_ok:
        row("config", "dotenv", "ok", ".env present with parseable assignments", "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
PY
