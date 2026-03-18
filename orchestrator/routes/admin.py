"""Admin endpoints: /, /health, /dashboard, /permissions, /review-queue, /backup, /restore, /export."""

import datetime
import json
import logging
import os
import zipfile
from pathlib import Path

import config
from auth import get_user
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from permissions import get_all_permissions, set_connector_mode
from pydantic import BaseModel

_DASHBOARD_HTML = Path(__file__).parent.parent / "dashboard" / "index.html"

router = APIRouter()
_log = logging.getLogger(__name__)

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
        raise HTTPException(status_code=404, detail="Dashboard not found. Check that orchestrator/dashboard/index.html exists.")
    return FileResponse(_DASHBOARD_HTML, media_type="text/html")


# ---------------------------------------------------------------------------
# Status + Health
# ---------------------------------------------------------------------------


def _check_service(name: str, check_fn) -> str:
    try:
        return "ok" if check_fn() else "unreachable"
    except Exception:
        return "unreachable"


@router.get("/")
def status_page():
    """System status: confirms the orchestrator is running and backends are healthy."""
    vs = config.get_vector_store()
    meta = config.get_metadata_store()
    embedder = config.get_embedder()

    services = {
        "qdrant": _check_service("qdrant", vs.ping),
        "postgres": _check_service("postgres", meta.ping),
        "embedder": _check_service("embedder", embedder.ping),
    }

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

    return {
        "status": "healthy" if all_ok else "degraded",
        "documents_indexed": docs_indexed,
        "sessions_stored": sessions_stored,
        "entities_known": entities_known,
        "services": services,
        "links": {
            "librechat": "http://localhost:3080",
            "api_docs": "http://localhost:8000/docs",
        },
    }


@router.get("/health")
def health():
    """Detailed health check used by Activepieces monitoring flows.

    Returns accurate doc/entity/file counts so the caller can detect drift
    (e.g. Qdrant doc count vs file_index row count mismatch > 5 %).
    """
    vs = config.get_vector_store()
    meta = config.get_metadata_store()

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

    return {
        "qdrant_doc_count": qdrant_doc_count,
        "file_index_count": file_index_count,
        "total_chunks_indexed": total_chunks,
        "entity_count": entity_count,
        "last_ingest": last_ingest,
        "error_count": error_count,
        "chunk_drift_pct": chunk_drift_pct,
    }


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
        raise HTTPException(status_code=500, detail=f"review_queue query failed: {exc}")

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
    zip_path = Path(body.zip_path)
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
                for row in rows:
                    columns = list(row.keys())
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
