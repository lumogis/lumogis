# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""stack-control: minimal FastAPI sidecar that can trigger a Compose stack restart.

Security:
- Never exposed on a host port — only reachable from within the Docker network.
- Requires X-Lumogis-Restart-Token to match RESTART_SECRET env var.
- Allows only a pre-defined allowlist of service names to restart.
- Runs as non-root inside the container.
"""

import hmac
import ipaddress
import json
import logging
import os
import re
import stat
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
_log = logging.getLogger(__name__)


def _verify_docker_socket() -> None:
    """Warn at startup if the Docker socket GID does not match this process's groups."""
    sock = "/var/run/docker.sock"
    try:
        s = os.stat(sock)
        sock_gid = s.st_gid
        proc_gid = os.getgid()
        proc_groups = os.getgroups()
        if sock_gid not in proc_groups and sock_gid != proc_gid:
            _log.warning(
                "Docker socket GID is %d but this process has groups %s — "
                "dashboard restarts may fail. Check /var/run/docker.sock is mounted; "
                "entrypoint.sh should map this GID at container start.",
                sock_gid,
                proc_groups,
            )
    except Exception as e:
        _log.warning("Could not stat Docker socket: %s", e)


_verify_docker_socket()

app = FastAPI(title="Lumogis stack-control", docs_url=None, redoc_url=None)

_COMPOSE_PROJECT = os.environ.get("COMPOSE_PROJECT_NAME", "")
_DEFAULT_COMPOSE_FILE = os.environ.get("COMPOSE_FILE", "docker-compose.yml")

# RESTART_SECRET is read from /project/.env at request time so it stays in sync
# after the orchestrator regenerates secrets on first boot and is then recreated.
# Fallback: env var set at container creation (pre-generation placeholder value).
_RESTART_SECRET_ENV = os.environ.get("RESTART_SECRET", "")
_PROJECT_ENV_FILE = Path("/project/.env")


def _current_restart_secret() -> str:
    """Return the live RESTART_SECRET from /project/.env, falling back to env var."""
    if _PROJECT_ENV_FILE.exists():
        try:
            for line in _PROJECT_ENV_FILE.read_text().splitlines():
                line = line.strip()
                if line.startswith("RESTART_SECRET="):
                    return line[len("RESTART_SECRET="):].strip()
        except Exception:
            pass
    return _RESTART_SECRET_ENV


def _current_compose_file() -> str:
    """Return live COMPOSE_FILE from /project/.env (D10), else container env default."""
    if _PROJECT_ENV_FILE.exists():
        try:
            for line in _PROJECT_ENV_FILE.read_text().splitlines():
                line = line.strip()
                if line.startswith("COMPOSE_FILE="):
                    value = line[len("COMPOSE_FILE=") :].strip()
                    if value:
                        return value
        except Exception:
            pass
    return _DEFAULT_COMPOSE_FILE


_ENV_KEY = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")


def _project_env_keys() -> frozenset[str]:
    """Every key assigned in /project/.env (values are never read here)."""
    keys: set[str] = set()
    if _PROJECT_ENV_FILE.exists():
        try:
            for line in _PROJECT_ENV_FILE.read_text().splitlines():
                m = _ENV_KEY.match(line)
                if m:
                    keys.add(m.group(1))
        except Exception:
            pass
    return frozenset(keys)


# The keys .env had when this sidecar started: its own environment carries their
# values from that moment (``env_file: .env`` in docker-compose.yml).
_STARTUP_ENV_KEYS = _project_env_keys()


def _compose_env() -> dict[str, str]:
    """The environment for every ``docker compose`` call (LUM-989).

    Compose gives the process environment precedence over the project's ``.env``.
    This container was created with ``env_file: .env``, so its environment holds
    the ``.env`` values of the day it started. Handing that to Compose would make a
    later change to ``.env`` (Turn on or off of the knowledge graph, a new public
    address) invisible to anything this sidecar recreates: ``backup`` would keep a
    stale ``GRAPH_MODE`` and silently drop the graph from backups. So every key
    that appears in ``.env``, now or at start-up, is removed here and Compose reads
    it from the file itself. ``COMPOSE_FILE`` and ``RESTART_SECRET`` are read from
    the file by their own helpers and are unaffected.

    A future endpoint that runs ``docker compose`` must pass this as ``env=``.
    """
    drop = _STARTUP_ENV_KEYS | _project_env_keys()
    return {k: v for k, v in os.environ.items() if k not in drop}


# Only these services may be individually restarted.
_ALLOWED_SERVICES: set[str] = {
    "orchestrator",
    "librechat",
    "ollama",
    "qdrant",
    "postgres",
    "mongodb",
}


def _with_default_override(compose_file: str) -> str:
    """Add docker-compose.override.yml as Compose itself would (LUM-980).

    On the host, `docker compose` with no COMPOSE_FILE reads docker-compose.yml and,
    when present, docker-compose.override.yml. This container always has COMPOSE_FILE
    set (the compose default), so without this a restart from Settings would drop the
    override a household put its local changes in. Only for the default file list: an
    explicit COMPOSE_FILE in .env is used exactly as written.
    """
    if compose_file != "docker-compose.yml":
        return compose_file
    if os.path.isfile(os.path.join(_project_dir(), "docker-compose.override.yml")):
        return "docker-compose.yml:docker-compose.override.yml"
    return compose_file


def _compose_cmd(args: list[str]) -> list[str]:
    """Build a `docker compose` command with optional project flags."""
    cmd = ["docker", "compose"]
    compose_file = _with_default_override(_current_compose_file())
    if compose_file:
        for f in compose_file.split(":"):
            f = f.strip()
            if f:
                cmd += ["-f", f]
    if _COMPOSE_PROJECT:
        cmd += ["-p", _COMPOSE_PROJECT]
    return cmd + args


def _check_token(request: Request) -> None:
    secret = _current_restart_secret()
    if not secret:
        raise HTTPException(status_code=503, detail="RESTART_SECRET not configured on server.")
    token = request.headers.get("X-Lumogis-Restart-Token", "")
    # Constant-time compare on bytes: a non-ASCII header must not raise (500).
    if not hmac.compare_digest(token.encode("utf-8"), secret.encode("utf-8")):
        raise HTTPException(status_code=403, detail="Invalid or missing restart token.")


class RestartRequest(BaseModel):
    services: list[str] | None = None  # None = restart full stack
    recreate: bool = False


# `docker compose` resolves RELATIVE bind-mount sources in the compose files against this
# working directory and sends them to the host daemon as HOST paths. Inside this container
# that path is /project, which does not exist on the host — so a stack-control-driven
# --force-recreate mounts empty dirs over /app, /data, etc. (orchestrator then crashes with
# "Could not import module main"). When HOST_PROJECT_DIR is set (RC gates bind-mount the repo
# at that same absolute host path), compose runs from there and relative sources resolve to
# real host paths. Unset (production default) preserves the historical /project behaviour.
# Read at call time — HOST_PROJECT_DIR may be injected after module import in RC overlays.
#
# LUM-980: HOST_PROJECT_DIR only works when that host path is also mounted at the same
# path in this container (the release compose and the RC overlay both do this). Without
# the mount `cwd=` would not exist and every restart would fail, so fall back to /project
# (the historical behaviour) and say so.
def _project_dir() -> str:
    host_dir = os.environ.get("HOST_PROJECT_DIR", "").strip()
    if not host_dir:
        return "/project"
    if not os.path.isdir(host_dir):
        _log.warning(
            "host_project_dir_not_mounted: HOST_PROJECT_DIR=%s is not a directory in this "
            "container; using /project (relative mounts may be empty on recreate)",
            host_dir,
        )
        return "/project"
    return host_dir


@app.post("/restart")
def restart(request: Request, body: RestartRequest = RestartRequest()):
    _check_token(request)

    services = body.services or []
    if services:
        unknown = [s for s in services if s not in _ALLOWED_SERVICES]
        if unknown:
            raise HTTPException(status_code=400, detail=f"Unknown services: {unknown}")

    # `compose restart` does not reload env_file — only recreating applies a new .env.
    # `--no-deps` is critical: without it, --force-recreate propagates to ALL dependency
    # services (postgres, qdrant, stack-control itself), killing this very container
    # mid-command and leaving dependents in "Created" (never-started) state.
    if body.recreate:
        args = ["up", "-d", "--no-build", "--no-deps", "--force-recreate"] + services
    else:
        args = ["restart"] + services

    cmd = _compose_cmd(args)
    _log.info("Running: %s", " ".join(cmd))
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            cwd=_project_dir(),
            env=_compose_env(),
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Command timed out.")

    if result.returncode != 0:
        _log.error("docker compose command failed: %s", result.stderr)
        raise HTTPException(
            status_code=500,
            detail=f"docker compose exited {result.returncode}: {result.stderr[:400]}",
        )

    _log.info("Command complete: %s", result.stdout.strip())
    return {"status": "restarted", "services": services or "all"}


@app.get("/health")
def health():
    return {"status": "ok"}


_COMPOSE_PS_TIMEOUT_SEC = 15
_DF_TIMEOUT_SEC = int(os.environ.get("LUMOGIS_STACK_STATUS_DF_TIMEOUT_SEC", "30"))

_df_lock = threading.Lock()
_df_busy = False
_last_system_df: object | None = None
_last_system_df_error: str | None = None


def _parse_compose_ps_output(stdout: str) -> list[dict]:
    """Accept NDJSON (one object per line) or a top-level JSON array."""
    text = stdout.strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return [row for row in parsed if isinstance(row, dict)]
    except json.JSONDecodeError:
        pass
    rows: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


def _run_compose_ps() -> list[dict]:
    cmd = _compose_cmd(["ps", "--format", "json"])
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=_COMPOSE_PS_TIMEOUT_SEC,
        cwd=_project_dir(),
        env=_compose_env(),
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"docker compose ps exited {result.returncode}: {result.stderr[:400]}"
        )
    return _parse_compose_ps_output(result.stdout)


def _run_system_df() -> tuple[object | None, str | None]:
    """Return (parsed rows or None, error message)."""
    cmd = ["docker", "system", "df", "--format", "{{json .}}"]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_DF_TIMEOUT_SEC,
            cwd=_project_dir(),
        )
    except subprocess.TimeoutExpired:
        raise
    if result.returncode != 0:
        return None, f"docker system df exited {result.returncode}: {result.stderr[:400]}"
    rows: list[dict] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    if not rows:
        return None, "docker system df returned no parseable rows"
    return rows, None


@app.get("/status")
def stack_status(request: Request):
    """Read-only compose ps + docker system df snapshot (token-gated)."""
    global _df_busy, _last_system_df, _last_system_df_error

    _check_token(request)
    fetched_at = datetime.now(timezone.utc).isoformat()

    try:
        compose_ps = _run_compose_ps()
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="docker compose ps timed out.")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)[:400])

    system_df: object | None = None
    system_df_error: str | None = None
    system_df_busy = False

    with _df_lock:
        if _df_busy:
            system_df_busy = True
            system_df = _last_system_df
            system_df_error = _last_system_df_error or "system_df_busy"
        else:
            _df_busy = True

    if not system_df_busy:
        try:
            system_df, system_df_error = _run_system_df()
        except subprocess.TimeoutExpired:
            with _df_lock:
                _df_busy = False
            raise HTTPException(status_code=504, detail="docker system df timed out.")
        with _df_lock:
            _last_system_df = system_df
            _last_system_df_error = system_df_error
            _df_busy = False

    return {
        "compose_ps": compose_ps,
        "system_df": system_df,
        "system_df_error": system_df_error,
        "system_df_busy": system_df_busy,
        "fetched_at": fetched_at,
    }


# ---------------------------------------------------------------------------
# LUM-331 — secure address at home (Lumogis Home DNS)
#
# Four fixed operations for Core. No request input ever reaches a command line:
# every argv below is a constant or a value this sidecar derived itself.
# ---------------------------------------------------------------------------

# Runs inside a throwaway host-network container: connect() on a UDP socket sends
# no packet, it only asks the kernel which source address the default route uses.
HOST_HELPER_PROGRAM = (
    "import socket\n"
    "s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)\n"
    "s.connect(('1.1.1.1', 53))\n"
    "print(s.getsockname()[0])\n"
)
HOST_HELPER_FLAGS = (
    "--rm",
    "--pull",
    "never",
    "--network",
    "host",
    "--read-only",
    "--cap-drop",
    "ALL",
    "--security-opt",
    "no-new-privileges",
    "--user",
    "65534:65534",
    "--pids-limit",
    "16",
    "--memory",
    "32m",
    "--entrypoint",
    "python3",
)
_OCTET = r"(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])"
_DOTTED_QUAD = re.compile(rf"{_OCTET}\.{_OCTET}\.{_OCTET}\.{_OCTET}")
_IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}")
_RFC1918 = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
)

CADDY_HOME_FILE = "/etc/caddy/home.d/home.caddy"
CADDY_PENDING_FILE = CADDY_HOME_FILE + ".pending"
CADDY_REJECTED_MARKER = "/tmp/lumogis-home-rejected"
_HOME_STATUS_SCRIPT = (
    f"test -f {CADDY_HOME_FILE}; echo $?; test -f {CADDY_REJECTED_MARKER}; echo $?"
)
_CADDY_EXEC_TIMEOUT_SEC = 30
_CADDY_RESTART_TIMEOUT_SEC = 60  # compose restart: stop (10 s grace) + start

# Caddy can echo config lines (including the acme-dns password) in errors.
_REDACT = (
    re.compile(r"(?i)(password\s+)\S+"),
    re.compile(r'(?i)("password"\s*:\s*)"[^"]*"'),
    re.compile(r"(?i)(x-api-key\s*[:=]\s*)\S+"),
    re.compile(r"()[A-Za-z0-9_-]{43,}"),
)


def _redact(text: str) -> str:
    for pattern in _REDACT:
        text = pattern.sub(lambda m: m.group(1) + "***", text)
    return text


def _run(
    cmd: list[str], timeout: float, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, cwd=_project_dir(), env=env
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        _log.warning("lum331: %s failed: %s", cmd[:3], exc.__class__.__name__)
        return None


def _caddy_exec(args: list[str]) -> subprocess.CompletedProcess | None:
    return _run(
        _compose_cmd(["exec", "-T", "caddy", *args]), _CADDY_EXEC_TIMEOUT_SEC, _compose_env()
    )


def _is_rfc1918(value: str) -> bool:
    if not _DOTTED_QUAD.fullmatch(value):
        return False
    addr = ipaddress.IPv4Address(value)
    return any(addr in net for net in _RFC1918)


def _own_image_id() -> str | None:
    """This sidecar's own image ID (``sha256:…``), present locally by definition.

    Resolved from compose labels rather than a configured tag, because the release
    compose builder rewrites only ``image:`` values (never env), so a configured
    reference would name a tag a release install does not have.
    """
    cmd = ["docker", "ps", "-q", "--filter", "label=com.docker.compose.service=stack-control"]
    if _COMPOSE_PROJECT:
        cmd += ["--filter", f"label=com.docker.compose.project={_COMPOSE_PROJECT}"]
    ps = _run(cmd, 10)
    if ps is None or ps.returncode != 0 or not ps.stdout.split():
        return None
    inspect = _run(["docker", "inspect", "--format", "{{.Image}}", ps.stdout.split()[0]], 10)
    if inspect is None or inspect.returncode != 0:
        return None
    image = inspect.stdout.strip()
    return image if _IMAGE_ID.fullmatch(image) else None


@app.get("/host-lan-address")
def host_lan_address(request: Request):
    """The host's home-network IPv4 (RFC1918), or ``null`` with a reason."""
    _check_token(request)
    info = _run(["docker", "info", "--format", "{{.OperatingSystem}}"], 10)
    if info is not None and "docker desktop" in info.stdout.lower():
        # Host networking there is the VM's, not the Mac's or PC's home network.
        return {"address": None, "reason": "docker_desktop"}
    image = _own_image_id()
    if image is None:
        return {"address": None, "reason": "helper_image_missing"}
    result = _run(["docker", "run", *HOST_HELPER_FLAGS, image, "-c", HOST_HELPER_PROGRAM], 15)
    if result is None or result.returncode != 0:
        return {"address": None, "reason": "failed"}
    out = result.stdout.strip()
    if _is_rfc1918(out):
        return {"address": out, "reason": None}
    return {"address": None, "reason": "not_rfc1918" if _DOTTED_QUAD.fullmatch(out) else "failed"}


@app.post("/caddy/apply")
def caddy_apply(request: Request):
    """Validate ``home.caddy.pending`` inside the Caddy container before Core uses it."""
    _check_token(request)
    running = _caddy_exec(["true"])
    if running is None or running.returncode != 0:
        return {"ok": False, "code": "caddy_unreachable"}
    present = _caddy_exec(["test", "-f", CADDY_PENDING_FILE])
    if present is None or present.returncode != 0:
        # Core wrote the file, so Caddy not seeing it means the shared volume is missing.
        return {"ok": False, "code": "caddy_volume_missing"}
    check = _caddy_exec(["/etc/caddy/validate-home.sh"])
    if check is None:
        return {"ok": False, "code": "caddy_unreachable"}
    if check.returncode in (126, 127):
        return {"ok": False, "code": "caddy_image_outdated"}
    if check.returncode != 0:
        return {"ok": False, "code": "caddy_snippet_rejected"}
    return {"ok": True, "code": "validated"}


@app.post("/caddy/reload")
def caddy_reload(request: Request):
    """Graceful ``caddy reload`` of the main Caddyfile. Never returns stderr."""
    _check_token(request)
    result = _caddy_exec(
        ["caddy", "reload", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
    )
    if result is None or result.returncode != 0:
        detail = _redact((result.stderr if result else "")[-800:])
        _log.warning("lum331: caddy reload failed: %s", detail)
        return {"ok": False, "code": "reload_failed"}
    # The full Caddyfile (household import included) is live again, so a start
    # without the household site is over: clear preflight.sh's marker, or Core
    # would re-apply the site on every upkeep run.
    _caddy_exec(["rm", "-f", CADDY_REJECTED_MARKER])
    return {"ok": True, "code": "reloaded"}


@app.post("/caddy/restart")
def caddy_restart(request: Request):
    """Restart the Caddy container (LUM-1004). Never returns stderr.

    A ``caddy reload`` keeps a still-valid certificate in Caddy's in-memory cache
    even when the site now names another CA (seen live on 2026-11-18, LUM-998
    step 0), so a CA switch needs a restart: Caddy then loads the certificate for
    the configured issuer from storage, or obtains one. A dedicated endpoint, not
    an entry in ``_ALLOWED_SERVICES`` (LUM-331 kept caddy out of the generic
    restart on purpose); takes no input.
    """
    _check_token(request)
    result = _run(_compose_cmd(["restart", "caddy"]), _CADDY_RESTART_TIMEOUT_SEC, _compose_env())
    if result is None or result.returncode != 0:
        detail = _redact((result.stderr if result else "")[-800:])
        _log.warning("lum1004: caddy restart failed: %s", detail)
        return {"ok": False, "code": "restart_failed"}
    return {"ok": True, "code": "restarted"}


@app.get("/caddy/home-status")
def caddy_home_status(request: Request):
    """Whether Caddy sees ``home.caddy`` and whether its preflight rejected it at start."""
    _check_token(request)
    result = _caddy_exec(["sh", "-c", _HOME_STATUS_SCRIPT])
    if result is None or result.returncode != 0:
        return {"present": None, "rejected_at_start": None, "reachable": False}
    lines = result.stdout.split()
    if len(lines) != 2:
        return {"present": None, "rejected_at_start": None, "reachable": False}
    return {"present": lines[0] == "0", "rejected_at_start": lines[1] == "0", "reachable": True}


# ---------------------------------------------------------------------------
# LUM-989 — knowledge graph on / off for installs from the Release files
#
# Two fixed sequences for Core, which decides (install kind, memory, consent) and
# edits .env first; this sidecar only runs the Compose commands. No request input
# reaches a command line, and nothing but ``{ok, code}`` is returned (stderr goes
# to the redacted log).
#
# Each sequence is one request so that Core being recreated by its own last step
# cannot cut it short. The request holds one worker thread for up to about 1700 s
# (the image pull dominates); one lock admits one sequence at a time.
# ---------------------------------------------------------------------------

GRAPH_SERVICES = ("falkordb", "lumogis-graph")
# Services that read the graph settings from .env at creation: the backup job
# (GRAPH_MODE, FALKORDB_URL pick its stores) and Core (registry discovery).
GRAPH_RECREATE = ("backup", "orchestrator")
_GRAPH_PULL_TIMEOUT_SEC = 1500
_GRAPH_STOP_TIMEOUT_SEC = 120
_GRAPH_RM_TIMEOUT_SEC = 60
_GRAPH_RECREATE_TIMEOUT_SEC = 120

_graph_lock = threading.Lock()


def _graph_step(label: str, args: list[str], timeout: float) -> str | None:
    """Run one ``docker compose`` step; return None on success, else a failure code."""
    cmd = _compose_cmd(args)
    _log.info("lum989: %s: %s", label, " ".join(cmd))
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=_project_dir(),
            env=_compose_env(),
        )
    except subprocess.TimeoutExpired:
        _log.warning("lum989: %s timed out after %ss", label, timeout)
        return "timeout"
    except OSError as exc:
        _log.warning("lum989: %s could not start: %s", label, exc.__class__.__name__)
        return "compose_failed"
    if result.returncode != 0:
        _log.warning(
            "lum989: %s exited %s: %s",
            label,
            result.returncode,
            _redact((result.stderr or "")[-400:]),
        )
        return "compose_failed"
    return None


def _graph_sequence(steps: list[tuple[str, list[str], float]], ok_code: str) -> dict:
    if not _graph_lock.acquire(blocking=False):
        return {"ok": False, "code": "busy"}
    try:
        for label, args, timeout in steps:
            failure = _graph_step(label, args, timeout)
            if failure is not None:
                return {"ok": False, "code": failure}
        return {"ok": True, "code": ok_code}
    finally:
        _graph_lock.release()


_GRAPH_RECREATE_STEP = (
    "recreate",
    ["up", "-d", "--no-build", "--no-deps", "--force-recreate", *GRAPH_RECREATE],
    _GRAPH_RECREATE_TIMEOUT_SEC,
)


@app.post("/graph/start")
def graph_start(request: Request):
    """Start FalkorDB and lumogis-graph, then recreate backup and Core."""
    _check_token(request)
    return _graph_sequence(
        [
            ("start", ["up", "-d", "--no-build", *GRAPH_SERVICES], _GRAPH_PULL_TIMEOUT_SEC),
            _GRAPH_RECREATE_STEP,
        ],
        "started",
    )


@app.post("/graph/stop")
def graph_stop(request: Request):
    """Stop and remove the graph containers (data volume kept), recreate backup and Core.

    ``down`` skips services of an inactive profile, and Turn off has already taken
    ``graph`` out of COMPOSE_PROFILES, so the services are named (proven to work).
    """
    _check_token(request)
    return _graph_sequence(
        [
            ("stop", ["stop", *GRAPH_SERVICES], _GRAPH_STOP_TIMEOUT_SEC),
            ("rm", ["rm", "-f", *GRAPH_SERVICES], _GRAPH_RM_TIMEOUT_SEC),
            _GRAPH_RECREATE_STEP,
        ],
        "stopped",
    )
