#!/usr/bin/env bash
# Lumogis doctor — storage category (LUM-199). Prints TSV rows; always exits 0.
set -euo pipefail
ROOT="${LUMOGIS_REPO_ROOT:?}"
export ROOT BACKUP_DIR="${BACKUP_DIR:-}"

exec python3 - <<'PY'
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(os.environ["ROOT"])


def row(category: str, name: str, status: str, message: str, remediation: str) -> None:
    def clean(s: str) -> str:
        return s.replace("\t", " ").replace("\n", " ").replace("\r", " ")

    print(f"{category}\t{name}\t{status}\t{clean(message)}\t{clean(remediation)}")


def main() -> int:
    # Docker root free space (best-effort paths)
    candidates = [Path("/var/lib/docker"), ROOT]
    chosen = None
    for p in candidates:
        if p.exists():
            chosen = p
            break
    if chosen is None:
        row("storage", "docker-root", "skipped", "No docker data path found to df", "")
    else:
        try:
            proc = subprocess.run(
                ["df", "-Pk", str(chosen)],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if proc.returncode == 0:
                lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
                tail = lines[-1] if lines else ""
                row("storage", "docker-root", "ok", f"df {chosen}: {tail}", "")
            else:
                row("storage", "docker-root", "warn", "df failed for docker path", "")
        except (OSError, subprocess.TimeoutExpired) as exc:
            row("storage", "docker-root", "warn", f"df error: {type(exc).__name__}", "")

    backup = os.environ.get("BACKUP_DIR", "").strip()
    if not backup:
        row(
            "storage",
            "BACKUP_DIR",
            "skipped",
            "BACKUP_DIR not set",
            "Set BACKUP_DIR in .env if you want backup directory freshness checked.",
        )
    else:
        p = Path(backup)
        if not p.is_dir():
            row("storage", "BACKUP_DIR", "warn", "BACKUP_DIR is not a directory", "Create the directory or fix BACKUP_DIR in .env")
        else:
            try:
                m = p.stat().st_mtime
                age = datetime.now(timezone.utc).timestamp() - m
                row(
                    "storage",
                    "BACKUP_DIR",
                    "ok",
                    f"BACKUP_DIR exists (mtime age seconds ~ {int(age)})",
                    "",
                )
            except OSError as exc:
                row("storage", "BACKUP_DIR", "warn", f"Cannot stat BACKUP_DIR: {exc}", "")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
PY
