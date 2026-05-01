# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Admin endpoints: health, dashboard, permissions, review-queue, backup, restore, export."""

import datetime
import json
import logging
import os
import re
import zipfile
from pathlib import Path

from auth import get_user
from fastapi import APIRouter
from fastapi import HTTPException
from fastapi import Request
from fastapi.responses import FileResponse
from fastapi.responses import StreamingResponse
from permissions import get_all_permissions
from permissions import set_connector_mode
from pydantic import BaseModel

import config
from settings_store import get_setting
from settings_store import put_settings

_DASHBOARD_HTML = Path(__file__).parent.parent / "dashboard" / "index.html"
_PROJECT_ENV_FILE = Path("/project/.env")

router = APIRouter()
_log = logging.getLogger(__name__)


def _current_restart_secret() -> str:
    """Read RESTART_SECRET from /project/.env at call time.

    The entrypoint generates a new secret on first boot and writes it to
    /project/.env, but the orchestrator's own env var still holds the old
    placeholder.  Reading the file ensures we always send the current token.
    """
    if _PROJECT_ENV_FILE.is_file():
        try:
            for line in _PROJECT_ENV_FILE.read_text().splitlines():
                stripped = line.strip()
                if stripped.startswith("RESTART_SECRET="):
                    return stripped[len("RESTART_SECRET="):].strip()
        except Exception:
            pass
    return os.environ.get("RESTART_SECRET", "")


def _rewrite_host_env_key(content: str, key: str, value: str) -> str:
    """Strip every `key=...` line (flexible whitespace) and append one canonical line.

    A strict ``^KEY=`` regex misses ``KEY = value`` and duplicate lines. Appending
    then leaves an older assignment in place; Compose can keep RERANKER_BACKEND=bge
    while app_settings says false, so the dashboard shows the wrong state after restart.
    """
    pattern = re.compile(
        rf"^[ \t]*{re.escape(key)}[ \t]*=.*(?:\r?\n)?",
        re.MULTILINE,
    )
    content = pattern.sub("", content).rstrip()
    if content:
        content += "\n"
    content += f"{key}={value}\n"
    return content


# Tables restored in dependency order (entities before entity_relations etc.)
_BACKUP_TABLES = [
    "file_index",
    "entities",
    "entity_relations",
    "review_queue",
    "connector_permissions",
    "routine_do_tracking",
    "action_log",
]

_BACKUP_DIR = Path(os.environ.get("BACKUP_DIR", "/workspace/backups"))
_BACKUP_RETENTION_DAYS = 7


class PermissionUpdate(BaseModel):
    mode: str


class SettingsUpdate(BaseModel):
    filesystem_root: str | None = None
    api_keys: dict[str, str] | None = None
    default_model: str | None = None
    optional_models: dict[str, bool] | None = None
    reranker_enabled: bool | None = None


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------


@router.get("/permissions")
def list_permissions():
    return get_all_permissions()


@router.put("/permissions/{connector}")
def update_permission(connector: str, body: PermissionUpdate):
    try:
        set_connector_mode(connector, body.mode.upper())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"connector": connector, "mode": body.mode.upper()}


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


@router.get("/dashboard")
def dashboard():
    """Serve the read-only admin dashboard SPA."""
    if not _DASHBOARD_HTML.exists():
        raise HTTPException(
            status_code=404,
            detail="Dashboard not found. Check that orchestrator/dashboard/index.html exists.",
        )
    return FileResponse(_DASHBOARD_HTML, media_type="text/html")


# ---------------------------------------------------------------------------
# Settings (dashboard control center)
# ---------------------------------------------------------------------------


def _safe_get_setting(key: str, store) -> str | None:
    """get_setting with fallback to None on any DB error (e.g. missing table)."""
    try:
        return get_setting(key, store)
    except Exception:
        return None


def _safe_is_enabled(name: str) -> bool:
    """is_model_enabled with fallback to False on any unexpected error."""
    try:
        return config.is_model_enabled(name)
    except Exception:
        return False


def _get_settings_response():
    store = config.get_metadata_store()
    all_models = config.get_all_models_config()
    model_names = list(all_models.keys())
    effective_root = os.environ.get("FILESYSTEM_ROOT_HOST", os.environ.get("FILESYSTEM_ROOT", ""))
    pending_root = _safe_get_setting("filesystem_root", store)

    # Resolve default_model, falling back to first enabled model if the stored
    # default is a disabled optional provider.
    stored_default = _safe_get_setting("default_model", store)
    if stored_default and _safe_is_enabled(stored_default):
        default_model = stored_default
    else:
        enabled_names = [n for n in model_names if _safe_is_enabled(n)]
        default_model = enabled_names[0] if enabled_names else (model_names[0] if model_names else None)

    api_key_envs = set()
    for cfg in all_models.values():
        env_key = cfg.get("api_key_env")
        if env_key:
            api_key_envs.add(env_key)

    api_key_status = {}
    for env_key in sorted(api_key_envs):
        stored = _safe_get_setting(env_key, store)
        env_val = os.environ.get(env_key, "")
        api_key_status[env_key] = "set" if (stored or env_val) else "not_set"

    # Build per-model info including optional toggle state
    models = []
    optional_models: dict[str, bool] = {}
    for name, cfg in all_models.items():
        base = (cfg.get("base_url") or "").lower()
        is_optional = bool(cfg.get("optional", False))
        enabled = _safe_is_enabled(name)
        entry: dict = {
            "name": name,
            "label": name.replace("-", " ").title(),
            "is_local": "ollama" in base,
            "api_key_env": cfg.get("api_key_env"),
            "optional": is_optional,
            "enabled": enabled,
        }
        models.append(entry)
        if is_optional:
            optional_models[name] = (_safe_get_setting(f"optional_{name}", store) == "true")

    reranker_backend = os.environ.get("RERANKER_BACKEND", "none")
    pending_reranker = _safe_get_setting("reranker_enabled", store)
    if pending_reranker is not None:
        reranker_enabled = pending_reranker.strip().lower() in ("true", "1", "yes")
    else:
        reranker_enabled = reranker_backend.strip().lower() not in ("none", "", "off", "false", "0")

    return {
        "filesystem_root": effective_root,
        "pending_filesystem_root": pending_root,
        "api_key_status": api_key_status,
        "models": models,
        "default_model": default_model,
        "optional_models": optional_models,
        "pending_prune": _safe_get_setting("pending_prune", store) == "true",
        "reranker_enabled": reranker_enabled,
    }


@router.get("/settings")
def get_settings():
    """Return current settings for the dashboard (root path, API key status, models)."""
    return _get_settings_response()


@router.put("/settings")
def update_settings(body: SettingsUpdate):
    """Update settings; API key changes take effect immediately; root path requires restart."""
    store = config.get_metadata_store()
    all_models = config.get_all_models_config()
    model_names = list(all_models.keys())
    known_api_keys = set()
    for cfg in all_models.values():
        if cfg.get("api_key_env"):
            known_api_keys.add(cfg["api_key_env"])

    updates = {}
    if body.filesystem_root is not None:
        new_root = body.filesystem_root.strip()
        if new_root:
            if " " in new_root:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Indexed folder path cannot contain spaces (Docker Compose limitation). "
                        "Move or rename the folder to a path without spaces."
                    ),
                )

            # Translate Windows container path back to host path for .env write-back.
            # When HOST_OS=windows, the browse API returns /host/c/Users/foo.
            # The .env must contain C:/Users/foo for Docker Desktop to mount it correctly.
            host_os = os.environ.get("HOST_OS", "").lower()
            if host_os == "windows" and new_root.startswith("/host/"):
                parts = new_root[len("/host/"):].split("/", 1)
                drive = parts[0].upper()
                rest = parts[1] if len(parts) > 1 else ""
                host_path = f"{drive}:/{rest}"
            else:
                host_path = new_root

            updates["filesystem_root"] = new_root

            env_path = Path("/project/.env")
            if env_path.is_file() and host_path:
                try:
                    content = env_path.read_text()
                    content = _rewrite_host_env_key(content, "FILESYSTEM_ROOT", host_path)
                    env_path.write_text(content)
                    _log.info("Updated FILESYSTEM_ROOT in /project/.env to %s", host_path)
                except Exception as exc:
                    _log.warning("Could not write /project/.env: %s", exc)
        # Empty string: ignore — clients often send the root field on every save;
        # do not clear app_settings or .env by mistake.
    if body.default_model is not None:
        if body.default_model not in model_names:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown model '{body.default_model}'. Available: {model_names}",
            )
        updates["default_model"] = body.default_model
    if body.api_keys is not None:
        for k, v in body.api_keys.items():
            if known_api_keys and k not in known_api_keys:
                continue
            updates[k] = v
    if body.optional_models is not None:
        optional_names = {n for n, cfg in all_models.items() if cfg.get("optional")}
        for name, enabled in body.optional_models.items():
            if name not in optional_names:
                continue
            updates[f"optional_{name}"] = "true" if enabled else "false"

    if body.reranker_enabled is not None:
        new_val = "bge" if body.reranker_enabled else "none"
        updates["reranker_enabled"] = "true" if body.reranker_enabled else "false"
        env_path = Path("/project/.env")
        if env_path.is_file():
            try:
                content = env_path.read_text()
                content = _rewrite_host_env_key(content, "RERANKER_BACKEND", new_val)
                env_path.write_text(content)
                _log.info("Updated RERANKER_BACKEND in /project/.env to %s", new_val)
            except Exception as exc:
                _log.warning("Could not write RERANKER_BACKEND to /project/.env: %s", exc)

    if updates:
        put_settings(store, updates)
        config.invalidate_llm_cache()
        _sync_librechat_config()

    return _get_settings_response()


# ---------------------------------------------------------------------------
# LibreChat config sync
# ---------------------------------------------------------------------------


def _sync_librechat_config() -> None:
    """Regenerate librechat.yaml and restart LibreChat so the model list updates."""
    import httpx as _httpx
    from librechat_config import generate_librechat_yaml

    if not generate_librechat_yaml():
        return
    token = _current_restart_secret()
    try:
        _httpx.post(
            f"{_STACK_CONTROL_URL}/restart",
            json={"services": ["librechat"]},
            headers={"X-Lumogis-Restart-Token": token},
            timeout=30,
        )
        _log.info("LibreChat restart triggered after config sync")
    except Exception as exc:
        _log.warning("Could not restart LibreChat: %s", exc)


# ---------------------------------------------------------------------------
# Restart (delegates to stack-control sidecar)
# ---------------------------------------------------------------------------

_STACK_CONTROL_URL = os.environ.get("STACK_CONTROL_URL", "http://stack-control:9000")


@router.post("/settings/restart")
def restart_stack():
    """Trigger a stack restart via the stack-control sidecar.

    Always uses `compose up --force-recreate` (not `restart`) so the
    orchestrator container reloads `.env` — required after any host.env
    write (e.g. toggling the BGE reranker or changing filesystem root).

    Only the orchestrator is recreated. LibreChat is excluded because it does
    not read the vars written by this endpoint. The response may never arrive
    because this process is killed mid-request as its container is recreated.
    """
    import httpx as _httpx

    store = config.get_metadata_store()
    pending_root = _safe_get_setting("filesystem_root", store)
    current_host_root = os.environ.get("FILESYSTEM_ROOT_HOST", "")
    root_changing = bool(
        pending_root
        and current_host_root
        and pending_root.strip() != current_host_root.strip()
    )

    if root_changing:
        put_settings(store, {"pending_prune": "true"})

    # Always recreate (not `compose restart`) so env_file — e.g. RERANKER_BACKEND — is re-read.
    # Only orchestrator: LibreChat doesn't read RERANKER_BACKEND or other settings-written vars.
    sc_payload: dict = {"recreate": True, "services": ["orchestrator"]}

    token = _current_restart_secret()
    try:
        r = _httpx.post(
            f"{_STACK_CONTROL_URL}/restart",
            headers={"X-Lumogis-Restart-Token": token},
            json=sc_payload,
            timeout=120.0,
        )
        r.raise_for_status()
    except _httpx.HTTPStatusError as exc:
        detail = f"stack-control HTTP {exc.response.status_code}"
        try:
            body = exc.response.json()
            if isinstance(body, dict):
                d = body.get("detail")
                if isinstance(d, str):
                    detail = d
                elif isinstance(d, list) and d:
                    detail = str(d[0]) if len(d) == 1 else str(d)
        except Exception:
            pass
        raise HTTPException(status_code=502, detail=detail)
    except Exception:
        # Orchestrator is always being recreated — the container will be killed mid-request,
        # dropping the connection to stack-control. Swallow the resulting network error;
        # the restart succeeded if stack-control accepted the command.
        pass
    return {"status": "restarting", "root_changed": root_changing}


@router.get("/settings/root-preview")
def root_preview(new_root: str):
    """Return the number of indexed files that would become stale if the root changes."""
    store = config.get_metadata_store()
    current_host_root = os.environ.get("FILESYSTEM_ROOT_HOST", "")
    row = store.fetch_one("SELECT COUNT(*) AS n FROM file_index")
    total = int(row["n"]) if row else 0
    changing = bool(
        current_host_root
        and new_root.strip() != current_host_root.strip()
    )
    return {
        "new_root": new_root,
        "current_root": current_host_root,
        "root_changing": changing,
        "stale_files": total if changing else 0,
    }


@router.post("/settings/prune")
def prune_index():
    """Remove stale index entries whose files no longer exist on disk.

    Called after the orchestrator container is recreated with a new /data
    mount. Deletes Qdrant vectors and Postgres rows for files that are
    no longer accessible, then clears the pending_prune flag.
    """
    store = config.get_metadata_store()
    vs = config.get_vector_store()
    rows = store.fetch_all("SELECT file_path, chunk_count FROM file_index")
    stale = [r for r in rows if not Path(r["file_path"]).exists()]
    pruned_chunks = 0
    for row in stale:
        vs.delete_where(
            "documents",
            {"must": [{"key": "file_path", "match": {"value": row["file_path"]}}]},
        )
        store.execute(
            "DELETE FROM file_index WHERE file_path = %s",
            (row["file_path"],),
        )
        pruned_chunks += row["chunk_count"] or 0
    put_settings(store, {"pending_prune": "", "filesystem_root": ""})
    return {"pruned_files": len(stale), "pruned_chunks": pruned_chunks}


# ---------------------------------------------------------------------------
# Ollama catalog + pull
# ---------------------------------------------------------------------------


class OllamaPullRequest(BaseModel):
    name: str


@router.get("/settings/ollama-discovery")
def ollama_discovery():
    """Return local Ollama models and the public catalog for the dashboard."""
    import ollama_client
    from ollama_client import _prettify_name

    local = ollama_client.list_local_models()
    catalog = ollama_client.fetch_catalog()
    local_names = {m.get("name", "").split(":")[0] for m in local}
    for entry in catalog:
        entry["installed"] = entry["name"].split(":")[0] in local_names
        entry["display_name"] = _prettify_name(entry["name"])

    for m in local:
        base = (m.get("name") or "").split(":")[0]
        m["display_name"] = _prettify_name(base) if base else "Unknown model"

    all_models = config.get_all_models_config()
    alias_map: dict[str, str] = {}
    for alias, cfg in all_models.items():
        ollama_model = cfg.get("model", "")
        base_url = (cfg.get("base_url") or "").lower()
        if "ollama" in base_url or cfg.get("dynamic_ollama"):
            alias_map[ollama_model] = alias

    return {"local": local, "catalog": catalog, "alias_map": alias_map}


@router.post("/settings/ollama-pull")
def ollama_pull(request: Request, body: OllamaPullRequest):
    """Trigger a pull for a specific Ollama model name."""
    import re as _re
    import ollama_client

    name = body.name.strip()
    if not name or not _re.match(r'^[a-zA-Z0-9_\-.:]+$', name):
        raise HTTPException(status_code=400, detail="Invalid model name.")

    try:
        ollama_client.pull_model(name)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Ollama pull failed: {exc}")

    _sync_librechat_config()

    # If this was the embedding model and collections are not yet initialized, do it now.
    # _EMBED_COLLECTIONS is inlined here to avoid importing from main.py (wrong direction).
    _EMBED_COLLECTIONS = ["documents", "conversations", "entities", "signals"]
    if name.split(":")[0] == os.environ.get("EMBEDDING_MODEL", "nomic-embed-text").split(":")[0]:
        try:
            embedder = config.get_embedder()
            if embedder.ping():
                dim = embedder.vector_size
                vs = config.get_vector_store()
                for coll in _EMBED_COLLECTIONS:
                    vs.create_collection(coll, dim)
                request.app.state.embedding_ready = True
                _log.info("Qdrant collections initialized after embedding model pull.")
        except Exception as exc:
            _log.warning(
                "Could not initialize Qdrant collections after pull (%s). "
                "Restart the orchestrator to retry.",
                exc,
            )

    return {"status": "pulled", "name": name}


def _browse_root_info() -> tuple[Path, str, str | None]:
    """Return (container_root, virtual_root_path, platform_note).

    Inspects the /host mount and HOST_OS env var (injected by the
    docker-compose.override.yml platform override) to build the
    correct virtual filesystem view per platform:

      Linux  → /host maps to real host root  → virtual root "/"
      macOS  → /host/Users, /host/Volumes    → virtual root "/"
               (docker-compose mounts macOS /Users → /host/Users)
      Windows→ /host/c, /host/d, …          → virtual root "/"
    """
    host_dir = Path("/host")
    host_os  = os.environ.get("HOST_OS", "").lower()

    if not host_dir.is_dir():
        # No /host mount at all — fallback to the indexed data folder
        data = Path(os.environ.get("FILESYSTEM_ROOT", "/data")).resolve()
        note = (
            "Folder browser is limited to your indexed root. "
            "Copy docker-compose.override.yml.<os> to docker-compose.override.yml "
            "and restart to enable full filesystem browsing."
        )
        return data, "/", note

    # Linux: /host IS the host root; expose it directly as "/"
    if host_os == "linux" or (host_os == "" and (host_dir / "etc").is_dir()):
        return host_dir, "/", None

    # macOS: /host/Users and /host/Volumes are individually mounted.
    # We expose /host itself as "/" but only its real mounted children.
    if host_os == "macos" or (host_dir / "Users").is_dir():
        return host_dir, "/", None

    # Windows: drives appear as /host/c, /host/d, …
    if host_os == "windows" or any((host_dir / d).is_dir() for d in ("c", "d", "e")):
        return host_dir, "/", None

    # /host exists but nothing useful is under it
    data = Path(os.environ.get("FILESYSTEM_ROOT", "/data")).resolve()
    note = (
        "Host filesystem mount is empty. "
        "Copy docker-compose.override.yml.<os> to docker-compose.override.yml "
        "for your platform and restart."
    )
    return data, "/", note


@router.get("/browse")
def browse_directories(path: str = "/"):
    """List immediate subdirectories at an absolute path.

    Automatically adapts to Linux / macOS / Windows depending on what
    docker-compose.override.yml (generated by 'make setup') has mounted.
    """
    container_root, _vroot, platform_note = _browse_root_info()

    safe_path = path.lstrip("/") if path not in ("", "/") else ""
    try:
        target = (container_root / safe_path).resolve()
        target.relative_to(container_root)
    except (ValueError, Exception):
        target = container_root

    if not target.is_dir():
        target = container_root

    try:
        children = sorted(
            [d.name for d in target.iterdir()
             if d.is_dir() and not d.name.startswith(".")],
            key=str.lower,
        )
    except PermissionError:
        children = []

    try:
        rel = str(target.relative_to(container_root))
        host_path = "/" + rel if rel != "." else "/"
    except ValueError:
        host_path = "/"

    parent = str(Path(host_path).parent) if host_path != "/" else None

    return {
        "path": host_path,
        "children": children,
        "is_root": host_path == "/",
        "parent": parent,
        "host_available": platform_note is None,
        "platform_note": platform_note,
    }


class MkdirRequest(BaseModel):
    path: str  # absolute host path to create


@router.post("/browse/mkdir")
def browse_mkdir(body: MkdirRequest):
    """Create a new directory at an absolute virtual path.

    Maps the virtual path back through the same root as the browse endpoint,
    then validates against an OS-appropriate allowlist before touching disk.
    """
    host_os = os.environ.get("HOST_OS", "linux").lower()

    # Allowed virtual prefixes per OS (these are paths as seen by the browser,
    # i.e. relative to the container_root returned by _browse_root_info).
    if host_os == "macos":
        _ALLOWED_PREFIXES = ("/Users", "/Volumes")
    elif host_os == "windows":
        _ALLOWED_PREFIXES = ("/c/Users", "/d/Users", "/c/tmp", "/d/tmp",
                              "/c/Projects", "/c/Work", "/c/Dev")
    else:
        # Linux — same list as before
        _ALLOWED_PREFIXES = (
            "/home", "/mnt", "/media", "/tmp", "/root",
            "/run/user", "/srv", "/opt", "/data",
        )

    container_root, _vroot, _note = _browse_root_info()

    raw = body.path.strip()
    if not raw or not raw.startswith("/"):
        raise HTTPException(status_code=400, detail="Path must be absolute.")

    normalised = str(Path(raw))  # collapse double slashes etc.
    if not any(normalised == p or normalised.startswith(p + "/") for p in _ALLOWED_PREFIXES):
        raise HTTPException(
            status_code=403,
            detail=f"Folder creation is only allowed under: {', '.join(_ALLOWED_PREFIXES)}",
        )

    target = (container_root / normalised.lstrip("/")).resolve()
    try:
        target.relative_to(container_root)
    except ValueError:
        raise HTTPException(status_code=400, detail="Path escapes the host root.")

    if target.exists():
        raise HTTPException(status_code=409, detail="Folder already exists.")

    try:
        target.mkdir(parents=True, exist_ok=False)
    except PermissionError:
        raise HTTPException(status_code=403, detail="Permission denied by the OS.")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    return {"created": normalised}


@router.post("/settings/ollama-delete")
def ollama_delete(body: OllamaPullRequest):
    """Remove a locally pulled Ollama model."""
    import re as _re
    import ollama_client

    name = body.name.strip()
    if not name or not _re.match(r'^[a-zA-Z0-9_\-.:]+$', name):
        raise HTTPException(status_code=400, detail="Invalid model name.")

    try:
        ollama_client.delete_model(name)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Ollama delete failed: {exc}")
    _sync_librechat_config()
    return {"status": "deleted", "name": name}


# ---------------------------------------------------------------------------
# Status + Health
# ---------------------------------------------------------------------------


def _check_service(name: str, check_fn) -> str:
    try:
        return "ok" if check_fn() else "unreachable"
    except Exception:
        return "unreachable"


@router.get("/")
def status_page(request: Request):
    """System status: confirms the orchestrator is running and backends are healthy.

    The `capability_services` section reports out-of-process capability
    services discovered via CAPABILITY_SERVICE_URLS (Area 2). Per the
    ecosystem-plumbing contract, capability service health is informational
    only — it never flips Core's `status` field to "degraded".
    """
    vs = config.get_vector_store()
    meta = config.get_metadata_store()
    embedder = config.get_embedder()

    services = {
        "qdrant": _check_service("qdrant", vs.ping),
        "postgres": _check_service("postgres", meta.ping),
        "embedder": _check_service("embedder", embedder.ping),
    }

    capability_services: dict[str, dict] = {}
    try:
        registry = config.get_capability_registry()
        for svc in registry.all_services():
            capability_services[svc.manifest.id] = {
                "healthy": svc.healthy,
                "version": svc.manifest.version,
                "tools_available": len(svc.manifest.tools),
                "last_seen_healthy": (
                    svc.last_seen_healthy.isoformat() if svc.last_seen_healthy else None
                ),
            }
    except Exception:
        _log.warning("status_page: capability registry read failed", exc_info=True)

    docs_indexed = 0
    sessions_stored = 0
    entities_known = 0
    try:
        docs_indexed = vs.count("documents")
    except Exception:
        pass
    try:
        sessions_stored = vs.count("conversations")
    except Exception:
        pass
    try:
        row = meta.fetch_one("SELECT count(*) as cnt FROM entities")
        entities_known = row["cnt"] if row else 0
    except Exception:
        pass

    all_ok = all(s == "ok" for s in services.values())

    # Use app.state as the single source of truth — set at startup and by post-pull init.
    # Do not re-call embedder.ping() here to avoid latency and divergence.
    embedding_ready = getattr(request.app.state, "embedding_ready", False)

    links: dict = {"api_docs": "http://localhost:8000/docs"}
    extra_links_raw = os.environ.get("STATUS_LINKS", "")
    for pair in extra_links_raw.split(","):
        pair = pair.strip()
        if "=" in pair:
            k, _, v = pair.partition("=")
            links[k.strip()] = v.strip()

    # First-run detection: no data ingested AND no API keys configured.
    # The dashboard uses this to auto-open the Settings tab on first visit.
    no_data = docs_indexed == 0 and sessions_stored == 0 and entities_known == 0
    try:
        api_key_status = _get_settings_response().get("api_key_status", {})
        api_keys_set = any(v == "set" for v in api_key_status.values())
    except Exception:
        api_keys_set = False
    setup_needed = no_data and not api_keys_set

    # MCP server status (Area 4) — surfaced here so the dashboard can show
    # endpoint URL + auth state without needing a dedicated endpoint.
    # `mcp_enabled` reflects whether the mcp package is installed and the
    # FastMCP server constructed successfully; `mcp_auth_required` reflects
    # whether MCP_AUTH_TOKEN is set (i.e. whether external clients must
    # present a Bearer token on /mcp/* requests).
    try:
        import mcp_server as _mcp_server

        mcp_enabled = _mcp_server.mcp is not None
    except Exception:
        mcp_enabled = False
    mcp_auth_required = bool(os.environ.get("MCP_AUTH_TOKEN", "").strip())

    return {
        "status": "healthy" if all_ok else "degraded",
        "embedding_model_ready": embedding_ready,
        "documents_indexed": docs_indexed,
        "sessions_stored": sessions_stored,
        "entities_known": entities_known,
        "services": services,
        "capability_services": capability_services,
        "mcp_enabled": mcp_enabled,
        "mcp_auth_required": mcp_auth_required,
        "links": links,
        "setup_needed": setup_needed,
    }


@router.get("/health")
def health():
    """Detailed health check for all services and data stores.

    Returns 503 if Postgres is unreachable so the dashboard restart-poll and
    Docker healthcheck both correctly treat a degraded DB as unhealthy.
    Returns accurate doc/entity/file counts so the caller can detect drift
    (e.g. Qdrant doc count vs file_index row count mismatch > 5 %).
    """
    from fastapi.responses import JSONResponse

    vs = config.get_vector_store()
    meta = config.get_metadata_store()

    postgres_ok = meta.ping()

    qdrant_doc_count = 0
    try:
        qdrant_doc_count = vs.count("documents")
    except Exception:
        pass

    file_index_count = 0
    total_chunks = 0
    last_ingest: str | None = None
    try:
        row = meta.fetch_one(
            "SELECT COUNT(*) AS cnt, SUM(chunk_count) AS chunks, "
            "MAX(updated_at) AS last_ingest FROM file_index"
        )
        if row:
            file_index_count = row["cnt"] or 0
            total_chunks = row["chunks"] or 0
            last_ingest = row["last_ingest"].isoformat() if row["last_ingest"] else None
    except Exception:
        pass

    entity_count = 0
    try:
        row = meta.fetch_one("SELECT COUNT(*) AS cnt FROM entities")
        entity_count = row["cnt"] if row else 0
    except Exception:
        pass

    # Count failed actions as a proxy for recent errors.
    error_count = 0
    try:
        row = meta.fetch_one("SELECT COUNT(*) AS cnt FROM action_log WHERE allowed = FALSE")
        error_count = row["cnt"] if row else 0
    except Exception:
        pass

    # Drift: Qdrant points vs indexed file chunks (warn if > 5 %).
    chunk_drift_pct: float | None = None
    if total_chunks > 0:
        chunk_drift_pct = round(abs(qdrant_doc_count - total_chunks) / total_chunks * 100, 1)

    capability_summary = {"registered": 0, "healthy": 0}
    try:
        registered = config.get_capability_registry().all_services()
        capability_summary = {
            "registered": len(registered),
            "healthy": sum(1 for s in registered if s.healthy),
        }
    except Exception:
        _log.warning("/health: capability registry read failed", exc_info=True)

    body = {
        "qdrant_doc_count": qdrant_doc_count,
        "file_index_count": file_index_count,
        "total_chunks_indexed": total_chunks,
        "entity_count": entity_count,
        "last_ingest": last_ingest,
        "error_count": error_count,
        "chunk_drift_pct": chunk_drift_pct,
        "postgres_ok": postgres_ok,
        "capability_services": capability_summary,
    }
    status_code = 200 if postgres_ok else 503
    return JSONResponse(content=body, status_code=status_code)


# ---------------------------------------------------------------------------
# Review queue
# ---------------------------------------------------------------------------


@router.get("/review-queue")
def review_queue():
    """Return pending entity merge candidates from the review_queue table."""
    meta = config.get_metadata_store()
    try:
        rows = meta.fetch_all(
            "SELECT rq.id, rq.reason, rq.created_at, "
            "  a.name AS candidate_a, a.entity_type AS type_a, "
            "  b.name AS candidate_b, b.entity_type AS type_b "
            "FROM review_queue rq "
            "JOIN entities a ON rq.candidate_a_id = a.entity_id "
            "JOIN entities b ON rq.candidate_b_id = b.entity_id "
            "ORDER BY rq.created_at DESC "
            "LIMIT 200"
        )
    except Exception as exc:
        _log.warning("review_queue: DB query failed — %s", exc)
        return []

    return [
        {
            "id": r["id"],
            "reason": r["reason"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "candidate_a": {"name": r["candidate_a"], "type": r["type_a"]},
            "candidate_b": {"name": r["candidate_b"], "type": r["type_b"]},
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Backup / Restore
# ---------------------------------------------------------------------------


def _prune_old_backups(backup_dir: Path, retention_days: int) -> None:
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=retention_days)
    for old_zip in backup_dir.glob("backup_*.zip"):
        try:
            mtime = datetime.datetime.utcfromtimestamp(old_zip.stat().st_mtime)
            if mtime < cutoff:
                old_zip.unlink()
                _log.info("Pruned old backup: %s", old_zip.name)
        except Exception:
            _log.exception("Failed to prune backup %s", old_zip)


@router.post("/backup")
def backup():
    """Create a timestamped backup zip in ai-workspace/backups/.

    Contains:
      - postgres/<table>.json — all Postgres tables as JSON rows
      - qdrant/<collection>.json — all Qdrant point payloads (no vectors)
      - manifest.json — metadata about this backup

    Vectors are omitted to keep file size small; restore re-embeds text from
    saved payloads.  7-day retention is applied automatically.

    Restore procedure:
      1. Stop services: ``docker compose stop``
      2. Delete volumes: ``docker volume rm <project>_postgres_data <project>_qdrant_data``
      3. Restart services: ``docker compose up -d``  (init.sql re-creates schema)
      4. Call ``POST /restore`` with ``{"zip_path": "<path to zip>"}``
      5. Verify: ``curl /search?q=test``
    """
    _BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    meta = config.get_metadata_store()
    vs = config.get_vector_store()
    ts = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    zip_path = _BACKUP_DIR / f"backup_{ts}.zip"

    manifest: dict = {
        "created_at": datetime.datetime.utcnow().isoformat(),
        "tables": [],
        "collections": [],
    }

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # ---- Postgres ----
        for table in _BACKUP_TABLES:
            try:
                rows = meta.fetch_all(f"SELECT * FROM {table}")  # noqa: S608
                zf.writestr(f"postgres/{table}.json", json.dumps(rows, default=str))
                manifest["tables"].append({"name": table, "rows": len(rows)})
                _log.info("Backup: %s — %d rows", table, len(rows))
            except Exception:
                _log.exception("Backup: failed to dump table '%s'", table)

        # ---- Qdrant ----
        _qdrant_collections = ["documents", "conversations", "entities"]
        scroll = getattr(vs, "scroll_collection", None)
        for coll in _qdrant_collections:
            if scroll is None:
                _log.warning("Backup: VectorStore has no scroll_collection(); skipping Qdrant dump")
                break
            try:
                points = scroll(coll, with_vectors=False)
                zf.writestr(f"qdrant/{coll}.json", json.dumps(points, default=str))
                manifest["collections"].append({"name": coll, "points": len(points)})
                _log.info("Backup: collection '%s' — %d points", coll, len(points))
            except Exception:
                _log.exception("Backup: failed to dump Qdrant collection '%s'", coll)

        zf.writestr("manifest.json", json.dumps(manifest, default=str))

    _prune_old_backups(_BACKUP_DIR, _BACKUP_RETENTION_DAYS)

    return {
        "status": "ok",
        "path": str(zip_path),
        "size_bytes": zip_path.stat().st_size,
        "manifest": manifest,
    }


class RestoreRequest(BaseModel):
    zip_path: str


@router.post("/restore")
def restore(body: RestoreRequest):
    """Restore from a backup zip created by POST /backup.

    Re-inserts Postgres rows (INSERT … ON CONFLICT DO NOTHING) and
    re-embeds Qdrant document payloads.  Run this *after* restarting the
    stack with fresh volumes so the schema and collections are clean.

    WARNING: this is additive.  Existing rows are kept; duplicate keys are
    silently skipped via ON CONFLICT DO NOTHING.
    """
    zip_path = Path(body.zip_path).resolve()
    allowed_dir = _BACKUP_DIR.resolve()
    if not str(zip_path).startswith(str(allowed_dir) + "/") and zip_path != allowed_dir:
        raise HTTPException(
            status_code=403,
            detail=f"Access denied: restore only reads from {allowed_dir}",
        )
    if not zip_path.exists():
        raise HTTPException(status_code=404, detail=f"Backup file not found: {zip_path}")

    meta = config.get_metadata_store()
    vs = config.get_vector_store()
    embedder = config.get_embedder()

    restored: dict[str, int] = {}

    with zipfile.ZipFile(zip_path, "r") as zf:
        names = set(zf.namelist())

        # ---- Postgres ----
        for table in _BACKUP_TABLES:
            fname = f"postgres/{table}.json"
            if fname not in names:
                continue
            try:
                rows: list[dict] = json.loads(zf.read(fname))
                count = 0
                _COL_RE = re.compile(r"^[a-z_][a-z0-9_]*$")
                for row in rows:
                    columns = list(row.keys())
                    if not all(_COL_RE.match(c) for c in columns):
                        _log.warning("Restore: skipping row with invalid column names in %s", table)
                        continue
                    placeholders = ", ".join(["%s"] * len(columns))
                    col_list = ", ".join(columns)
                    values = tuple(row[c] for c in columns)
                    try:
                        meta.execute(
                            f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) "  # noqa: S608
                            f"ON CONFLICT DO NOTHING",
                            values,
                        )
                        count += 1
                    except Exception:
                        _log.debug("Restore: skipped row in %s (conflict or error)", table)
                restored[table] = count
                _log.info("Restore: %s — %d rows inserted", table, count)
            except Exception:
                _log.exception("Restore: failed to restore table '%s'", table)

        # ---- Qdrant: re-embed from saved payloads ----
        if "qdrant/documents.json" in names:
            try:
                points: list[dict] = json.loads(zf.read("qdrant/documents.json"))
                count = 0
                for pt in points:
                    text = (pt.get("payload") or {}).get("text", "")
                    if not text:
                        continue
                    try:
                        vec = embedder.embed(text)
                        vs.upsert(
                            collection="documents",
                            id=pt["id"],
                            vector=vec,
                            payload=pt["payload"],
                        )
                        count += 1
                    except Exception:
                        _log.exception("Restore: failed to re-embed point %s", pt.get("id"))
                restored["qdrant/documents"] = count
                _log.info("Restore: documents — %d points re-embedded", count)
            except Exception:
                _log.exception("Restore: failed to restore Qdrant documents collection")

        # conversations: no vectors needed — re-upsert payloads with zero vector
        for coll in ("conversations", "entities"):
            fname = f"qdrant/{coll}.json"
            if fname not in names:
                continue
            try:
                points = json.loads(zf.read(fname))
                dim = embedder.vector_size
                count = 0
                for pt in points:
                    try:
                        vs.upsert(
                            collection=coll,
                            id=pt["id"],
                            vector=[0.0] * dim,
                            payload=pt.get("payload") or {},
                        )
                        count += 1
                    except Exception:
                        _log.debug("Restore: skipped point %s in %s", pt.get("id"), coll)
                restored[f"qdrant/{coll}"] = count
                _log.info("Restore: %s — %d points restored", coll, count)
            except Exception:
                _log.exception("Restore: failed to restore Qdrant collection '%s'", coll)

    return {"status": "ok", "restored": restored}


# ---------------------------------------------------------------------------
# Data export
# ---------------------------------------------------------------------------


@router.get("/export")
def export_data(request: Request):
    """Stream a portable NDJSON export of all data for the authenticated user.

    Each line is a JSON object with ``{"section": "<name>", "rows": [...]}``.
    Sections: file_index, entities, entity_relations, review_queue, sessions.

    Unlike /backup (opaque dump for disaster recovery), this is human-readable
    and portable — "your data is yours."
    """
    user_id = get_user(request).user_id
    meta = config.get_metadata_store()
    vs = config.get_vector_store()

    def _emit(section: str, rows: list) -> str:
        return json.dumps({"section": section, "rows": rows}, default=str) + "\n"

    def generate():
        # file_index
        try:
            rows = meta.fetch_all(
                "SELECT file_path, file_type, chunk_count, ocr_used, ingested_at, updated_at "
                "FROM file_index WHERE user_id = %s ORDER BY ingested_at",
                (user_id,),
            )
        except Exception:
            rows = []
        yield _emit("file_index", rows)

        # entities
        try:
            rows = meta.fetch_all(
                "SELECT name, entity_type, aliases, context_tags, mention_count, created_at "
                "FROM entities WHERE user_id = %s ORDER BY mention_count DESC",
                (user_id,),
            )
        except Exception:
            rows = []
        yield _emit("entities", rows)

        # entity_relations (scoped via entities join)
        try:
            rows = meta.fetch_all(
                "SELECT er.relation_type, er.evidence_type, er.evidence_id, "
                "  e.name AS entity_name, er.created_at "
                "FROM entity_relations er "
                "JOIN entities e ON er.source_id = e.entity_id "
                "WHERE e.user_id = %s ORDER BY er.created_at",
                (user_id,),
            )
        except Exception:
            rows = []
        yield _emit("entity_relations", rows)

        # review_queue
        try:
            rows = meta.fetch_all(
                "SELECT rq.reason, rq.created_at, "
                "  a.name AS candidate_a, b.name AS candidate_b "
                "FROM review_queue rq "
                "JOIN entities a ON rq.candidate_a_id = a.entity_id "
                "JOIN entities b ON rq.candidate_b_id = b.entity_id "
                "WHERE rq.user_id = %s ORDER BY rq.created_at",
                (user_id,),
            )
        except Exception:
            rows = []
        yield _emit("review_queue", rows)

        # sessions from Qdrant conversations collection
        scroll = getattr(vs, "scroll_collection", None)
        sessions: list[dict] = []
        if scroll is not None:
            try:
                pts = scroll("conversations", user_id=user_id, with_vectors=False)
                sessions = [{"id": p["id"], **p["payload"]} for p in pts]
            except Exception:
                _log.exception("Export: failed to scroll conversations collection")
        yield _emit("sessions", sessions)

    return StreamingResponse(generate(), media_type="application/x-ndjson")
