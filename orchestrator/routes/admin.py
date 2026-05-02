# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Admin endpoints: health, dashboard, permissions, review-queue, backup, restore, export."""

import datetime
import json
import logging
import os
import re
import uuid
import zipfile
from datetime import timezone
from pathlib import Path

from auth import UserContext, auth_enabled, get_user
from authz import require_admin, require_user
from csrf import require_same_origin
from fastapi import APIRouter
from fastapi import BackgroundTasks
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Query
from fastapi import Request
from fastapi import Response
from fastapi import status
from fastapi.responses import FileResponse
from fastapi.responses import JSONResponse
from fastapi.responses import StreamingResponse
from permissions import get_all_permissions
from permissions import set_connector_mode
from pydantic import BaseModel

import config
from settings_store import get_setting
from settings_store import put_settings

_DASHBOARD_HTML = Path(__file__).parent.parent / "dashboard" / "index.html"
_GRAPH_MGM_HTML  = Path(__file__).parent.parent / "static" / "graph_mgm.html"
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
# edge_scores is intentionally excluded — recomputable from entity_relations by the weekly job.
_BACKUP_TABLES = [
    "file_index",
    "entities",
    "entity_relations",
    "sessions",
    "review_queue",
    "known_distinct_entity_pairs",
    "review_decisions",
    "connector_permissions",
    "routine_do_tracking",
    "action_log",
    "deduplication_runs",
    "dedup_candidates",
    "kg_settings",
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
# KG settings metadata (used by GET /kg/settings and POST /kg/settings)
# ---------------------------------------------------------------------------

# Maps each configurable key to its type, default value, and description.
# This is the canonical definition of all hot-reload settings.
_SETTING_META: dict[str, dict] = {
    "entity_quality_lower": {
        "type": "float",
        "default": 0.35,
        "description": (
            "Entities scoring below this threshold are discarded immediately. "
            "Lower = keep more entities (more noise). Higher = discard more (may miss real entities). "
            "Default: 0.35."
        ),
    },
    "entity_quality_upper": {
        "type": "float",
        "default": 0.60,
        "description": (
            "Entities scoring between lower and upper thresholds are staged for review. "
            "Above upper = added to graph immediately. Default: 0.60."
        ),
    },
    "entity_promote_on_mention_count": {
        "type": "int",
        "default": 3,
        "description": (
            "A staged entity is automatically promoted to the graph when it reaches this many mentions. "
            "Default: 3."
        ),
    },
    "graph_edge_quality_threshold": {
        "type": "float",
        "default": 0.3,
        "description": (
            "Edges with a quality score below this are hidden from queries and the visualization. "
            "Lower = more edges visible (more noise). Default: 0.3."
        ),
    },
    "graph_cooccurrence_threshold": {
        "type": "int",
        "default": 3,
        "description": (
            "Minimum number of co-occurrences required for a RELATES_TO edge to be visible. "
            "Default: 3."
        ),
    },
    "graph_min_mention_count": {
        "type": "int",
        "default": 2,
        "description": (
            "Entities mentioned fewer times than this are hidden from graph queries and context injection. "
            "Default: 2."
        ),
    },
    "graph_max_cooccurrence_pairs": {
        "type": "int",
        "default": 100,
        "description": (
            "Maximum co-occurrence edge writes per ingestion event. "
            "Prevents slow ingestion on very dense documents. Default: 100."
        ),
    },
    "graph_viz_max_nodes": {
        "type": "int",
        "default": 150,
        "description": "Maximum nodes returned by the visualization API. Default: 150.",
    },
    "graph_viz_max_edges": {
        "type": "int",
        "default": 300,
        "description": "Maximum edges returned by the visualization API. Default: 300.",
    },
    "decay_half_life_relates_to": {
        "type": "int",
        "default": 365,
        "description": (
            "Days until a RELATES_TO edge weight halves. "
            "Longer = relationships stay relevant longer. Default: 365."
        ),
    },
    "decay_half_life_mentions": {
        "type": "int",
        "default": 180,
        "description": "Days until a MENTIONS edge weight halves. Default: 180.",
    },
    "decay_half_life_discussed_in": {
        "type": "int",
        "default": 30,
        "description": "Days until a DISCUSSED_IN edge weight halves. Default: 30.",
    },
    "dedup_cron_hour_utc": {
        "type": "int",
        "default": 2,
        "description": (
            "Hour (UTC) when the weekly deduplication job runs on Sundays. Default: 2."
        ),
    },
}

# Range validation rules per key: (min_inclusive, max_inclusive) or None for no range check.
_SETTING_RANGES: dict[str, tuple] = {
    "entity_quality_lower":          (0.0, 1.0),
    "entity_quality_upper":          (0.0, 1.0),
    "graph_edge_quality_threshold":  (0.0, 1.0),
    "entity_promote_on_mention_count": (1, None),
    "graph_cooccurrence_threshold":  (1, None),
    "graph_min_mention_count":       (1, None),
    "graph_max_cooccurrence_pairs":  (1, None),
    "graph_viz_max_nodes":           (1, None),
    "graph_viz_max_edges":           (1, None),
    "decay_half_life_relates_to":    (1, None),
    "decay_half_life_mentions":      (1, None),
    "decay_half_life_discussed_in":  (1, None),
    "dedup_cron_hour_utc":           (0, 23),
}

_KNOWN_SETTING_KEYS = frozenset(_SETTING_META.keys())


def _cast_setting_value(key: str, raw: str):
    """Parse and validate a raw string value for a setting key.

    Returns the typed Python value on success.
    Raises HTTPException(400) on type mismatch or out-of-range value.
    """
    meta = _SETTING_META[key]
    dtype = meta["type"]
    try:
        if dtype == "float":
            value = float(raw)
        elif dtype == "int":
            value = int(raw)
        elif dtype == "bool":
            if raw.strip().lower() in ("true", "1", "yes", "on"):
                value = True
            elif raw.strip().lower() in ("false", "0", "no", "off"):
                value = False
            else:
                raise ValueError(f"cannot parse bool from {raw!r}")
        else:
            value = raw
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=400,
            detail=f"invalid value for {key!r}: expected {dtype}, got {raw!r} ({exc})",
        ) from exc

    rng = _SETTING_RANGES.get(key)
    if rng is not None:
        lo, hi = rng
        if lo is not None and value < lo:
            raise HTTPException(
                status_code=400,
                detail=f"{key!r} must be >= {lo}, got {value}",
            )
        if hi is not None and value > hi:
            raise HTTPException(
                status_code=400,
                detail=f"{key!r} must be <= {hi}, got {value}",
            )
    return value


def _read_all_kg_settings_from_db() -> dict[str, str]:
    """Return all kg_settings rows as {key: value}.  Returns {} on any error."""
    try:
        meta = config.get_metadata_store()
        rows = meta.fetch_all("SELECT key, value FROM kg_settings")
        return {r["key"]: r["value"] for r in rows}
    except Exception:
        _log.warning("kg/settings GET: failed to read kg_settings table", exc_info=True)
        return {}


# ---------------------------------------------------------------------------
# GET /kg/settings
# ---------------------------------------------------------------------------


@router.get("/kg/settings", dependencies=[Depends(require_admin)])
def kg_settings_get():
    """Return all hot-reload KG settings with current value, type, default, and source.

    source is "database" when the value comes from kg_settings table,
    or "default" when no DB row exists (env var or hardcoded default is used).
    """
    db_rows = _read_all_kg_settings_from_db()

    settings_out = []
    for key, meta in _SETTING_META.items():
        dtype = meta["type"]
        default_raw = meta["default"]
        if key in db_rows:
            raw = db_rows[key]
            source = "database"
            # Cast to native type for the response; fall back to raw string on error
            try:
                if dtype == "float":
                    typed_value = float(raw)
                elif dtype == "int":
                    typed_value = int(raw)
                elif dtype == "bool":
                    typed_value = raw.strip().lower() in ("true", "1", "yes", "on")
                else:
                    typed_value = raw
            except (ValueError, TypeError):
                typed_value = raw
        else:
            typed_value = default_raw
            source = "default"

        settings_out.append({
            "key":         key,
            "value":       typed_value,
            "type":        dtype,
            "default":     default_raw,
            "source":      source,
            "description": meta["description"],
        })

    return {"settings": settings_out}


# ---------------------------------------------------------------------------
# POST /kg/settings
# ---------------------------------------------------------------------------


class KgSettingsUpsertItem(BaseModel):
    key: str
    value: str


class KgSettingsUpsertRequest(BaseModel):
    settings: list[KgSettingsUpsertItem]


@router.post("/kg/settings", dependencies=[Depends(require_admin)])
def kg_settings_post(body: KgSettingsUpsertRequest):
    """Upsert one or more KG settings.  Values take effect on the next getter call (≤30 s)."""
    if not body.settings:
        raise HTTPException(status_code=400, detail="settings list is empty")

    updated_keys: list[str] = []
    for item in body.settings:
        key = item.key.strip()
        if key not in _KNOWN_SETTING_KEYS:
            raise HTTPException(status_code=400, detail=f"unknown key: {key}")
        _cast_setting_value(key, item.value)  # validate type + range; raises 400 on error
        updated_keys.append(key)

    # All validated — write to DB
    meta = config.get_metadata_store()
    try:
        for item in body.settings:
            meta.execute(
                "INSERT INTO kg_settings (key, value) VALUES (%s, %s) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                (item.key.strip(), item.value.strip()),
            )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"DB write failed: {exc}") from exc

    config.invalidate_settings_cache()
    return {"status": "ok", "updated": updated_keys}


# ---------------------------------------------------------------------------
# DELETE /kg/settings/{key}
# ---------------------------------------------------------------------------


@router.delete("/kg/settings/{key}", dependencies=[Depends(require_admin)])
def kg_settings_delete(key: str):
    """Remove a setting from the DB, reverting it to the env var / hardcoded default."""
    if key not in _KNOWN_SETTING_KEYS:
        raise HTTPException(status_code=400, detail=f"unknown key: {key}")

    meta = config.get_metadata_store()
    try:
        meta.execute("DELETE FROM kg_settings WHERE key = %s", (key,))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"DB delete failed: {exc}") from exc

    config.invalidate_settings_cache()
    default_val = _SETTING_META[key]["default"]
    return {"status": "ok", "key": key, "reverted_to": default_val}


# ---------------------------------------------------------------------------
# Graph Management Page
# ---------------------------------------------------------------------------


@router.get("/graph/mgm", dependencies=[Depends(require_admin)])
def graph_mgm():
    """Serve the Knowledge Graph Management Page SPA."""
    if not _GRAPH_MGM_HTML.exists():
        raise HTTPException(
            status_code=404,
            detail="Graph management page not found. Check that orchestrator/static/graph_mgm.html exists.",
        )
    return FileResponse(_GRAPH_MGM_HTML, media_type="text/html")


# ---------------------------------------------------------------------------
# GET /kg/job-status
# ---------------------------------------------------------------------------


@router.get("/kg/job-status", dependencies=[Depends(require_admin)])
def kg_job_status():
    """Return last-run timestamps and status for the three KG background jobs.

    Reads _job_last_reconciliation and _job_last_weekly directly from kg_settings
    via MetadataStore (bypasses the 30s TTL cache).  Derives deduplication status
    from the deduplication_runs table.  Returns graceful nulls on any missing data.
    Never fails the entire endpoint because one field is unavailable.
    """
    meta = config.get_metadata_store()

    # ── reconciliation ──────────────────────────────────────────────────────
    last_reconciliation: str | None = None
    try:
        row = meta.fetch_one(
            "SELECT value FROM kg_settings WHERE key = '_job_last_reconciliation'"
        )
        if row:
            last_reconciliation = row["value"]
    except Exception:
        _log.warning("kg_job_status: failed to read _job_last_reconciliation", exc_info=True)

    # ── weekly quality job ──────────────────────────────────────────────────
    last_weekly: str | None = None
    try:
        row = meta.fetch_one(
            "SELECT value FROM kg_settings WHERE key = '_job_last_weekly'"
        )
        if row:
            last_weekly = row["value"]
    except Exception:
        _log.warning("kg_job_status: failed to read _job_last_weekly", exc_info=True)

    # ── deduplication ───────────────────────────────────────────────────────
    dedup_last_run: str | None = None
    dedup_running: bool = False
    dedup_last_auto_merged: int | None = None
    dedup_last_queued: int | None = None
    dedup_last_candidate_count: int | None = None
    try:
        # Most recent finished run
        run_row = meta.fetch_one(
            "SELECT started_at, finished_at, auto_merged, queued_for_review, candidate_count "
            "FROM deduplication_runs "
            "WHERE user_id = 'default' AND finished_at IS NOT NULL "
            "ORDER BY finished_at DESC LIMIT 1"
        )
        if run_row:
            dedup_last_run = run_row["finished_at"].isoformat() if run_row["finished_at"] else None
            dedup_last_auto_merged = run_row.get("auto_merged")
            dedup_last_queued = run_row.get("queued_for_review")
            dedup_last_candidate_count = run_row.get("candidate_count")

        # Check for in-progress run
        in_progress = meta.fetch_one(
            "SELECT run_id FROM deduplication_runs "
            "WHERE user_id = 'default' AND finished_at IS NULL LIMIT 1"
        )
        dedup_running = in_progress is not None
    except Exception:
        _log.warning("kg_job_status: failed to read deduplication_runs", exc_info=True)

    return {
        "reconciliation": {
            "last_run": last_reconciliation,
        },
        "weekly_quality": {
            "last_run": last_weekly,
        },
        "deduplication": {
            "last_run": dedup_last_run,
            "running": dedup_running,
            "last_auto_merged": dedup_last_auto_merged,
            "last_queued_for_review": dedup_last_queued,
            "last_candidate_count": dedup_last_candidate_count,
        },
    }


# ---------------------------------------------------------------------------
# POST /kg/trigger-weekly
# ---------------------------------------------------------------------------


@router.post("/kg/trigger-weekly", status_code=202, dependencies=[Depends(require_admin)])
def kg_trigger_weekly(background_tasks: BackgroundTasks):
    """Trigger the weekly KG quality maintenance job as a background task.

    Returns 202 immediately.
    Returns 409 if a deduplication job is already running (the weekly job
    includes deduplication and cannot safely run concurrently).
    """
    meta = config.get_metadata_store()

    try:
        in_progress = meta.fetch_one(
            "SELECT run_id FROM deduplication_runs "
            "WHERE user_id = 'default' AND finished_at IS NULL LIMIT 1"
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"DB error: {exc}") from exc

    if in_progress is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                "A deduplication job is already running. The weekly quality job includes "
                "deduplication and cannot run concurrently. Try again when it completes."
            ),
        )

    def _run():
        try:
            from services.edge_quality import run_weekly_quality_job
            run_weekly_quality_job()
        except Exception:
            _log.exception("kg/trigger-weekly: background job failed")

    background_tasks.add_task(_run)
    _log.info("kg/trigger-weekly: weekly quality job triggered via API")
    return {"status": "started", "message": "Weekly KG quality job started in background."}


# ---------------------------------------------------------------------------
# Stop entity list  (GET /kg/stop-entities, POST /kg/stop-entities)
# ---------------------------------------------------------------------------

_STOP_ENTITY_MAX_PHRASE_LEN = 200


class StopEntityRequest(BaseModel):
    action: str   # "add" or "remove"
    phrase: str


def _read_stop_entity_file(path: str) -> list[str]:
    """Return the ordered list of non-blank, non-comment lines from the file.

    Lines are returned with their original case and whitespace stripped.
    Raises OSError if the file exists but cannot be read.
    """
    lines: list[str] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            stripped = line.rstrip("\n").strip()
            if stripped and not stripped.startswith("#"):
                lines.append(stripped)
    return lines


def _write_stop_entity_file_atomic(path: str, phrases: list[str]) -> None:
    """Write phrases to path atomically via a temp file + os.replace().

    The header comment block is preserved.
    """
    import tempfile

    header = (
        "# Lumogis KG Quality — stop entity list\n"
        "# Format: UTF-8, one phrase per line, leading/trailing whitespace stripped.\n"
        "# Lines starting with # are comments and are ignored.\n"
        "# Matching is case-insensitive against the normalised entity name.\n"
        "# Add project-specific noise phrases below to prevent them entering the graph.\n"
        "# Phrases added here are discarded regardless of their extraction_quality score.\n"
    )
    parent = Path(path).parent
    parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(parent), prefix=".stop_entities_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(header)
            for phrase in phrases:
                fh.write(phrase + "\n")
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


@router.get("/kg/stop-entities", dependencies=[Depends(require_admin)])
def kg_stop_entities_get():
    """Return the current stop entity list.

    Returns 200 with an empty list when the file does not exist.
    Returns 500 when the file exists but cannot be read.
    """
    path = config.get_stop_entities_path()
    phrases: list[str] = []
    try:
        phrases = _read_stop_entity_file(path)
    except FileNotFoundError:
        pass  # file not yet created — return empty list
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Stop entity file exists but could not be read: {exc}",
        ) from exc
    return {
        "phrases":     sorted(phrases, key=str.lower),
        "count":       len(phrases),
        "source_path": path,
    }


@router.post("/kg/stop-entities", dependencies=[Depends(require_admin)])
def kg_stop_entities_post(body: StopEntityRequest):
    """Add or remove a phrase from the stop entity list.

    Writes atomically via temp file + os.replace().
    """
    action = (body.action or "").strip().lower()
    if action not in ("add", "remove"):
        raise HTTPException(status_code=400, detail="action must be 'add' or 'remove'")

    phrase = body.phrase.strip() if body.phrase else ""
    if not phrase:
        raise HTTPException(status_code=400, detail="phrase must not be empty")
    if len(phrase) > _STOP_ENTITY_MAX_PHRASE_LEN:
        raise HTTPException(
            status_code=400,
            detail=f"phrase must not exceed {_STOP_ENTITY_MAX_PHRASE_LEN} characters",
        )
    if "\n" in phrase or "\r" in phrase:
        raise HTTPException(status_code=400, detail="phrase must not contain newlines")
    if any(ord(c) < 32 for c in phrase):
        raise HTTPException(status_code=400, detail="phrase must not contain control characters")

    path = config.get_stop_entities_path()

    # Load current phrases (empty if file missing)
    current: list[str] = []
    try:
        current = _read_stop_entity_file(path)
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not read stop entity file: {exc}") from exc

    lower_current = [p.lower() for p in current]

    if action == "add":
        if phrase.lower() in lower_current:
            raise HTTPException(status_code=400, detail="phrase already in list")
        current.append(phrase)
    else:  # remove
        if phrase.lower() not in lower_current:
            raise HTTPException(status_code=400, detail="phrase not found")
        current = [p for p in current if p.lower() != phrase.lower()]

    try:
        _write_stop_entity_file_atomic(path, current)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not write stop entity file: {exc}") from exc

    config.invalidate_settings_cache()
    return {"status": "ok", "count": len(current)}


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------


_LEGACY_PERMISSIONS_DEPRECATION_HEADERS = {
    "Deprecation": "true",
    # The legacy alias has *no* per-connector path equivalent on the
    # successor — the new admin enumeration is /api/v1/admin/permissions
    # (cross-user) and the per-connector reads/writes live under
    # /api/v1/me/permissions/{connector} (caller's own) and
    # /api/v1/admin/users/{user_id}/permissions/{connector} (admin
    # on-behalf-of). The Link header points at the cross-user
    # successor that this GET is closest in shape to.
    "Link": '</api/v1/admin/permissions>; rel="successor-version"',
}


@router.get("/permissions", dependencies=[Depends(require_admin)])
def list_permissions(response: Response):
    """Legacy alias — kept for one release.

    Reframed (since plan ``per_user_connector_permissions``) as a
    cross-user enumeration. Replaced by
    ``GET /api/v1/admin/permissions``; slated for ``410 Gone`` in
    release N+1. Adds the ``Deprecation`` and ``Link`` advisory
    headers so the SPA + curl callers get visible migration breadcrumbs.
    """
    for k, v in _LEGACY_PERMISSIONS_DEPRECATION_HEADERS.items():
        response.headers[k] = v
    _log.warning("legacy_get_permissions_used")
    return get_all_permissions()


@router.put("/permissions/{connector}", dependencies=[Depends(require_admin)])
def update_permission(
    connector: str,
    body: PermissionUpdate,
    request: Request,
    response: Response,
):
    """Legacy alias — kept for one release.

    Per plan ``per_user_connector_permissions``, this writes the
    *calling admin's own* per-user row (the surface predates the
    per-user lift; collapsing it onto the caller is the least-surprising
    behaviour for existing scripts that still target this path). Slated
    for ``410 Gone`` in release N+1. Successor: ``PUT
    /api/v1/me/permissions/{connector}``. Lenient-case ``mode``
    (``do``/``ASK``/``Ask``) is preserved on this surface; the v1
    surface locks canonical uppercase only.
    """
    caller = get_user(request)
    response.headers["Deprecation"] = "true"
    response.headers["Link"] = (
        f'</api/v1/me/permissions/{connector}>; rel="successor-version"'
    )
    try:
        set_connector_mode(
            user_id=caller.user_id,
            connector=connector,
            mode=body.mode.upper(),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    _log.warning(
        "legacy_put_permissions_used user_id=%s connector=%s",
        caller.user_id, connector,
    )
    return {"connector": connector, "mode": body.mode.upper()}


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


@router.get("/dashboard", dependencies=[Depends(require_admin)])
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
    """is_model_enabled with fallback to False on any unexpected error.

    Plan llm_provider_keys_per_user_migration Pass 3.11: explicitly passes
    ``user_id=None`` so the auth-on path returns False for cloud models —
    the household ``models[].enabled`` field becomes "household toggle on"
    only, since "is this model enabled for the household as a whole" no
    longer has a per-user-free answer when keys are user-scoped.
    """
    try:
        return config.is_model_enabled(name, user_id=None)
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

    # Plan llm_provider_keys_per_user_migration Pass 3.11: under
    # ``AUTH_ENABLED=true`` the household-wide "is this key set" view is
    # intentionally dropped (per the user instruction "drop that field
    # entirely in auth-on mode rather than repurpose it into cross-user
    # aggregate state"). Each user manages their own LLM keys via
    # ``/api/v1/me/connector-credentials/llm_<vendor>``; the dashboard
    # surfaces those in a per-user "My LLM keys" panel and an admin-side
    # "Connector credentials" panel under Users. Auth-off keeps the legacy
    # global status field so single-user installs are unchanged.
    api_key_status: dict[str, str] | None
    if auth_enabled():
        api_key_status = None
    else:
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

    response: dict = {
        "filesystem_root": effective_root,
        "pending_filesystem_root": pending_root,
        "models": models,
        "default_model": default_model,
        "optional_models": optional_models,
        "pending_prune": _safe_get_setting("pending_prune", store) == "true",
        "reranker_enabled": reranker_enabled,
    }
    if api_key_status is not None:
        response["api_key_status"] = api_key_status
    return response


@router.get("/settings", dependencies=[Depends(require_admin)])
def get_settings():
    """Return current settings for the dashboard (root path, API key status, models)."""
    return _get_settings_response()


@router.put("/settings", dependencies=[Depends(require_admin)])
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
    # Plan llm_provider_keys_per_user_migration Pass 3.11: legacy global
    # LLM key writes are disabled under ``AUTH_ENABLED=true`` — each user
    # writes their own key via ``/api/v1/me/connector-credentials/llm_<vendor>``
    # (admins may write on behalf at the matching admin route). The legacy
    # ``app_settings`` rows still resolve under auth-off so single-user
    # installs are unchanged. An empty ``api_keys: {}`` body is a no-op
    # under both modes (no writes attempted, no 422).
    if body.api_keys is not None and body.api_keys:
        if auth_enabled():
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "legacy_global_api_keys_disabled",
                    "message": (
                        "Global LLM API keys are disabled when AUTH_ENABLED=true. "
                        "Each user manages their own keys at "
                        "/api/v1/me/connector-credentials/llm_<vendor>; admins may "
                        "write on behalf at "
                        "/api/v1/admin/users/{user_id}/connector-credentials/llm_<vendor>. "
                        "See docs/connect-and-verify.md."
                    ),
                },
            )
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


@router.post("/settings/restart", dependencies=[Depends(require_admin)])
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


@router.get("/settings/root-preview", dependencies=[Depends(require_admin)])
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


@router.post("/settings/prune", dependencies=[Depends(require_admin)])
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


@router.get("/settings/ollama-discovery", dependencies=[Depends(require_admin)])
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


@router.post("/settings/ollama-pull", dependencies=[Depends(require_admin)])
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


@router.post("/browse/mkdir", dependencies=[Depends(require_admin)])
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


@router.post("/settings/ollama-delete", dependencies=[Depends(require_admin)])
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
# Graph Health
# ---------------------------------------------------------------------------


@router.get("/graph/health")
def graph_health():
    """Six KG quality metrics sourced exclusively from Postgres.

    Never queries FalkorDB — metrics are always available even when the graph
    store is down.  Returns 503 if Postgres itself is unreachable (consistent
    with the /health endpoint behaviour).

    Individual metric failures return 0/null for that field only; they do not
    fail the entire response.
    """
    from fastapi.responses import JSONResponse

    meta = config.get_metadata_store()
    if not meta.ping():
        return JSONResponse(
            content={"error": "Postgres unreachable"},
            status_code=503,
        )

    user_id = "default"

    # ── duplicate_candidate_count ────────────────────────────────────────────
    duplicate_candidate_count = 0
    try:
        # ADMIN-BYPASS: /admin/graph/health is an admin god-mode KG quality
        # dashboard (plan §2.8); reads global review_queue counts, not a
        # user-facing memory surface.
        row = meta.fetch_one(
            "SELECT COUNT(*) AS cnt FROM review_queue WHERE user_id = %s",
            (user_id,),
        )
        duplicate_candidate_count = int(row["cnt"]) if row else 0
    except Exception:
        _log.warning("graph_health: duplicate_candidate_count query failed", exc_info=True)

    # ── orphan_entity_pct ────────────────────────────────────────────────────
    orphan_entity_pct = 0.0
    try:
        # ADMIN-BYPASS: /admin/graph/health KG quality metric (plan §2.8).
        row = meta.fetch_one(
            "SELECT "
            "  COUNT(*) FILTER (WHERE entity_id NOT IN ("
            "    SELECT DISTINCT source_id FROM entity_relations"
            "  )) * 100.0 / NULLIF(COUNT(*), 0) AS pct "
            "FROM entities "
            # ADMIN-BYPASS: see function-level tag — admin KG quality metric.
            "WHERE user_id = %s AND is_staged = FALSE",
            (user_id,),
        )
        if row and row.get("pct") is not None:
            orphan_entity_pct = round(float(row["pct"]), 2)
    except Exception:
        _log.warning("graph_health: orphan_entity_pct query failed", exc_info=True)

    # ── mean_entity_completeness ─────────────────────────────────────────────
    mean_entity_completeness = 0.0
    try:
        # ADMIN-BYPASS: /admin/graph/health KG quality metric (plan §2.8).
        row = meta.fetch_one(
            "SELECT AVG(extraction_quality) AS mean_quality "
            "FROM entities "
            "WHERE user_id = %s AND is_staged = FALSE AND extraction_quality IS NOT NULL",
            (user_id,),
        )
        if row and row.get("mean_quality") is not None:
            mean_entity_completeness = round(float(row["mean_quality"]), 4)
    except Exception:
        _log.warning("graph_health: mean_entity_completeness query failed", exc_info=True)

    # ── constraint_violation_counts ──────────────────────────────────────────
    constraint_violation_counts: dict[str, int] = {"CRITICAL": 0, "WARNING": 0, "INFO": 0}
    try:
        # ADMIN-BYPASS: /admin/graph/health KG quality metric (plan §2.8);
        # `constraint_violations` is in plan §2.10's excluded-from-scope list.
        rows = meta.fetch_all(
            "SELECT severity, COUNT(*) AS cnt "
            "FROM constraint_violations "
            "WHERE user_id = %s AND resolved_at IS NULL "
            "GROUP BY severity",
            (user_id,),
        )
        for row in rows:
            sev = row.get("severity", "")
            if sev in constraint_violation_counts:
                constraint_violation_counts[sev] = int(row["cnt"])
    except Exception:
        _log.warning("graph_health: constraint_violation_counts query failed", exc_info=True)

    # ── ingestion_quality_trend_7d ───────────────────────────────────────────
    ingestion_quality_trend_7d: float | None = None
    try:
        # ADMIN-BYPASS: /admin/graph/health KG quality metric (plan §2.8).
        row = meta.fetch_one(
            "SELECT AVG(extraction_quality) AS trend "
            "FROM entities "
            "WHERE user_id = %s "
            "  AND created_at >= NOW() - INTERVAL '7 days' "
            "  AND extraction_quality IS NOT NULL",
            (user_id,),
        )
        if row and row.get("trend") is not None:
            ingestion_quality_trend_7d = round(float(row["trend"]), 4)
    except Exception:
        _log.warning("graph_health: ingestion_quality_trend_7d query failed", exc_info=True)

    # ── temporal_freshness ───────────────────────────────────────────────────
    temporal_freshness: dict[str, int] = {"last_7d": 0, "8_30d": 0, "31_90d": 0, "90d_plus": 0}
    try:
        # ADMIN-BYPASS: /admin/graph/health KG quality metric (plan §2.8).
        row = meta.fetch_one(
            "SELECT "
            "  COUNT(*) FILTER (WHERE updated_at >= NOW() - INTERVAL '7 days') AS last_7d, "
            "  COUNT(*) FILTER (WHERE updated_at < NOW() - INTERVAL '7 days' "
            "                     AND updated_at >= NOW() - INTERVAL '30 days') AS d8_30, "
            "  COUNT(*) FILTER (WHERE updated_at < NOW() - INTERVAL '30 days' "
            "                     AND updated_at >= NOW() - INTERVAL '90 days') AS d31_90, "
            "  COUNT(*) FILTER (WHERE updated_at < NOW() - INTERVAL '90 days') AS d90_plus "
            "FROM entities "
            # ADMIN-BYPASS: see function-level tag — admin KG quality metric.
            "WHERE user_id = %s AND is_staged = FALSE",
            (user_id,),
        )
        if row:
            temporal_freshness = {
                "last_7d":  int(row.get("last_7d") or 0),
                "8_30d":    int(row.get("d8_30") or 0),
                "31_90d":   int(row.get("d31_90") or 0),
                "90d_plus": int(row.get("d90_plus") or 0),
            }
    except Exception:
        _log.warning("graph_health: temporal_freshness query failed", exc_info=True)

    return {
        "duplicate_candidate_count": duplicate_candidate_count,
        "orphan_entity_pct": orphan_entity_pct,
        "mean_entity_completeness": mean_entity_completeness,
        "constraint_violation_counts": constraint_violation_counts,
        "ingestion_quality_trend_7d": ingestion_quality_trend_7d,
        "temporal_freshness": temporal_freshness,
    }


# ---------------------------------------------------------------------------
# Review queue
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Unified review queue  (GET /review-queue and GET /review-queue?source=all)
# ---------------------------------------------------------------------------

_REVIEW_QUEUE_ITEM_TYPES = {"ambiguous_entity", "staged_entity", "constraint_violation", "orphan_entity"}
_REVIEW_QUEUE_ACTIONS = {"merge", "distinct", "promote", "discard", "suppress", "dismiss"}
_REVIEW_QUEUE_MAX_LIMIT = 100
_REVIEW_QUEUE_DEFAULT_LIMIT = 20

_ACTION_TABLE: dict[str, set] = {
    "ambiguous_entity":    {"merge", "distinct"},
    "staged_entity":       {"promote", "discard"},
    "constraint_violation": {"suppress"},
    "orphan_entity":       {"dismiss"},
}


def _fetch_unified_queue(meta, admin_user_id: str, limit: int) -> list[dict]:
    """Fetch items from all four queue sources and return sorted by priority DESC.

    **Admin god-mode surface (plan §2.8 + §2.16).** Reads across every
    user — the unified review queue is one of the four enumerated
    cross-user admin/audit/review surfaces. ``admin_user_id`` is captured
    for log attribution but **not** used as a SQL filter. Every query
    below is tagged ``# ADMIN-BYPASS:`` so the bypass surface is grep-able
    per acceptance criterion #4.

    Each returned item carries a ``scope`` field (sourced from the
    underlying row) so the admin UI can badge personal vs shared/system
    rows in the unified queue without a second round-trip.
    """
    items: list[dict] = []
    sub_limit = min(limit, _REVIEW_QUEUE_MAX_LIMIT)

    _log.info(
        "component=unified_queue admin_user_id=%s limit=%d", admin_user_id, sub_limit,
    )

    # 1. ambiguous_entity — from review_queue (priority 1.0)
    try:
        # ADMIN-BYPASS: cross-user review queue (plan §2.8); admin_user_id captured for audit only.
        rows = meta.fetch_all(
            "SELECT rq.id, rq.reason, rq.created_at, rq.user_id AS rq_user_id, rq.scope, "
            "  a.entity_id AS eid_a, a.name AS name_a, a.entity_type AS type_a, a.scope AS scope_a, "
            "  b.entity_id AS eid_b, b.name AS name_b, b.entity_type AS type_b, b.scope AS scope_b "
            "FROM review_queue rq "
            "JOIN entities a ON rq.candidate_a_id = a.entity_id "
            "JOIN entities b ON rq.candidate_b_id = b.entity_id "
            "ORDER BY rq.created_at ASC "
            "LIMIT %s",
            (sub_limit,),
        )
        for r in rows:
            items.append({
                "item_type": "ambiguous_entity",
                "item_id":   str(r["id"]),
                "priority":  1.0,
                "_sort_ts":  r["created_at"],
                "user_id":   r.get("rq_user_id") or "",
                "scope":     r.get("scope") or "personal",
                "candidate_a": {
                    "entity_id":   str(r["eid_a"]),
                    "name":        r["name_a"],
                    "entity_type": r["type_a"],
                    "scope":       r.get("scope_a") or "personal",
                },
                "candidate_b": {
                    "entity_id":   str(r["eid_b"]),
                    "name":        r["name_b"],
                    "entity_type": r["type_b"],
                    "scope":       r.get("scope_b") or "personal",
                },
                "reason": r["reason"],
            })
    except Exception as exc:
        _log.warning("unified_queue: ambiguous_entity fetch failed — %s", exc)

    # 2. constraint_violation — CRITICAL only (priority 0.9)
    try:
        # ADMIN-BYPASS: cross-user constraint violations (plan §2.8).
        rows = meta.fetch_all(
            "SELECT cv.violation_id, cv.rule_name, cv.severity, cv.detail, "
            "  cv.entity_id, cv.detected_at, cv.user_id AS cv_user_id "
            "FROM constraint_violations cv "
            "WHERE cv.resolved_at IS NULL AND cv.severity = 'CRITICAL' "
            "  AND cv.rule_name != 'orphan_entity' "
            "ORDER BY cv.detected_at ASC "
            "LIMIT %s",
            (sub_limit,),
        )
        for r in rows:
            items.append({
                "item_type": "constraint_violation",
                "item_id":   str(r["violation_id"]),
                "priority":  0.9,
                "_sort_ts":  r["detected_at"],
                "user_id":   r.get("cv_user_id") or "",
                "violation": {
                    "rule_name": r["rule_name"],
                    "severity":  r["severity"],
                    "detail":    r.get("detail") or "",
                    "entity_id": str(r["entity_id"]) if r.get("entity_id") else "",
                },
            })
    except Exception as exc:
        _log.warning("unified_queue: constraint_violation fetch failed — %s", exc)

    # 3. staged_entity (priority 0.7)
    try:
        # ADMIN-BYPASS: cross-user staged entities (plan §2.8).
        rows = meta.fetch_all(
            "SELECT entity_id, name, entity_type, extraction_quality, mention_count, "
            "  created_at, user_id, scope "
            "FROM entities "
            "WHERE is_staged = TRUE "
            "ORDER BY created_at ASC "
            "LIMIT %s",
            (sub_limit,),
        )
        for r in rows:
            items.append({
                "item_type": "staged_entity",
                "item_id":   str(r["entity_id"]),
                "priority":  0.7,
                "_sort_ts":  r.get("created_at"),
                "user_id":   r.get("user_id") or "",
                "scope":     r.get("scope") or "personal",
                "entity": {
                    "entity_id":          str(r["entity_id"]),
                    "name":               r["name"],
                    "entity_type":        r["entity_type"],
                    "extraction_quality": float(r["extraction_quality"]) if r.get("extraction_quality") is not None else 0.0,
                    "mention_count":      r.get("mention_count") or 0,
                    "scope":              r.get("scope") or "personal",
                },
            })
    except Exception as exc:
        _log.warning("unified_queue: staged_entity fetch failed — %s", exc)

    # 4. orphan_entity — from constraint_violations (priority 0.5)
    try:
        # ADMIN-BYPASS: cross-user orphan-entity violations (plan §2.8).
        rows = meta.fetch_all(
            "SELECT cv.violation_id, cv.entity_id, cv.detected_at, cv.user_id AS cv_user_id, "
            "  e.name, e.entity_type, e.mention_count, e.created_at AS entity_created_at, e.scope "
            "FROM constraint_violations cv "
            "JOIN entities e ON cv.entity_id = e.entity_id "
            "WHERE cv.rule_name = 'orphan_entity' "
            "  AND cv.resolved_at IS NULL "
            "ORDER BY cv.detected_at ASC "
            "LIMIT %s",
            (sub_limit,),
        )
        for r in rows:
            items.append({
                "item_type": "orphan_entity",
                "item_id":   str(r["violation_id"]),
                "priority":  0.5,
                "_sort_ts":  r["detected_at"],
                "user_id":   r.get("cv_user_id") or "",
                "scope":     r.get("scope") or "personal",
                "entity": {
                    "entity_id":   str(r["entity_id"]),
                    "name":        r["name"],
                    "entity_type": r["entity_type"],
                    "mention_count": r.get("mention_count") or 0,
                    "created_at":  r["entity_created_at"].isoformat() if r.get("entity_created_at") else "",
                    "scope":       r.get("scope") or "personal",
                },
            })
    except Exception as exc:
        _log.warning("unified_queue: orphan_entity fetch failed — %s", exc)

    # Sort: priority DESC, then timestamp ASC
    _EPOCH = datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)
    items.sort(key=lambda x: (-x["priority"], x["_sort_ts"] or _EPOCH))
    for item in items:
        item.pop("_sort_ts", None)

    return items[:limit]


@router.get("/review-queue", dependencies=[Depends(require_admin)])
def review_queue_endpoint(
    request: Request,
    source: str | None = Query(default=None),
):
    """Return pending review items (admin-only).

    The unified review queue is one of the four cross-user admin/audit/
    review surfaces enumerated in plan §2.8. Both branches read across
    every household user (admin god-mode bypass); the resolved admin
    user is captured for log attribution only — not used as a SQL
    filter. Per §2.16, the legacy hard-coded ``user_id="default"`` has
    been removed.

    Default (no ``?source`` param): returns ambiguous entity merge
    candidates only, preserving backward compatibility with existing
    callers — but now scoped admin-only and unrestricted by user_id.

    ``?source=all``: returns the unified, prioritised list across all
    queue sources:

      - ``ambiguous_entity`` (priority 1.0) — merge candidates
      - ``constraint_violation`` (priority 0.9) — CRITICAL violations
      - ``staged_entity`` (priority 0.7) — entities awaiting promotion
      - ``orphan_entity`` (priority 0.5) — entities with no edges

    Each item carries the originating ``user_id`` and ``scope`` so the
    admin UI can render badges and filter without a second round-trip.
    """
    meta = config.get_metadata_store()
    admin_user_id = get_user(request).user_id

    if source != "all":
        # Backward-compatible default branch — admin-only cross-user view.
        try:
            # ADMIN-BYPASS: cross-user review queue legacy default branch (plan §2.16).
            rows = meta.fetch_all(
                "SELECT rq.id, rq.reason, rq.created_at, rq.user_id AS rq_user_id, "
                "  rq.scope, "
                "  a.name AS candidate_a, a.entity_type AS type_a, a.scope AS scope_a, "
                "  b.name AS candidate_b, b.entity_type AS type_b, b.scope AS scope_b "
                "FROM review_queue rq "
                "JOIN entities a ON rq.candidate_a_id = a.entity_id "
                "JOIN entities b ON rq.candidate_b_id = b.entity_id "
                "ORDER BY rq.created_at DESC "
                "LIMIT 200"
            )
        except Exception as exc:
            _log.warning("review_queue: DB query failed — %s", exc)
            return []

        _log.info(
            "component=review_queue mode=default admin_user_id=%s rows=%d",
            admin_user_id,
            len(rows),
        )
        return [
            {
                "id": r["id"],
                "reason": r["reason"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                "user_id": r.get("rq_user_id") or "",
                "scope": r.get("scope") or "personal",
                "candidate_a": {
                    "name": r["candidate_a"],
                    "type": r["type_a"],
                    "scope": r.get("scope_a") or "personal",
                },
                "candidate_b": {
                    "name": r["candidate_b"],
                    "type": r["type_b"],
                    "scope": r.get("scope_b") or "personal",
                },
            }
            for r in rows
        ]

    limit = _REVIEW_QUEUE_DEFAULT_LIMIT
    items = _fetch_unified_queue(meta, admin_user_id, limit)
    return {"items": items, "next_cursor": None}


# ---------------------------------------------------------------------------
# POST /review-queue/decide
# ---------------------------------------------------------------------------


class DecideRequest(BaseModel):
    item_type: str
    item_id: str
    action: str
    user_id: str = "default"


def _insert_review_decision(
    meta,
    *,
    item_type: str,
    item_id: str,
    action: str,
    user_id: str,
    payload: dict | None = None,
    acted_by_user_id: str | None = None,
) -> None:
    """Insert a row into review_decisions for audit trail.

    ``user_id`` is the originating-data owner (the row's per-user scope).
    ``acted_by_user_id`` (when different) records the admin who decided
    on behalf of the originating user, so admin-on-behalf actions are
    traceable from the JSONB payload alone — no schema change needed.
    """
    enriched: dict = dict(payload or {})
    if acted_by_user_id and acted_by_user_id != user_id:
        enriched.setdefault("acted_by_user_id", acted_by_user_id)
    try:
        meta.execute(
            "INSERT INTO review_decisions "
            "(user_id, item_type, item_id, action, payload) "
            "VALUES (%s, %s, %s, %s, %s::jsonb)",
            (user_id, item_type, item_id, action, json.dumps(enriched)),
        )
    except Exception:
        _log.exception(
            "review_queue/decide: failed to insert review_decision item_type=%s item_id=%s action=%s",
            item_type,
            item_id,
            action,
        )


def _resolve_originating_user_id(meta, item_type: str, item_id: str) -> str | None:
    """Return the ``user_id`` that authored the review-queue item, or
    ``None`` if the item is not found.

    Centralises the per-item ownership lookup used by the B9 (audit
    review_queue_per_user_approval_scope) authorization check. Each
    item_type has its own source-of-truth table; this helper hides the
    per-type SQL so the route handler can ask one question only:
    "who owns this item?".
    """
    try:
        if item_type == "ambiguous_entity":
            row = meta.fetch_one(
                "SELECT user_id FROM review_queue WHERE id = %s",
                (item_id,),
            )
        elif item_type == "staged_entity":
            row = meta.fetch_one(
                "SELECT user_id FROM entities WHERE entity_id = %s",
                (item_id,),
            )
        elif item_type in ("constraint_violation", "orphan_entity"):
            row = meta.fetch_one(
                "SELECT user_id FROM constraint_violations WHERE violation_id = %s",
                (item_id,),
            )
        else:
            return None
    except Exception:
        _log.exception(
            "review_queue/decide: ownership lookup failed item_type=%s item_id=%s",
            item_type,
            item_id,
        )
        return None
    if row is None:
        return None
    return str(row.get("user_id") or "default")


@router.post("/review-queue/decide", dependencies=[Depends(require_user)])
def review_queue_decide(body: DecideRequest, ctx: UserContext = Depends(require_user)):
    """Process an operator decision on a review queue item.

    Supported item_type / action combinations:
      ambiguous_entity  + merge    → merge_entities(winner, loser)
      ambiguous_entity  + distinct → insert known_distinct_entity_pairs; resolve queue row
      staged_entity     + promote  → SET is_staged=FALSE, graph_projected_at=NULL
      staged_entity     + discard  → DELETE entity
      constraint_violation + suppress → SET resolved_at=NOW()
      orphan_entity     + dismiss  → SET resolved_at=NOW()

    Authorization (audit B9 — review_queue_per_user_approval_scope):
      Only the *originating user* OR an admin may approve a review-queue
      item. Non-admin callers can never act on another user's items;
      attempts return 403 (probe attempt is logged WARNING). Admins may
      additionally pass ``body.user_id`` to act on behalf of a target
      user (consistent with the ``target_user_id`` body-field convention
      used by ``POST /api/v1/me/export``); the originating ``user_id``
      from the item itself is always the source of truth and is what the
      DB writes scope against. The admin-on-behalf identity is recorded
      in the ``review_decisions.payload`` JSONB as ``acted_by_user_id``.

    Every successful action inserts a ``review_decisions`` row scoped to
    the originating user; ``acted_by_user_id`` is added to the payload
    when an admin acted on someone else's item.
    """
    item_type = body.item_type
    item_id   = body.item_id
    action    = body.action

    if item_type not in _REVIEW_QUEUE_ITEM_TYPES:
        raise HTTPException(status_code=400, detail=f"unknown item_type {item_type!r}")
    allowed = _ACTION_TABLE.get(item_type, set())
    if action not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"action {action!r} not valid for item_type {item_type!r}; allowed: {sorted(allowed)}",
        )

    meta = config.get_metadata_store()

    is_admin = (ctx.role == "admin")

    requested_target = (body.user_id or "").strip()
    if not is_admin and requested_target and requested_target not in ("default", ctx.user_id):
        _log.warning(
            "review_queue/decide: 403 non-admin user_id=%s tried to act on behalf of %s",
            ctx.user_id,
            requested_target,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="cannot act on behalf of another user",
        )

    originating_user_id = _resolve_originating_user_id(meta, item_type, item_id)
    if originating_user_id is None:
        raise HTTPException(
            status_code=404,
            detail=f"{item_type} item {item_id!r} not found",
        )

    if not is_admin and originating_user_id != ctx.user_id:
        _log.warning(
            "review_queue/decide: 403 user_id=%s tried to approve item owned by %s "
            "(item_type=%s item_id=%s)",
            ctx.user_id,
            originating_user_id,
            item_type,
            item_id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="review item belongs to a different user",
        )

    user_id = originating_user_id
    acted_by = ctx.user_id

    # ── ambiguous_entity ────────────────────────────────────────────
    if item_type == "ambiguous_entity":
        # Fetch the review_queue row to get candidate IDs
        try:
            rq_row = meta.fetch_one(
                "SELECT id, candidate_a_id, candidate_b_id FROM review_queue "
                "WHERE id = %s",
                (item_id,),
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"DB error: {exc}") from exc

        if rq_row is None:
            raise HTTPException(status_code=404, detail=f"review_queue item {item_id!r} not found")

        cand_a = str(rq_row["candidate_a_id"])
        cand_b = str(rq_row["candidate_b_id"])

        if action == "merge":
            # winner = candidate_a, loser = candidate_b (convention: a was first-seen)
            from services.entity_merge import merge_entities
            try:
                result = merge_entities(winner_id=cand_a, loser_id=cand_b, user_id=user_id)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            except RuntimeError as exc:
                raise HTTPException(status_code=500, detail=str(exc)) from exc

            _insert_review_decision(
                meta,
                item_type=item_type,
                item_id=item_id,
                action=action,
                user_id=user_id,
                payload={"winner_id": cand_a, "loser_id": cand_b},
                acted_by_user_id=acted_by,
            )
            return {
                "status": "ok",
                "action": action,
                "result": result.model_dump(),
            }

        else:  # distinct
            # Canonical ordering for known_distinct_entity_pairs
            eid_a, eid_b = sorted([cand_a, cand_b])
            try:
                meta.execute(
                    "INSERT INTO known_distinct_entity_pairs "
                    "(user_id, entity_id_a, entity_id_b) "
                    "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                    (user_id, eid_a, eid_b),
                )
                # review_queue has no resolved_at — remove the row
                meta.execute(
                    "DELETE FROM review_queue WHERE id = %s",
                    (item_id,),
                )
            except Exception as exc:
                raise HTTPException(status_code=500, detail=f"DB error: {exc}") from exc

            _insert_review_decision(
                meta,
                item_type=item_type,
                item_id=item_id,
                action=action,
                user_id=user_id,
                payload={"entity_id_a": eid_a, "entity_id_b": eid_b},
                acted_by_user_id=acted_by,
            )
            return {"status": "ok", "action": action, "result": None}

    # ── staged_entity ───────────────────────────────────────────────
    if item_type == "staged_entity":
        entity_row = meta.fetch_one(
            "SELECT entity_id FROM entities WHERE entity_id = %s AND user_id = %s",
            (item_id, user_id),
        )
        if entity_row is None:
            raise HTTPException(status_code=404, detail=f"entity {item_id!r} not found")

        if action == "promote":
            try:
                meta.execute(
                    "UPDATE entities SET is_staged = FALSE, graph_projected_at = NULL, "
                    "updated_at = NOW() WHERE entity_id = %s AND user_id = %s",
                    (item_id, user_id),
                )
            except Exception as exc:
                raise HTTPException(status_code=500, detail=f"DB error: {exc}") from exc

        else:  # discard
            try:
                meta.execute(
                    "DELETE FROM entities WHERE entity_id = %s AND user_id = %s",
                    (item_id, user_id),
                )
            except Exception as exc:
                raise HTTPException(status_code=500, detail=f"DB error: {exc}") from exc

        _insert_review_decision(
            meta,
            item_type=item_type,
            item_id=item_id,
            action=action,
            user_id=user_id,
            payload={"entity_id": item_id},
            acted_by_user_id=acted_by,
        )
        return {"status": "ok", "action": action, "result": None}

    # ── constraint_violation / orphan_entity — both set resolved_at ─
    if item_type in ("constraint_violation", "orphan_entity"):
        vrow = meta.fetch_one(
            "SELECT violation_id FROM constraint_violations "
            "WHERE violation_id = %s AND user_id = %s",
            (item_id, user_id),
        )
        if vrow is None:
            raise HTTPException(status_code=404, detail=f"violation {item_id!r} not found")
        try:
            meta.execute(
                "UPDATE constraint_violations SET resolved_at = NOW() "
                "WHERE violation_id = %s AND user_id = %s",
                (item_id, user_id),
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"DB error: {exc}") from exc

        _insert_review_decision(
            meta,
            item_type=item_type,
            item_id=item_id,
            action=action,
            user_id=user_id,
            payload={"violation_id": item_id},
            acted_by_user_id=acted_by,
        )
        return {"status": "ok", "action": action, "result": None}

    # Should not be reachable given the checks above
    raise HTTPException(status_code=400, detail="unhandled item_type/action combination")


# ---------------------------------------------------------------------------
# POST /entities/merge
# ---------------------------------------------------------------------------


class MergeRequest(BaseModel):
    winner_id: str
    loser_id: str
    user_id: str = "default"


@router.post("/entities/merge", dependencies=[Depends(require_admin)])
def entities_merge(body: MergeRequest):
    """Manually merge two entities.

    Auth: no token required — intentionally unauthenticated, matching the
    existing POST /backup and POST /restore pattern for this single-user,
    LAN-only deployment.

    PHASE 6 NOTE: When multi-user isolation lands, this endpoint (along with
    POST /backup, POST /restore, and POST /review-queue/decide) must be gated
    behind an admin token.  /entities/merge is destructive and irreversible
    (the loser entity is permanently deleted); it has higher consequence than
    the read-only endpoints that share the same unauthenticated pattern today.

    Validates UUIDs before any DB call.  Calls merge_entities() which executes
    a single Postgres transaction (Phase A) followed by best-effort Qdrant
    cleanup (Phase B).  Inserts a review_decisions audit row on success.

    Response codes:
      200  on success
      400  same winner/loser, invalid UUID, or business rule violation (ValueError)
      404  entity not found
      500  SQL error during Phase A (transaction rolled back)
    """
    # UUID validation before any DB call
    try:
        winner_uuid = str(uuid.UUID(body.winner_id))
        loser_uuid  = str(uuid.UUID(body.loser_id))
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail="invalid uuid")

    user_id = body.user_id or "default"

    if winner_uuid == loser_uuid:
        raise HTTPException(status_code=400, detail="winner_id and loser_id must differ")

    from services.entity_merge import merge_entities

    try:
        result = merge_entities(winner_id=winner_uuid, loser_id=loser_uuid, user_id=user_id)
    except ValueError as exc:
        msg = str(exc)
        status = 404 if "not found" in msg else 400
        raise HTTPException(status_code=status, detail=msg) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    # Audit trail
    meta = config.get_metadata_store()
    try:
        meta.execute(
            "INSERT INTO review_decisions "
            "(user_id, item_type, item_id, action, payload) "
            "VALUES (%s, %s, %s, %s, %s::jsonb)",
            (
                user_id,
                "ambiguous_entity",
                f"{winner_uuid}:{loser_uuid}",
                "merge",
                json.dumps({"winner_id": winner_uuid, "loser_id": loser_uuid}),
            ),
        )
    except Exception:
        _log.exception("entities/merge: failed to insert review_decisions audit row")

    return {
        "status": "ok",
        "winner_id": winner_uuid,
        "loser_id":  loser_uuid,
        **result.model_dump(),
    }


# ---------------------------------------------------------------------------
# POST /entities/deduplicate
# ---------------------------------------------------------------------------


@router.post("/entities/deduplicate", dependencies=[Depends(require_admin)])
def entities_deduplicate(background_tasks: BackgroundTasks):
    """Launch a probabilistic deduplication job (Pass 4b).

    Auth: same unauthenticated pattern as POST /backup, POST /restore,
    and POST /entities/merge — intentionally open for this single-user,
    LAN-only deployment.  When multi-user isolation lands (Phase 6), this
    endpoint must be gated behind an admin token.

    Concurrency: if a deduplication run is already in progress for user
    'default' (finished_at IS NULL), returns 409 to prevent duplicate jobs.
    For scheduler overlap, APScheduler max_instances=1 already prevents a
    second scheduler instance from starting.  This endpoint is the operator
    escape hatch for ad-hoc runs outside the weekly schedule.

    Response:
      202  {"status": "started", "run_id": "<uuid>"}  — job launched in background
      409  {"detail": "deduplication already running"} — run already in progress
    """
    meta = config.get_metadata_store()
    user_id = "default"

    # Check for an in-progress run (finished_at IS NULL)
    try:
        # ADMIN-BYPASS: admin-only manual dedup trigger (plan §2.8);
        # `deduplication_runs` is in plan §2.10's excluded-from-scope list.
        in_progress = meta.fetch_one(
            "SELECT run_id FROM deduplication_runs "
            "WHERE user_id = %s AND finished_at IS NULL "
            "LIMIT 1",
            (user_id,),
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"DB error: {exc}") from exc

    if in_progress is not None:
        raise HTTPException(status_code=409, detail="deduplication already running")

    # Insert the run row now so the 202 response can include the run_id,
    # and so a concurrent request immediately sees finished_at IS NULL.
    try:
        run_row = meta.fetch_one(
            "INSERT INTO deduplication_runs (user_id) VALUES (%s) RETURNING run_id",
            (user_id,),
        )
        run_id = str(run_row["run_id"])
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"DB error inserting run: {exc}") from exc

    def _run_in_background(pre_created_run_id: str) -> None:
        try:
            from services.deduplication import (
                _build_candidates,
                _score_candidates_with_splink,
                _route_pair,
                _update_run,
                _update_run_error,
                _SPLINK_MODEL_PATH,
                _log as _dedup_log,
            )
            import time as _time
            t0 = _time.monotonic()
            ms2 = config.get_metadata_store()
            vs2 = config.get_vector_store()

            # ADMIN-BYPASS: admin-triggered dedup batch (plan §2.8). Mirrors
            # services/deduplication.py's personal-only narrowing — but the
            # admin path is intentionally exempt: operator may want to
            # rebuild dedup state across all data the user owns. For
            # consistency with the scheduler path we keep this as-is and
            # rely on `services/deduplication.py` for the canonical run.
            entity_rows = ms2.fetch_all(
                "SELECT entity_id, name, entity_type, aliases, mention_count "
                # ADMIN-BYPASS: see comment above — admin-triggered dedup batch.
                "FROM entities WHERE user_id = %s AND is_staged = FALSE",
                (user_id,),
            )
            entity_map = {str(r["entity_id"]): r for r in entity_rows}

            # ADMIN-BYPASS: see above; `known_distinct_entity_pairs` is in
            # plan §2.10's excluded-from-scope list.
            distinct_rows = ms2.fetch_all(
                "SELECT entity_id_a, entity_id_b FROM known_distinct_entity_pairs "
                "WHERE user_id = %s",
                (user_id,),
            )
            known_distinct = {(str(dr["entity_id_a"]), str(dr["entity_id_b"])) for dr in distinct_rows}

            candidates, cos_sims = _build_candidates(list(entity_map.values()), vs2, user_id, known_distinct)
            scored = _score_candidates_with_splink(candidates, entity_map, cos_sims, _SPLINK_MODEL_PATH)

            auto_merged = 0
            queued = 0
            for item in scored:
                e_a = entity_map.get(item["entity_id_a"])
                e_b = entity_map.get(item["entity_id_b"])
                if e_a is None or e_b is None:
                    continue
                outcome = _route_pair(
                    ms2, pre_created_run_id, user_id, e_a, e_b,
                    item["match_probability"], item.get("features") or {},
                )
                if outcome == "auto_merged":
                    auto_merged += 1
                elif outcome == "queued":
                    queued += 1

            _update_run(
                ms2, pre_created_run_id,
                candidate_count=len(scored),
                auto_merged=auto_merged,
                queued_for_review=queued,
                known_distinct=len(known_distinct),
            )
            _log.info(
                "entities/deduplicate: background job complete run_id=%s auto_merged=%d queued=%d",
                pre_created_run_id, auto_merged, queued,
            )
        except Exception as exc:
            _log.error(
                "entities/deduplicate: background job failed run_id=%s: %s",
                pre_created_run_id, exc, exc_info=True,
            )
            try:
                meta2 = config.get_metadata_store()
                meta2.execute(
                    "UPDATE deduplication_runs SET finished_at = NOW(), error_message = %s "
                    "WHERE run_id = %s",
                    (str(exc)[:1000], pre_created_run_id),
                )
            except Exception:
                pass

    background_tasks.add_task(_run_in_background, run_id)
    return JSONResponse(status_code=202, content={"status": "started", "run_id": run_id})


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


@router.post(
    "/backup",
    dependencies=[Depends(require_admin), Depends(require_same_origin)],
)
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


@router.post(
    "/restore",
    dependencies=[Depends(require_admin), Depends(require_same_origin)],
)
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


@router.get("/export", deprecated=True)
def export_data(request: Request):
    """DEPRECATED — use ``POST /api/v1/me/export`` instead.

    Per the per-user backup export plan (D5 + §"API routes"): the
    legacy NDJSON dump emitted credential-shaped columns and lacked a
    portable manifest. The replacement is a per-user ZIP archive with
    a ``manifest.json`` and explicit credential redaction; admins can
    target another user via the body field on
    :func:`routes.me.export_self`.

    This handler now returns ``410 Gone`` with a pointer to the
    successor endpoint. Slated for removal in a follow-up release once
    the dashboard fully migrates.
    """
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail={
            "error": "deprecated",
            "successor": "POST /api/v1/me/export",
            "see": ".cursor/plans/per_user_backup_export.plan.md",
        },
    )
