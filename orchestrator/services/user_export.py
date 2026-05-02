# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Per-user backup export and import service.

Owns the entire per-user export / import / dry-run / pruning lifecycle.
Route handlers in :mod:`routes.me` and :mod:`routes.admin_users` stay
thin — they delegate immediately to functions in this module.

See ``.cursor/plans/per_user_backup_export.plan.md`` for the binding
decisions table (D1–D16). Single source of truth for:

* the per-user Postgres table allowlist (``_USER_EXPORT_TABLES``),
* the credential redaction deny-list (``_REDACTED_FIELD_SUFFIXES``),
* the parent vs leaf PK collision policy (D4 + arbitration F4),
* the hybrid retention pruning policy (D8),
* the manifest schema (``_MANIFEST_FORMAT_VERSION``).

Public API (callable from routes / tests):

* :func:`export_user`
* :func:`enumerate_user_data_sections`
* :func:`dry_run_import`
* :func:`import_user`
* :func:`list_archives`
* :func:`prune_user_archives`
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

import config
from actions.audit import write_audit
from models.actions import AuditEntry
from models.auth import Role
from models.user_export import (
    ArchiveEntry,
    ArchiveInventoryEntry,
    ArchiveMeta,
    DanglingReference,
    ImportPlan,
    ImportPreconditions,
    ImportReceipt,
    ImportRefused,
    Manifest,
    PruneDecision,
    PruneReceipt,
    SectionSummary,
)
from services import media_storage
from services import users as users_service
from visibility import authored_by_filter

_log = logging.getLogger(__name__)


# ─── Constants ──────────────────────────────────────────────────────────────

_BACKUP_DIR = Path(os.environ.get("BACKUP_DIR", "/workspace/backups"))
_USER_EXPORT_DIR = Path(
    os.environ.get("USER_EXPORT_DIR", str(_BACKUP_DIR / "users"))
)
_USER_EXPORT_KEEP_MIN = int(os.environ.get("USER_EXPORT_KEEP_MIN", "3"))
_USER_EXPORT_MAX_AGE_DAYS = int(os.environ.get("USER_EXPORT_MAX_AGE_DAYS", "30"))

_MANIFEST_FORMAT_VERSION = 1
_SUPPORTED_FORMAT_VERSIONS = frozenset({1})

_MAX_ARCHIVE_BYTES = int(os.environ.get("USER_EXPORT_MAX_ARCHIVE_BYTES",
                                        str(500 * 1024 * 1024)))
_MAX_PER_ENTRY_BYTES = int(os.environ.get("USER_EXPORT_MAX_PER_ENTRY_BYTES",
                                          str(100 * 1024 * 1024)))

_QDRANT_COLLECTIONS: tuple[str, ...] = (
    "documents", "conversations", "entities", "signals",
)

# The hard-coded allowlist of per-user Postgres tables to export.
# Every entry is verified against postgres/init.sql + migrations during
# self-review (plan §"Postgres tables — _USER_EXPORT_TABLES").
# A table is in this list ⇔ it has a user_id column AND its rows belong
# to a single user. Global tables (app_settings, kg_settings) are
# excluded; the regression test `test_user_export_tables_exhaustive`
# (Pass 4) trips when a future migration adds a user-scoped table
# not declared here.
_USER_EXPORT_TABLES: tuple[str, ...] = (
    # Tables with a `scope` column → use `authored_by_filter` (D4).
    "file_index",
    "entities",
    "sessions",
    "notes",
    "audio_memos",
    "signals",
    "review_queue",
    "action_log",
    "audit_log",
    # Tables WITHOUT a `scope` column → user_id-only filter.
    "entity_relations",
    "sources",
    "relevance_profiles",
    "review_decisions",
    "known_distinct_entity_pairs",
    "connector_permissions",
    "routine_do_tracking",
    "deduplication_runs",
    "dedup_candidates",
    "routines",
    "feedback_log",
    "edge_scores",
    "constraint_violations",
    # Phase-5 capture tables — user_id-only filter (no scope column).
    "captures",
    "capture_attachments",
    "capture_transcripts",
)

# Tables with a `user_id` column that are deliberately omitted from
# the standard per-user zip export. Source of truth for both:
#
# * the exhaustiveness regression test
#   (``_INTENTIONAL_EXCLUSIONS`` in
#   ``tests/test_user_export_tables_exhaustive.py`` reads this set so
#   newly-omitted tables propagate automatically), AND
# * the manifest emission below — the ``omissions`` key turns each
#   entry into a structured ``{table, reason}`` row so downstream
#   tooling and tests can assert per-table without parsing free text.
#
# Each entry's value is the human-readable reason that lands in the
# archive manifest. Reasons MUST be distinct
# (``test_omitted_user_tables_have_unique_reason_strings`` pins this),
# preventing accidental copy/paste when a future chunk omits another
# table.
_OMITTED_USER_TABLES: dict[str, str] = {
    # Per-user connector credentials (per ADR
    # ``per_user_connector_credentials``) — the table holds Fernet
    # ciphertext sealed with the household ``LUMOGIS_CREDENTIAL_KEY``,
    # which is NOT included in the export bundle. Re-encrypting under
    # the recipient's key would require the operator's secret to
    # transit a portable archive; out of scope for v1. Raw pg_dump
    # backups still carry the ciphertext (and need the matching
    # household key to decrypt).
    "user_connector_credentials":
        "excluded (sensitive, non-portable in standard export)",
    "user_batch_jobs":
        "excluded (operational queue state, non-portable)",
    # Web Push subscription rows are per-user *device handles* —
    # endpoint URLs minted by the recipient's browser/push service
    # against this Lumogis origin. Replaying them at a destination
    # install would either 404 (different origin) or — worse — push
    # the user's notifications to a stale device they have since
    # forgotten. Subscriptions are re-registered at the destination
    # by the SPA service worker.
    "webpush_subscriptions":
        "excluded (per-device push endpoint; re-registered at destination)",
    # Refresh-token revocations are per-instance auth state. The JTIs
    # are minted under the source instance's ``LUMOGIS_JWT_REFRESH_SECRET``
    # and are meaningless at the destination (different secret → tokens
    # would never validate, so revocations are inert). Forward-compat
    # scaffolding only in v1 (table is INERT — see migration 019).
    "auth_refresh_revocations":
        "excluded (per-instance auth state; tokens unrecoverable at destination)",
}


# ---------------------------------------------------------------------------
# Tables WITHOUT a `user_id` column that are nonetheless OUT OF SCOPE
# for the per-user export (per ADR ``credential_scopes_shared_system``).
#
# These tables hold household-shared / operator-owned secrets and are
# never user-owned. The schema-walk in
# ``tests/test_user_export_tables_exhaustive.py:_user_scoped_tables_from_sql``
# already skips tables without a ``user_id`` column, so these never
# appear in ``_USER_EXPORT_TABLES`` in the first place — but the
# omission is recorded explicitly here as the **declarative omission
# registry** that the dedicated regression test
# ``tests/test_non_user_export_omissions.py`` reads. This guarantees a
# future schema-touching change to either tier table doesn't silently
# add it to a user export bundle.
#
# Reasons MUST be distinct (mirrors the
# ``test_omitted_user_tables_have_unique_reason_strings`` invariant
# above; the new test for ``_OMITTED_NON_USER_TABLES`` enforces the
# same uniqueness contract).
# ---------------------------------------------------------------------------
_OMITTED_NON_USER_TABLES: dict[str, str] = {
    # Household-shared connector credentials (per ADR
    # ``credential_scopes_shared_system``) — Fernet ciphertext sealed
    # with the household ``LUMOGIS_CREDENTIAL_KEY``. Per-user export
    # bundles never carry tier-table material; operator backup =
    # pg_dump of the table (recipient needs the household key).
    "household_connector_credentials":
        "excluded (household-tier; sensitive, non-portable in standard export)",
    # Instance/system connector credentials (per ADR
    # ``credential_scopes_shared_system``) — same crypto as household
    # tier; operator-owned, never user-owned. Same omission rationale.
    "instance_system_connector_credentials":
        "excluded (instance/system tier; sensitive, non-portable in standard export)",
}


# Tables that have a `scope` column. The export filter is
# (scope IN ('personal','shared') AND user_id = $me); other tables get
# user_id-only filtering.
_TABLES_WITH_SCOPE: frozenset[str] = frozenset({
    "file_index", "entities", "sessions", "notes", "audio_memos",
    "signals", "review_queue", "action_log", "audit_log",
})

# Parent tables — PKs other tables FK into. Per the F4 arbitration, a
# UUID collision on any of these refuses the import (409); a leaf
# collision is recorded as a warning.
# Each entry is (table_name, pk_column_name).
_PARENT_TABLES: tuple[tuple[str, str], ...] = (
    ("entities", "entity_id"),
    ("sessions", "session_id"),
    ("notes", "note_id"),
    ("audio_memos", "audio_id"),
    ("signals", "signal_id"),
    ("sources", "id"),
    ("deduplication_runs", "run_id"),
)
_PARENT_TABLE_NAMES: frozenset[str] = frozenset(t for t, _ in _PARENT_TABLES)

# Leaf tables with SERIAL/BIGSERIAL primary keys. On import we drop the
# `id` column so Postgres allocates a fresh sequence value — re-using
# the originating instance's serial id would either collide (unsafe in
# the source-instance round-trip case) or skip a row that operators
# expect to land.
_SERIAL_PK_TABLES: frozenset[str] = frozenset({
    "file_index", "entity_relations", "review_queue",
    "connector_permissions", "routine_do_tracking",
    "action_log", "audit_log",
    "feedback_log", "edge_scores", "dedup_candidates",
})

_REDACTED_FIELD_SUFFIXES: tuple[str, ...] = (
    "_secret", "_hash", "_token", "_credential", "_jti",
)

# Safe column-name regex; used as defence-in-depth before splicing
# column names back into INSERT statements on the import path.
_COL_RE = re.compile(r"^[a-z_][a-z0-9_]*$")


# ─── Pure helpers (no I/O — individually unit-testable) ─────────────────────


def _redact_credentials(user_row: dict) -> dict:
    """Apply D5: blank every credential-shaped column.

    Returns a NEW dict (does not mutate input). Any column name ending
    in one of ``_REDACTED_FIELD_SUFFIXES`` is set to ``None``. The
    archive carries enough information to recreate the user account but
    never the credentials needed to forge a session against either the
    source or destination instance.
    """
    out: dict = {}
    for key, value in user_row.items():
        if any(key.endswith(suffix) for suffix in _REDACTED_FIELD_SUFFIXES):
            out[key] = None
        else:
            out[key] = value
    return out


def _validate_zip_entry_names(names: list[str]) -> list[str]:
    """Return the list of entry names that fail the zip-slip check.

    Returned list is empty when every entry is safe. Rejects:

    * absolute paths (``/foo/bar``)
    * leading or embedded ``..`` segments (``../etc/passwd``,
      ``foo/../bar``)
    * Windows drive prefixes (``C:/foo``)
    * NUL bytes anywhere
    * blank entry names
    """
    bad: list[str] = []
    for name in names:
        if not name or "\x00" in name:
            bad.append(name)
            continue
        if name.startswith("/") or (len(name) >= 2 and name[1] == ":"):
            bad.append(name)
            continue
        # Resolve the path notionally; any segment of `..` is a slip.
        parts = PurePosixPath(name).parts
        if any(p == ".." for p in parts):
            bad.append(name)
            continue
    return bad


def _resolve_archive_path(archive_path: str) -> Path:
    """Validate ``archive_path`` is under the ``_USER_EXPORT_DIR`` allowlist.

    Resolves both the input and the allowed root, then asserts the input
    is a sub-path. Raises :class:`ImportRefused` with
    ``refusal_reason='forbidden_path'`` on mismatch.
    """
    candidate = Path(archive_path)
    if not candidate.is_absolute():
        candidate = (_USER_EXPORT_DIR / candidate).resolve()
    else:
        candidate = candidate.resolve()
    allowed = _USER_EXPORT_DIR.resolve()
    try:
        candidate.relative_to(allowed)
    except ValueError as exc:
        raise ImportRefused("forbidden_path", {
            "archive_path": archive_path,
            "allowed_root": str(allowed),
        }) from exc
    return candidate


def _parse_manifest(zf: zipfile.ZipFile) -> Manifest:
    """Extract + parse ``manifest.json`` from an open archive.

    Raises :class:`ImportRefused` (``manifest_invalid``) when the file
    is absent, unreadable, or has the wrong shape.
    """
    if "manifest.json" not in zf.namelist():
        raise ImportRefused("manifest_invalid", {"missing": "manifest.json"})
    try:
        raw = json.loads(zf.read("manifest.json"))
    except (json.JSONDecodeError, KeyError) as exc:
        raise ImportRefused("manifest_invalid", {"parse_error": str(exc)}) from exc
    try:
        return Manifest(
            format_version=int(raw["format_version"]),
            exported_at=datetime.fromisoformat(raw["exported_at"]),
            exporting_user_id=str(raw["exporting_user_id"]),
            exported_user_email=str(raw["exported_user_email"]),
            exported_user_role=str(raw["exported_user_role"]),
            scope_filter=str(raw.get("scope_filter", "authored_by_me")),
            falkordb_edge_policy=str(
                raw.get("falkordb_edge_policy", "personal_intra_user_authored")
            ),
            sections=list(raw.get("sections") or []),
            falkordb_external_edge_count=int(
                raw.get("falkordb_external_edge_count", 0)
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ImportRefused("manifest_invalid", {"shape_error": str(exc)}) from exc


def _validate_manifest(manifest: Manifest) -> list[str]:
    """Return human-readable errors describing manifest content problems.

    Empty list means the manifest is valid. Pure: no I/O.
    """
    errors: list[str] = []
    if manifest.format_version not in _SUPPORTED_FORMAT_VERSIONS:
        errors.append(
            f"format_version {manifest.format_version} not supported "
            f"(this build supports: {sorted(_SUPPORTED_FORMAT_VERSIONS)})"
        )
    if manifest.scope_filter != "authored_by_me":
        errors.append(
            f"scope_filter {manifest.scope_filter!r} not supported (v1 ships only 'authored_by_me')"
        )
    for section in manifest.sections:
        kind = section.get("kind")
        if kind not in {
            "postgres",
            "qdrant",
            "falkordb",
            "user_record",
            "capture_media",
        }:
            errors.append(f"unknown section kind: {kind!r}")
    return errors


def _enumerate_archive(zf: zipfile.ZipFile) -> list[ArchiveEntry]:
    """List every entry in the archive (filename + uncompressed size)."""
    return [ArchiveEntry(name=info.filename, size_bytes=info.file_size)
            for info in zf.infolist()]


def _decide_pruning(
    archives: list[ArchiveMeta],
    *,
    keep_min: int,
    max_age_days: int,
    now: datetime,
) -> PruneDecision:
    """D8 hybrid policy: keep newest ``keep_min``; prune anything older
    than ``max_age_days`` once the floor is satisfied.

    Pure function — no I/O, no clock side-effects (caller passes ``now``).
    """
    if not archives:
        return PruneDecision()
    cutoff = now.timestamp() - max_age_days * 86400
    by_newest = sorted(archives, key=lambda a: a.mtime.timestamp(), reverse=True)
    decision = PruneDecision()
    # The newest `keep_min` are always kept.
    keep_set = set(a.path for a in by_newest[:keep_min])
    for arch in by_newest:
        if arch.path in keep_set:
            decision.keep.append(arch.path)
            decision.reason_per_path[arch.path] = "within_keep_min"
            continue
        if arch.mtime.timestamp() < cutoff:
            decision.prune.append(arch.path)
            decision.reason_per_path[arch.path] = "over_max_age"
        else:
            decision.keep.append(arch.path)
            decision.reason_per_path[arch.path] = "under_max_age"
    return decision


# ─── Audit lifecycle ────────────────────────────────────────────────────────


def _audit_event(
    action: str,
    *,
    user_id: str,
    input_summary: dict | None = None,
    result_summary: dict | None = None,
) -> None:
    """Write a single audit row using the D10 schema.

    ``action`` already includes the ``__user_export__.`` /
    ``__user_import__.`` prefix; the connector is derived from the
    prefix so the audit table can be filtered by either.

    Fail-soft: an audit write error is logged and swallowed; a route
    handler that called this never sees an exception. This matches the
    semantics of the wider ``actions/audit.py:write_audit`` helper.
    """
    if action.startswith("__user_export__."):
        connector = "user_export"
    elif action.startswith("__user_import__."):
        connector = "user_import"
    else:
        connector = "user_export"  # defensive default
    try:
        write_audit(AuditEntry(
            action_name=action,
            connector=connector,
            mode="system",
            input_summary=json.dumps(input_summary or {}, default=str),
            result_summary=json.dumps(result_summary or {}, default=str),
            executed_at=datetime.now(timezone.utc),
            user_id=user_id,
        ))
    except Exception:
        _log.exception("audit write for %s failed", action)


def _audit_refusal(
    exc: ImportRefused,
    *,
    user_id: str,
    archive_name: str,
    new_user_email: str,
    stage: str,
) -> None:
    """Emit a ``__user_import__.refused`` audit row.

    Used by the top-level ``try/except ImportRefused`` wrappers in
    :func:`dry_run_import` and :func:`import_user` so every precondition
    refusal — whether it fired before or after ``__user_import__.started``
    — leaves a distinct ``.refused`` trail. Failures (uncaught exceptions
    after writes begin) keep their separate ``.failed`` lifecycle event;
    operators can therefore tell ``refused`` (clean rollback, no writes)
    apart from ``failed`` (partial state possible) at a glance.
    """
    _audit_event(
        "__user_import__.refused",
        user_id=user_id,
        input_summary={
            "archive": archive_name,
            "new_user_email": new_user_email,
        },
        result_summary={
            "refusal_reason": exc.refusal_reason,
            "payload": exc.payload,
            "stage": stage,
        },
    )


# ─── Per-table SQL filter helper ────────────────────────────────────────────


def _table_filter(table: str, user_id: str) -> tuple[str, tuple]:
    """Return the ``(WHERE clause, params)`` tuple for ``table``.

    Tables with a ``scope`` column use the `(scope IN
    ('personal','shared') AND user_id = %s)` filter (delegates to
    :func:`visibility.authored_by_filter`); tables without get the
    user_id-only filter.
    """
    if table in _TABLES_WITH_SCOPE:
        return authored_by_filter(user_id)
    return ("user_id = %s", (user_id,))


def _plan_capture_media_export(
    user_id: str,
    attachment_rows: list[dict],
    *,
    media_root: Path | None = None,
) -> tuple[list[dict], list[dict]]:
    """Plan capture attachment blobs for the ZIP under ``captures/media/``.

    Returns ``(included, omissions)``. Each *included* row is a dict with
    ``zip_entry``, ``path`` (absolute :class:`Path` for :meth:`ZipFile.write`),
    ``storage_key``, ``attachment_id``, ``capture_id``, ``size_bytes``.
    ``path`` must not be serialised to JSON.
    """
    included: list[dict] = []
    omissions: list[dict] = []

    for row in attachment_rows:
        att_id = str(row.get("id", ""))
        cap_id = str(row.get("capture_id", ""))
        sk_raw = row.get("storage_key")
        if not isinstance(sk_raw, str) or not sk_raw.strip():
            omissions.append({
                "attachment_id": att_id,
                "capture_id": cap_id,
                "storage_key": sk_raw,
                "reason": "bad_storage_key",
            })
            continue
        sk = sk_raw.strip().replace("\\", "/")
        if row.get("user_id") != user_id:
            omissions.append({
                "attachment_id": att_id,
                "capture_id": cap_id,
                "storage_key": sk,
                "reason": "user_mismatch",
            })
            continue
        try:
            path = media_storage.resolve_storage_key_file(sk, root=media_root)
        except ValueError as exc:
            omissions.append({
                "attachment_id": att_id,
                "capture_id": cap_id,
                "storage_key": sk,
                "reason": "bad_storage_key",
                "detail": str(exc)[:500],
            })
            continue
        if not path.is_file():
            omissions.append({
                "attachment_id": att_id,
                "capture_id": cap_id,
                "storage_key": sk,
                "reason": "missing_on_disk",
            })
            continue
        try:
            sz = path.stat().st_size
        except OSError:
            omissions.append({
                "attachment_id": att_id,
                "capture_id": cap_id,
                "storage_key": sk,
                "reason": "stat_failed",
            })
            continue
        if sz > _MAX_PER_ENTRY_BYTES:
            omissions.append({
                "attachment_id": att_id,
                "capture_id": cap_id,
                "storage_key": sk,
                "reason": "too_large",
                "size_bytes": sz,
            })
            continue
        arcname = f"captures/media/{sk}"
        included.append({
            "zip_entry": arcname,
            "path": path,
            "storage_key": sk,
            "attachment_id": att_id,
            "capture_id": cap_id,
            "size_bytes": sz,
        })

    return included, omissions


def _restore_capture_media_from_zip(
    zf: zipfile.ZipFile,
    *,
    exporting_user_id: str,
    new_user_id: str,
    receipt_warnings: list[str],
) -> int:
    """Extract ``captures/media/{storage_key}`` blobs into ``CAPTURE_MEDIA_ROOT``.

    Zip paths use the exporter's ``storage_key`` (``exporting_user_id`` prefix).
    Import rewrites ``capture_attachments.storage_key`` to ``new_user_id`` — we
    mirror that mapping when writing files so rows and blobs stay aligned.
    """
    from_uid = str(exporting_user_id).strip()
    prefix = f"{from_uid}/"
    new_uid = str(new_user_id).strip()
    n_written = 0
    for name in zf.namelist():
        if not name.startswith("captures/media/"):
            continue
        rel = name[len("captures/media/") :]
        if not rel or rel == "index.json":
            continue
        if rel.startswith(prefix):
            new_sk = f"{new_uid}/{rel[len(prefix):]}"
        elif "/" not in rel:
            new_sk = f"{new_uid}/{rel}"
        else:
            receipt_warnings.append(f"capture_media_unexpected_zip_path:{name}")
            continue
        try:
            blob = zf.read(name)
        except Exception as exc:
            _log.debug("import: read zip entry %s failed: %s", name, exc)
            receipt_warnings.append(f"capture_media_read_failed:{name}")
            continue
        if len(blob) > _MAX_PER_ENTRY_BYTES:
            receipt_warnings.append(f"capture_media_oversized:{name}")
            continue
        try:
            dest = media_storage.resolve_storage_key_file(new_sk)
        except ValueError as exc:
            _log.debug("import: bad storage key %s: %s", new_sk, exc)
            receipt_warnings.append(f"capture_media_bad_key:{new_sk}")
            continue
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(blob)
        except OSError as exc:
            _log.warning("import: write capture media %s failed: %s", dest, exc)
            receipt_warnings.append(f"capture_media_write_failed:{name}")
            continue
        n_written += 1
    return n_written


# ─── Export ─────────────────────────────────────────────────────────────────


def enumerate_user_data_sections(user_id: str) -> list[SectionSummary]:
    """Read-only inventory of what would be exported for ``user_id``.

    Used by an admin "preview-before-export" dashboard hint. Performs
    one ``SELECT COUNT(*)`` per per-user table; no Qdrant or FalkorDB
    round-trip. Synchronous.
    """
    meta = config.get_metadata_store()
    summaries: list[SectionSummary] = []
    for table in _USER_EXPORT_TABLES:
        clause, params = _table_filter(table, user_id)
        try:
            row = meta.fetch_one(
                f"SELECT COUNT(*) AS n FROM {table} WHERE {clause}",  # noqa: S608
                params,
            )
            n = int((row or {}).get("n") or 0)
        except Exception:
            _log.exception("enumerate: count failed for %s", table)
            n = 0
        summaries.append(SectionSummary(name=f"postgres/{table}.json",
                                        kind="postgres", row_count=n))
    return summaries


def export_user(user_id: str) -> tuple[bytes, str]:
    """Build a portable per-user archive and return ``(zip_bytes, filename)``.

    Side effects: writes a copy of the archive under
    ``${USER_EXPORT_DIR}/<user_id>/``, runs hybrid pruning, emits the
    ``__user_export__.started`` / ``.completed`` audit lifecycle.

    The returned bytes are also the body of ``POST /api/v1/me/export`` —
    routes don't re-read the file from disk.

    Raises ``RuntimeError`` only on impossible failures (Postgres
    unreachable mid-export). Per-collection Qdrant / FalkorDB failures
    are recorded as warnings in the manifest, not raised.
    """
    meta = config.get_metadata_store()
    vs = config.get_vector_store()

    _audit_event(
        "__user_export__.started",
        user_id=user_id,
        input_summary={"target_user_id": user_id},
    )

    # User record (redacted credentials).
    user_row_dict: dict | None = None
    try:
        user_row_dict = meta.fetch_one("SELECT * FROM users WHERE id = %s", (user_id,))
    except Exception:
        _log.exception("export: failed to read user record")
    redacted_user = _redact_credentials(user_row_dict or {"id": user_id})

    # Postgres tables.
    sections: list[dict] = []
    pg_payload: dict[str, list[dict]] = {}
    for table in _USER_EXPORT_TABLES:
        clause, params = _table_filter(table, user_id)
        try:
            rows = meta.fetch_all(
                f"SELECT * FROM {table} WHERE {clause}",  # noqa: S608
                params,
            )
        except Exception:
            _log.exception("export: SELECT failed for %s", table)
            rows = []
        pg_payload[table] = rows
        sections.append({"name": f"postgres/{table}.json",
                         "kind": "postgres", "row_count": len(rows)})

    # Qdrant collections (D12 — graceful degradation per collection).
    qdrant_payload: dict[str, list[dict]] = {}
    qdrant_warnings: list[str] = []
    for coll in _QDRANT_COLLECTIONS:
        try:
            points = vs.scroll_collection(coll, user_id=user_id, with_vectors=False)
            # Client-side scope filter (qdrant_store.scroll_collection
            # only supports user_id exact match — see plan §Qdrant).
            # Missing-scope payloads default to 'personal'.
            filtered = [
                p for p in points
                if (p.get("payload", {}).get("scope") or "personal")
                in ("personal", "shared")
            ]
            qdrant_payload[coll] = filtered
            sections.append({"name": f"qdrant/{coll}.json",
                             "kind": "qdrant", "row_count": len(filtered)})
        except Exception as exc:
            _log.exception("export: scroll failed for collection %s", coll)
            qdrant_warnings.append(f"qdrant:{coll}:{type(exc).__name__}")
            qdrant_payload[coll] = []

    # FalkorDB nodes + edges (D15) — best-effort.
    falkor_nodes: list[dict] = []
    falkor_edges: list[dict] = []
    falkor_external_count = 0
    falkor_warnings: list[str] = []
    try:
        gs = config.get_graph_store()
    except Exception:
        gs = None
    if gs is not None:
        try:
            node_rows = gs.query(
                "MATCH (n {user_id: $me}) "
                "RETURN labels(n) AS labels, n.lumogis_id AS lumogis_id, "
                "properties(n) AS properties",
                {"me": user_id},
            )
            for row in node_rows or []:
                falkor_nodes.append({
                    "labels": row.get("labels") or [],
                    "lumogis_id": row.get("lumogis_id"),
                    "properties": row.get("properties") or {},
                })
        except Exception as exc:
            _log.exception("export: FalkorDB node read failed")
            falkor_warnings.append(f"falkordb:nodes:{type(exc).__name__}")
        try:
            edge_rows = gs.query(
                "MATCH (a)-[r {user_id: $me}]->(b) "
                "RETURN a.lumogis_id AS from_lumogis_id, "
                "       a.user_id AS from_user_id, "
                "       b.lumogis_id AS to_lumogis_id, "
                "       b.user_id AS to_user_id, "
                "       type(r) AS rel_type, "
                "       properties(r) AS properties",
                {"me": user_id},
            )
            for row in edge_rows or []:
                if (row.get("from_user_id") != user_id
                        or row.get("to_user_id") != user_id):
                    falkor_external_count += 1
                    continue
                falkor_edges.append({
                    "from_lumogis_id": row.get("from_lumogis_id"),
                    "to_lumogis_id": row.get("to_lumogis_id"),
                    "rel_type": row.get("rel_type"),
                    "properties": row.get("properties") or {},
                })
        except Exception as exc:
            _log.exception("export: FalkorDB edge read failed")
            falkor_warnings.append(f"falkordb:edges:{type(exc).__name__}")

    sections.append({"name": "falkordb/nodes.json", "kind": "falkordb",
                     "row_count": len(falkor_nodes)})
    sections.append({"name": "falkordb/edges.json", "kind": "falkordb",
                     "row_count": len(falkor_edges)})
    sections.append({"name": f"users/{user_id}.json", "kind": "user_record",
                     "row_count": 1})

    att_rows = pg_payload.get("capture_attachments") or []
    capture_included: list[dict] = []
    capture_omissions: list[dict] = []
    if att_rows:
        capture_included, capture_omissions = _plan_capture_media_export(
            user_id, att_rows,
        )
        sections.append({
            "name": "captures/media/index.json",
            "kind": "capture_media",
            "row_count": len(capture_included),
        })

    manifest = {
        "format_version": _MANIFEST_FORMAT_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "exporting_user_id": user_id,
        "exported_user_email": (user_row_dict or {}).get("email", ""),
        "exported_user_role": (user_row_dict or {}).get("role", "user"),
        "scope_filter": "authored_by_me",
        "falkordb_edge_policy": "personal_intra_user_authored",
        "sections": sections,
        "falkordb_external_edge_count": falkor_external_count,
        "warnings": qdrant_warnings + falkor_warnings,
        # Structured record of user-scoped Postgres tables that the
        # export deliberately omits — sourced from
        # ``_OMITTED_USER_TABLES`` so the manifest stays in lock-step
        # with the allowlist on every future change.
        "omissions": [
            {"table": table, "reason": reason}
            for table, reason in sorted(_OMITTED_USER_TABLES.items())
        ],
    }
    if att_rows:
        manifest["capture_media"] = {
            "format": 1,
            "attachment_row_count": len(att_rows),
            "included_files": len(capture_included),
            "omissions": capture_omissions,
        }

    # Build the zip in memory.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, default=str, indent=2))
        zf.writestr(f"users/{user_id}.json",
                    json.dumps(redacted_user, default=str, indent=2))
        for table, rows in pg_payload.items():
            zf.writestr(f"postgres/{table}.json",
                        json.dumps(rows, default=str))
        for coll, points in qdrant_payload.items():
            zf.writestr(f"qdrant/{coll}.json",
                        json.dumps(points, default=str))
        zf.writestr("falkordb/nodes.json", json.dumps(falkor_nodes, default=str))
        zf.writestr("falkordb/edges.json", json.dumps(falkor_edges, default=str))
        if att_rows:
            index_payload = [
                {k: v for k, v in e.items() if k != "path"}
                for e in capture_included
            ]
            zf.writestr(
                "captures/media/index.json",
                json.dumps(index_payload, default=str),
            )
            for ent in capture_included:
                zf.write(ent["path"], ent["zip_entry"])

    archive_bytes = buf.getvalue()
    if len(archive_bytes) > _MAX_ARCHIVE_BYTES:
        _audit_event(
            "__user_export__.failed",
            user_id=user_id,
            input_summary={"target_user_id": user_id},
            result_summary={"error_class": "ExportTooLarge",
                            "size_bytes": len(archive_bytes)},
        )
        raise RuntimeError(
            f"export exceeds {_MAX_ARCHIVE_BYTES} bytes; "
            "the streaming follow-up is not yet implemented"
        )

    # Persist the archive under ${USER_EXPORT_DIR}/<user_id>/.
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"export_{timestamp}.zip"
    user_dir = _USER_EXPORT_DIR / user_id
    try:
        user_dir.mkdir(parents=True, exist_ok=True)
        (user_dir / filename).write_bytes(archive_bytes)
    except OSError:
        _log.exception("export: failed to persist archive to disk")
        # Returning the bytes still works — the caller (route handler)
        # streams them to the client. Disk persistence is best-effort.

    # Hybrid pruning — never fails the export.
    try:
        prune_user_archives(user_id)
    except Exception:
        _log.exception("export: pruning failed")
        _audit_event(
            "__user_export__.prune_failed",
            user_id=user_id,
            input_summary={"target_user_id": user_id},
            result_summary={"warning": "prune_failed"},
        )

    _audit_event(
        "__user_export__.completed",
        user_id=user_id,
        input_summary={"target_user_id": user_id},
        result_summary={
            "archive_filename": filename,
            "size_bytes": len(archive_bytes),
            "qdrant_warnings": qdrant_warnings,
            "falkordb_warnings": falkor_warnings,
            "section_count": len(sections),
        },
    )
    return archive_bytes, filename


# ─── Pruning ────────────────────────────────────────────────────────────────


def prune_user_archives(user_id: str) -> PruneReceipt:
    """Apply D8 hybrid policy to ``${USER_EXPORT_DIR}/<user_id>/``."""
    user_dir = _USER_EXPORT_DIR / user_id
    if not user_dir.is_dir():
        return PruneReceipt(user_id=user_id, archives_kept=0,
                            archives_pruned=0, pruned_filenames=[],
                            policy={"keep_min": _USER_EXPORT_KEEP_MIN,
                                    "max_age_days": _USER_EXPORT_MAX_AGE_DAYS})
    archives = []
    for path in user_dir.glob("export_*.zip"):
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            archives.append(ArchiveMeta(path=path, mtime=mtime))
        except OSError:
            _log.exception("prune: stat failed for %s", path)
    decision = _decide_pruning(
        archives,
        keep_min=_USER_EXPORT_KEEP_MIN,
        max_age_days=_USER_EXPORT_MAX_AGE_DAYS,
        now=datetime.now(timezone.utc),
    )
    pruned_names: list[str] = []
    for path in decision.prune:
        try:
            path.unlink()
            pruned_names.append(path.name)
        except OSError:
            _log.exception("prune: unlink failed for %s", path)
    return PruneReceipt(
        user_id=user_id,
        archives_kept=len(decision.keep),
        archives_pruned=len(pruned_names),
        pruned_filenames=pruned_names,
        policy={"keep_min": _USER_EXPORT_KEEP_MIN,
                "max_age_days": _USER_EXPORT_MAX_AGE_DAYS},
    )


# ─── Listing ────────────────────────────────────────────────────────────────


def list_archives() -> list[ArchiveInventoryEntry]:
    """List every archive under ``${USER_EXPORT_DIR}/*/`` with manifest_status."""
    out: list[ArchiveInventoryEntry] = []
    if not _USER_EXPORT_DIR.is_dir():
        return out
    for user_dir in sorted(_USER_EXPORT_DIR.iterdir()):
        if not user_dir.is_dir():
            continue
        user_id = user_dir.name
        for path in sorted(user_dir.glob("export_*.zip")):
            try:
                stat = path.stat()
            except OSError:
                continue
            entry = ArchiveInventoryEntry(
                user_id=user_id,
                archive_filename=path.name,
                bytes=stat.st_size,
                mtime=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
                manifest_status="missing_manifest",
                manifest_version=None,
                exported_user_email=None,
            )
            try:
                with zipfile.ZipFile(path) as zf:
                    manifest = _parse_manifest(zf)
                if manifest.format_version not in _SUPPORTED_FORMAT_VERSIONS:
                    entry.manifest_status = "unsupported_version"
                else:
                    entry.manifest_status = "valid"
                entry.manifest_version = manifest.format_version
                entry.exported_user_email = manifest.exported_user_email
            except ImportRefused as exc:
                if exc.refusal_reason == "manifest_invalid":
                    payload = exc.payload or {}
                    if payload.get("missing") == "manifest.json":
                        entry.manifest_status = "missing_manifest"
                    else:
                        entry.manifest_status = "unparseable"
                else:
                    entry.manifest_status = "unparseable"
            except Exception:
                entry.manifest_status = "unparseable"
            out.append(entry)
    return out


# ─── Import: dry-run ────────────────────────────────────────────────────────


def _detect_dangling_references(
    zf: zipfile.ZipFile, manifest: Manifest,
) -> list[DanglingReference]:
    """Detect cross-section FK dangling references inside the archive.

    Pure (reads from the zip only). Reports dangling for the five FK
    pairs called out in the plan; safe to silently return empty when a
    section is missing — that's reported separately by ``missing_sections``.
    """
    sections: dict[str, list[dict]] = {}
    for entry_name in zf.namelist():
        if entry_name.startswith("postgres/") and entry_name.endswith(".json"):
            try:
                sections[entry_name] = json.loads(zf.read(entry_name))
            except Exception:
                sections[entry_name] = []

    def _ids(table: str, col: str) -> set[Any]:
        rows = sections.get(f"postgres/{table}.json", [])
        return {r[col] for r in rows if r.get(col) is not None}

    dangling: list[DanglingReference] = []

    def _check(section: str, field: str, refs: set, target_ids: set) -> None:
        bad = sorted(str(r) for r in refs - target_ids)
        if bad:
            dangling.append(DanglingReference(
                section=section, field=field, count=len(bad),
                sample_values=bad[:5],
            ))

    entity_ids = _ids("entities", "entity_id")
    session_ids = _ids("sessions", "session_id")

    er_rows = sections.get("postgres/entity_relations.json", [])
    _check("entity_relations", "source_id",
           {r["source_id"] for r in er_rows if r.get("source_id")},
           entity_ids)
    _check("entity_relations", "target_id",
           {r["target_id"] for r in er_rows if r.get("target_id")},
           entity_ids)

    rq_rows = sections.get("postgres/review_queue.json", [])
    _check("review_queue", "candidate_a_id",
           {r["candidate_a_id"] for r in rq_rows if r.get("candidate_a_id")},
           entity_ids)
    _check("review_queue", "candidate_b_id",
           {r["candidate_b_id"] for r in rq_rows if r.get("candidate_b_id")},
           entity_ids)

    rd_rows = sections.get("postgres/review_decisions.json", [])
    if rd_rows:
        # review_decisions.item_id may reference entities or sessions
        # depending on item_type; report only when item_type='entity'.
        bad_refs: list[str] = []
        for r in rd_rows:
            if r.get("item_type") == "entity" and r.get("item_id") not in entity_ids:
                bad_refs.append(str(r.get("item_id")))
        if bad_refs:
            dangling.append(DanglingReference(
                section="review_decisions", field="item_id",
                count=len(bad_refs), sample_values=sorted(bad_refs)[:5],
            ))

    audit_rows = sections.get("postgres/audit_log.json", [])
    if audit_rows:
        evidence_targets = entity_ids | session_ids | {
            r.get("note_id") for r in sections.get("postgres/notes.json", [])
        } | {
            r.get("audio_id") for r in sections.get("postgres/audio_memos.json", [])
        }
        bad_refs = [
            str(r.get("reverse_token")) for r in audit_rows
            if r.get("reverse_token") and r.get("reverse_token") not in evidence_targets
        ]
        # Note: reverse_token is rarely a hard FK; skipping reporting in v1
        # to avoid false positives. (Left as scaffolding for a follow-up.)
        del bad_refs  # explicit no-op; intentional in v1

    dc_rows = sections.get("postgres/dedup_candidates.json", [])
    run_ids = _ids("deduplication_runs", "run_id")
    _check("dedup_candidates", "run_id",
           {r["run_id"] for r in dc_rows if r.get("run_id")},
           run_ids)

    return dangling


def _parent_uuid_collisions(zf: zipfile.ZipFile) -> list[dict]:
    """Pre-check parent-table UUID collisions against destination Postgres.

    Returns a list of ``{table, count, sample_ids}`` dicts; empty when
    no parent UUID in the archive collides with an existing destination
    row. Implements the F4-arbitration "refuse-before-write" contract.

    Each parent table is enumerated separately so the receipt's
    ``collisions`` field can call out which table conflicted.
    """
    meta = config.get_metadata_store()
    out: list[dict] = []
    for table, pk in _PARENT_TABLES:
        entry = f"postgres/{table}.json"
        if entry not in zf.namelist():
            continue
        try:
            rows = json.loads(zf.read(entry))
        except Exception:
            continue
        ids = [r[pk] for r in rows if r.get(pk) is not None]
        if not ids:
            continue
        try:
            existing = meta.fetch_all(
                f"SELECT {pk} AS pk FROM {table} "  # noqa: S608
                f"WHERE {pk} = ANY(%s)",
                (ids,),
            )
        except Exception:
            _log.exception("collision check failed for %s.%s", table, pk)
            continue
        clashes = [str(r["pk"]) for r in existing or []]
        if clashes:
            out.append({"table": table, "count": len(clashes),
                        "sample_ids": clashes[:5]})
    return out


def _build_dry_run_plan(
    zf: zipfile.ZipFile,
    manifest: Manifest,
    new_user_email: str,
    *,
    archive_integrity_ok: bool,
) -> ImportPlan:
    """Pure-ish (only Postgres SELECTs against destination state).

    Builds the structured ``ImportPlan`` that the dry-run path returns
    and that the real-import path consults to refuse early.
    """
    entries = _enumerate_archive(zf)
    name_set = {e.name for e in entries}

    declared_sections = [
        SectionSummary(
            name=s.get("name", "?"),
            kind=s.get("kind", "postgres"),
            row_count=int(s.get("row_count", 0)),
        )
        for s in manifest.sections
    ]
    missing_sections = [
        s.name for s in declared_sections if s.name not in name_set
    ]

    validation_errors = _validate_manifest(manifest)

    dangling = _detect_dangling_references(zf, manifest)

    target_email_available = (
        users_service.get_user_by_email(new_user_email) is None
    )

    parent_collisions = _parent_uuid_collisions(zf)
    no_parent_pk_collisions = not parent_collisions

    preconditions = ImportPreconditions(
        archive_integrity_ok=archive_integrity_ok,
        manifest_present=True,
        manifest_parses=True,
        manifest_version_supported=(
            manifest.format_version in _SUPPORTED_FORMAT_VERSIONS
        ),
        target_email_available=target_email_available,
        all_required_sections_present=not missing_sections,
        no_parent_pk_collisions=no_parent_pk_collisions,
    )

    would_succeed = all([
        preconditions.archive_integrity_ok,
        preconditions.manifest_present,
        preconditions.manifest_parses,
        preconditions.manifest_version_supported,
        preconditions.target_email_available,
        preconditions.all_required_sections_present,
        preconditions.no_parent_pk_collisions,
        not validation_errors,
    ])

    warnings_list: list[str] = list(validation_errors)
    if parent_collisions:
        warnings_list.append(
            f"parent_uuid_collisions: {[c['table'] for c in parent_collisions]}"
        )

    exported_user_payload: dict = {
        "id": manifest.exporting_user_id,
        "email": manifest.exported_user_email,
        "role": manifest.exported_user_role,
    }

    return ImportPlan(
        manifest_version=manifest.format_version,
        scope_filter=manifest.scope_filter,
        falkordb_edge_policy=manifest.falkordb_edge_policy,
        exported_user=exported_user_payload,
        sections=declared_sections,
        missing_sections=missing_sections,
        dangling_references=dangling,
        falkordb_external_edge_count=manifest.falkordb_external_edge_count,
        preconditions=preconditions,
        would_succeed=would_succeed,
        warnings=warnings_list,
    )


def _dry_run_import_impl(
    archive_path: Path | str, new_user_email: str,
) -> ImportPlan:
    """Internal: see :func:`dry_run_import` for the public contract."""
    archive_path = _resolve_archive_path(str(archive_path))
    if not archive_path.exists():
        raise ImportRefused("archive_integrity_failed",
                            {"detail": f"file not found: {archive_path}"})
    if archive_path.stat().st_size > _MAX_ARCHIVE_BYTES:
        raise ImportRefused("archive_too_large",
                            {"size_bytes": archive_path.stat().st_size,
                             "cap": _MAX_ARCHIVE_BYTES})

    try:
        with zipfile.ZipFile(archive_path) as zf:
            bad = _validate_zip_entry_names(zf.namelist())
            if bad:
                raise ImportRefused("archive_unsafe_entry_names",
                                    {"bad_entries": bad[:10]})
            for info in zf.infolist():
                if info.file_size > _MAX_PER_ENTRY_BYTES:
                    raise ImportRefused("archive_too_large", {
                        "entry": info.filename,
                        "uncompressed_size": info.file_size,
                        "per_entry_cap": _MAX_PER_ENTRY_BYTES,
                    })
            manifest = _parse_manifest(zf)
            user_record_name = f"users/{manifest.exporting_user_id}.json"
            if user_record_name not in zf.namelist():
                raise ImportRefused("missing_user_record",
                                    {"expected": user_record_name})

            _audit_event(
                "__user_import__.dry_run_requested",
                user_id=manifest.exporting_user_id,
                input_summary={"archive": archive_path.name,
                               "new_user_email": new_user_email},
            )

            plan = _build_dry_run_plan(
                zf, manifest, new_user_email,
                archive_integrity_ok=True,
            )

            if plan.would_succeed:
                _audit_event(
                    "__user_import__.dry_run_validation_passed",
                    user_id=manifest.exporting_user_id,
                    input_summary={"archive": archive_path.name},
                    result_summary={"sections": len(plan.sections)},
                )
            else:
                _audit_event(
                    "__user_import__.dry_run_validation_failed",
                    user_id=manifest.exporting_user_id,
                    input_summary={"archive": archive_path.name},
                    result_summary={
                        "missing_sections": plan.missing_sections,
                        "preconditions": plan.preconditions.model_dump(),
                        "warnings": plan.warnings,
                    },
                )
            return plan
    except zipfile.BadZipFile as exc:
        raise ImportRefused("archive_integrity_failed",
                            {"detail": str(exc)}) from exc


# ─── Import: real ───────────────────────────────────────────────────────────


def _strip_id_for_serial_table(table: str, row: dict) -> dict:
    """Drop the auto-allocated SERIAL ``id`` column on import.

    Re-using the originating instance's serial id would either collide
    with an existing destination row (forensic re-import case) or jump
    the destination's sequence forward by an unbounded amount. Letting
    Postgres allocate a fresh value is the safe default for these
    tables; nothing depends on the id stability across instances.
    """
    if table not in _SERIAL_PK_TABLES:
        return row
    return {k: v for k, v in row.items() if k != "id"}


def _insert_rows_for_table(
    meta, table: str, rows: list[dict], *, on_conflict: bool,
) -> tuple[int, int]:
    """Bulk-insert ``rows`` into ``table``.

    Returns ``(inserted_count, conflict_skipped_count)``. ``inserted +
    skipped`` is at most ``len(rows)``; rows with malformed column
    names (defence-in-depth — should be impossible from a manifest-shaped
    archive) are silently skipped without counting toward either bucket.
    """
    inserted = 0
    skipped = 0
    for raw in rows:
        row = _strip_id_for_serial_table(table, raw)
        if not row:
            continue
        cols = list(row.keys())
        if not all(_COL_RE.match(c) for c in cols):
            _log.warning("import: skipping row with invalid column in %s", table)
            continue
        col_list = ", ".join(cols)
        placeholders = ", ".join(["%s"] * len(cols))
        values = tuple(row[c] for c in cols)
        sql = f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})"  # noqa: S608
        if on_conflict:
            sql += " ON CONFLICT DO NOTHING"
        try:
            meta.execute(sql, values)
            inserted += 1
        except Exception:
            skipped += 1
            _log.debug("import: insert failed for %s row (likely conflict)", table)
    return inserted, skipped


def _import_user_impl(
    archive_path: Path | str,
    new_user_email: str,
    new_user_password: str,
    new_user_role: Role = "user",
) -> ImportReceipt:
    """Internal: see :func:`import_user` for the public contract."""
    archive_path = _resolve_archive_path(str(archive_path))
    if not archive_path.exists():
        raise ImportRefused("archive_integrity_failed",
                            {"detail": f"file not found: {archive_path}"})
    if archive_path.stat().st_size > _MAX_ARCHIVE_BYTES:
        raise ImportRefused("archive_too_large",
                            {"size_bytes": archive_path.stat().st_size,
                             "cap": _MAX_ARCHIVE_BYTES})

    meta = config.get_metadata_store()
    vs = config.get_vector_store()
    embedder = config.get_embedder()
    try:
        gs = config.get_graph_store()
    except Exception:
        gs = None

    receipt_warnings: list[str] = []
    leaf_collisions: dict[str, int] = {}
    sections_imported: list[SectionSummary] = []
    qdrant_zero_vector_count = 0
    falkor_nodes_imported = 0
    falkor_edges_imported = 0
    falkor_external_skipped = 0

    try:
        with zipfile.ZipFile(archive_path) as zf:
            bad = _validate_zip_entry_names(zf.namelist())
            if bad:
                raise ImportRefused("archive_unsafe_entry_names",
                                    {"bad_entries": bad[:10]})
            for info in zf.infolist():
                if info.file_size > _MAX_PER_ENTRY_BYTES:
                    raise ImportRefused("archive_too_large", {
                        "entry": info.filename,
                        "uncompressed_size": info.file_size,
                        "per_entry_cap": _MAX_PER_ENTRY_BYTES,
                    })
            manifest = _parse_manifest(zf)
            validation_errors = _validate_manifest(manifest)
            if any("not supported" in e for e in validation_errors
                   if "format_version" in e):
                raise ImportRefused("unsupported_format_version",
                                    {"detail": validation_errors})

            user_record_name = f"users/{manifest.exporting_user_id}.json"
            if user_record_name not in zf.namelist():
                raise ImportRefused("missing_user_record",
                                    {"expected": user_record_name})

            # Manifest section count check.
            archive_names = set(zf.namelist())
            mismatches = []
            for s in manifest.sections:
                fname = s.get("name", "")
                if fname.startswith("postgres/") and fname in archive_names:
                    try:
                        actual = len(json.loads(zf.read(fname)))
                    except Exception:
                        actual = -1
                    declared = int(s.get("row_count", 0))
                    if actual != declared:
                        mismatches.append({
                            "section": fname,
                            "declared": declared,
                            "actual": actual,
                        })
            if mismatches:
                raise ImportRefused("manifest_section_count_mismatch",
                                    {"mismatches": mismatches})

            missing_sections = [
                s["name"] for s in manifest.sections
                if s["name"] not in archive_names
            ]
            if missing_sections:
                raise ImportRefused("missing_sections",
                                    {"missing": missing_sections})

            _audit_event(
                "__user_import__.started",
                user_id=manifest.exporting_user_id,
                input_summary={
                    "archive": archive_path.name,
                    "new_user_email": new_user_email,
                    "new_user_role": new_user_role,
                },
            )

            # Email pre-check (race fallback caught later from create_user).
            if users_service.get_user_by_email(new_user_email) is not None:
                raise ImportRefused("email_exists",
                                    {"email": new_user_email})

            # Parent UUID pre-check (mirrors dry-run; closes race window).
            parent_collisions = _parent_uuid_collisions(zf)
            if parent_collisions:
                raise ImportRefused("uuid_collision_on_parent_table",
                                    {"collisions": parent_collisions})

            # Mint user + bulk insert in a single transaction.
            try:
                with meta.transaction():
                    try:
                        new_user = users_service.create_user(
                            email=new_user_email,
                            password=new_user_password,
                            role=new_user_role,
                        )
                    except ValueError as exc:
                        raise ImportRefused(
                            "email_exists",
                            {"email": new_user_email,
                             "race_detected": True,
                             "detail": str(exc)},
                        ) from exc
                    except users_service.PasswordPolicyViolationError as exc:
                        raise ImportRefused(
                            "manifest_invalid",
                            {"password_policy": str(exc)},
                        ) from exc
                    new_user_id = new_user.id

                    for table in _USER_EXPORT_TABLES:
                        entry = f"postgres/{table}.json"
                        if entry not in archive_names:
                            continue
                        try:
                            rows = json.loads(zf.read(entry))
                        except Exception:
                            _log.exception("import: failed to read %s", entry)
                            continue
                        # Re-write user_id to the freshly minted id so
                        # rows survive re-import into a different
                        # instance (where the original UUID does not
                        # exist as a `users` row).
                        for row in rows:
                            if "user_id" in row:
                                row["user_id"] = new_user_id
                            if (
                                table == "capture_attachments"
                                and "storage_key" in row
                                and row["storage_key"] is not None
                            ):
                                old_key = str(row["storage_key"])
                                from_user = str(manifest.exporting_user_id)
                                prefix = f"{from_user}/"
                                if old_key.startswith(prefix):
                                    row["storage_key"] = (
                                        f"{new_user_id}/"
                                        + old_key[len(prefix) :]
                                    )
                                elif "/" not in old_key:
                                    row["storage_key"] = (
                                        f"{new_user_id}/{old_key}"
                                    )
                        on_conflict = table not in _PARENT_TABLE_NAMES
                        inserted, skipped = _insert_rows_for_table(
                            meta, table, rows, on_conflict=on_conflict,
                        )
                        sections_imported.append(SectionSummary(
                            name=entry, kind="postgres", row_count=inserted,
                        ))
                        if skipped:
                            leaf_collisions[table] = skipped
            except ImportRefused:
                raise
            except Exception as exc:
                _audit_event(
                    "__user_import__.failed",
                    user_id=manifest.exporting_user_id,
                    input_summary={"archive": archive_path.name,
                                   "new_user_email": new_user_email},
                    result_summary={
                        "error_class": type(exc).__name__,
                        "partial_state_warning": True,
                    },
                )
                raise

            capture_media_written = _restore_capture_media_from_zip(
                zf,
                exporting_user_id=str(manifest.exporting_user_id),
                new_user_id=str(new_user_id),
                receipt_warnings=receipt_warnings,
            )
            if capture_media_written:
                sections_imported.append(SectionSummary(
                    name="captures/media",
                    kind="capture_media",
                    row_count=capture_media_written,
                ))

            # Qdrant — outside the transaction (different backend).
            for entry in [n for n in archive_names if n.startswith("qdrant/")]:
                coll = entry[len("qdrant/"):-len(".json")]
                try:
                    points = json.loads(zf.read(entry))
                except Exception:
                    _log.exception("import: failed to read %s", entry)
                    continue
                inserted_pts = 0
                for pt in points:
                    payload = pt.get("payload") or {}
                    payload["user_id"] = new_user_id
                    text = payload.get("text", "")
                    vec: list[float] | None = None
                    if text and coll == "documents":
                        try:
                            vec = embedder.embed(text)
                        except Exception:
                            qdrant_zero_vector_count += 1
                            try:
                                vec = [0.0] * embedder.vector_size
                            except Exception:
                                vec = [0.0] * 768
                    if vec is None:
                        try:
                            vec = [0.0] * embedder.vector_size
                        except Exception:
                            vec = [0.0] * 768
                        qdrant_zero_vector_count += 1
                    try:
                        vs.upsert(collection=coll, id=pt["id"], vector=vec,
                                  payload=payload)
                        inserted_pts += 1
                    except Exception:
                        _log.debug("import: upsert failed for %s/%s",
                                   coll, pt.get("id"))
                sections_imported.append(SectionSummary(
                    name=entry, kind="qdrant", row_count=inserted_pts,
                ))

            # FalkorDB re-MERGE — best-effort.
            if gs is not None:
                if "falkordb/nodes.json" in archive_names:
                    try:
                        nodes = json.loads(zf.read("falkordb/nodes.json"))
                        for n in nodes:
                            props = dict(n.get("properties") or {})
                            props["user_id"] = new_user_id
                            props["lumogis_id"] = n.get("lumogis_id")
                            try:
                                gs.create_node(
                                    labels=n.get("labels") or ["Node"],
                                    properties=props,
                                )
                                falkor_nodes_imported += 1
                            except Exception:
                                _log.debug("import: falkor node create failed")
                    except Exception:
                        _log.exception("import: falkor nodes parse failed")
                if "falkordb/edges.json" in archive_names:
                    # Edges require resolved node ids; in v1 we just
                    # skip edge restoration and surface a warning so the
                    # operator knows to re-derive via re-ingest. The
                    # MERGE-by-(from_id,to_id) contract requires
                    # internal id() values which differ on the
                    # destination.
                    try:
                        edges = json.loads(zf.read("falkordb/edges.json"))
                        falkor_external_skipped = 0
                        if edges:
                            receipt_warnings.append(
                                "falkordb_edges_not_restored: "
                                "re-derive via re-ingest"
                            )
                    except Exception:
                        pass

            archive_filename = archive_path.name

            receipt = ImportReceipt(
                new_user_id=new_user_id,
                archive_filename=archive_filename,
                sections_imported=sections_imported,
                qdrant_zero_vector_count=qdrant_zero_vector_count,
                falkordb_nodes_imported=falkor_nodes_imported,
                falkordb_edges_imported=falkor_edges_imported,
                falkordb_external_edges_skipped=falkor_external_skipped,
                leaf_pk_collisions_per_table=leaf_collisions,
                warnings=receipt_warnings,
            )

            _audit_event(
                "__user_import__.completed",
                user_id=manifest.exporting_user_id,
                input_summary={
                    "archive": archive_filename,
                    "new_user_email": new_user_email,
                    "new_user_role": new_user_role,
                },
                result_summary={
                    "new_user_id": new_user_id,
                    "section_count": len(sections_imported),
                    "qdrant_zero_vector_count": qdrant_zero_vector_count,
                    "leaf_pk_collisions_per_table": leaf_collisions,
                    "warnings": receipt_warnings,
                },
            )
            return receipt
    except zipfile.BadZipFile as exc:
        raise ImportRefused("archive_integrity_failed",
                            {"detail": str(exc)}) from exc


# ─── Public wrappers — refusal-audit catch ──────────────────────────────────
#
# These wrappers exist so every ``ImportRefused`` raised by the
# implementations leaves a distinct ``__user_import__.refused`` audit
# row regardless of where in the precondition chain it fired (path
# resolution / size cap / unsafe entry names / manifest invalid /
# missing user record / parent UUID collision / email collision …).
# ``.failed`` keeps its dedicated lifecycle event for true exceptions
# raised AFTER write-phase entry, so operators can grep:
#   * ``.refused`` → no writes happened, clean rollback
#   * ``.failed``  → partial state possible, investigate
# without consulting payload fields. The wrapper is the single source of
# truth for that contract; the impls never emit ``.refused`` directly.


def _archive_name_hint(archive_path: Path | str) -> str:
    try:
        return Path(str(archive_path)).name
    except Exception:
        return str(archive_path)


def _exporting_user_id_from_archive(archive_path: Path | str) -> str:
    """Best-effort manifest peek so the audit row carries the originating
    user_id when available. Returns ``"unknown"`` if the archive can't
    be opened or the manifest can't be parsed.

    Defensive: catches ``ImportRefused`` and ``OSError`` so this helper
    cannot itself trigger a second refusal during audit emission.
    """
    try:
        resolved = _resolve_archive_path(str(archive_path))
        if not resolved.exists():
            return "unknown"
        with zipfile.ZipFile(resolved) as zf:
            manifest = _parse_manifest(zf)
            return manifest.exporting_user_id
    except (ImportRefused, OSError, zipfile.BadZipFile, Exception):
        return "unknown"


def dry_run_import(archive_path: Path | str, new_user_email: str) -> ImportPlan:
    """Validate an archive without writing anything.

    ``new_user_email`` is required so
    :class:`ImportPreconditions.target_email_available` can be
    evaluated against the destination's ``users`` table without a
    second round-trip from the caller.

    Audit lifecycle:
      * ``__user_import__.dry_run_requested`` on entry (after the
        manifest parses cleanly).
      * ``__user_import__.dry_run_validation_passed`` /
        ``__user_import__.dry_run_validation_failed`` on exit.
      * ``__user_import__.refused`` on every precondition refusal
        (forbidden_path, archive_too_large, unsafe entry names,
        manifest invalid, missing user record, archive integrity
        failed) — emitted by this wrapper, not the impl.
    """
    try:
        return _dry_run_import_impl(archive_path, new_user_email)
    except ImportRefused as exc:
        _audit_refusal(
            exc,
            user_id=_exporting_user_id_from_archive(archive_path),
            archive_name=_archive_name_hint(archive_path),
            new_user_email=new_user_email,
            stage="dry_run",
        )
        raise


def import_user(
    archive_path: Path | str,
    new_user_email: str,
    new_user_password: str,
    new_user_role: Role = "user",
) -> ImportReceipt:
    """Mint a fresh user and bulk-insert the archive's contents.

    Refuses (raises :class:`ImportRefused`) on any precondition failure;
    every refusal — whether before or after ``__user_import__.started``
    — emits a dedicated ``__user_import__.refused`` audit event from
    this wrapper. Wraps the entire write phase in a single explicit
    transaction so a refusal mid-flight (parent UUID race) leaves
    Postgres untouched.

    True failures (uncaught exceptions raised AFTER the write phase
    begins) keep the separate ``__user_import__.failed`` lifecycle
    event emitted by the impl so operators can distinguish refusal
    (clean rollback, no writes) from failure (partial state possible).

    Per-collection Qdrant re-embed is best-effort: failures are
    captured as warnings in the receipt rather than aborting the whole
    import (D12).
    """
    try:
        return _import_user_impl(
            archive_path, new_user_email, new_user_password, new_user_role,
        )
    except ImportRefused as exc:
        _audit_refusal(
            exc,
            user_id=_exporting_user_id_from_archive(archive_path),
            archive_name=_archive_name_hint(archive_path),
            new_user_email=new_user_email,
            stage="real_import",
        )
        raise


__all__ = [
    "export_user",
    "enumerate_user_data_sections",
    "dry_run_import",
    "import_user",
    "list_archives",
    "prune_user_archives",
]
