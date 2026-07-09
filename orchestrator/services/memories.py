# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""`memories` table writer/reader — the atomic observation store (LUM-291).

Postgres is the system-of-record for the raw memory text; Qdrant holds an
embedding for future recall (LUM-295 — this module only writes it). `user_id`
isolates per user; `bank` isolates per context. `valid_until` is always NULL
on write here (currently valid); the deferred forget/update tools set it.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from datetime import timezone

from models.mcp_write import MemoryRow

import config

_log = logging.getLogger(__name__)

COLLECTION = "memories"


def store_memory(
    *,
    user_id: str,
    bank: str,
    content: str,
    tags: list[str] | None = None,
    metadata: dict | None = None,
    ms=None,
    embedder=None,
    vs=None,
) -> str:
    """Persist a memory row + its embedding. Returns the new ``memory_id``.

    Postgres is the SoR: if the Qdrant embed/upsert fails the row is still
    committed and the id returned (recall degraded, logged WARNING) — mirrors
    the tombstone "partial" honesty rather than rolling back the memory.
    """
    ms = ms or config.get_metadata_store()
    memory_id = uuid.uuid4().hex
    tags = tags or []
    metadata_json = json.dumps(metadata or {})

    ms.execute(
        "INSERT INTO memories (id, user_id, bank, content, tags, metadata) "
        "VALUES (%s, %s, %s, %s, %s, %s::jsonb)",
        (memory_id, user_id, bank, content, tags, metadata_json),
    )

    try:
        embedder = embedder or config.get_embedder()
        vs = vs or config.get_vector_store()
        config.ensure_memories_qdrant_indexes(vs)
        vector = embedder.embed(content)
        point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"memory::{user_id}::{memory_id}"))
        vs.upsert(
            collection=COLLECTION,
            id=point_id,
            vector=vector,
            payload={"memory_id": memory_id, "user_id": user_id, "bank": bank},
        )
    except Exception as exc:  # noqa: BLE001 — SoR is Postgres; degrade recall, never lose the row.
        _log.warning(
            "memory %s stored in Postgres but Qdrant embed/upsert failed "
            "(recall degraded until re-embed): %s",
            memory_id,
            exc,
        )

    return memory_id


def get_memory(memory_id: str, *, user_id: str, ms=None) -> MemoryRow | None:
    """Return the user-scoped memory row, or ``None`` if absent for this user."""
    ms = ms or config.get_metadata_store()
    row = ms.fetch_one(
        "SELECT id, user_id, bank, content, tags, metadata, "
        "valid_from, valid_until, created_at "
        "FROM memories WHERE id = %s AND user_id = %s",
        (memory_id, user_id),
    )
    if row is None:
        return None
    meta = row.get("metadata")
    if isinstance(meta, str):
        meta = json.loads(meta)
    return MemoryRow(
        id=row["id"],
        user_id=row["user_id"],
        bank=row["bank"],
        content=row["content"],
        tags=list(row.get("tags") or []),
        metadata=meta or {},
        valid_from=_as_dt(row.get("valid_from")),
        valid_until=row.get("valid_until"),
        created_at=_as_dt(row.get("created_at")),
    )


def _as_dt(v) -> datetime:
    if isinstance(v, datetime):
        return v
    if v is None:
        return datetime.now(timezone.utc)
    return datetime.fromisoformat(str(v))


def archive_memory(memory_id: str, *, user_id: str, ms=None) -> bool:
    """Soft-archive a memory (LUM-526): set ``valid_until = now()``.

    Returns True iff a row was **newly** archived; False if the memory does not
    exist for the user OR was already archived (idempotent — the
    ``valid_until IS NULL`` guard makes a repeat call a no-op, no timestamp
    bump). The Qdrant point is left in place; recall filters by validity.
    Not-found vs already-archived is the caller's concern (via ``get_memory``).
    """
    ms = ms or config.get_metadata_store()
    row = ms.fetch_one(
        "SELECT valid_until FROM memories WHERE id = %s AND user_id = %s",
        (memory_id, user_id),
    )
    if row is None or row.get("valid_until") is not None:
        return False
    ms.execute(
        "UPDATE memories SET valid_until = now() "
        "WHERE id = %s AND user_id = %s AND valid_until IS NULL",
        (memory_id, user_id),
    )
    return True
