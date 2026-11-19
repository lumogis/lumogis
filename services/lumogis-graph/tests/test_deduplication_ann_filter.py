# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""The graph service's dedup ANN search keeps both filter clauses (LUM-938).

``quality.deduplication._build_ann_blocks`` is a forked copy of Core's
``orchestrator/services/deduplication.py``. Core filters every Qdrant ANN search
on ``user_id`` (the owner) **and** ``scope == "personal"`` (so dedup never pairs
a personal entity with a shared or system projection). The graph copy lost the
scope clause. This file pins both clauses at the call level.

The ANN branch of the graph copy cannot run in a shipped mode today: the graph
tree ships no vector-store or embedder adapter, so ``config.get_embedder()`` and
``config.get_vector_store()`` return ``None`` and the guard skips ANN. This is
the regression guard that has to exist before the copies are converged (LUM-938
"Later", FU-1 §2.5.9).

What a mock cannot show: that Qdrant excludes a point with no ``scope`` payload
key. That is server-side behaviour, so it is not asserted here.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock
from unittest.mock import patch

from quality.deduplication import _build_ann_blocks

# Deliberately not "default": a hard-coded default owner must not satisfy the test.
USER_ID = "alice"

_ENTITY_A = {
    "entity_id": str(uuid.uuid4()),
    "name": "Alice Smith",
    "entity_type": "PERSON",
    "aliases": ["A. Smith"],
    "mention_count": 3,
}
_ENTITY_B = {
    "entity_id": str(uuid.uuid4()),
    "name": "Alice Smyth",
    "entity_type": "PERSON",
    "aliases": [],
    "mention_count": 1,
}


def _filters(vs: MagicMock) -> list[dict]:
    """Return the ``filter`` keyword of every ``vs.search`` call."""
    filters = []
    for call in vs.search.call_args_list:
        assert "filter" in call.kwargs, f"search called without a filter: {call}"
        filters.append(call.kwargs["filter"])
    return filters


def test_every_ann_search_filters_on_owner_and_personal_scope():
    embedder = MagicMock()
    embedder.embed.return_value = [0.1] * 768
    vs = MagicMock()
    vs.search.return_value = []

    with patch("quality.deduplication.config") as cfg:
        cfg.get_embedder.return_value = embedder
        _build_ann_blocks([_ENTITY_A, _ENTITY_B], vs, USER_ID)

    # One search per entity. Zero would make the loop below pass vacuously.
    assert vs.search.call_count == 2
    for call in vs.search.call_args_list:
        assert call.kwargs["collection"] == "entities"
    for f in _filters(vs):
        must = f["must"]
        assert {"key": "user_id", "match": {"value": USER_ID}} in must
        assert {"key": "scope", "match": {"value": "personal"}} in must


def test_ann_search_skipped_when_embedder_unavailable():
    vs = MagicMock()

    with patch("quality.deduplication.config") as cfg:
        cfg.get_embedder.return_value = None
        result = _build_ann_blocks([_ENTITY_A, _ENTITY_B], vs, USER_ID)

    assert result == (set(), {})
    vs.search.assert_not_called()


def test_ann_search_skipped_when_vector_store_unavailable():
    embedder = MagicMock()

    with patch("quality.deduplication.config") as cfg:
        cfg.get_embedder.return_value = embedder
        result = _build_ann_blocks([_ENTITY_A, _ENTITY_B], None, USER_ID)

    assert result == (set(), {})
    embedder.embed.assert_not_called()
