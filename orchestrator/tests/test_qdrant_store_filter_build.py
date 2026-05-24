# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Regression: :class:`adapters.qdrant_store.QdrantStore` must honour ``should`` filters.

Default :func:`visibility.visible_qdrant_filter` emits a top-level ``should`` (household
union). Ignoring it previously produced ``Filter(must=[])``, which is not a restrictive
filter in Qdrant — a cross-tenant isolation failure for semantic search / CONTEXT_BUILDING.
"""

from __future__ import annotations

from adapters.qdrant_store import QdrantStore
from auth import UserContext
from qdrant_client.models import FieldCondition
from qdrant_client.models import Filter
from qdrant_client.models import MatchAny
from qdrant_client.models import MatchValue
from visibility import visible_qdrant_filter


def test_build_filter_default_visibility_uses_should_or_union() -> None:
    d = visible_qdrant_filter(UserContext(user_id="alice"))
    flt = QdrantStore._build_filter(d)
    assert flt.should is not None
    assert len(flt.should) == 2
    assert flt.must is None
    # Branch 1: personal + user_id
    b0 = flt.should[0]
    assert isinstance(b0, Filter)
    assert b0.must is not None and len(b0.must) == 2
    assert b0.must[0] == FieldCondition(key="scope", match=MatchValue(value="personal"))
    assert b0.must[1] == FieldCondition(key="user_id", match=MatchValue(value="alice"))
    # Branch 2: shared or system
    b1 = flt.should[1]
    assert isinstance(b1, Filter)
    assert b1.must is not None and len(b1.must) == 1
    assert b1.must[0] == FieldCondition(key="scope", match=MatchAny(any=["shared", "system"]))


def test_build_filter_personal_scope_top_level_must() -> None:
    d = visible_qdrant_filter(UserContext(user_id="bob"), scope_filter="personal")
    flt = QdrantStore._build_filter(d)
    assert flt.should is None
    assert flt.must is not None and len(flt.must) == 2


def test_build_filter_entity_merge_style_must() -> None:
    flt = QdrantStore._build_filter({"must": [{"key": "entity_id", "match": {"value": "e-1"}}]})
    assert flt.must is not None and len(flt.must) == 1
    assert flt.must[0] == FieldCondition(key="entity_id", match=MatchValue(value="e-1"))
