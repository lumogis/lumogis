# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""KG service operator-admin routes.

Ported verbatim (with imports rewritten and Core-only auth removed) from
Core's `orchestrator/routes/admin.py`. Endpoints exposed:

  - GET  /kg/settings                  — list all hot-reload knobs
  - POST /kg/settings                  — upsert one or more knobs
  - DELETE /kg/settings/{key}          — revert a knob to its default
  - GET  /kg/job-status                — last-run timestamps for the three jobs
  - POST /kg/trigger-weekly            — fire the weekly quality job
  - GET  /kg/stop-entities             — 410: the list is managed by Core (LUM-952)
  - POST /kg/stop-entities             — 410: the list is managed by Core (LUM-952)
  - POST /kg/orphan-gc                 — orphan GC; dry run by default (LUM-952)
  - POST /kg/recount-cooccurrence      — recount RELATES_TO counts; dry run by default
  - GET  /graph/health                 — six KG quality metrics from Postgres

Auth model:
  - GETs are unauthenticated by default (operator dashboards, monitoring).
  - All POSTs/DELETEs require `X-Graph-Admin-Token` (matching the pattern
    used by `/graph/backfill`). When `GRAPH_ADMIN_TOKEN` is unset, the
    write endpoints are open — same dev-default posture as the backfill
    route. Exception: a non-dry run of /kg/orphan-gc or
    /kg/recount-cooccurrence requires the token to be configured (LUM-952).
"""

import hmac
import logging
import os

from fastapi import APIRouter
from fastapi import BackgroundTasks
from fastapi import HTTPException
from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from pydantic import Field

import config

router = APIRouter()
_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Admin-token gate (write endpoints only)
# ---------------------------------------------------------------------------


def _require_admin(request: Request) -> None:
    admin_token = os.environ.get("GRAPH_ADMIN_TOKEN", "")
    if not admin_token:
        return
    presented = request.headers.get("X-Graph-Admin-Token", "")
    # Constant-time, over bytes: see routes/webhook.py::check_webhook_auth.
    if not presented or not hmac.compare_digest(presented.encode(), admin_token.encode()):
        raise HTTPException(
            status_code=403,
            detail="Admin token required (X-Graph-Admin-Token header)",
        )


# ---------------------------------------------------------------------------
# kg_settings: metadata, casting, ranges
# ---------------------------------------------------------------------------

_SETTING_META: dict[str, dict] = {
    "entity_quality_lower": {
        "type": "float",
        "default": 0.35,
        "description": "Entities below this score are discarded immediately.",
    },
    "entity_quality_upper": {
        "type": "float",
        "default": 0.60,
        "description": "Entities between lower and upper are staged for review.",
    },
    "entity_promote_on_mention_count": {
        "type": "int",
        "default": 3,
        "description": "Mentions before a staged entity is auto-promoted.",
    },
    "graph_edge_quality_threshold": {
        "type": "float",
        "default": 0.3,
        "description": "Edges below this quality score are hidden from queries.",
    },
    "graph_cooccurrence_threshold": {
        "type": "int",
        "default": 3,
        "description": "Min co-occurrences before a RELATES_TO edge is visible.",
    },
    "graph_min_mention_count": {
        "type": "int",
        "default": 2,
        "description": "Entities below this mention count are hidden from queries.",
    },
    "graph_max_cooccurrence_pairs": {
        "type": "int",
        "default": 100,
        "description": "Max co-occurrence edge writes per ingestion event.",
    },
    "graph_viz_max_nodes": {
        "type": "int",
        "default": 150,
        "description": "Max nodes returned by /graph/viz.",
    },
    "graph_viz_max_edges": {
        "type": "int",
        "default": 300,
        "description": "Max edges returned by /graph/viz.",
    },
    "decay_half_life_relates_to": {
        "type": "int",
        "default": 365,
        "description": "Days until a RELATES_TO edge weight halves.",
    },
    "decay_half_life_mentions": {
        "type": "int",
        "default": 180,
        "description": "Days until a MENTIONS edge weight halves.",
    },
    "decay_half_life_discussed_in": {
        "type": "int",
        "default": 30,
        "description": "Days until a DISCUSSED_IN edge weight halves.",
    },
    "dedup_cron_hour_utc": {
        "type": "int",
        "default": 2,
        "description": "Hour (UTC) when the weekly dedup job runs on Sundays.",
    },
}

_KNOWN_SETTING_KEYS = frozenset(_SETTING_META.keys())

_SETTING_RANGES: dict[str, tuple] = {
    "entity_quality_lower": (0.0, 1.0),
    "entity_quality_upper": (0.0, 1.0),
    "graph_edge_quality_threshold": (0.0, 1.0),
    "entity_promote_on_mention_count": (1, None),
    "graph_cooccurrence_threshold": (1, None),
    "graph_min_mention_count": (1, None),
    "graph_max_cooccurrence_pairs": (1, None),
    "graph_viz_max_nodes": (1, None),
    "graph_viz_max_edges": (1, None),
    "decay_half_life_relates_to": (1, None),
    "decay_half_life_mentions": (1, None),
    "decay_half_life_discussed_in": (1, None),
    "dedup_cron_hour_utc": (0, 23),
}


def _cast_setting_value(key: str, raw: str):
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
            raise HTTPException(status_code=400, detail=f"{key!r} must be >= {lo}, got {value}")
        if hi is not None and value > hi:
            raise HTTPException(status_code=400, detail=f"{key!r} must be <= {hi}, got {value}")
    return value


def _read_all_kg_settings_from_db() -> dict[str, str]:
    try:
        meta = config.get_metadata_store()
        rows = meta.fetch_all("SELECT key, value FROM kg_settings")
        return {r["key"]: r["value"] for r in rows}
    except Exception:
        _log.warning("kg/settings: failed to read kg_settings table", exc_info=True)
        return {}


# ---------------------------------------------------------------------------
# GET /kg/settings
# ---------------------------------------------------------------------------


@router.get("/kg/settings")
def kg_settings_get():
    """Return all hot-reload settings with current value, type, default, source."""
    db_rows = _read_all_kg_settings_from_db()
    settings_out = []
    for key, meta in _SETTING_META.items():
        dtype = meta["type"]
        default_raw = meta["default"]
        if key in db_rows:
            raw = db_rows[key]
            source = "database"
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

        settings_out.append(
            {
                "key": key,
                "value": typed_value,
                "type": dtype,
                "default": default_raw,
                "source": source,
                "description": meta["description"],
            }
        )
    return {"settings": settings_out}


# ---------------------------------------------------------------------------
# POST /kg/settings
# ---------------------------------------------------------------------------


class KgSettingsUpsertItem(BaseModel):
    key: str
    value: str


class KgSettingsUpsertRequest(BaseModel):
    settings: list[KgSettingsUpsertItem]


@router.post("/kg/settings")
def kg_settings_post(body: KgSettingsUpsertRequest, request: Request):
    _require_admin(request)
    if not body.settings:
        raise HTTPException(status_code=400, detail="settings list is empty")

    updated_keys: list[str] = []
    for item in body.settings:
        key = item.key.strip()
        if key not in _KNOWN_SETTING_KEYS:
            raise HTTPException(status_code=400, detail=f"unknown key: {key}")
        _cast_setting_value(key, item.value)
        updated_keys.append(key)

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


@router.delete("/kg/settings/{key}")
def kg_settings_delete(key: str, request: Request):
    _require_admin(request)
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
# GET /kg/job-status
# ---------------------------------------------------------------------------


@router.get("/kg/job-status")
def kg_job_status():
    """Last-run timestamps for the three KG background jobs."""
    meta = config.get_metadata_store()

    last_reconciliation: str | None = None
    try:
        row = meta.fetch_one("SELECT value FROM kg_settings WHERE key = '_job_last_reconciliation'")
        if row:
            last_reconciliation = row["value"]
    except Exception:
        _log.warning("kg_job_status: failed to read _job_last_reconciliation", exc_info=True)

    last_weekly: str | None = None
    try:
        row = meta.fetch_one("SELECT value FROM kg_settings WHERE key = '_job_last_weekly'")
        if row:
            last_weekly = row["value"]
    except Exception:
        _log.warning("kg_job_status: failed to read _job_last_weekly", exc_info=True)

    dedup_last_run: str | None = None
    dedup_running: bool = False
    dedup_last_auto_merged: int | None = None
    dedup_last_queued: int | None = None
    dedup_last_candidate_count: int | None = None
    try:
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

        in_progress = meta.fetch_one(
            "SELECT run_id FROM deduplication_runs "
            "WHERE user_id = 'default' AND finished_at IS NULL LIMIT 1"
        )
        dedup_running = in_progress is not None
    except Exception:
        _log.warning("kg_job_status: failed to read deduplication_runs", exc_info=True)

    return {
        "reconciliation": {"last_run": last_reconciliation},
        "weekly_quality": {"last_run": last_weekly},
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


@router.post("/kg/trigger-weekly", status_code=202)
def kg_trigger_weekly(background_tasks: BackgroundTasks, request: Request):
    _require_admin(request)
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
                "A deduplication job is already running. The weekly quality job "
                "includes deduplication and cannot run concurrently. Try again "
                "when it completes."
            ),
        )

    def _run():
        try:
            from quality.edge_quality import run_weekly_quality_job

            run_weekly_quality_job()
        except Exception:
            _log.exception("kg/trigger-weekly: background job failed")

    background_tasks.add_task(_run)
    _log.info("kg/trigger-weekly: weekly quality job triggered via API")
    return {"status": "started", "message": "Weekly KG quality job started in background."}


# ---------------------------------------------------------------------------
# Stop entity list — managed by Core (LUM-952)
# ---------------------------------------------------------------------------
#
# The graph service never filters entities: Core's `services/entities.py`
# applies the stop list before an entity is stored, in every graph mode, and the
# graph service only projects what Core kept. Its own copy (loader, file and
# these routes) edited a list nothing read, and was lost on every recreate.
# The routes stay so the legacy `/mgm` page shows a clear message.

_STOP_LIST_MOVED = (
    "The stop-entity list is managed by Lumogis Core (/graph/mgm); "
    "the graph service does not use one."
)


@router.get("/kg/stop-entities")
def kg_stop_entities_get():
    raise HTTPException(status_code=410, detail=_STOP_LIST_MOVED)


@router.post("/kg/stop-entities")
def kg_stop_entities_post(request: Request):
    _require_admin(request)
    raise HTTPException(status_code=410, detail=_STOP_LIST_MOVED)


# ---------------------------------------------------------------------------
# Orphan GC and co-occurrence recount (LUM-952)
# ---------------------------------------------------------------------------


class OrphanGcRequest(BaseModel):
    dry_run: bool = True
    force: bool = False
    limit: int | None = Field(default=None, ge=1)


class RecountRequest(BaseModel):
    dry_run: bool = True
    user_id: str | None = None


def _require_destructive_allowed(request: Request, dry_run: bool) -> None:
    """Admin check, plus: a non-dry run needs GRAPH_ADMIN_TOKEN configured.

    `_require_admin` lets every write through when no token is set (the dev
    default). The service publishes no host port, but any container on the
    compose network could otherwise run a forced mass delete.
    """
    _require_admin(request)
    if not dry_run and not os.environ.get("GRAPH_ADMIN_TOKEN", ""):
        raise HTTPException(
            status_code=403,
            detail="Set GRAPH_ADMIN_TOKEN to run a destructive pass (dry runs need no token).",
        )
    if config.get_graph_store() is None:
        raise HTTPException(status_code=503, detail="graph store unavailable")


@router.post("/kg/orphan-gc")
def kg_orphan_gc(body: OrphanGcRequest, request: Request):
    _require_destructive_allowed(request, body.dry_run)
    from graph.reconcile import garbage_collect_orphan_nodes

    result = garbage_collect_orphan_nodes(limit=body.limit, dry_run=body.dry_run, force=body.force)
    _log.info(
        "kg/orphan-gc: dry_run=%s force=%s orphans_found=%d deleted_ok=%d refused=%s",
        body.dry_run,
        body.force,
        result["orphans_found"],
        result["deleted_ok"],
        result["refused"],
    )
    return result


@router.post("/kg/recount-cooccurrence")
def kg_recount_cooccurrence(body: RecountRequest, request: Request):
    _require_destructive_allowed(request, body.dry_run)
    from graph.reconcile import recount_cooccurrence

    return recount_cooccurrence(user_id=body.user_id, dry_run=body.dry_run)


# ---------------------------------------------------------------------------
# GET /graph/health
# ---------------------------------------------------------------------------


@router.get("/graph/health")
def graph_health(request: Request):
    """Six KG quality metrics sourced exclusively from Postgres.

    Never queries FalkorDB — metrics are always available even when the
    graph store is down. Returns 503 if Postgres itself is unreachable.

    Per the plan §"KG service routes (new)", this endpoint is gated by
    `X-Graph-Admin-Token`. (Core's existing copy is unauthenticated, but
    KG's same-named route is admin-only because operators reach it via
    the mgm UI's authenticated request, and external monitoring should
    use `/health` instead.)
    """
    _require_admin(request)

    meta = config.get_metadata_store()
    try:
        if hasattr(meta, "ping") and not meta.ping():
            return JSONResponse(
                content={"error": "Postgres unreachable"},
                status_code=503,
            )
    except Exception:
        return JSONResponse(content={"error": "Postgres unreachable"}, status_code=503)

    user_id = "default"

    duplicate_candidate_count = 0
    try:
        # ADMIN-BYPASS: KG service /graph/health is admin-gated via
        # X-Graph-Admin-Token (plan §2.8); KG quality dashboard.
        row = meta.fetch_one(
            "SELECT COUNT(*) AS cnt FROM review_queue WHERE user_id = %s",
            (user_id,),
        )
        duplicate_candidate_count = int(row["cnt"]) if row else 0
    except Exception:
        _log.warning("graph_health: duplicate_candidate_count query failed", exc_info=True)

    orphan_entity_pct = 0.0
    try:
        # ADMIN-BYPASS: KG service /graph/health quality metric (plan §2.8).
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

    mean_entity_completeness = 0.0
    try:
        # ADMIN-BYPASS: KG service /graph/health quality metric (plan §2.8).
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

    constraint_violation_counts: dict[str, int] = {"CRITICAL": 0, "WARNING": 0, "INFO": 0}
    try:
        # ADMIN-BYPASS: KG service /graph/health quality metric (plan §2.8);
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

    ingestion_quality_trend_7d: float | None = None
    try:
        # ADMIN-BYPASS: KG service /graph/health quality metric (plan §2.8).
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

    temporal_freshness: dict[str, int] = {"last_7d": 0, "8_30d": 0, "31_90d": 0, "90d_plus": 0}
    try:
        # ADMIN-BYPASS: KG service /graph/health quality metric (plan §2.8).
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
                "last_7d": int(row.get("last_7d") or 0),
                "8_30d": int(row.get("d8_30") or 0),
                "31_90d": int(row.get("d31_90") or 0),
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
