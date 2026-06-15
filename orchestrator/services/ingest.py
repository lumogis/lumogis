# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Ingest pipeline: extract, chunk, embed, store.

Includes performance guardrails (rate limiting, CPU monitoring) and a
filesystem watcher for real-time ingestion of files dropped into the inbox.
"""

import errno
import hashlib
import json
import logging
import os
import re
import shutil
import time
import traceback
import unicodedata
from collections.abc import Callable
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Literal

import hooks
import psutil
import tiktoken
from auth import UserContext
from events import Event
from models.ingest import IngestResult
from models.ingest import IngestStats
from services.injection_sanitiser import sanitise_at_ingest
from services.injection_sanitiser import sanitize_attribute_source_token
from services.point_ids import document_chunk_point_id
from services.point_ids import external_document_chunk_point_id
from visibility import visible_filter
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

import config

_log = logging.getLogger(__name__)

_CHUNK_MAX_TOKENS = 512

# Matches markdown headings (# Title) and ALL-CAPS section labels (≥ 4 chars).
_HEADING_RE = re.compile(r"^#{1,6}\s+\S|^[A-Z][A-Z\s\d,.\-]{3,59}$")


def _extract_section_headers(text: str, chunks: list[str]) -> list[str | None]:
    """Return the closest preceding section heading for each chunk.

    Scans the source text for markdown/ALL-CAPS headings, then for each chunk
    finds the last heading that appears before the chunk's position.
    """
    lines = text.splitlines()
    heading_positions: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped and _HEADING_RE.match(stripped):
            heading_positions.append((i, stripped.lstrip("#").strip()))

    if not heading_positions:
        return [None] * len(chunks)

    headers: list[str | None] = []
    search_from = 0
    for chunk in chunks:
        probe = chunk[:80].strip()
        pos = text.find(probe, search_from)
        if pos == -1:
            headers.append(None)
            continue
        line_num = text[:pos].count("\n")
        best: str | None = None
        for h_line, h_text in heading_positions:
            if h_line <= line_num:
                best = h_text
            else:
                break
        headers.append(best)
        search_from = max(0, pos - 50)

    return headers


_CHUNK_OVERLAP_TOKENS = 50

try:
    _enc = tiktoken.get_encoding("cl100k_base")
except Exception:
    _enc = None


def _file_hash(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def chunk_text(text: str) -> list[str]:
    """Split text into sentence-aware chunks using tiktoken token counting."""
    if not text or not text.strip():
        return []

    if _enc is None:
        words = text.split()
        chunk_size = _CHUNK_MAX_TOKENS
        chunks = []
        for i in range(0, len(words), chunk_size - _CHUNK_OVERLAP_TOKENS):
            chunk = " ".join(words[i : i + chunk_size])
            if chunk.strip():
                chunks.append(chunk)
        return chunks

    sentences = []
    for paragraph in text.split("\n"):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        for sent in paragraph.replace(". ", ".\n").split("\n"):
            sent = sent.strip()
            if sent:
                sentences.append(sent)

    chunks = []
    current: list[str] = []
    current_tokens = 0

    for sent in sentences:
        sent_tokens = len(_enc.encode(sent))
        if current_tokens + sent_tokens > _CHUNK_MAX_TOKENS and current:
            chunks.append(" ".join(current))
            overlap_text = " ".join(current)
            overlap_tokens = _enc.encode(overlap_text)
            if len(overlap_tokens) > _CHUNK_OVERLAP_TOKENS:
                keep = overlap_tokens[-_CHUNK_OVERLAP_TOKENS:]
            else:
                keep = overlap_tokens
            current = [_enc.decode(keep)]
            current_tokens = len(keep)
        current.append(sent)
        current_tokens += sent_tokens

    if current:
        chunks.append(" ".join(current))

    return chunks


def _ingest_chunked_text(
    *,
    user_id: str,
    logical_path: str,
    file_type: str,
    text: str,
    chunks: list[str],
    point_id_for_chunk: Callable[[int], str],
) -> tuple[int, int]:
    """Sanitise, embed, and upsert chunks. Returns (chunk_count_written, drops_blocked_high)."""
    embedder = config.get_embedder()
    vs = config.get_vector_store()
    scanner = config.get_injection_scanner()

    section_headers_all = _extract_section_headers(text, chunks)
    sanitized_rows: list[tuple[int, str, dict]] = []
    drops_blocked_high = 0

    for i, chunk in enumerate(chunks):
        outcome = sanitise_at_ingest(chunk, scanner=scanner, skip_if_empty=True)
        if outcome["blocked_high"]:
            drops_blocked_high += 1
            doc_id = point_id_for_chunk(i)
            try:
                vs.delete(collection="documents", id=doc_id)
            except Exception:
                _log.warning(
                    "ingest_blocked_high_delete_failed doc_id=%r file_path=%r user=%r",
                    doc_id,
                    logical_path,
                    user_id,
                    exc_info=True,
                )
            _log.warning(
                (
                    "ingest_blocked_high user_id=%s file_path=%s chunk_index=%s "
                    "pattern_hits=%s severity=%s"
                ),
                user_id,
                logical_path,
                i,
                outcome["pattern_hits"],
                outcome["max_severity"],
            )
            hooks.fire_background(
                Event.INJECTION_FLAGGED,
                user_id=user_id,
                source="ingest_pipeline",
                file_path=logical_path,
                chunk_index=i,
                severity=outcome["max_severity"],
                action=os.environ.get("INJECTION_ACTION", "wrap"),
                pattern_hits=outcome["pattern_hits"],
                sanitised_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                stage="ingest",
            )
            continue

        if outcome["injection_flagged"]:
            severity = outcome["max_severity"]
            if severity == "high":
                lvl = logging.WARNING
            else:
                lvl = logging.INFO
            _log.log(
                lvl,
                "inject_flag_at_ingest user_id=%s file_path=%s chunk_index=%s pattern_hits=%s",
                user_id,
                logical_path,
                i,
                outcome["pattern_hits"],
            )
            hooks.fire_background(
                Event.INJECTION_FLAGGED,
                user_id=user_id,
                source="ingest_pipeline",
                file_path=logical_path,
                chunk_index=i,
                severity=severity,
                action=os.environ.get("INJECTION_ACTION", "wrap"),
                pattern_hits=outcome["pattern_hits"],
                sanitised_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                stage="ingest",
            )

        sanitized_text = outcome["text"]
        iso_ingested = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        safe_source = sanitize_attribute_source_token(str(logical_path))

        payload_origin: dict = {
            "trusted": False,
            "scope": "personal",
            "source": safe_source,
            "ingested": iso_ingested,
            "pattern_hits": list(outcome["pattern_hits"]),
            "injection_flagged": bool(outcome["injection_flagged"]),
            "pre_wrapped": False,
        }

        sanitized_rows.append((i, sanitized_text, payload_origin))

    if sanitized_rows:
        vectors = embedder.embed_batch([t for _, t, __ in sanitized_rows])
    else:
        vectors = []

    chunk_count_written = 0
    for (idx, sanitized_text, origin_meta), vec in zip(sanitized_rows, vectors):
        doc_id = point_id_for_chunk(idx)
        payload: dict = {
            "file_path": logical_path,
            "chunk_index": idx,
            "text": sanitized_text,
            "file_type": file_type,
            "user_id": user_id,
            "scope": "personal",
        }
        if config.is_injection_sanitiser_enabled():
            payload["origin"] = origin_meta
        sec = section_headers_all[idx]
        if sec:
            payload["section_header"] = sec
        vs.upsert(
            collection="documents",
            id=doc_id,
            vector=vec,
            payload=payload,
        )
        chunk_count_written += 1

    return chunk_count_written, drops_blocked_high


def _emit_document_ingested_and_entities(
    *,
    logical_path: str,
    chunk_count_written: int,
    user_id: str,
    text: str,
    ingestion_source_kind: str = "filesystem",
) -> None:
    hooks.fire_background(
        Event.DOCUMENT_INGESTED,
        file_path=logical_path,
        chunk_count=chunk_count_written,
        user_id=user_id,
        ingestion_source_kind=ingestion_source_kind,
    )
    try:
        from services.entities import extract_entities
        from services.entities import store_entities

        entities = extract_entities(text, user_id=user_id)
        if entities:
            store_entities(
                entities,
                evidence_id=logical_path,
                evidence_type="DOCUMENT",
                user_id=user_id,
            )
            _log.info("Stored %d entities from document %s", len(entities), logical_path)
    except Exception:
        _log.exception("Entity extraction failed for document %s", logical_path)


def ingest_file(file_path: str, *, user_id: str) -> IngestResult:
    """Ingest one file and attribute every artifact to ``user_id``.

    Phase 3: ``user_id`` is keyword-only and required. The watcher and
    the ``POST /ingest`` route both resolve a real owner before calling
    this; we no longer accept a "default" fallback.
    """
    if not isinstance(user_id, str) or not user_id:
        raise TypeError("ingest_file: user_id (keyword-only) is required")
    path = Path(file_path)
    ext = path.suffix.lower()

    extractors = config.get_extractors()
    if ext not in extractors:
        _log.debug("No extractor for %s, skipping %s", ext, file_path)
        return IngestResult(file_path=file_path, chunk_count=0, skipped=True)

    new_hash = _file_hash(file_path)

    meta = config.get_metadata_store()
    # Personal duplicate detection: a file already ingested under THIS user's
    # personal scope short-circuits re-extraction. Shared/system projection
    # rows must not collapse the precheck (a publisher can re-ingest the
    # original personal version even after publishing a shared projection).
    where_clause, where_params = visible_filter(
        UserContext(user_id=user_id), scope_filter="personal"
    )
    existing = meta.fetch_one(
        f"SELECT file_hash FROM file_index WHERE file_path = %s AND {where_clause}",
        (file_path, *where_params),
    )
    if existing and existing["file_hash"] == new_hash:
        _log.info("Skipping unchanged: %s", file_path)
        return IngestResult(file_path=file_path, chunk_count=0, skipped=True)

    text = extractors[ext](file_path)
    chunks = chunk_text(text)
    if not chunks:
        _log.info("No text extracted from %s", file_path)
        return IngestResult(file_path=file_path, chunk_count=0, skipped=True)

    chunk_count_written, drops_blocked_high = _ingest_chunked_text(
        user_id=user_id,
        logical_path=file_path,
        file_type=ext,
        text=text,
        chunks=chunks,
        point_id_for_chunk=lambda i: document_chunk_point_id(user_id, file_path, i),
    )

    has_index_row = existing is not None
    file_index_chunk_count_arg = chunk_count_written

    if drops_blocked_high > 0:
        if has_index_row:
            meta.execute(
                (
                    "UPDATE file_index SET "
                    "chunk_count = %s, "
                    "file_type = %s, "
                    "updated_at = NOW() "
                    f"WHERE file_path = %s AND {where_clause}"
                ),
                (file_index_chunk_count_arg, ext, file_path, *where_params),
            )
            _log.warning(
                (
                    "Ingest completed with BLOCKED HIGH chunks — file_hash deliberately not "
                    "advanced (drops_blocked_high=%d file_path=%s user=%s); policy change + "
                    "re-ingest will retry."
                ),
                drops_blocked_high,
                file_path,
                user_id,
            )
        else:
            _log.warning(
                "Ingest aborted before file_index row (drops_blocked_high=%d, file_path=%s)",
                drops_blocked_high,
                file_path,
            )
        _log.info(
            "Partial ingest bookkeeping: wrote %s / %s chunks (%s skipped high-severity)",
            chunk_count_written,
            len(chunks),
            drops_blocked_high,
        )
        _emit_document_ingested_and_entities(
            logical_path=file_path,
            chunk_count_written=chunk_count_written,
            user_id=user_id,
            text=text,
            ingestion_source_kind="filesystem",
        )
        return IngestResult(file_path=file_path, chunk_count=chunk_count_written)

    meta.execute(
        "INSERT INTO file_index (file_path, file_hash, file_type, chunk_count, user_id, scope) "
        "VALUES (%s, %s, %s, %s, %s, 'personal') "
        "ON CONFLICT (user_id, file_path) DO UPDATE SET "
        "file_hash = EXCLUDED.file_hash, "
        "chunk_count = EXCLUDED.chunk_count, "
        "updated_at = NOW()",
        (file_path, new_hash, ext, chunk_count_written, user_id),
    )

    _log.info(
        "Ingested %s: %d chunks written (planned=%d blocked_high_skips=%d)",
        file_path,
        chunk_count_written,
        len(chunks),
        drops_blocked_high,
    )
    _emit_document_ingested_and_entities(
        logical_path=file_path,
        chunk_count_written=chunk_count_written,
        user_id=user_id,
        text=text,
        ingestion_source_kind="filesystem",
    )

    return IngestResult(file_path=file_path, chunk_count=chunk_count_written)


def ingest_external_document(
    *,
    user_id: str,
    source_id: str,
    external_kind: str,
    external_document_id: str,
    content: str,
    poll_watermark: str,
    stored_source_poll_cursor: str | None,
) -> IngestResult:
    """Ingest OCR text from an external REST source (paperless-ngx v0.1).

    ``poll_watermark`` is the paperless ``added`` ISO timestamp for this row.
    ``stored_source_poll_cursor`` is the current ``sources.poll_cursor`` watermark
    (``None`` when unset). Documents with ``poll_watermark`` strictly before the
    stored cursor are skipped (clock-skew guard).
    """
    if not isinstance(user_id, str) or not user_id:
        raise TypeError("ingest_external_document: user_id is required")
    logical_path = f"paperless://{source_id}/documents/{external_document_id}"
    norm = unicodedata.normalize("NFC", content)
    new_hash = hashlib.sha256(norm.encode("utf-8")).hexdigest()

    if stored_source_poll_cursor and poll_watermark < stored_source_poll_cursor:
        _log.warning(
            "external_ingest_skew_skip logical_path=%s poll_watermark=%r stored=%r",
            logical_path,
            poll_watermark,
            stored_source_poll_cursor,
        )
        return IngestResult(file_path=logical_path, chunk_count=0, skipped=True)

    meta = config.get_metadata_store()
    # SCOPE-EXEMPT: ``external_documents`` is per-user ingest bookkeeping
    # (same isolation model as ``file_index``); ``user_id`` is always the
    # ``sources`` row owner passed in by the poller.
    existing = meta.fetch_one(
        (
            "SELECT content_hash, chunk_count FROM external_documents "
            "WHERE user_id = %s AND source_id = %s::uuid AND external_kind = %s "
            "AND external_id = %s"
        ),
        (user_id, source_id, external_kind, external_document_id),
    )
    if existing and existing.get("content_hash") == new_hash:
        _log.info("Skipping unchanged external document: %s", logical_path)
        with meta.transaction():
            meta.execute(
                (
                    "UPDATE sources SET poll_cursor = %s WHERE id = %s::uuid "
                    "AND (poll_cursor IS NULL OR poll_cursor < %s)"
                ),
                (poll_watermark, source_id, poll_watermark),
            )
        return IngestResult(
            file_path=logical_path,
            chunk_count=0,
            skipped=True,
            advance_external_poll_cursor=True,
        )

    old_chunk_count = int(existing["chunk_count"]) if existing else 0

    chunks = chunk_text(content)
    if not chunks:
        _log.info("No chunks for external document %s", logical_path)
        with meta.transaction():
            meta.execute(
                (
                    "UPDATE sources SET poll_cursor = %s WHERE id = %s::uuid "
                    "AND (poll_cursor IS NULL OR poll_cursor < %s)"
                ),
                (poll_watermark, source_id, poll_watermark),
            )
        return IngestResult(
            file_path=logical_path,
            chunk_count=0,
            skipped=True,
            advance_external_poll_cursor=True,
        )

    vs = config.get_vector_store()
    new_n = len(chunks)
    if old_chunk_count > new_n:
        for j in range(new_n, old_chunk_count):
            pid = external_document_chunk_point_id(
                user_id, source_id, external_kind, external_document_id, j
            )
            try:
                vs.delete(collection="documents", id=pid)
            except Exception:
                _log.warning("external_ingest_orphan_delete_failed point_id=%r", pid, exc_info=True)

    chunk_count_written, drops_blocked_high = _ingest_chunked_text(
        user_id=user_id,
        logical_path=logical_path,
        file_type=".paperless",
        text=content,
        chunks=chunks,
        point_id_for_chunk=lambda i: external_document_chunk_point_id(
            user_id, source_id, external_kind, external_document_id, i
        ),
    )

    has_row = existing is not None

    if drops_blocked_high > 0:
        if has_row:
            # SCOPE-EXEMPT: same table contract as the SELECT above.
            meta.execute(
                (
                    "UPDATE external_documents SET chunk_count = %s, "
                    "logical_path = %s, updated_at = NOW() "
                    "WHERE user_id = %s AND source_id = %s::uuid AND external_kind = %s "
                    "AND external_id = %s"
                ),
                (
                    chunk_count_written,
                    logical_path,
                    user_id,
                    source_id,
                    external_kind,
                    external_document_id,
                ),
            )
            _log.warning(
                "external_ingest_blocked_high_no_hash_advance logical_path=%s drops=%s",
                logical_path,
                drops_blocked_high,
            )
        else:
            _log.warning(
                "external_ingest_blocked_high_before_row logical_path=%s",
                logical_path,
            )
        _emit_document_ingested_and_entities(
            logical_path=logical_path,
            chunk_count_written=chunk_count_written,
            user_id=user_id,
            text=content,
            ingestion_source_kind="external",
        )
        return IngestResult(
            file_path=logical_path,
            chunk_count=chunk_count_written,
            advance_external_poll_cursor=False,
        )

    with meta.transaction():
        meta.execute(
            (
                "INSERT INTO external_documents "
                "(user_id, source_id, external_kind, external_id, "
                "content_hash, chunk_count, logical_path) "
                "VALUES (%s, %s::uuid, %s, %s, %s, %s, %s) "
                "ON CONFLICT (user_id, source_id, external_kind, external_id) DO UPDATE SET "
                "content_hash = EXCLUDED.content_hash, "
                "chunk_count = EXCLUDED.chunk_count, "
                "logical_path = EXCLUDED.logical_path, "
                "updated_at = NOW()"
            ),
            (
                user_id,
                source_id,
                external_kind,
                external_document_id,
                new_hash,
                chunk_count_written,
                logical_path,
            ),
        )
        meta.execute(
            (
                "UPDATE sources SET poll_cursor = %s WHERE id = %s::uuid "
                "AND (poll_cursor IS NULL OR poll_cursor < %s)"
            ),
            (poll_watermark, source_id, poll_watermark),
        )

    _log.info(
        "Ingested external %s: %d chunks (blocked_high_skips=%d)",
        logical_path,
        chunk_count_written,
        drops_blocked_high,
    )
    _emit_document_ingested_and_entities(
        logical_path=logical_path,
        chunk_count_written=chunk_count_written,
        user_id=user_id,
        text=content,
        ingestion_source_kind="external",
    )
    return IngestResult(
        file_path=logical_path,
        chunk_count=chunk_count_written,
        advance_external_poll_cursor=True,
    )


class _PerformanceGuard:
    """Rate limiting and CPU monitoring for bulk ingest."""

    _RATE_LIMIT_PER_MIN = 10
    _CPU_THRESHOLD = 80.0
    _CPU_SUSTAIN_SECS = 30
    _CPU_PAUSE_SECS = 300

    def __init__(self):
        self._timestamps: list[float] = []
        self._cpu_high_since: float | None = None

    def wait_if_needed(self):
        self._enforce_rate_limit()
        self._check_cpu()

    def _enforce_rate_limit(self):
        now = time.monotonic()
        self._timestamps = [t for t in self._timestamps if now - t < 60]
        if len(self._timestamps) >= self._RATE_LIMIT_PER_MIN:
            sleep_for = 60.0 - (now - self._timestamps[0])
            if sleep_for > 0:
                _log.info(
                    "Rate limit: pausing %.1fs (%d files in last minute)",
                    sleep_for,
                    len(self._timestamps),
                )
                time.sleep(sleep_for)
        self._timestamps.append(time.monotonic())

    def _check_cpu(self):
        cpu = psutil.cpu_percent(interval=0.5)
        if cpu > self._CPU_THRESHOLD:
            if self._cpu_high_since is None:
                self._cpu_high_since = time.monotonic()
            elif time.monotonic() - self._cpu_high_since > self._CPU_SUSTAIN_SECS:
                _log.warning(
                    "CPU > %.0f%% for %ds, pausing ingest for %ds",
                    self._CPU_THRESHOLD,
                    self._CPU_SUSTAIN_SECS,
                    self._CPU_PAUSE_SECS,
                )
                time.sleep(self._CPU_PAUSE_SECS)
                self._cpu_high_since = None
        else:
            self._cpu_high_since = None


_INBOX_IGNORE_SUFFIXES = (".tmp", ".part", ".crdownload")
_inbox_poll_last_scan: str = "never"
_poll_stability_failures: dict[str, int] = {}
_inbox_containment_violations: int = 0

_observer: Observer | None = None
_ingest_paths_observer: Observer | None = None
_ingest_paths_scheduled_roots: int = 0
_initial_ingest_scan_enqueued: bool = False
_INBOX_POLL_JOB_ID = "inbox_poll"


def _should_ignore_inbox_basename(name: str) -> bool:
    if name.startswith("."):
        return True
    lower = name.lower()
    return any(lower.endswith(sfx) for sfx in _INBOX_IGNORE_SUFFIXES)


def _is_transient_ingest_error(exc: BaseException) -> bool:
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return True
    if isinstance(exc, OSError) and exc.errno in (
        errno.ECONNREFUSED,
        errno.ETIMEDOUT,
        errno.EHOSTUNREACH,
        errno.ENETUNREACH,
        errno.ECONNRESET,
        errno.ENETDOWN,
    ):
        return True
    return False


def wait_for_stable_file(path: Path, *, budget_ms: int, poll_ms: int = 100) -> bool:
    """Return True when two consecutive (size, mtime_ns) samples match within budget."""
    deadline = time.monotonic() + (budget_ms / 1000.0)
    prev: tuple[int, int] | None = None
    while time.monotonic() < deadline:
        try:
            st = path.stat()
        except FileNotFoundError:
            return False
        sample = (st.st_size, st.st_mtime_ns)
        if prev is not None and sample == prev:
            return True
        prev = sample
        time.sleep(poll_ms / 1000.0)
    return False


def inbox_poll_should_ingest(path: Path, *, user_id: str) -> bool:
    """Poll-mode fast path: skip unchanged indexed files (mtime vs ``file_index.updated_at``)."""
    try:
        st = path.stat()
    except FileNotFoundError:
        return False

    file_path = str(path)
    meta = config.get_metadata_store()
    where_clause, where_params = visible_filter(
        UserContext(user_id=user_id), scope_filter="personal"
    )
    existing = meta.fetch_one(
        f"SELECT updated_at FROM file_index WHERE file_path = %s AND {where_clause}",
        (file_path, *where_params),
    )
    if not existing:
        return True
    updated_at = existing.get("updated_at")
    if updated_at is None:
        return True
    if hasattr(updated_at, "timestamp"):
        db_ts = updated_at.timestamp()
    else:
        return True
    return st.st_mtime > db_ts + 1.0


def _validate_inbox_containment(resolved: Path) -> bool:
    from services.path_containment import _resolved_path_under_root

    global _inbox_containment_violations
    inbox_root = config.get_inbox_path().resolve(strict=False)
    if _resolved_path_under_root(resolved, inbox_root):
        return True
    _log.error(
        "Inbox path containment violation: %s is not under %s",
        resolved,
        inbox_root,
    )
    _inbox_containment_violations += 1
    return False


def _truncate_text(value: str, max_bytes: int) -> str:
    encoded = value.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return value
    trimmed = encoded[: max_bytes - 3].decode("utf-8", errors="ignore")
    return trimmed + "…"


def _quarantine_inbox_file(
    src: Path,
    *,
    user_id: str,
    source: str,
    reason: str,
    exc: BaseException | None = None,
) -> None:
    safe_basename = src.name
    if "/" in safe_basename or "\\" in safe_basename:
        _log.error("Quarantine rejected unsafe basename: %r", safe_basename)
        return

    quarantine_dir = config.get_quarantine_path()
    try:
        quarantine_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc_mk:
        _log.error("Cannot create quarantine directory %s: %s", quarantine_dir, exc_mk)
        return

    ts_ns = time.time_ns()
    dest_name = f"{ts_ns}-{safe_basename}"
    dest = quarantine_dir / dest_name
    sidecar_final = quarantine_dir / f"{dest_name}.error.json"
    sidecar_tmp = src.parent / f"{safe_basename}.error.json.tmp"

    tb_summary = ""
    if exc is not None:
        tb_summary = _truncate_text(
            "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
            2048,
        )

    sidecar = {
        "error": _truncate_text(reason, 512),
        "traceback_summary": tb_summary,
        "ext": src.suffix.lower(),
        "size_bytes": src.stat().st_size if src.exists() else 0,
        "user_id": user_id,
        "ts": datetime.now(timezone.utc).isoformat(),
        "source": source,
    }
    try:
        sidecar_tmp.write_text(json.dumps(sidecar, indent=2), encoding="utf-8")
        if src.exists():
            shutil.move(str(src), str(dest))
        sidecar_tmp.rename(sidecar_final)
        _log.warning("Quarantined inbox file %s → %s (%s)", src, dest, reason)
    except OSError as move_exc:
        _log.error("Quarantine move failed for %s: %s — file left in inbox", src, move_exc)
        if sidecar_tmp.exists():
            try:
                sidecar_tmp.unlink()
            except OSError:
                pass


_IngestSafeSource = Literal["watcher", "poll", "folder"]


def _ingest_file_safe(
    path: str | Path,
    *,
    user_id: str,
    containment_root: Path,
    source: _IngestSafeSource,
) -> IngestResult | None:
    """Containment, size guard, stability wait, then ``ingest_file`` with quarantine on failure."""
    from services.path_containment import _resolved_path_under_root

    resolved = Path(path).resolve(strict=False)
    if not resolved.exists():
        _log.debug("_ingest_file_safe: path gone before ingest: %s", resolved)
        return None

    root = containment_root.expanduser().resolve(strict=False)
    if not _resolved_path_under_root(resolved, root):
        inbox_root = config.get_inbox_path().resolve(strict=False)
        if root == inbox_root:
            _validate_inbox_containment(resolved)
        else:
            _log.error(
                "Folder ingest path containment violation: %s is not under %s",
                resolved,
                root,
            )
        return None

    try:
        size = resolved.stat().st_size
    except OSError:
        _log.debug("_ingest_file_safe: cannot stat %s", resolved)
        return None

    max_bytes = config.get_inbox_max_file_bytes()
    if size > max_bytes:
        _log.warning(
            "Skipping oversized file %s (%d bytes > %d)",
            resolved,
            size,
            max_bytes,
        )
        return None

    budget_ms = config.get_inbox_stability_delay_ms()
    path_key = str(resolved)
    if not wait_for_stable_file(resolved, budget_ms=budget_ms):
        if source == "poll":
            failures = _poll_stability_failures.get(path_key, 0) + 1
            _poll_stability_failures[path_key] = failures
            if failures >= 3:
                _quarantine_inbox_file(
                    resolved,
                    user_id=user_id,
                    source=source,
                    reason="stability_timeout",
                )
                _poll_stability_failures.pop(path_key, None)
            else:
                _log.warning(
                    "Inbox file not stable after %dms (poll attempt %d/3): %s",
                    budget_ms,
                    failures,
                    resolved,
                )
        else:
            _log.warning("File not stable after %dms: %s", budget_ms, resolved)
        return None

    _poll_stability_failures.pop(path_key, None)

    try:
        return ingest_file(str(resolved), user_id=user_id)
    except Exception as exc:
        if _is_transient_ingest_error(exc):
            _log.error(
                "Transient ingest failure for %s (%s) — leaving file in place",
                resolved,
                type(exc).__name__,
            )
            return None
        _log.exception("Terminal ingest failure for %s", resolved)
        _quarantine_inbox_file(
            resolved,
            user_id=user_id,
            source=source,
            reason=f"{type(exc).__name__}: {exc}",
            exc=exc,
        )
        return None


def enqueue_inbox_file(
    path: str | Path,
    *,
    user_id: str,
    source: Literal["watcher", "poll"],
) -> None:
    """Single seam from watcher/poll into ``ingest_file`` (LUM-330)."""
    _ingest_file_safe(
        path,
        user_id=user_id,
        containment_root=config.get_inbox_path(),
        source=source,
    )


def watcher_status() -> dict[str, str]:
    """Public liveness subset for ``GET /healthz`` (no absolute paths)."""
    mode = config.get_inbox_mode()
    owner = config.get_inbox_owner_user_id()
    inbox_path = config.get_inbox_path()
    status: dict[str, str] = {
        "inbox_mode": mode,
        "inbox_watcher": "disabled",
        "ingest_paths_watch": "off",
        "ingest_paths_watch_roots": "0",
    }
    if mode == "poll":
        status["inbox_poll_last_scan"] = _inbox_poll_last_scan
    if not owner or mode == "off":
        pass
    elif not inbox_path.exists():
        status["inbox_watcher"] = "degraded"
    elif mode == "event":
        if _observer is not None and _observer.is_alive():
            status["inbox_watcher"] = "ok"
        else:
            status["inbox_watcher"] = "degraded"
    elif mode == "poll":
        try:
            job = config.get_scheduler().get_job(_INBOX_POLL_JOB_ID)
            status["inbox_watcher"] = "ok" if job is not None else "degraded"
        except Exception:
            status["inbox_watcher"] = "degraded"

    ingest_watch_mode = config.get_ingest_paths_watch_mode()
    ingest_owner = config.get_ingest_paths_owner_user_id()
    status["ingest_paths_watch_roots"] = str(_ingest_paths_scheduled_roots)
    if not ingest_owner or ingest_watch_mode == "off":
        status["ingest_paths_watch"] = "off"
    elif ingest_watch_mode == "event":
        existing_roots = sum(1 for p in config.get_effective_ingest_paths() if Path(p).is_dir())
        if existing_roots == 0:
            status["ingest_paths_watch"] = "degraded"
        elif (
            _ingest_paths_observer is not None
            and _ingest_paths_observer.is_alive()
            and _ingest_paths_scheduled_roots > 0
        ):
            status["ingest_paths_watch"] = "ok"
        else:
            status["ingest_paths_watch"] = "degraded"
    return status


def inbox_operator_status() -> dict[str, str]:
    """Auth-gated superset including resolved ``inbox_path``."""
    out = dict(watcher_status())
    out["inbox_path"] = str(config.get_inbox_path().resolve(strict=False))
    return out


def _run_inbox_poll() -> None:
    global _inbox_poll_last_scan
    owner = config.get_inbox_owner_user_id()
    if not owner:
        return
    inbox = config.get_inbox_path()
    if not inbox.is_dir():
        return
    extractors = config.get_extractors()
    for entry in os.scandir(inbox):
        if not entry.is_file():
            continue
        if _should_ignore_inbox_basename(entry.name):
            continue
        if Path(entry.name).suffix.lower() not in extractors:
            continue
        path = Path(entry.path)
        if not inbox_poll_should_ingest(path, user_id=owner):
            continue
        enqueue_inbox_file(path, user_id=owner, source="poll")
    _inbox_poll_last_scan = datetime.now(timezone.utc).isoformat()


def schedule_inbox_poll() -> None:
    """Register APScheduler inbox poll job (``poll`` mode only)."""
    if config.get_inbox_mode() != "poll":
        return
    if not config.get_inbox_owner_user_id():
        _log.warning("INBOX_OWNER_USER_ID unset — inbox poll job not scheduled")
        return
    scheduler = config.get_scheduler()
    if not scheduler.running:
        _log.info("inbox_poll: scheduler not running yet, skipping")
        return
    interval = config.get_inbox_poll_interval_s()
    scheduler.add_job(
        _run_inbox_poll,
        trigger="interval",
        seconds=interval,
        id=_INBOX_POLL_JOB_ID,
        name="Inbox filesystem poll",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    _log.info("Scheduled inbox poll job every %ds", interval)


def unschedule_inbox_poll() -> None:
    try:
        job = config.get_scheduler().get_job(_INBOX_POLL_JOB_ID)
        if job is not None:
            job.remove()
            _log.info("inbox_poll job removed")
    except Exception as exc:
        _log.warning("inbox_poll unschedule error: %s", exc)


class _InboxHandler(FileSystemEventHandler):
    """Watches inbox directory and routes drops through ``enqueue_inbox_file``."""

    def __init__(self, owner_user_id: str) -> None:
        super().__init__()
        if not isinstance(owner_user_id, str) or not owner_user_id:
            raise TypeError("_InboxHandler: owner_user_id is required (set INBOX_OWNER_USER_ID)")
        self._owner_user_id = owner_user_id

    def _handle_path(self, path: str) -> None:
        if _should_ignore_inbox_basename(Path(path).name):
            return
        ext = Path(path).suffix.lower()
        if ext not in config.get_extractors():
            return
        _log.info("Watcher detected file: %s (owner=%s)", path, self._owner_user_id)
        enqueue_inbox_file(path, user_id=self._owner_user_id, source="watcher")

    def on_created(self, event):
        if event.is_directory:
            return
        self._handle_path(event.src_path)

    def on_moved(self, event):
        if event.is_directory:
            return
        dest = getattr(event, "dest_path", None)
        if not dest:
            return
        self._handle_path(dest)


def start_watcher(*, inbox_path: str | None = None) -> None:
    """Start filesystem observer for ``event`` inbox mode."""
    global _observer
    mode = config.get_inbox_mode()
    if mode != "event":
        return

    resolved = str(inbox_path if inbox_path is not None else config.get_inbox_path())
    if not os.path.isdir(resolved):
        _log.warning("Inbox path %s does not exist, watcher not started", resolved)
        return

    owner = config.get_inbox_owner_user_id()
    if not owner:
        _log.warning(
            "INBOX_OWNER_USER_ID is not set — inbox watcher will NOT start. "
            "Set it to the user_id that owns files dropped into %s.",
            resolved,
        )
        return

    _observer = Observer()
    _observer.schedule(_InboxHandler(owner_user_id=owner), resolved, recursive=True)
    _observer.daemon = True
    _observer.start()
    _log.info("Filesystem watcher started on %s (owner_user_id=%s)", resolved, owner)


def stop_watcher() -> None:
    """Stop the filesystem watcher. Call during shutdown."""
    global _observer
    if _observer is not None:
        _observer.stop()
        _observer.join(timeout=5)
        _observer = None
        _log.info("Filesystem watcher stopped")


def enqueue_ingest_watch_file(
    path: str | Path,
    *,
    user_id: str,
) -> None:
    """Stable-file gate then batch ``ingest_watch_file`` (LUM-397 ingest paths)."""
    resolved = Path(path).resolve(strict=False)
    if not resolved.is_file():
        _log.debug("enqueue_ingest_watch_file: not a file: %s", resolved)
        return

    budget_ms = config.get_inbox_stability_delay_ms()
    if not wait_for_stable_file(resolved, budget_ms=budget_ms):
        _log.warning("Ingest path file not stable after %dms: %s", budget_ms, resolved)
        return

    from services.batch_queue import enqueue

    from services import batch_handlers as _batch_handlers_registered  # noqa: F401

    enqueue(
        user_id=user_id,
        kind="ingest_watch_file",
        payload={"path": str(resolved)},
    )


class _IngestPathHandler(FileSystemEventHandler):
    """Watches one ingest root; routes stable files through ``enqueue_ingest_watch_file``."""

    def __init__(self, owner_user_id: str, root: Path) -> None:
        super().__init__()
        if not isinstance(owner_user_id, str) or not owner_user_id:
            raise TypeError(
                "_IngestPathHandler: owner_user_id is required (set INGEST_PATHS_OWNER_USER_ID)"
            )
        self._owner_user_id = owner_user_id
        self._root = root.expanduser().resolve(strict=False)

    def _handle_path(self, path: str) -> None:
        from services.path_containment import _resolved_path_under_root

        if _should_ignore_inbox_basename(Path(path).name):
            return
        ext = Path(path).suffix.lower()
        if ext not in config.get_extractors():
            return
        resolved = Path(path).resolve(strict=False)
        if not _resolved_path_under_root(resolved, self._root):
            _log.error(
                "Ingest path containment violation: %s is not under %s",
                resolved,
                self._root,
            )
            return
        _log.info(
            "Ingest path watcher detected file: %s (owner=%s)",
            resolved,
            self._owner_user_id,
        )
        enqueue_ingest_watch_file(resolved, user_id=self._owner_user_id)

    def on_created(self, event):
        if event.is_directory:
            return
        self._handle_path(event.src_path)

    def on_moved(self, event):
        if event.is_directory:
            return
        dest = getattr(event, "dest_path", None)
        if not dest:
            return
        self._handle_path(dest)


def start_ingest_path_watchers() -> None:
    """Start per-root ingest path observers and enqueue initial folder scans."""
    global _ingest_paths_observer, _ingest_paths_scheduled_roots

    if not config.get_ingest_paths_watch_enabled():
        return
    if config.get_ingest_paths_watch_mode() != "event":
        return

    owner = config.get_ingest_paths_owner_user_id()
    if not owner:
        _log.warning(
            "INGEST_PATHS_OWNER_USER_ID / INBOX_OWNER_USER_ID unset — "
            "ingest path watchers not started"
        )
        return

    roots = config.get_effective_ingest_paths()
    scheduled = 0
    _ingest_paths_observer = Observer()
    for root_str in roots:
        root = Path(root_str)
        if not root.is_dir():
            _log.warning("Ingest path %s does not exist, watcher not scheduled", root)
            continue
        resolved = str(root.expanduser().resolve(strict=False))
        _ingest_paths_observer.schedule(
            _IngestPathHandler(owner_user_id=owner, root=root),
            resolved,
            recursive=True,
        )
        scheduled += 1

    _ingest_paths_scheduled_roots = scheduled
    if scheduled == 0:
        _ingest_paths_observer = None
        return

    _ingest_paths_observer.daemon = True
    _ingest_paths_observer.start()
    _log.info(
        "Ingest path watchers started on %d root(s) (owner_user_id=%s)",
        scheduled,
        owner,
    )


def enqueue_initial_ingest_scan() -> bool:
    """Enqueue the first bulk folder scan for each configured ingest root.

    Separated from :func:`start_ingest_path_watchers` so the bulk scan can be
    deferred until the embedding model is ready. Embedding every chunk requires
    a live embedder; running the scan against a cold Ollama (model still being
    pulled on a Hub first run) would fail every file and leave the index empty
    with nothing to re-trigger it. Idempotent: only the first successful call
    enqueues, so the startup path and the embedding-readiness retry can both
    invoke it safely.

    Returns ``True`` once the initial scan has been enqueued (now or earlier).
    """
    global _initial_ingest_scan_enqueued
    if _initial_ingest_scan_enqueued:
        return True
    if not config.get_ingest_paths_watch_enabled():
        return False
    owner = config.get_ingest_paths_owner_user_id()
    if not owner:
        return False

    from services.batch_queue import enqueue

    from services import batch_handlers as _batch_handlers_registered  # noqa: F401
    from services.index_bootstrap import mark_scan_queued

    enqueued_any = False
    for root_str in config.get_effective_ingest_paths():
        root = Path(root_str)
        if not root.is_dir():
            continue
        mark_scan_queued()
        enqueue(
            user_id=owner,
            kind="ingest_folder",
            payload={"path": str(root.expanduser().resolve(strict=False))},
        )
        enqueued_any = True

    if enqueued_any:
        _initial_ingest_scan_enqueued = True
        _log.info("Initial library scan enqueued for owner_user_id=%s", owner)
    return _initial_ingest_scan_enqueued


def stop_ingest_path_watchers() -> None:
    """Stop ingest-path filesystem observers. Call during shutdown."""
    global _ingest_paths_observer, _ingest_paths_scheduled_roots
    global _initial_ingest_scan_enqueued
    _initial_ingest_scan_enqueued = False
    if _ingest_paths_observer is not None:
        _ingest_paths_observer.stop()
        _ingest_paths_observer.join(timeout=5)
        _ingest_paths_observer = None
        _ingest_paths_scheduled_roots = 0
        _log.info("Ingest path watchers stopped")


def _count_supported_files(root: Path, supported_exts: set[str]) -> int:
    total = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fname in filenames:
            if Path(fname).suffix.lower() in supported_exts:
                total += 1
    return total


def ingest_folder(folder_path: str, *, user_id: str) -> IngestStats:
    """Walk ``folder_path`` and ingest every supported file as ``user_id``.

    Phase 3: ``user_id`` is keyword-only and required. Bulk ingest of a
    folder must always declare an owner; "default" is no longer
    accepted.
    """
    if not isinstance(user_id, str) or not user_id:
        raise TypeError("ingest_folder: user_id (keyword-only) is required")
    root = Path(folder_path).expanduser().resolve(strict=False)
    total = 0
    ingested = 0
    skipped = 0
    errors = 0
    extractors = config.get_extractors()
    supported_exts = set(extractors.keys())
    guard = _PerformanceGuard()

    from services.index_bootstrap import (
        begin_folder_scan,
        complete_folder_scan,
        report_folder_scan_progress,
    )

    expected_total = _count_supported_files(root, supported_exts)
    begin_folder_scan(total_files=expected_total)
    processed = 0

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fname in filenames:
            fpath = os.path.join(dirpath, fname)
            ext = Path(fname).suffix.lower()
            if ext not in supported_exts:
                continue
            total += 1
            guard.wait_if_needed()
            try:
                result = _ingest_file_safe(
                    fpath,
                    user_id=user_id,
                    containment_root=root,
                    source="folder",
                )
                if result is None:
                    skipped += 1
                elif result.skipped:
                    skipped += 1
                else:
                    ingested += 1
            except Exception:
                _log.exception("Failed to ingest %s", fpath)
                errors += 1
            processed += 1
            if processed == 1 or processed % 5 == 0 or processed == expected_total:
                report_folder_scan_progress(done=processed, total=expected_total)

    complete_folder_scan(
        total_files=total,
        ingested=ingested,
        skipped=skipped,
        errors=errors,
    )
    return IngestStats(total_files=total, ingested=ingested, skipped=skipped, errors=errors)
