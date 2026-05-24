# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Ingest pipeline: extract, chunk, embed, store.

Includes performance guardrails (rate limiting, CPU monitoring) and a
filesystem watcher for real-time ingestion of files dropped into the inbox.
"""

import hashlib
import logging
import os
import re
import time
import unicodedata
from collections.abc import Callable
from datetime import datetime
from datetime import timezone
from pathlib import Path

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
        return IngestResult(file_path=logical_path, chunk_count=chunk_count_written)

    with meta.transaction():
        meta.execute(
            (
                "INSERT INTO external_documents "
                "(user_id, source_id, external_kind, external_id, content_hash, chunk_count, logical_path) "
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


class _InboxHandler(FileSystemEventHandler):
    """Watches ai-workspace/inbox/ and triggers ingest on new files.

    Phase 3: every watcher-ingested file is attributed to the
    operator-configured ``INBOX_OWNER_USER_ID`` (resolved at watcher
    start). Files dropped into the shared inbox have no inherent
    "owner", so the operator must opt one in explicitly — there is no
    safe default.
    """

    def __init__(self, owner_user_id: str) -> None:
        super().__init__()
        if not isinstance(owner_user_id, str) or not owner_user_id:
            raise TypeError("_InboxHandler: owner_user_id is required (set INBOX_OWNER_USER_ID)")
        self._owner_user_id = owner_user_id

    def on_created(self, event):
        if event.is_directory:
            return
        path = event.src_path
        ext = Path(path).suffix.lower()
        extractors = config.get_extractors()
        if ext not in extractors:
            return
        time.sleep(2)
        _log.info("Watcher detected new file: %s (owner=%s)", path, self._owner_user_id)
        try:
            ingest_file(path, user_id=self._owner_user_id)
        except Exception:
            _log.exception("Watcher failed to ingest %s", path)


_observer: Observer | None = None


def start_watcher(inbox_path: str = "/workspace/inbox") -> None:
    """Start watching inbox_path for new files. Call once at startup.

    Reads ``INBOX_OWNER_USER_ID`` from the environment and refuses to
    start if it is unset — better to drop ingest than to silently
    pollute another user's index.
    """
    global _observer
    if not os.path.isdir(inbox_path):
        _log.warning("Inbox path %s does not exist, watcher not started", inbox_path)
        return

    owner = os.environ.get("INBOX_OWNER_USER_ID", "").strip()
    if not owner:
        _log.warning(
            "INBOX_OWNER_USER_ID is not set — inbox watcher will NOT start. "
            "Set it to the user_id that owns files dropped into %s.",
            inbox_path,
        )
        return

    _observer = Observer()
    _observer.schedule(_InboxHandler(owner_user_id=owner), inbox_path, recursive=True)
    _observer.daemon = True
    _observer.start()
    _log.info("Filesystem watcher started on %s (owner_user_id=%s)", inbox_path, owner)


def stop_watcher():
    """Stop the filesystem watcher. Call during shutdown."""
    global _observer
    if _observer is not None:
        _observer.stop()
        _observer.join(timeout=5)
        _observer = None
        _log.info("Filesystem watcher stopped")


def ingest_folder(folder_path: str, *, user_id: str) -> IngestStats:
    """Walk ``folder_path`` and ingest every supported file as ``user_id``.

    Phase 3: ``user_id`` is keyword-only and required. Bulk ingest of a
    folder must always declare an owner; "default" is no longer
    accepted.
    """
    if not isinstance(user_id, str) or not user_id:
        raise TypeError("ingest_folder: user_id (keyword-only) is required")
    root = Path(folder_path)
    total = 0
    ingested = 0
    skipped = 0
    errors = 0
    extractors = config.get_extractors()
    supported_exts = set(extractors.keys())
    guard = _PerformanceGuard()

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
                result = ingest_file(fpath, user_id=user_id)
                if result.skipped:
                    skipped += 1
                else:
                    ingested += 1
            except Exception:
                _log.exception("Failed to ingest %s", fpath)
                errors += 1

    return IngestStats(total_files=total, ingested=ingested, skipped=skipped, errors=errors)
