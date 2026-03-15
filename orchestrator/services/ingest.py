"""Ingest pipeline: extract, chunk, embed, store."""

import hashlib
import logging
import os
import uuid
from pathlib import Path

import hooks
import tiktoken
from events import Event
from models.ingest import IngestResult
from models.ingest import IngestStats

import config

_log = logging.getLogger(__name__)

_CHUNK_MAX_TOKENS = 512
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


def ingest_file(file_path: str) -> IngestResult:
    path = Path(file_path)
    ext = path.suffix.lower()

    extractors = config.get_extractors()
    if ext not in extractors:
        _log.debug("No extractor for %s, skipping %s", ext, file_path)
        return IngestResult(file_path=file_path, chunk_count=0, skipped=True)

    new_hash = _file_hash(file_path)

    meta = config.get_metadata_store()
    existing = meta.fetch_one("SELECT file_hash FROM file_index WHERE file_path = %s", (file_path,))
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

    for i, (chunk, vec) in enumerate(zip(chunks, vectors)):
        doc_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{file_path}::chunk-{i}"))
        vs.upsert(
            collection="documents",
            id=doc_id,
            vector=vec,
            payload={
                "file_path": file_path,
                "chunk_index": i,
                "text": chunk,
                "file_type": ext,
            },
        )

    if existing:
        meta.execute(
            "UPDATE file_index SET file_hash=%s, chunk_count=%s, updated_at=NOW() "
            "WHERE file_path=%s",
            (new_hash, len(chunks), file_path),
        )
    else:
        meta.execute(
            "INSERT INTO file_index (file_path, file_hash, file_type, chunk_count) "
            "VALUES (%s, %s, %s, %s)",
            (file_path, new_hash, ext, len(chunks)),
        )

    _log.info("Ingested %s: %d chunks", file_path, len(chunks))
    hooks.fire(Event.DOCUMENT_INGESTED, file_path=file_path, chunk_count=len(chunks))
    return IngestResult(file_path=file_path, chunk_count=len(chunks))


def ingest_folder(folder_path: str) -> IngestStats:
    root = Path(folder_path)
    total = 0
    ingested = 0
    skipped = 0
    errors = 0
    extractors = config.get_extractors()
    supported_exts = set(extractors.keys())

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fname in filenames:
            fpath = os.path.join(dirpath, fname)
            ext = Path(fname).suffix.lower()
            if ext not in supported_exts:
                continue
            total += 1
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
