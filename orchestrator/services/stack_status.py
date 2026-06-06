# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Read-only aggregation for ``GET /api/v1/admin/diagnostics/stack-status``."""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from datetime import datetime
from datetime import timezone
from typing import Any
from typing import Literal

import httpx
import ollama_client
from models.api_v1 import AdminDiagnosticsStoreItem
from models.api_v1 import AdminDiagnosticsWarning
from models.api_v1 import StackStatusMeta
from models.api_v1 import StackStatusOllamaModel
from models.api_v1 import StackStatusResponse
from models.api_v1 import StackStatusServiceItem
from models.api_v1 import StackStatusStorageItem
from services.admin_diagnostics import _graph_store_row
from services.admin_diagnostics import _store_row

import config

_log = logging.getLogger(__name__)

_STACK_CONTROL_URL = os.environ.get("STACK_CONTROL_URL", "http://stack-control:9000")
_CACHE_TTL_SEC = float(os.environ.get("LUMOGIS_STACK_STATUS_CACHE_TTL_SEC", "60"))
_HTTP_TIMEOUT_SEC = float(os.environ.get("LUMOGIS_STACK_STATUS_HTTP_TIMEOUT_SEC", "45"))
_WARN_PERCENT = float(os.environ.get("LUMOGIS_STORAGE_WARN_PERCENT", "80"))
_CRITICAL_PERCENT = float(os.environ.get("LUMOGIS_STORAGE_CRITICAL_PERCENT", "95"))

_COMPOSE_ID_MAP: dict[str, str] = {
    "lumogis-web": "lumogis_web",
    "stack-control": "stack_control",
}

_DISPLAY_NAMES: dict[str, str] = {
    "orchestrator": "Orchestrator",
    "postgres": "Postgres",
    "qdrant": "Qdrant",
    "ollama": "Ollama",
    "mongodb": "MongoDB",
    "librechat": "LibreChat",
    "lumogis_web": "Lumogis Web",
    "caddy": "Caddy",
    "stack_control": "Stack control",
    "graph": "Graph",
}

_KNOWN_COMPOSE_IDS: tuple[str, ...] = tuple(_DISPLAY_NAMES.keys())

_PING_BY_SERVICE_ID: dict[str, str] = {
    "postgres": "postgres",
    "qdrant": "qdrant",
    "graph": "graph",
}

_REQUIRED_FOR_OVERALL: frozenset[str] = frozenset({"postgres", "orchestrator", "qdrant"})

_cache_lock = threading.Lock()
_cache_payload: dict[str, Any] | None = None
_cache_at: float | None = None
_fetch_in_progress = False

ServiceState = Literal["healthy", "degraded", "down", "unknown", "not_configured"]


def _env_extra_mounts() -> list[str]:
    raw = os.environ.get("LUMOGIS_STACK_STATUS_EXTRA_MOUNTS", "").strip()
    if not raw:
        return []
    return [p.strip() for p in raw.split(",") if p.strip()]


def _normalize_compose_service_name(name: str) -> str:
    key = name.strip().lower()
    return _COMPOSE_ID_MAP.get(key, re.sub(r"[^a-z0-9]+", "_", key).strip("_"))


def _display_name(service_id: str) -> str:
    if service_id in _DISPLAY_NAMES:
        return _DISPLAY_NAMES[service_id]
    return service_id.replace("_", " ").title()


def _compose_row_service_name(row: dict[str, Any]) -> str:
    for key in ("Service", "service", "Name", "name"):
        val = row.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _sanitize_runtime_detail(row: dict[str, Any]) -> dict[str, str | int | None]:
    out: dict[str, str | int | None] = {}
    state = row.get("State") or row.get("state")
    if state is not None:
        out["compose_state"] = str(state)
    health = row.get("Health") or row.get("health")
    if health is not None:
        out["health"] = str(health)
    rc = (
        row.get("RestartCount") if row.get("RestartCount") is not None else row.get("restart_count")
    )
    if rc is not None:
        try:
            out["restart_count"] = int(rc)
        except (TypeError, ValueError):
            pass
    return out


def _compose_only_state(row: dict[str, Any]) -> ServiceState:
    state_raw = str(row.get("State") or row.get("state") or "").lower()
    health_raw = str(row.get("Health") or row.get("health") or "").lower()
    if state_raw in ("exited", "dead", "removing"):
        return "down"
    if state_raw in ("running", "up"):
        if health_raw == "healthy":
            return "healthy"
        if health_raw in ("", "starting", "unhealthy"):
            return "degraded"
        return "degraded"
    if state_raw in ("starting", "restarting", "paused"):
        return "degraded"
    return "unknown"


def _merge_compose_and_ping(
    compose_row: dict[str, Any] | None,
    ping: AdminDiagnosticsStoreItem | None,
) -> tuple[ServiceState, dict[str, str | int | None], str | None]:
    runtime_detail = _sanitize_runtime_detail(compose_row) if compose_row else {}
    compose_state = _compose_only_state(compose_row) if compose_row else None
    ping_status = ping.status if ping else None

    if ping_status == "not_configured":
        return "not_configured", runtime_detail, ping.message if ping else None

    if compose_row is None:
        if ping_status in ("unreachable", "unknown"):
            return (
                "down" if ping_status == "unreachable" else "unknown",
                runtime_detail,
                ("Store unreachable and compose status unavailable."),
            )
        if ping_status == "ok":
            return "degraded", runtime_detail, "Reachable via ping; compose status unavailable."
        return "unknown", runtime_detail, None

    if compose_state == "down":
        return "down", runtime_detail, None

    if ping_status == "unreachable":
        if compose_state in ("healthy", "degraded"):
            return "degraded", runtime_detail, "Compose reports running but store ping failed."
        return "down", runtime_detail, None

    if ping_status == "unknown":
        return "unknown", runtime_detail, None

    if ping_status == "ok":
        if compose_state == "healthy":
            return "healthy", runtime_detail, None
        if compose_state == "degraded":
            return "degraded", runtime_detail, None
        return "degraded", runtime_detail, "Running without health signal."

    if compose_state == "healthy":
        return "healthy", runtime_detail, None
    if compose_state == "degraded":
        return "degraded", runtime_detail, None
    return compose_state or "unknown", runtime_detail, None


def _restart_secret() -> str:
    from routes.admin import _current_restart_secret

    return _current_restart_secret()


def _fetch_stack_control_status_unlocked() -> tuple[dict[str, Any] | None, bool]:
    """Call stack-control ``GET /status``. Returns (payload, reachable)."""
    secret = _restart_secret()
    if not secret:
        _log.error("stack_status: RESTART_SECRET missing — stack-control call skipped")
        return None, False

    url = f"{_STACK_CONTROL_URL.rstrip('/')}/status"
    try:
        with httpx.Client(timeout=_HTTP_TIMEOUT_SEC) as client:
            resp = client.get(
                url,
                headers={"X-Lumogis-Restart-Token": secret},
            )
    except Exception as exc:
        _log.warning("stack_status: stack-control unreachable (%s)", exc.__class__.__name__)
        return None, False

    if resp.status_code != 200:
        _log.warning(
            "stack_status: stack-control returned %s",
            resp.status_code,
        )
        return None, False

    try:
        return resp.json(), True
    except Exception:
        _log.warning("stack_status: stack-control body not JSON")
        return None, False


def _get_stack_control_payload() -> tuple[dict[str, Any] | None, bool, int | None]:
    """TTL cache + single-flight for stack-control fetches."""
    global _cache_payload, _cache_at, _fetch_in_progress

    now = time.monotonic()
    with _cache_lock:
        if _cache_payload is not None and _cache_at is not None:
            age = int(now - _cache_at)
            if age < _CACHE_TTL_SEC:
                return _cache_payload, True, age
        if _fetch_in_progress and _cache_payload is not None:
            age = int(now - _cache_at) if _cache_at is not None else None
            return _cache_payload, bool(_cache_payload), age

        _fetch_in_progress = True

    payload, reachable = _fetch_stack_control_status_unlocked()

    with _cache_lock:
        if reachable and payload is not None:
            _cache_payload = payload
            _cache_at = time.monotonic()
        _fetch_in_progress = False
        age = int(time.monotonic() - _cache_at) if _cache_at is not None else None

    if payload is not None:
        return payload, reachable, age
    return _cache_payload, False, age


def _index_compose_rows(compose_ps: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in compose_ps:
        raw_name = _compose_row_service_name(row)
        if not raw_name:
            continue
        sid = _normalize_compose_service_name(raw_name)
        out[sid] = row
    return out


def _build_store_pings() -> dict[str, AdminDiagnosticsStoreItem]:
    vs = config.get_vector_store()
    meta = config.get_metadata_store()
    return {
        "postgres": _store_row("postgres", meta.ping),
        "qdrant": _store_row("qdrant", vs.ping),
        "graph": _graph_store_row(),
    }


def _build_services(
    compose_by_id: dict[str, dict[str, Any]],
    pings: dict[str, AdminDiagnosticsStoreItem],
) -> list[StackStatusServiceItem]:
    service_ids: set[str] = set(_KNOWN_COMPOSE_IDS) | set(_PING_BY_SERVICE_ID.keys())
    service_ids.update(compose_by_id.keys())

    rows: list[StackStatusServiceItem] = []
    for sid in sorted(service_ids):
        compose_row = compose_by_id.get(sid)
        ping_name = _PING_BY_SERVICE_ID.get(sid)
        ping = pings.get(ping_name) if ping_name else None
        state, runtime_detail, message = _merge_compose_and_ping(compose_row, ping)
        rows.append(
            StackStatusServiceItem(
                id=sid,
                display_name=_display_name(sid),
                state=state,
                runtime_kind="docker_compose",
                runtime_detail=runtime_detail,
                message=message,
            )
        )
    return rows


def _storage_status(used_percent: float | None) -> Literal["ok", "warn", "critical", "unknown"]:
    if used_percent is None:
        return "unknown"
    if used_percent >= _CRITICAL_PERCENT:
        return "critical"
    if used_percent >= _WARN_PERCENT:
        return "warn"
    return "ok"


def _statvfs_row(path: str, mount_id: str, path_label: str) -> StackStatusStorageItem:
    try:
        st_vfs = os.statvfs(path)
        st = os.stat(path)
        total = int(st_vfs.f_frsize * st_vfs.f_blocks)
        free = int(st_vfs.f_frsize * st_vfs.f_bavail)
        used = max(0, total - free)
        pct = (used / total * 100.0) if total > 0 else None
        partition_id = str(st.st_dev)
        return StackStatusStorageItem(
            mount_id=mount_id,
            path_label=path_label,
            partition_id=partition_id,
            used_bytes=used,
            total_bytes=total,
            used_percent=round(pct, 2) if pct is not None else None,
            warn_threshold_percent=_WARN_PERCENT,
            status=_storage_status(pct),
        )
    except OSError:
        return StackStatusStorageItem(
            mount_id=mount_id,
            path_label=path_label,
            status="unknown",
            warn_threshold_percent=_WARN_PERCENT,
        )


def _local_storage_rows(system_df: object | None) -> list[StackStatusStorageItem]:
    rows: list[StackStatusStorageItem] = []
    seen_partitions: set[str] = set()

    for path, mount_id, label in (
        ("/data", "host_data", "Host partition free space (bind mount `/data`)"),
        ("/", "host_root", "Host root partition"),
    ):
        row = _statvfs_row(path, mount_id, label)
        if row.partition_id:
            if row.partition_id in seen_partitions:
                continue
            seen_partitions.add(row.partition_id)
        rows.append(row)

    for extra in _env_extra_mounts():
        row = _statvfs_row(extra, f"extra_{extra.replace('/', '_')}", f"Extra mount {extra}")
        if row.partition_id and row.partition_id in seen_partitions:
            continue
        if row.partition_id:
            seen_partitions.add(row.partition_id)
        rows.append(row)

    if isinstance(system_df, list) and system_df:
        rows.append(
            StackStatusStorageItem(
                mount_id="docker_breakdown",
                path_label="Docker images/volumes (informational)",
                status="ok",
                warn_threshold_percent=_WARN_PERCENT,
            )
        )
    return rows


def _ollama_read_only_block() -> tuple[list[StackStatusOllamaModel], list[AdminDiagnosticsWarning]]:
    warnings: list[AdminDiagnosticsWarning] = []
    try:
        local = ollama_client.list_local_models()
    except Exception:
        local = []
    if not local:
        warnings.append(
            AdminDiagnosticsWarning(
                code="ollama_unreachable",
                message="Ollama model list is empty or Ollama is unreachable.",
            )
        )
        return [], warnings

    loaded_names: set[str] = set()
    ollama_url = os.environ.get("OLLAMA_URL", "http://ollama:11434").rstrip("/")
    try:
        r = httpx.get(f"{ollama_url}/api/ps", timeout=3.0)
        if r.is_success:
            for entry in r.json().get("models", []) or []:
                name = entry.get("name") if isinstance(entry, dict) else None
                if isinstance(name, str):
                    loaded_names.add(name)
    except Exception:
        pass

    models: list[StackStatusOllamaModel] = []
    for m in local:
        name = str(m.get("name", ""))
        if not name:
            continue
        size = m.get("size")
        modified = m.get("modified_at")
        modified_dt: datetime | None = None
        if isinstance(modified, datetime):
            modified_dt = modified
        elif isinstance(modified, str) and modified.strip():
            try:
                modified_dt = datetime.fromisoformat(modified.replace("Z", "+00:00"))
            except ValueError:
                modified_dt = None
        loaded: bool | None = name in loaded_names if loaded_names else None
        models.append(
            StackStatusOllamaModel(
                name=name,
                size_bytes=int(size) if size is not None else None,
                modified_at=modified_dt,
                loaded=loaded,
            )
        )
    return models, warnings


def _compute_overall(
    services: list[StackStatusServiceItem],
    storage: list[StackStatusStorageItem],
) -> Literal["ok", "degraded", "down"]:
    by_id = {s.id: s for s in services}
    for critical_id in ("postgres", "orchestrator"):
        svc = by_id.get(critical_id)
        if svc and svc.state == "down":
            return "down"

    for req in _REQUIRED_FOR_OVERALL:
        svc = by_id.get(req)
        if svc and svc.state != "healthy":
            if svc.state == "down":
                return "down"
            return "degraded"

    if any(s.status == "critical" for s in storage):
        return "degraded"

    if any(
        s.state not in ("healthy", "not_configured") for s in services if s.id in _KNOWN_COMPOSE_IDS
    ):
        return "degraded"

    return "ok"


def build_stack_status_response() -> StackStatusResponse:
    """Assemble the wire DTO for ``GET /api/v1/admin/diagnostics/stack-status``."""
    generated_at = datetime.now(timezone.utc)
    warnings: list[AdminDiagnosticsWarning] = []

    payload, stack_control_reachable, cache_age_sec = _get_stack_control_payload()

    compose_ps: list[dict[str, Any]] = []
    system_df: object | None = None
    if payload:
        raw_ps = payload.get("compose_ps")
        if isinstance(raw_ps, list):
            compose_ps = [r for r in raw_ps if isinstance(r, dict)]
        system_df = payload.get("system_df")
        if payload.get("system_df_busy"):
            warnings.append(
                AdminDiagnosticsWarning(
                    code="stack_control_system_df_busy",
                    message="Docker disk usage refresh already in progress; showing last snapshot.",
                )
            )
        if payload.get("system_df_error"):
            warnings.append(
                AdminDiagnosticsWarning(
                    code="stack_control_system_df_error",
                    message="Docker disk usage breakdown unavailable on this host.",
                )
            )
    else:
        warnings.append(
            AdminDiagnosticsWarning(
                code="stack_control_unreachable",
                message="Stack-control sidecar unreachable; service list may be incomplete.",
            )
        )

    if not _restart_secret():
        warnings.append(
            AdminDiagnosticsWarning(
                code="restart_secret_missing",
                message="RESTART_SECRET is not configured; stack-control status was not fetched.",
            )
        )

    compose_by_id = _index_compose_rows(compose_ps)
    pings = _build_store_pings()
    services = _build_services(compose_by_id, pings)
    storage = _local_storage_rows(system_df)
    ollama, ollama_warnings = _ollama_read_only_block()
    warnings.extend(ollama_warnings)

    overall = _compute_overall(services, storage)

    return StackStatusResponse(
        meta=StackStatusMeta(
            generated_at=generated_at,
            cache_age_sec=cache_age_sec,
            stack_control_reachable=stack_control_reachable,
            overall_status=overall,
        ),
        services=services,
        storage=storage,
        ollama=ollama,
        warnings=warnings,
    )
