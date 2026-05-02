# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Deterministic Qdrant point-id helpers (per-user namespaced).

Audit B11: every deterministic Qdrant ``point_id`` that derives from a
user-shared key (a file path, a session id, a CalDAV uid) MUST include
``user_id`` in its uuid5 input string. Without that, two users ingesting
the same path silently overwrite each other's vectors under the same
deterministic point id.

Rule for new surfaces: any new collection whose point ids are computed
from a user-shared key must add a function here. Do NOT scatter another
``uuid5(NAMESPACE_URL, f"...")`` at a call site — that pattern is the
B11 footgun this module exists to retire.

The functions are pure: no I/O, no config, no singletons. They take only
the values they need and return ``str`` (the shape every Qdrant adapter
expects for ``id=``).
"""

from __future__ import annotations

import uuid

_NS = uuid.NAMESPACE_URL


def document_chunk_point_id(user_id: str, file_path: str, chunk_index: int) -> str:
    """Deterministic point id for a chunk in the ``documents`` collection."""
    return str(uuid.uuid5(_NS, f"{user_id}::{file_path}::chunk-{chunk_index}"))


def session_conversation_point_id(user_id: str, session_id: str) -> str:
    """Deterministic point id for a session summary in ``conversations``."""
    return str(uuid.uuid5(_NS, f"session::{user_id}::{session_id}"))


def caldav_signal_id(user_id: str, caldav_uid: str) -> str:
    """Deterministic ``signal_id`` for a CalDAV event.

    The signal_processor (services/signal_processor.py) keys its Qdrant
    ``signals`` point id off ``signal_id`` opaquely; namespacing here is
    sufficient to namespace the downstream point id.
    """
    return str(uuid.uuid5(_NS, f"caldav::{user_id}::{caldav_uid}"))


def note_conversation_point_id(user_id: str, note_id: str) -> str:
    """Deterministic point id for an indexed capture note in ``conversations``.

    Used by ``POST /api/v1/captures/{id}/index`` (Phase 5G) when upserting
    the Qdrant ``conversations`` vector for a promoted capture (plan §12.3).
    Namespace prefix ``capture-note::`` keeps these ids distinct from
    ``session::`` prefixes produced by ``session_conversation_point_id``.
    """
    return str(uuid.uuid5(_NS, f"capture-note::{user_id}::{note_id}"))
