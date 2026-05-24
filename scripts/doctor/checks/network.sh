#!/usr/bin/env bash
# Lumogis doctor — network category (LUM-199). Prints TSV rows; always exits 0.
set -euo pipefail

ROOT="${LUMOGIS_REPO_ROOT:?}"
export ROOT DOCTOR_JSON="${DOCTOR_JSON:-0}"

exec python3 - <<'PY'
import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(os.environ["ROOT"])
JSON_MODE = os.environ.get("DOCTOR_JSON", "0") == "1"


def row(category: str, name: str, status: str, message: str, remediation: str) -> None:
    def clean(s: str) -> str:
        return s.replace("\t", " ").replace("\n", " ").replace("\r", " ")

    print(f"{category}\t{name}\t{status}\t{clean(message)}\t{clean(remediation)}")


def main() -> int:
    try:
        subprocess.run(
            ["curl", "-sf", "--max-time", "8", "-o", "/dev/null", "http://127.0.0.1/"],
            check=True,
            capture_output=True,
        )
        row("network", "caddy-http", "ok", "http://127.0.0.1/ reachable (Caddy front door)", "")
    except subprocess.CalledProcessError as exc:
        row(
            "network",
            "caddy-http",
            "warn",
            f"http://127.0.0.1/ probe failed (exit {exc.returncode})",
            "Ensure Caddy is published on port 80 and the stack is up.",
        )

    ts = shutil.which("tailscale")
    if not ts:
        row("network", "tailscale", "skipped", "tailscale binary not found", "")
        return 0

    try:
        proc = subprocess.run(
            ["timeout", "10s", ts, "status", "--json"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if proc.returncode != 0:
            row("network", "tailscale", "skipped", "tailscale status --json failed", "")
            return 0
        data = json.loads(proc.stdout)
        peers = 0
        if isinstance(data, dict):
            # Prefer Self + Peer counts without echoing hostnames
            backend = data.get("BackendState")
            if isinstance(backend, str) and backend.lower() == "running":
                pm = data.get("Peer")
                if isinstance(pm, dict):
                    peers = len(pm)
        if JSON_MODE:
            msg = f"tailscale: {peers} peers in status json (count only)"
        else:
            msg = f"tailscale: {peers} peers (details omitted; see tailscale status)"
        row("network", "tailscale", "ok", msg, "")
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        row("network", "tailscale", "skipped", "tailscale status not available", "")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
PY
