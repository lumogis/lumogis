#!/usr/bin/env bash
# LUM-320 — doctor repair planner / executor (host operator tool; no orchestrator imports).
set -euo pipefail


if [ -z "${DOCTOR_STREAM_PATH:-}" ] || [ -z "${DOCTOR_REPAIR_RESULT_PATH:-}" ]; then
  echo "DOCTOR_FATAL: repair.sh requires DOCTOR_STREAM_PATH and DOCTOR_REPAIR_RESULT_PATH" >&2
  exit 3
fi

export LUMOGIS_REPO_ROOT="${LUMOGIS_REPO_ROOT:?}"
export DOCTOR_CONFIG_CACHE="${DOCTOR_CONFIG_CACHE:?}"

_doctor_repair_refuse_audit_dir() {
  echo "DOCTOR_REFUSED: cannot initialise audit log directory" >&2
  exit 4
}

_doctor_repair_refuse_concurrent_apply() {
  # repair.sh result contract (3 keys) for direct invocation / tests — not public schema.v2.json
  printf '%s\n' '{"repairs":[],"any_applied":false,"apply_requested":true}' >"$DOCTOR_REPAIR_RESULT_PATH"
  echo "DOCTOR_REFUSED: another doctor --fix --apply is already running" >&2
  exit 4
}

if [ "${DOCTOR_APPLY_MUTATIONS:-0}" = "1" ]; then
  if ! command -v flock >/dev/null 2>&1; then
    echo "DOCTOR_FATAL: flock(1) required for --fix --apply" >&2
    exit 3
  fi
  _raw_audit="${LUMOGIS_DOCTOR_AUDIT_DIR:-}"
  if [ -n "$_raw_audit" ]; then
    AUDIT_DIR="$_raw_audit"
  else
    AUDIT_DIR="${LUMOGIS_REPO_ROOT}/scripts/doctor/.audit"
  fi
  if [ ! -d "$AUDIT_DIR" ]; then
    if ! install -d -m 0700 "$AUDIT_DIR" 2>/dev/null; then
      if ! mkdir -p "$AUDIT_DIR" 2>/dev/null || ! chmod 700 "$AUDIT_DIR" 2>/dev/null; then
        _doctor_repair_refuse_audit_dir
      fi
    fi
  fi
  if ! touch "${AUDIT_DIR}/.write_test" 2>/dev/null; then
    _doctor_repair_refuse_audit_dir
  fi
  rm -f "${AUDIT_DIR}/.write_test"
  LOCKFILE="${AUDIT_DIR}/repair.lock"
  touch "$LOCKFILE"
  chmod 600 "$LOCKFILE"
  exec 200>"$LOCKFILE"
  if ! flock -n 200; then
    _doctor_repair_refuse_concurrent_apply
  fi
fi

exec python3 - <<'PY'
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(os.environ["LUMOGIS_REPO_ROOT"]).resolve()
STREAM = Path(os.environ["DOCTOR_STREAM_PATH"])
OUT = Path(os.environ["DOCTOR_REPAIR_RESULT_PATH"])
CFG_CACHE = Path(os.environ["DOCTOR_CONFIG_CACHE"])

APPLY = os.environ.get("DOCTOR_APPLY_MUTATIONS", "0") == "1"
YES = os.environ.get("DOCTOR_YES", "0") == "1"
if APPLY and not YES:
    if not (sys.stdin.isatty() and sys.stderr.isatty()):
        print(
            "DOCTOR_REFUSED: --fix --apply requires --yes when stdin and stderr are not both TTYs",
            file=sys.stderr,
        )
        raise SystemExit(4)
_raw_audit = os.environ.get("LUMOGIS_DOCTOR_AUDIT_DIR", "").strip()
AUDIT_DIR = Path(_raw_audit) if _raw_audit else (ROOT / "scripts" / "doctor" / ".audit")

TIMEOUT_UP = int(os.environ.get("LUMOGIS_DOCTOR_REPAIR_TIMEOUT_COMPOSE_UP", "120") or "120")
TIMEOUT_PULL = int(os.environ.get("LUMOGIS_DOCTOR_REPAIR_TIMEOUT_OLLAMA_PULL", "1800") or "1800")
TIMEOUT_MKDIR = int(os.environ.get("LUMOGIS_DOCTOR_REPAIR_TIMEOUT_MKDIR", "30") or "30")

K_CORE = frozenset(
    {
        "orchestrator",
        "postgres",
        "caddy",
        "lumogis-web",
        "ollama",
        "qdrant",
        "falkordb",
        "redis",
        "paperless-ngx",
        "gotenberg",
    }
)

SVC_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,62}$")
MODEL_RE = re.compile(r"^[a-zA-Z0-9_./:-]+$")
KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

ALLOW_ROOTS = (
    ROOT,
    Path(os.path.expanduser("~")).resolve(),
    Path("/mnt"),
    Path("/var/lib"),
    Path("/media"),
)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_services_from_config(raw: str) -> set[str]:
    if not raw.strip():
        return set()
    try:
        cfg = json.loads(raw)
    except json.JSONDecodeError:
        return set()
    return set((cfg.get("services") or {}).keys())


def read_dotenv(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
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


def load_redaction_values() -> list[str]:
    envp = ROOT / ".env"
    if not envp.is_file():
        return []
    out: list[str] = []
    for line in envp.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("export "):
            s = s[7:].lstrip()
        if "=" not in s:
            continue
        _k, v = s.split("=", 1)
        v = v.strip().strip('"').strip("'")
        if len(v) >= 8:
            out.append(v)
    return sorted(set(out), key=len, reverse=True)


def redact_text(s: str, secrets: list[str]) -> str:
    for sec in secrets:
        if sec and len(sec) >= 8 and sec in s:
            s = s.replace(sec, "***REDACTED***")
    return s


AUDIT_DEFAULT_MAX_BYTES = 5242880
AUDIT_DEFAULT_MAX_FILES = 5

_AUDIT_MAX_BYTES: int | None = None
_AUDIT_MAX_FILES: int | None = None


def _parse_int_env(name: str) -> int | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        return int(raw, 10)
    except ValueError:
        return None


def _ensure_audit_limits() -> None:
    global _AUDIT_MAX_BYTES, _AUDIT_MAX_FILES
    if _AUDIT_MAX_BYTES is not None and _AUDIT_MAX_FILES is not None:
        return
    max_bytes = _parse_int_env("LUMOGIS_DOCTOR_AUDIT_MAX_BYTES")
    max_files = _parse_int_env("LUMOGIS_DOCTOR_AUDIT_MAX_FILES")
    use_defaults = False
    if max_bytes is None:
        max_bytes = AUDIT_DEFAULT_MAX_BYTES
    elif max_bytes < 1:
        use_defaults = True
    if max_files is None:
        max_files = AUDIT_DEFAULT_MAX_FILES
    elif max_files < 2:
        use_defaults = True
    if use_defaults:
        print(
            "doctor: invalid LUMOGIS_DOCTOR_AUDIT_MAX_BYTES or "
            "LUMOGIS_DOCTOR_AUDIT_MAX_FILES; using defaults "
            f"({AUDIT_DEFAULT_MAX_BYTES} bytes, {AUDIT_DEFAULT_MAX_FILES} files)",
            file=sys.stderr,
        )
        max_bytes = AUDIT_DEFAULT_MAX_BYTES
        max_files = AUDIT_DEFAULT_MAX_FILES
    _AUDIT_MAX_BYTES = max_bytes
    _AUDIT_MAX_FILES = max_files


def _chmod_audit_file(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def maybe_rotate_audit_log(audit_path: Path) -> None:
    _ensure_audit_limits()
    assert _AUDIT_MAX_BYTES is not None and _AUDIT_MAX_FILES is not None
    if not audit_path.is_file():
        return
    try:
        size = audit_path.stat().st_size
    except OSError:
        return
    if size < _AUDIT_MAX_BYTES:
        return

    parent = audit_path.parent
    name = audit_path.name
    max_files = _AUDIT_MAX_FILES

    try:
        oldest = parent / f"{name}.{max_files - 1}"
        if oldest.exists() and not oldest.is_dir():
            oldest.unlink()

        for i in range(max_files - 2, 0, -1):
            src = parent / f"{name}.{i}"
            dst = parent / f"{name}.{i + 1}"
            if src.exists():
                src.replace(dst)
                _chmod_audit_file(dst)

        backup1 = parent / f"{name}.1"
        if audit_path.exists() and not audit_path.is_dir():
            audit_path.replace(backup1)
            _chmod_audit_file(backup1)
    except OSError as exc:
        print(f"doctor: audit rotation failed: {exc}", file=sys.stderr)


def audit_append(
    path: Path,
    *,
    secrets: list[str],
    kind: str,
    target: dict,
    command_argv: list[str],
    exit_status: int | None,
    outcome: str,
    argv_full: list[str],
) -> None:
    disp = redact_text(shlex.join(command_argv), secrets) if command_argv else ""
    rec = {
        "ts": utc_now(),
        "user": os.environ.get("USER") or os.environ.get("LOGNAME") or "",
        "hostname": os.environ.get("HOSTNAME", ""),
        "cwd": str(ROOT),
        "argv": [redact_text(str(x), secrets) for x in argv_full],
        "kind": kind,
        "target": json.loads(redact_text(json.dumps(target, separators=(",", ":")), secrets)),
        "command_argv": [redact_text(str(x), secrets) for x in command_argv],
        "command_display": disp,
        "exit_status": exit_status,
        "outcome": outcome,
    }
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    _ensure_audit_limits()
    maybe_rotate_audit_log(path)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    _chmod_audit_file(path)


def path_allowed_for_mkdir(target: Path) -> tuple[bool, str]:
    if ".." in target.parts:
        return False, "path contains parent segments"
    rp = target
    try:
        rp = target.resolve()
    except OSError as exc:
        return False, f"cannot resolve: {exc}"
    s = str(rp)
    if "/.." in s or s.endswith("/.."):
        return False, "path escape"
    try:
        rp.relative_to(ROOT)
        if rp.parent.is_dir():
            return True, ""
    except ValueError:
        pass
    except OSError as exc:
        return False, str(exc)
    for base in ALLOW_ROOTS:
        if base == ROOT:
            continue
        try:
            br = base.resolve()
            rp.relative_to(br)
            if rp.parent.is_dir():
                return True, ""
        except (ValueError, OSError):
            continue
    return False, "path outside repo or allow-listed roots"


def parse_stream() -> tuple[list[tuple[str, dict]], list[tuple[str, dict]], list[tuple[str, dict]]]:
    compose_rows: list[tuple[str, dict]] = []
    mkdir_rows: list[tuple[str, dict]] = []
    ollama_rows: list[tuple[str, dict]] = []
    for line in STREAM.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split("\t")
        if len(parts) != 7:
            continue
        fk = parts[5]
        try:
            ft = json.loads(parts[6])
        except json.JSONDecodeError:
            ollama_rows.append(("__bad_target__", {"doctor_kind": fk, "raw": parts[6]}))
            continue
        if not isinstance(ft, dict):
            continue
        if fk == "compose_up_service":
            compose_rows.append((fk, ft))
        elif fk == "mkdir_backup_dir":
            mkdir_rows.append((fk, ft))
        elif fk == "ollama_pull_model":
            ollama_rows.append((fk, ft))
    return compose_rows, mkdir_rows, ollama_rows


def validate_compose(service: str, S: set[str]) -> tuple[bool, str, list[str] | None]:
    if not SVC_RE.match(service or ""):
        return False, "invalid service name", None
    if service not in S:
        return False, "service not in compose config (S)", None
    if service not in K_CORE:
        return False, "service not in Lumogis core allowlist (K)", None
    argv = [
        "timeout",
        f"{TIMEOUT_UP}s",
        "docker",
        "compose",
        "up",
        "-d",
        service,
    ]
    return True, "", argv


def validate_model(model: str, want_embed: str, want_llm: str) -> tuple[bool, str, list[str] | None]:
    if not model or model.startswith("-") or not MODEL_RE.match(model):
        return False, "invalid model token", None
    ok_targets = {want_embed.split(":", 1)[0], want_llm.split(":", 1)[0]}
    ok_targets.discard("")
    if model not in ok_targets:
        return False, "model not referenced by .env inference targets", None
    argv = [
        "timeout",
        f"{TIMEOUT_PULL}s",
        "docker",
        "compose",
        "exec",
        "-T",
        "ollama",
        "ollama",
        "pull",
        "--",
        model,
    ]
    return True, "", argv


def validate_mkdir(path_s: str) -> tuple[bool, str, list[str] | None, str | None]:
    if ".." in path_s:
        return False, "path contains ..", None, None
    try:
        p = Path(os.path.expandvars(os.path.expanduser(path_s))).resolve()
    except OSError as exc:
        return False, f"cannot resolve path: {exc}", None, None
    ok, msg = path_allowed_for_mkdir(p)
    if not ok:
        return False, msg, None, None
    if p.exists():
        return False, "path already exists", None, None
    if not p.parent.is_dir():
        return False, "parent directory must exist (no deep mkdir -p)", None, None
    argv = ["timeout", f"{TIMEOUT_MKDIR}s", "mkdir", str(p)]
    return True, "", argv, str(p)


def main() -> int:
    try:
        cfg_raw = CFG_CACHE.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"DOCTOR_FATAL: cannot read compose config cache: {exc}", file=sys.stderr)
        return 3
    S = read_services_from_config(cfg_raw)
    dot = read_dotenv(ROOT / ".env")
    want_embed = dot.get("EMBEDDING_MODEL") or os.environ.get("EMBEDDING_MODEL", "") or ""
    want_llm = dot.get("LUMOGIS_DEFAULT_LLM") or os.environ.get("LUMOGIS_DEFAULT_LLM", "") or ""

    compose_rows, mkdir_rows, ollama_rows = parse_stream()
    ordered: list[tuple[str, dict]] = [*compose_rows, *mkdir_rows, *ollama_rows]

    secrets = load_redaction_values()
    try:
        argv_list = json.loads(os.environ.get("DOCTOR_FULL_ARGV_JSON", "[]"))
        if not isinstance(argv_list, list):
            argv_list = []
    except json.JSONDecodeError:
        argv_list = []

    audit_path = AUDIT_DIR / "repair.ndjson"
    audit_ok = True
    if APPLY:
        try:
            AUDIT_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
            testp = AUDIT_DIR / ".write_test"
            testp.write_text("", encoding="utf-8")
            testp.unlink()
        except OSError:
            audit_ok = False

    if APPLY and not audit_ok:
        print("DOCTOR_REFUSED: cannot initialise audit log directory", file=sys.stderr)
        return 4

    planned: list[dict] = []
    for fk, ft in ordered:
        if fk == "__bad_target__":
            planned.append(
                {
                    "kind": str(ft.get("doctor_kind", "unknown")),
                    "target": {},
                    "outcome": "error",
                    "message": "invalid fix_target JSON",
                    "command_argv": [],
                }
            )
            continue
        if fk == "compose_up_service":
            svc = str(ft.get("service", ""))
            ok, msg, argv = validate_compose(svc, S)
            tgt = {"service": svc}
            if not ok:
                out = "error"
                if "allowlist" in msg or "compose config" in msg:
                    out = "skipped"
                planned.append(
                    {
                        "kind": fk,
                        "target": tgt,
                        "outcome": out,
                        "message": msg,
                        "command_argv": [],
                    }
                )
                continue
            planned.append({"kind": fk, "target": tgt, "argv": argv or []})
        elif fk == "mkdir_backup_dir":
            ps = str(ft.get("path", ""))
            ok, msg, argv, canon = validate_mkdir(ps)
            tgt = {"path": ps}
            if not ok:
                planned.append(
                    {
                        "kind": fk,
                        "target": tgt,
                        "outcome": "error",
                        "message": msg,
                        "command_argv": [],
                    }
                )
                continue
            planned.append({"kind": fk, "target": {"path": canon or ""}, "argv": argv or []})
        elif fk == "ollama_pull_model":
            model = str(ft.get("model", ""))
            ok, msg, argv = validate_model(model, want_embed, want_llm)
            tgt = {"model": model}
            if not ok:
                planned.append(
                    {
                        "kind": fk,
                        "target": tgt,
                        "outcome": "error",
                        "message": msg,
                        "command_argv": [],
                    }
                )
                continue
            planned.append({"kind": fk, "target": tgt, "argv": argv or []})
        else:
            planned.append(
                {
                    "kind": fk,
                    "target": ft,
                    "outcome": "skipped",
                    "message": "unknown fix_kind",
                    "command_argv": [],
                }
            )

    do_mut = APPLY and audit_ok
    exec_items = [p for p in planned if "argv" in p]
    declined = False
    if do_mut and not YES and exec_items and sys.stdin.isatty() and sys.stderr.isatty():
        for i, item in enumerate(exec_items, 1):
            disp = redact_text(shlex.join(item["argv"]), secrets)
            print(
                f"{i}. kind={item['kind']} target={item['target']} cmd={disp}",
                file=sys.stderr,
            )
        try:
            ans = input(f"apply {len(exec_items)} repairs? [y/N] ").strip().lower()
        except EOFError:
            ans = "n"
        if ans not in ("y", "yes"):
            do_mut = False
            declined = True

    argv_wants_apply = os.environ.get("DOCTOR_ARGV_WANTS_APPLY", "0") == "1"
    apply_requested = bool(argv_wants_apply and audit_ok and not declined)

    repairs: list[dict] = []
    any_applied = False

    for item in planned:
        if "outcome" in item:
            r = {
                "kind": item["kind"],
                "target": item.get("target", {}),
                "outcome": item["outcome"],
            }
            if item.get("message"):
                r["message"] = item["message"]
            if item.get("command_argv") is not None:
                r["command_argv"] = item.get("command_argv") or []
            repairs.append(r)
            continue

        fk = item["kind"]
        tgt = item["target"]
        argv = item["argv"]
        disp = redact_text(shlex.join(argv), secrets)

        if not do_mut:
            repairs.append(
                {
                    "kind": fk,
                    "target": tgt,
                    "outcome": "dry_run",
                    "command_argv": argv,
                    "command_display": disp,
                }
            )
            continue

        try:
            proc = subprocess.run(
                argv,
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                timeout=None,
            )
            ec = int(proc.returncode)
        except OSError as exc:
            repairs.append(
                {
                    "kind": fk,
                    "target": tgt,
                    "outcome": "failed",
                    "message": str(exc),
                    "command_argv": argv,
                    "command_display": disp,
                }
            )
            audit_append(
                audit_path,
                secrets=secrets,
                kind=fk,
                target=tgt,
                command_argv=argv,
                exit_status=None,
                outcome="failed",
                argv_full=argv_list,
            )
            continue

        outcome = "applied" if ec == 0 else "failed"
        if ec == 0:
            any_applied = True
        msg = ""
        if ec != 0:
            tail = (proc.stderr or proc.stdout or "").strip()
            msg = redact_text(tail[:2000], secrets)

        rec = {
            "kind": fk,
            "target": tgt,
            "outcome": outcome,
            "command_argv": argv,
            "command_display": disp,
        }
        if msg:
            rec["message"] = msg
        repairs.append(rec)

        audit_append(
            audit_path,
            secrets=secrets,
            kind=fk,
            target=tgt,
            command_argv=argv,
            exit_status=ec,
            outcome=outcome,
            argv_full=argv_list,
        )

    OUT.write_text(
        json.dumps(
            {
                "repairs": repairs,
                "any_applied": any_applied,
                "apply_requested": apply_requested,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
PY
