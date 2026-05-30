#!/usr/bin/env bash
# Lumogis doctor — storage category (LUM-199). Prints TSV rows; always exits 0.
set -euo pipefail
ROOT="${LUMOGIS_REPO_ROOT:?}"
export ROOT BACKUP_DIR="${BACKUP_DIR:-}"

exec python3 - <<'PY'
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(os.environ["ROOT"])
KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def read_dotenv(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    raw = path.read_text(encoding="utf-8", errors="replace")
    if raw.startswith("\ufeff"):
        raw = raw[1:]
    out: dict[str, str] = {}
    for line in raw.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("export "):
            s = s[7:].lstrip()
        if "=" not in s:
            continue
        k, v = s.split("=", 1)
        k = k.strip()
        if not KEY.match(k):
            continue
        out[k] = v.strip()
    return out


def row(category: str, name: str, status: str, message: str, remediation: str) -> None:
    def clean(s: str) -> str:
        return s.replace("\t", " ").replace("\n", " ").replace("\r", " ")

    print(f"{category}\t{name}\t{status}\t{clean(message)}\t{clean(remediation)}")


def row7(
    category: str,
    name: str,
    status: str,
    message: str,
    remediation: str,
    fix_kind: str,
    fix_target: dict,
) -> None:
    def clean(s: str) -> str:
        return s.replace("\t", " ").replace("\n", " ").replace("\r", " ")

    tgt = json.dumps(fix_target, separators=(",", ":"), ensure_ascii=False)
    if "\t" in tgt:
        tgt = json.dumps(fix_target, ensure_ascii=True)
    print(f"{category}\t{name}\t{status}\t{clean(message)}\t{clean(remediation)}\t{fix_kind}\t{tgt}")


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

    dot = read_dotenv(ROOT / ".env")
    backup = (dot.get("BACKUP_DIR") or os.environ.get("BACKUP_DIR", "") or "").strip()
    if not backup:
        row(
            "storage",
            "BACKUP_DIR",
            "skipped",
            "BACKUP_DIR not set",
            "Set BACKUP_DIR in .env if you want backup directory freshness checked.",
        )
    else:
        expanded = os.path.expandvars(os.path.expanduser(backup))
        p = Path(expanded)
        try:
            rp = str(p.resolve())
        except OSError as exc:
            row("storage", "BACKUP_DIR", "warn", f"Cannot resolve BACKUP_DIR: {exc}", "")
        else:
            if not p.is_dir():
                row7(
                    "storage",
                    "BACKUP_DIR",
                    "warn",
                    "BACKUP_DIR is not a directory",
                    "Create the directory or fix BACKUP_DIR in .env",
                    "mkdir_backup_dir",
                    {"path": rp},
                )
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
