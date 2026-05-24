#!/usr/bin/env bash
# Lumogis doctor — models category (LUM-199). Prints TSV rows; always exits 0.
set -euo pipefail
ROOT="${LUMOGIS_REPO_ROOT:?}"
export ROOT

exec python3 - <<'PY'
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(os.environ["ROOT"])
KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def row(category: str, name: str, status: str, message: str, remediation: str) -> None:
    def clean(s: str) -> str:
        return s.replace("\t", " ").replace("\n", " ").replace("\r", " ")

    print(f"{category}\t{name}\t{status}\t{clean(message)}\t{clean(remediation)}")


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


def ollama_list_names(text: str) -> list[str]:
    names: list[str] = []
    for line in text.splitlines():
        parts = line.split()
        if not parts or parts[0].upper() == "NAME":
            continue
        names.append(parts[0])
    return names


def model_matches(required: str, names: list[str]) -> bool:
    req = required.strip()
    if not req:
        return False
    prefix = req.split(":", 1)[0]
    for n in names:
        if n == req:
            return True
        if n.startswith(prefix + ":") or n.startswith(prefix + "@"):
            return True
        if n == prefix:
            return True
    return False


def main() -> int:
    dot = read_dotenv(ROOT / ".env")
    want_embed = (
        dot.get("EMBEDDING_MODEL")
        or os.environ.get("EMBEDDING_MODEL", "")
        or ""
    )
    want_llm = (
        dot.get("LUMOGIS_DEFAULT_LLM")
        or os.environ.get("LUMOGIS_DEFAULT_LLM", "")
        or ""
    )

    if not want_embed.strip() and not want_llm.strip():
        row(
            "models",
            "ollama-probes",
            "skipped",
            "No model env targets set (EMBEDDING_MODEL / LUMOGIS_DEFAULT_LLM)",
            "Set EMBEDDING_MODEL and optionally LUMOGIS_DEFAULT_LLM in .env to verify pulls.",
        )
        return 0

    try:
        proc = subprocess.run(
            ["timeout", "15s", "docker", "compose", "exec", "-T", "ollama", "ollama", "list"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        row(
            "models",
            "ollama-list",
            "skipped",
            f"Could not run ollama list: {type(exc).__name__}",
            "Start the stack and ensure the ollama service is running (docker compose up -d ollama).",
        )
        return 0

    if proc.returncode != 0:
        row(
            "models",
            "ollama-list",
            "skipped",
            "docker compose exec ollama ollama list failed (stack down or profile disabled?)",
            "docker compose up -d ollama — ensure COMPOSE_PROFILES includes ollama if gated.",
        )
        return 0

    names = ollama_list_names(proc.stdout + "\n" + (proc.stderr or ""))

    if want_embed.strip():
        if model_matches(want_embed, names):
            row("models", "EMBEDDING_MODEL", "ok", "embedding model present in ollama list", "")
        else:
            row(
                "models",
                "EMBEDDING_MODEL",
                "warn",
                f"EMBEDDING_MODEL not matched in ollama list (prefix rule)",
                f"docker compose exec -T ollama ollama pull {want_embed.split(':',1)[0]}",
            )

    if want_llm.strip():
        if model_matches(want_llm, names):
            row("models", "LUMOGIS_DEFAULT_LLM", "ok", "default LLM present in ollama list", "")
        else:
            row(
                "models",
                "LUMOGIS_DEFAULT_LLM",
                "warn",
                "LUMOGIS_DEFAULT_LLM not matched in ollama list (prefix rule)",
                f"docker compose exec -T ollama ollama pull {want_llm.split(':',1)[0]}",
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
PY
