# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""``services/entity_browser.py`` — row → response mapping and privacy nulling (LUM-161).

The SQL (kind mapping, distinct-item counts, the viewer-openable mention rule,
non-UUID ids) is proven against real Postgres in
``tests/integration/test_entity_browser_live.py``; a SQL-substring test here
would pass on a query that is wrong.
"""

from __future__ import annotations

from datetime import datetime
from datetime import timezone
from unittest.mock import patch

import pytest
from auth import UserContext
from services.entity_browser import EntityStoreUnavailable

import config as _config
from services import entity_browser

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
EID = "11111111-1111-4111-9111-111111111111"


class _Store:
    def __init__(self, items=None, counts=None, shared_ids=None, fail=None):
        self.items = items or []
        self.counts = counts or []
        self.shared_ids = shared_ids or []
        self.fail = fail or set()
        self.item_params = None

    def fetch_all(self, sql, params=None):
        s = " ".join(sql.split())
        if "DISTINCT published_from" in s:
            if "shared_ids" in self.fail:
                raise RuntimeError("db down")
            return [{"published_from": x} for x in self.shared_ids]
        if "GROUP BY 1" in s:
            if "list" in self.fail:
                raise RuntimeError("db down")
            return self.counts
        if "LEFT JOIN LATERAL" in s:
            if "list" in self.fail:
                raise RuntimeError("db down")
            self.item_params = params
            return self.items
        if s.startswith("WITH m AS"):
            if "mentions" in self.fail:
                raise RuntimeError("db down")
            return self.mention_rows if "LIMIT %s OFFSET %s" in s else self.mention_counts
        raise AssertionError(s)  # pragma: no cover

    mention_rows: list = []
    mention_counts: list = []

    def fetch_one(self, sql, params=None):
        s = " ".join(sql.split())
        if "allows_shared" in s:
            return {"allows_shared": True}
        if "entity_id::text" in s:
            return self.head
        raise AssertionError(s)  # pragma: no cover

    head = None


@pytest.fixture
def store():
    s = _Store()
    _config._instances["metadata_store"] = s
    yield s
    _config._instances.clear()


def _bob():
    return UserContext(user_id="bob", allows_shared=True)


def _item_row(**over):
    row = {
        "entity_id": EID,
        "name": "Dr. Meier",
        "entity_type": "PERSON",
        "aliases": ["Meier"],
        "alias_count": 1,
        "scope": "shared",
        "user_id": "alice",
        "published_from": "src",
        "kind": "person",
        "own_mentions": 0,  # count(*) over a skipped lateral is 0, never NULL
        "own_last_mentioned_at": None,
    }
    row.update(over)
    return row


def test_list_hides_mentions_for_non_owned_rows(store):
    store.items = [_item_row()]
    store.counts = [{"kind": "person", "n": 1}]
    out = entity_browser.list_entities(_bob())
    item = out.entities[0]
    assert item.mention_count is None
    assert item.last_mentioned_at is None
    assert item.is_owner is False
    assert item.share_status == "shared"
    assert out.counts.person == 1 and out.total == 1


def test_list_owned_row_reports_distinct_item_count(store):
    store.items = [
        _item_row(
            scope="personal",
            user_id="bob",
            published_from=None,
            own_mentions=3,
            own_last_mentioned_at=NOW,
        )
    ]
    store.counts = [{"kind": "person", "n": 1}]
    item = entity_browser.list_entities(_bob()).entities[0]
    assert item.mention_count == 3
    assert item.last_mentioned_at == NOW
    assert item.is_owner is True


def test_list_owned_zero_mentions_is_zero_not_none(store):
    store.items = [_item_row(scope="personal", user_id="bob", published_from=None, own_mentions=0)]
    assert entity_browser.list_entities(_bob()).entities[0].mention_count == 0


def test_list_total_follows_kind_filter(store):
    store.items = []
    store.counts = [{"kind": "person", "n": 2}, {"kind": "org", "n": 5}]
    assert entity_browser.list_entities(_bob()).total == 7
    assert entity_browser.list_entities(_bob(), kind="org").total == 5


def test_list_param_order_matches_sql_text_order(store):
    store.items = []
    entity_browser.list_entities(_bob(), kind="org", limit=7, offset=3)
    # lateral caller, visibility (me), Hide (me, LUM-966), collapse caller, kind,
    # limit, offset. In production the first four are always user.user_id, so only
    # the tail's order is distinguishable here; the live suite seeds two members.
    assert store.item_params == ("bob", "bob", "bob", "bob", "org", 7, 3)


def test_list_rejects_unknown_enum_values(store):
    for kw in ({"sort": "name; DROP TABLE"}, {"kind": "place"}, {"shared": "maybe"}):
        with pytest.raises(ValueError):
            entity_browser.list_entities(_bob(), **kw)


def test_list_share_ids_outage_is_503(store):
    store.fail = {"shared_ids"}
    store.items = [_item_row()]
    with pytest.raises(EntityStoreUnavailable):
        entity_browser.list_entities(_bob())
    # …while the default (card / search) callers keep the old swallow-and-continue.
    assert entity_browser._fetch_shared_entity_source_ids("bob") == set()


def test_list_query_failure_is_503(store):
    store.fail = {"list"}
    with pytest.raises(EntityStoreUnavailable):
        entity_browser.list_entities(_bob())


def test_mention_titles():
    t = entity_browser._mention_title
    assert (
        t("document", {"display_name": "Invoice 42", "file_path": "paperless://s/documents/42"})
        == "Invoice 42"
    )
    assert t("document", {"display_name": None, "file_path": "/data/a/report.pdf"}) == "report.pdf"
    assert t("chat", {"summary": "  "}) == "Chat"
    assert t("chat", {"summary": None}) == "Chat"
    assert t("ai_tool", {}) == "Added by an AI tool"
    assert len(t("chat", {"summary": "x" * 200})) == 120


def test_mentions_none_when_not_visible(store):
    store.head = None
    assert entity_browser.list_mentions(_bob(), EID) is None


def test_mentions_head_failure_is_none_body_failure_is_503(store):
    with patch.object(entity_browser, "resolve_viewable_entity", side_effect=RuntimeError("x")):
        assert entity_browser.list_mentions(_bob(), EID) is None
    store.head = {"entity_id": EID, "user_id": "alice", "published_from": None}
    store.fail = {"mentions"}
    with pytest.raises(EntityStoreUnavailable):
        entity_browser.list_mentions(_bob(), EID)


def test_mentions_maps_rows_and_counts(store):
    store.head = {"entity_id": EID, "user_id": "bob", "published_from": None}
    store.mention_counts = [{"kind": "document", "n": 1}, {"kind": "ai_tool", "n": 1}]
    store.mention_rows = [
        {
            "kind": "document",
            "ref_id": "7",
            "display_name": None,
            "file_path": "/data/a/report.pdf",
            "summary": None,
            "mentioned_at": NOW,
        },
        {
            "kind": "ai_tool",
            "ref_id": "mcp:add_entity:ab",
            "display_name": None,
            "file_path": None,
            "summary": None,
            "mentioned_at": NOW,
        },
    ]
    out = entity_browser.list_mentions(_bob(), EID)
    assert out.total == 2
    assert out.counts.document == 1 and out.counts.ai_tool == 1 and out.counts.chat == 0
    assert [m.title for m in out.mentions] == ["report.pdf", "Added by an AI tool"]


def test_ai_tool_arm_is_owner_only(store):
    me = _bob()
    own_sql, _ = entity_browser._mention_arms(me, source_id="s", owner="bob", is_owner_view=True)
    other_sql, other_params = entity_browser._mention_arms(
        me, source_id="s", owner="alice", is_owner_view=False
    )
    assert "'ai_tool'" in own_sql
    assert "'ai_tool'" not in other_sql
    assert "MEMORY" not in other_sql
    assert other_params.count("alice") == 2  # document + chat arm only


def test_viewer_resolves_allows_shared_once(store):
    from routes.api_v1.kg import _viewer

    store.head = {"entity_id": EID, "user_id": "alice", "published_from": None}
    store.mention_counts = []
    store.mention_rows = []

    class _Req:
        class state:  # noqa: N801
            user = UserContext(user_id="bob", is_authenticated=True, role="user")

    with (
        patch("routes.api_v1.kg.get_user", return_value=_Req.state.user),
        patch("services.users.get_allows_shared", return_value=True) as gas,
    ):
        viewer = _viewer(_Req())
        entity_browser.list_mentions(viewer, EID)
    assert gas.call_count == 1


def test_B1_every_mention_arm_carries_relation_id(store):
    """The mover (LUM-965) addresses mentions by ``entity_relations.id``."""
    me = _bob()
    own_sql, _ = entity_browser._mention_arms(me, source_id="s", owner="bob", is_owner_view=True)
    assert own_sql.count("er.id AS relation_id") == 3  # document + chat + ai_tool
    other_sql, _ = entity_browser._mention_arms(
        me, source_id="s", owner="alice", is_owner_view=False
    )
    assert other_sql.count("er.id AS relation_id") == 2  # no ai_tool arm for a viewer


class _SinceStore(_Store):
    def __init__(self, rows=None, fail=False):
        super().__init__()
        self.rows = rows or []
        self.fail_since = fail
        self.since_sql = None
        self.since_params = None

    def fetch_all(self, sql, params=None):
        s = " ".join(sql.split())
        if s.startswith("WITH m AS") and "relation_id <> ALL" in s:
            if self.fail_since:
                raise RuntimeError("db down")
            self.since_sql, self.since_params = s, params
            return self.rows
        return super().fetch_all(sql, params)


def test_B2_list_mentions_since_filters_by_time_and_excluded_ids():
    s = _SinceStore(
        rows=[
            {
                "kind": "document",
                "ref_id": "7",
                "display_name": None,
                "file_path": "/data/a/report.pdf",
                "summary": None,
                "mentioned_at": NOW,
                "relation_id": 41,
            }
        ]
    )
    s.head = {"entity_id": EID, "user_id": "bob", "published_from": None}
    _config._instances["metadata_store"] = s
    try:
        out = entity_browser.list_mentions_since(
            _bob(), EID, after=NOW, exclude_relation_ids=[5, 6]
        )
    finally:
        _config._instances.clear()
    assert [(m.relation_id, m.title, m.kind) for m in out] == [(41, "report.pdf", "document")]
    assert "mentioned_at > %s" in s.since_sql and "relation_id <> ALL(%s)" in s.since_sql
    assert s.since_params[-3:] == (NOW, [5, 6], 200)


def test_B2_list_mentions_since_is_owner_only_and_store_failure_is_503():
    s = _SinceStore(fail=True)
    _config._instances["metadata_store"] = s
    try:
        s.head = {"entity_id": EID, "user_id": "alice", "published_from": None}
        out = entity_browser.list_mentions_since(_bob(), EID, after=NOW, exclude_relation_ids=[])
        assert out == []
        assert s.since_sql is None  # another member's entry: no query at all
        s.head = {"entity_id": EID, "user_id": "bob", "published_from": None}
        with pytest.raises(EntityStoreUnavailable):
            entity_browser.list_mentions_since(_bob(), EID, after=NOW, exclude_relation_ids=[])
    finally:
        _config._instances.clear()
