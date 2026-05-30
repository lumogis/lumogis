# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""stack-control: minimal FastAPI sidecar that can trigger a Compose stack restart.

Security:
- Never exposed on a host port — only reachable from within the Docker network.
- Requires X-Lumogis-Restart-Token to match RESTART_SECRET env var.
- Allows only a pre-defined allowlist of service names to restart.
- Runs as non-root inside the container.
"""

import logging
import os
import stat
import subprocess
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


# Only these services may be individually restarted.
_ALLOWED_SERVICES: set[str] = {
    "orchestrator",
    "librechat",
    "ollama",
    "qdrant",
    "postgres",
    "mongodb",
}


def _compose_cmd(args: list[str]) -> list[str]:
    """Build a `docker compose` command with optional project flags."""
    cmd = ["docker", "compose"]
    compose_file = _current_compose_file()
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
    if token != secret:
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
_PROJECT_DIR = os.environ.get("HOST_PROJECT_DIR", "").strip() or "/project"


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
            cwd=_PROJECT_DIR,
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
