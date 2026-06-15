# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Activate the embedder + Qdrant collections when Ollama becomes ready."""

from __future__ import annotations

import logging
import os
from typing import Any

import config

_log = logging.getLogger(__name__)

_EMBED_COLLECTIONS = ("documents", "conversations", "entities", "signals")


def try_activate_embedding(app_state: Any) -> bool:
    """Probe Ollama and initialise Qdrant collections; set ``embedding_ready`` when done."""
    if getattr(app_state, "embedding_ready", False):
        return True

    embedder = config.get_embedder()
    if not embedder.ping():
        return False

    try:
        vs = config.get_vector_store()
        dim = embedder.vector_size
        for coll in _EMBED_COLLECTIONS:
            vs.create_collection(coll, dim)
            vs.ensure_payload_index(coll, "scope")
            vs.ensure_payload_index(coll, "user_id")
        app_state.embedding_ready = True
        _log.info(
            "Embedder ready (%s) — collections + payload indexes initialised",
            os.environ.get("EMBEDDING_MODEL", "nomic-embed-text"),
        )
        return True
    except Exception as exc:
        _log.warning(
            "Embedding activation failed (%s). Will retry while Ollama is starting.",
            exc,
        )
        return False
