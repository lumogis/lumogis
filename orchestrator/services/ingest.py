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
import uuid
from pathlib import Path

import hooks
import psutil
import tiktoken
from events import Event
from models.ingest import IngestResult
from models.ingest import IngestStats
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


def ingest_file(file_path: str, user_id: str = "default") -> IngestResult:
    path = Path(file_path)
    ext = path.suffix.lower()

    extractors = config.get_extractors()
    if ext not in extractors:
        _log.debug("No extractor for %s, skipping %s", ext, file_path)
        return IngestResult(file_path=file_path, chunk_count=0, skipped=True)

    new_hash = _file_hash(file_path)

    meta = config.get_metadata_store()
    existing = meta.fetch_one(
        "SELECT file_hash FROM file_index WHERE file_path = %s AND user_id = %s",
        (file_path, user_id),
    )
    if existing and existing["file_hash"] == new_hash:
        _log.info("Skipping unchanged: %s", file_path)
        return IngestResult(file_path=file_path, chunk_count=0, skipped=True)

    text = extractors[ext](file_path)
    chunks = chunk_text(text)
    if not chunks:
        _log.info("No text extracted from %s", file_path)
        return IngestResult(file_path=file_path, chunk_count=0, skipped=True)

    embedder = config.get_embedder()
    vs = config.get_vector_store()

    vectors = embedder.embed_batch(chunks)
    section_headers = _extract_section_headers(text, chunks)

    for i, (chunk, vec, section) in enumerate(zip(chunks, vectors, section_headers)):
        doc_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{file_path}::chunk-{i}"))
        payload: dict = {
            "file_path": file_path,
            "chunk_index": i,
            "text": chunk,
            "file_type": ext,
            "user_id": user_id,
        }
        if section:
            payload["section_header"] = section
        vs.upsert(
            collection="documents",
            id=doc_id,
            vector=vec,
            payload=payload,
        )

    if existing:
        meta.execute(
            "UPDATE file_index SET file_hash=%s, chunk_count=%s, updated_at=NOW() "
            "WHERE file_path=%s AND user_id=%s",
            (new_hash, len(chunks), file_path, user_id),
        )
    else:
        meta.execute(
            "INSERT INTO file_index (file_path, file_hash, file_type, chunk_count, user_id) "
            "VALUES (%s, %s, %s, %s, %s)",
            (file_path, new_hash, ext, len(chunks), user_id),
        )

    _log.info("Ingested %s: %d chunks", file_path, len(chunks))
    hooks.fire(Event.DOCUMENT_INGESTED, file_path=file_path, chunk_count=len(chunks))

    # Extract entities from document text and store MENTIONED_IN_DOCUMENT relations.
    # Runs synchronously here because ingest_file is already called from a background
    # thread (FastAPI BackgroundTask or the watcher thread).
    try:
        from services.entities import extract_entities
        from services.entities import store_entities

        entities = extract_entities(text)
        if entities:
            store_entities(
                entities,
                evidence_id=file_path,
                evidence_type="DOCUMENT",
                user_id=user_id,
            )
            _log.info("Stored %d entities from document %s", len(entities), file_path)
    except Exception:
        _log.exception("Entity extraction failed for document %s", file_path)

    return IngestResult(file_path=file_path, chunk_count=len(chunks))


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
    """Watches ai-workspace/inbox/ and triggers ingest on new files."""

    def on_created(self, event):
        if event.is_directory:
            return
        path = event.src_path
        ext = Path(path).suffix.lower()
        extractors = config.get_extractors()
        if ext not in extractors:
            return
        time.sleep(2)  # let the file finish writing
        _log.info("Watcher detected new file: %s", path)
        try:
            ingest_file(path)
        except Exception:
            _log.exception("Watcher failed to ingest %s", path)


_observer: Observer | None = None


def start_watcher(inbox_path: str = "/workspace/inbox"):
    """Start watching inbox_path for new files. Call once at startup."""
    global _observer
    if not os.path.isdir(inbox_path):
        _log.warning("Inbox path %s does not exist, watcher not started", inbox_path)
        return
    _observer = Observer()
    _observer.schedule(_InboxHandler(), inbox_path, recursive=True)
    _observer.daemon = True
    _observer.start()
    _log.info("Filesystem watcher started on %s", inbox_path)


def stop_watcher():
    """Stop the filesystem watcher. Call during shutdown."""
    global _observer
    if _observer is not None:
        _observer.stop()
        _observer.join(timeout=5)
        _observer = None
        _log.info("Filesystem watcher stopped")


def ingest_folder(folder_path: str) -> IngestStats:
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
                result = ingest_file(fpath)
                if result.skipped:
                    skipped += 1
                else:
                    ingested += 1
            except Exception:
                _log.exception("Failed to ingest %s", fpath)
                errors += 1

    return IngestStats(total_files=total, ingested=ingested, skipped=skipped, errors=errors)
