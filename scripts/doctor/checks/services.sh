#!/usr/bin/env bash
# Lumogis doctor — services category (LUM-199). Prints TSV rows; always exits 0.
set -euo pipefail
ROOT="${LUMOGIS_REPO_ROOT:?}"
PS_CACHE="${DOCTOR_PS_CACHE:?}"
CFG_CACHE="${DOCTOR_CONFIG_CACHE:?}"
PS_EC="${DOCTOR_PS_EC:-1}"
CFG_EC="${DOCTOR_CONFIG_EC:-1}"

export ROOT PS_CACHE CFG_CACHE PS_EC CFG_EC ORCHESTRATOR_HOST_PORT="${ORCHESTRATOR_HOST_PORT:-}"

exec python3 - <<'PY'
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(os.environ["ROOT"])
PS_CACHE = Path(os.environ["PS_CACHE"])
CFG_CACHE = Path(os.environ["CFG_CACHE"])
try:
    PS_EC = int(os.environ.get("PS_EC", "1"))
except ValueError:
    PS_EC = 1
try:
    CFG_EC = int(os.environ.get("CFG_EC", "1"))
except ValueError:
    CFG_EC = 1

KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


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


def parse_compose_ps(raw: str) -> list[dict]:
    raw = raw.strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
        if isinstance(data, dict):
            return [data]
    except json.JSONDecodeError:
        pass
    out: list[dict] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid compose ps json line: {exc}") from exc
        if isinstance(obj, dict):
            out.append(obj)
    return out


def orchestrator_host_port(cfg_raw: str) -> int:
    override = os.environ.get("ORCHESTRATOR_HOST_PORT", "").strip()
    if override.isdigit():
        return int(override)
    if not cfg_raw.strip():
        return 8000
    try:
        cfg = json.loads(cfg_raw)
    except json.JSONDecodeError:
        return 8000
    try:
        orch = (cfg.get("services") or {}).get("orchestrator") or {}
        ports = orch.get("ports")
        if not ports:
            return 8000
        for p in ports:
            if isinstance(p, dict):
                pub = p.get("published")
                tgt = p.get("target")
                if pub is None or tgt is None:
                    continue
                try:
                    t = int(str(tgt).split("/")[0])
                except ValueError:
                    continue
                if t != 8000:
                    continue
                s = str(pub).split("/")[0]
                if s.isdigit():
                    return int(s)
        for p in ports:
            if isinstance(p, dict) and p.get("published") is not None:
                s = str(p["published"]).split("/")[0]
                if s.isdigit():
                    return int(s)
    except (TypeError, ValueError, KeyError, AttributeError):
        pass
    return 8000


def curl_healthz(port: int) -> tuple[str, str]:
    url = f"http://127.0.0.1:{port}/healthz"
    try:
        subprocess.run(
            ["curl", "-sf", "--max-time", "5", url],
            check=True,
            capture_output=True,
        )
        return "ok", f"orchestrator responded on {url}"
    except subprocess.CalledProcessError as exc:
        return "warn", f"curl /healthz failed (exit {exc.returncode}) for {url}"


def curl_caddy_healthz() -> tuple[str, str]:
    try:
        subprocess.run(
            ["curl", "-sf", "--max-time", "5", "http://127.0.0.1/healthz"],
            check=True,
            capture_output=True,
        )
        return "ok", "Caddy edge /healthz responded"
    except subprocess.CalledProcessError as exc:
        return "warn", f"Caddy edge /healthz probe failed (exit {exc.returncode})"


def main() -> int:
    dot = read_dotenv(ROOT / ".env")
    graph_mode = (dot.get("GRAPH_MODE") or os.environ.get("GRAPH_MODE", "") or "").strip().lower()
    compose_file = dot.get("COMPOSE_FILE") or os.environ.get("COMPOSE_FILE", "")

    if PS_EC != 0:
        row(
            "services",
            "docker-ps",
            "error",
            "docker compose ps failed or timed out",
            "Ensure Docker is running and COMPOSE_FILE / COMPOSE_PROJECT_NAME are correct.",
        )
        return 0

    raw_ps = PS_CACHE.read_text(encoding="utf-8", errors="replace")
    try:
        services = parse_compose_ps(raw_ps)
    except ValueError:
        row(
            "services",
            "compose-ps-json",
            "error",
            "Could not parse docker compose ps --format json output",
            "Check docker compose version and daemon health.",
        )
        services = []

    by_name: dict[str, dict] = {}
    for ent in services:
        svc = ent.get("Service") or ent.get("service")
        if not isinstance(svc, str):
            continue
        by_name[svc] = ent

    for svc, ent in sorted(by_name.items()):
        state = str(ent.get("State") or ent.get("state") or "unknown").lower()
        health = ent.get("Health") or ent.get("health")
        health_s = "" if health is None else str(health).lower()
        if state != "running":
            if state in ("exited", "created"):
                row7(
                    "services",
                    svc,
                    "warn",
                    f"service state is {state}",
                    f"docker compose up -d {svc}",
                    "compose_up_service",
                    {"service": svc},
                )
            else:
                row(
                    "services",
                    svc,
                    "warn",
                    f"service state is {state}",
                    f"Inspect docker compose logs {svc} (auto compose up is not offered for this state).",
                )
            continue
        if health_s in ("", "none"):
            row("services", svc, "ok", "running (no compose health field in ps json)", "")
        elif health_s == "healthy":
            row("services", svc, "ok", "healthy", "")
        elif health_s == "unhealthy":
            row(
                "services",
                svc,
                "error",
                "health=unhealthy",
                f"docker compose logs {svc}",
            )
        elif health_s == "starting":
            row(
                "services",
                svc,
                "warn",
                "health=starting",
                f"Wait for healthcheck or inspect: docker compose logs {svc}",
            )
        else:
            row("services", svc, "warn", f"health={health_s}", f"docker compose logs {svc}")

    cfg_raw = ""
    if CFG_CACHE.is_file():
        cfg_raw = CFG_CACHE.read_text(encoding="utf-8", errors="replace")
    port = orchestrator_host_port(cfg_raw)

    orch = by_name.get("orchestrator")
    if not orch:
        row(
            "services",
            "orchestrator-http",
            "skipped",
            "orchestrator container not present in compose ps",
            "docker compose up -d orchestrator",
        )
    else:
        ostate = str(orch.get("State") or "").lower()
        if ostate != "running":
            row(
                "services",
                "orchestrator-http",
                "skipped",
                "orchestrator container is not running",
                "docker compose up -d orchestrator",
            )
        else:
            st, msg = curl_healthz(port)
            rem = ""
            if st != "ok":
                rem = f"Set ORCHESTRATOR_HOST_PORT if port {port} is not the published orchestrator HTTP port."
            row("services", "orchestrator-http", st, msg, rem)

    caddy = by_name.get("caddy")
    web = by_name.get("lumogis-web")
    if caddy and web:
        c_st = str(caddy.get("State") or "").lower()
        w_st = str(web.get("State") or "").lower()
        ch = str(caddy.get("Health") or "").lower()
        wh = str(web.get("Health") or "").lower()
        if c_st == "running" and w_st == "running" and ch == "healthy" and wh == "healthy":
            st, msg = curl_caddy_healthz()
            if st != "ok":
                od, _om = curl_healthz(port)
                if od != "ok":
                    msg += " — direct orchestrator /healthz also failing (likely upstream, not only Caddy edge)"
                else:
                    msg += " — direct orchestrator /healthz ok (check Caddy routing vs upstream)"
            row(
                "services",
                "caddy-edge",
                st,
                msg,
                "Inspect docker/caddy/Caddyfile and docker compose logs caddy",
            )

    overlay = ROOT / "docker-compose.falkordb.yml"
    if graph_mode == "service" and overlay.is_file():
        mentions = "falkordb" in compose_file.lower()
        has_svc = False
        if cfg_raw.strip():
            try:
                cfg = json.loads(cfg_raw)
                has_svc = "falkordb" in (cfg.get("services") or {})
            except json.JSONDecodeError:
                has_svc = False
        if not mentions and not has_svc:
            row(
                "services",
                "compose-graph",
                "warn",
                "GRAPH_MODE=service but FalkorDB does not appear merged in compose config",
                "Add docker-compose.falkordb.yml to COMPOSE_FILE (see docker-compose.falkordb.yml header comments).",
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
PY
