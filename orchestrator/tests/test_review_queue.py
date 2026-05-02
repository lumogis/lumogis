# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Tests for unified review queue endpoints — Pass 4a KG Quality Pipeline.

Test groups:
  1.  Default GET /review-queue — backward compat (no ?source param)
  2.  GET /review-queue?source=all — unified items across all types
  3.  Priority ordering — items sorted priority DESC
  4.  limit parameter respected (checked via sub-query LIMIT logic)
  5.  Empty corpus returns empty items list
  6.  POST /review-queue/decide action=merge calls merge service
  7.  POST /review-queue/decide action=promote sets is_staged=FALSE
  8.  POST /review-queue/decide action=distinct inserts known_distinct_entity_pairs
  9.  Unknown action returns 400
  10. review_decisions row inserted for every successful decide action
  11. Auth: no token required (matches existing admin read pattern)
  12. item not found returns 404

Runs: docker compose -f docker-compose.test.yml run --rm orchestrator pytest
"""

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Test data
# ---------------------------------------------------------------------------

WINNER_ID = str(uuid.uuid4())
LOSER_ID  = str(uuid.uuid4())
RQ_ID     = "42"
VIO_ID    = str(uuid.uuid4())
ENTITY_ID = str(uuid.uuid4())

_TS = datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)


def _rq_row():
    return {
        "id": RQ_ID,
        "reason": "1 tag overlap",
        "created_at": _TS,
        "eid_a": WINNER_ID,
        "name_a": "Alice",
        "type_a": "PERSON",
        "eid_b": LOSER_ID,
        "name_b": "Alice Smith",
        "type_b": "PERSON",
    }


def _staged_row():
    return {
        "entity_id": ENTITY_ID,
        "name": "Bob",
        "entity_type": "PERSON",
        "extraction_quality": 0.45,
        "mention_count": 1,
        "created_at": _TS,
    }


def _vio_row():
    return {
        "violation_id": VIO_ID,
        "rule_name": "person_name_required",
        "severity": "CRITICAL",
        "detail": "Name is empty",
        "entity_id": ENTITY_ID,
        "detected_at": _TS,
    }


def _orphan_row():
    return {
        "violation_id": VIO_ID,
        "entity_id": ENTITY_ID,
        "detected_at": _TS,
        "name": "Ghost",
        "entity_type": "CONCEPT",
        "mention_count": 0,
        "entity_created_at": _TS,
    }


# ---------------------------------------------------------------------------
# App fixture
# ---------------------------------------------------------------------------

def _make_app():
    from routes.admin import router
    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture
def client():
    return TestClient(_make_app(), raise_server_exceptions=False)


def _ms_returning(*side_effects):
    ms = MagicMock()
    ms.fetch_all.side_effect = list(side_effects)
    ms.fetch_one.return_value = None
    return ms


# ---------------------------------------------------------------------------
# 1. Default GET /review-queue — backward compatibility
# ---------------------------------------------------------------------------

class TestDefaultReviewQueue:
    def test_returns_list_not_dict(self, client):
        ms = MagicMock()
        ms.fetch_all.return_value = [
            {
                "id": RQ_ID,
                "reason": "test",
                "created_at": _TS,
                "candidate_a": "Alice",
                "type_a": "PERSON",
                "candidate_b": "Alice S.",
                "type_b": "PERSON",
            }
        ]
        with patch("routes.admin.config.get_metadata_store", return_value=ms):
            resp = client.get("/review-queue")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert data[0]["id"] == RQ_ID
        assert "candidate_a" in data[0]

    def test_returns_empty_list_on_db_error(self, client):
        ms = MagicMock()
        ms.fetch_all.side_effect = RuntimeError("db gone")
        with patch("routes.admin.config.get_metadata_store", return_value=ms):
            resp = client.get("/review-queue")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_does_not_include_source_all_fields(self, client):
        ms = MagicMock()
        ms.fetch_all.return_value = []
        with patch("routes.admin.config.get_metadata_store", return_value=ms):
            resp = client.get("/review-queue")
        data = resp.json()
        assert isinstance(data, list)
        # No 'items' key — backward-compat shape
        assert "items" not in data


# ---------------------------------------------------------------------------
# 2. GET /review-queue?source=all — unified items
# ---------------------------------------------------------------------------

class TestUnifiedReviewQueue:
    def test_returns_items_and_next_cursor(self, client):
        ms = _ms_returning(
            [_rq_row()],         # ambiguous_entity
            [_vio_row()],        # constraint_violation
            [_staged_row()],     # staged_entity
            [_orphan_row()],     # orphan_entity
        )
        with patch("routes.admin.config.get_metadata_store", return_value=ms):
            resp = client.get("/review-queue?source=all")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "next_cursor" in data
        assert data["next_cursor"] is None

    def test_all_four_types_present(self, client):
        ms = _ms_returning(
            [_rq_row()],
            [_vio_row()],
            [_staged_row()],
            [_orphan_row()],
        )
        with patch("routes.admin.config.get_metadata_store", return_value=ms):
            resp = client.get("/review-queue?source=all")
        types = {i["item_type"] for i in resp.json()["items"]}
        assert types == {"ambiguous_entity", "constraint_violation", "staged_entity", "orphan_entity"}

    def test_ambiguous_entity_shape(self, client):
        ms = _ms_returning([_rq_row()], [], [], [])
        with patch("routes.admin.config.get_metadata_store", return_value=ms):
            resp = client.get("/review-queue?source=all")
        items = [i for i in resp.json()["items"] if i["item_type"] == "ambiguous_entity"]
        assert items
        item = items[0]
        assert "candidate_a" in item
        assert "candidate_b" in item
        assert "reason" in item
        assert item["priority"] == 1.0

    def test_staged_entity_shape(self, client):
        ms = _ms_returning([], [], [_staged_row()], [])
        with patch("routes.admin.config.get_metadata_store", return_value=ms):
            resp = client.get("/review-queue?source=all")
        items = [i for i in resp.json()["items"] if i["item_type"] == "staged_entity"]
        assert items
        item = items[0]
        assert "entity" in item
        assert item["priority"] == 0.7

    def test_constraint_violation_shape(self, client):
        ms = _ms_returning([], [_vio_row()], [], [])
        with patch("routes.admin.config.get_metadata_store", return_value=ms):
            resp = client.get("/review-queue?source=all")
        items = [i for i in resp.json()["items"] if i["item_type"] == "constraint_violation"]
        assert items
        item = items[0]
        assert "violation" in item
        assert item["priority"] == 0.9

    def test_orphan_entity_shape(self, client):
        ms = _ms_returning([], [], [], [_orphan_row()])
        with patch("routes.admin.config.get_metadata_store", return_value=ms):
            resp = client.get("/review-queue?source=all")
        items = [i for i in resp.json()["items"] if i["item_type"] == "orphan_entity"]
        assert items
        item = items[0]
        assert "entity" in item
        assert item["priority"] == 0.5


# ---------------------------------------------------------------------------
# 3. Priority ordering
# ---------------------------------------------------------------------------

class TestPriorityOrdering:
    def test_items_sorted_priority_desc(self, client):
        ms = _ms_returning(
            [_rq_row()],      # priority 1.0
            [_vio_row()],     # priority 0.9
            [_staged_row()],  # priority 0.7
            [_orphan_row()],  # priority 0.5
        )
        with patch("routes.admin.config.get_metadata_store", return_value=ms):
            resp = client.get("/review-queue?source=all")
        items = resp.json()["items"]
        priorities = [i["priority"] for i in items]
        assert priorities == sorted(priorities, reverse=True)


# ---------------------------------------------------------------------------
# 5. Empty corpus
# ---------------------------------------------------------------------------

class TestEmptyCorpus:
    def test_empty_items(self, client):
        ms = _ms_returning([], [], [], [])
        with patch("routes.admin.config.get_metadata_store", return_value=ms):
            resp = client.get("/review-queue?source=all")
        assert resp.status_code == 200
        assert resp.json()["items"] == []


# ---------------------------------------------------------------------------
# 6. POST /review-queue/decide — merge
# ---------------------------------------------------------------------------

class TestDecideMerge:
    def test_merge_calls_merge_service(self, client):
        from models.entities import MergeResult

        ms = MagicMock()
        ms.fetch_one.return_value = {"id": RQ_ID, "candidate_a_id": WINNER_ID, "candidate_b_id": LOSER_ID}
        ms.execute.return_value = None

        mock_result = MergeResult(
            winner_id=WINNER_ID, loser_id=LOSER_ID,
            aliases_merged=1, relations_moved=2,
            sessions_updated=0, qdrant_cleaned=True,
        )

        with (
            patch("routes.admin.config.get_metadata_store", return_value=ms),
            patch("services.entity_merge.merge_entities", return_value=mock_result),
        ):
            resp = client.post("/review-queue/decide", json={
                "item_type": "ambiguous_entity",
                "item_id":   RQ_ID,
                "action":    "merge",
            })

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["action"] == "merge"
        assert data["result"]["winner_id"] == WINNER_ID

    def test_merge_inserts_review_decision(self, client):
        from models.entities import MergeResult

        audit_sqls = []
        ms = MagicMock()
        ms.fetch_one.return_value = {"id": RQ_ID, "candidate_a_id": WINNER_ID, "candidate_b_id": LOSER_ID}

        def _exec(sql, params=None):
            if "review_decisions" in (sql or ""):
                audit_sqls.append(sql)
        ms.execute.side_effect = _exec

        mock_result = MergeResult(
            winner_id=WINNER_ID, loser_id=LOSER_ID,
            aliases_merged=0, relations_moved=0,
            sessions_updated=0, qdrant_cleaned=True,
        )

        with (
            patch("routes.admin.config.get_metadata_store", return_value=ms),
            patch("services.entity_merge.merge_entities", return_value=mock_result),
        ):
            client.post("/review-queue/decide", json={
                "item_type": "ambiguous_entity",
                "item_id":   RQ_ID,
                "action":    "merge",
            })

        assert audit_sqls, "review_decisions not inserted on merge"


# ---------------------------------------------------------------------------
# 7. POST /review-queue/decide — promote
# ---------------------------------------------------------------------------

class TestDecidePromote:
    def test_promote_sets_is_staged_false(self, client):
        ms = MagicMock()
        ms.fetch_one.return_value = {"entity_id": ENTITY_ID}
        ms.execute.return_value = None

        update_sqls = []
        def _exec(sql, params=None):
            if "is_staged" in (sql or ""):
                update_sqls.append(sql)
        ms.execute.side_effect = _exec

        with patch("routes.admin.config.get_metadata_store", return_value=ms):
            resp = client.post("/review-queue/decide", json={
                "item_type": "staged_entity",
                "item_id":   ENTITY_ID,
                "action":    "promote",
            })

        assert resp.status_code == 200
        assert update_sqls, "UPDATE with is_staged not called"
        sql = update_sqls[0]
        assert "is_staged = FALSE" in sql or "is_staged=FALSE" in sql.replace(" ", "")

    def test_promote_inserts_review_decision(self, client):
        audit = []
        ms = MagicMock()
        ms.fetch_one.return_value = {"entity_id": ENTITY_ID}
        def _exec(sql, params=None):
            if "review_decisions" in (sql or ""):
                audit.append(sql)
        ms.execute.side_effect = _exec

        with patch("routes.admin.config.get_metadata_store", return_value=ms):
            client.post("/review-queue/decide", json={
                "item_type": "staged_entity",
                "item_id":   ENTITY_ID,
                "action":    "promote",
            })

        assert audit, "review_decisions not inserted on promote"


# ---------------------------------------------------------------------------
# 8. POST /review-queue/decide — distinct
# ---------------------------------------------------------------------------

class TestDecideDistinct:
    def test_distinct_inserts_known_distinct_pair(self, client):
        known_distinct_sqls = []
        ms = MagicMock()
        ms.fetch_one.return_value = {"id": RQ_ID, "candidate_a_id": WINNER_ID, "candidate_b_id": LOSER_ID}

        def _exec(sql, params=None):
            if "known_distinct_entity_pairs" in (sql or ""):
                known_distinct_sqls.append(sql)
        ms.execute.side_effect = _exec

        with patch("routes.admin.config.get_metadata_store", return_value=ms):
            resp = client.post("/review-queue/decide", json={
                "item_type": "ambiguous_entity",
                "item_id":   RQ_ID,
                "action":    "distinct",
            })

        assert resp.status_code == 200
        assert known_distinct_sqls, "known_distinct_entity_pairs INSERT not called"

    def test_distinct_removes_review_queue_row(self, client):
        delete_sqls = []
        ms = MagicMock()
        ms.fetch_one.return_value = {"id": RQ_ID, "candidate_a_id": WINNER_ID, "candidate_b_id": LOSER_ID}

        def _exec(sql, params=None):
            if "DELETE FROM review_queue" in (sql or ""):
                delete_sqls.append(sql)
        ms.execute.side_effect = _exec

        with patch("routes.admin.config.get_metadata_store", return_value=ms):
            client.post("/review-queue/decide", json={
                "item_type": "ambiguous_entity",
                "item_id":   RQ_ID,
                "action":    "distinct",
            })

        assert delete_sqls, "DELETE FROM review_queue not called for distinct action"


# ---------------------------------------------------------------------------
# 9. Unknown action returns 400
# ---------------------------------------------------------------------------

class TestUnknownAction:
    def test_unknown_item_type(self, client):
        resp = client.post("/review-queue/decide", json={
            "item_type": "unknown_type",
            "item_id":   "123",
            "action":    "merge",
        })
        assert resp.status_code == 400

    def test_invalid_action_for_item_type(self, client):
        resp = client.post("/review-queue/decide", json={
            "item_type": "staged_entity",
            "item_id":   ENTITY_ID,
            "action":    "merge",  # not valid for staged_entity
        })
        assert resp.status_code == 400

    def test_valid_action_returns_not_400(self, client):
        ms = MagicMock()
        ms.fetch_one.return_value = {"violation_id": VIO_ID}
        ms.execute.return_value = None

        with patch("routes.admin.config.get_metadata_store", return_value=ms):
            resp = client.post("/review-queue/decide", json={
                "item_type": "constraint_violation",
                "item_id":   VIO_ID,
                "action":    "suppress",
            })
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 10. review_decisions for suppress/dismiss
# ---------------------------------------------------------------------------

class TestReviewDecisionsAllActions:
    @pytest.mark.parametrize("item_type,action", [
        ("constraint_violation", "suppress"),
        ("orphan_entity",        "dismiss"),
    ])
    def test_review_decision_inserted(self, client, item_type, action):
        audit = []
        ms = MagicMock()
        ms.fetch_one.return_value = {"violation_id": VIO_ID}

        def _exec(sql, params=None):
            if "review_decisions" in (sql or ""):
                audit.append(sql)
        ms.execute.side_effect = _exec

        with patch("routes.admin.config.get_metadata_store", return_value=ms):
            resp = client.post("/review-queue/decide", json={
                "item_type": item_type,
                "item_id":   VIO_ID,
                "action":    action,
            })

        assert resp.status_code == 200
        assert audit, f"review_decisions not inserted for {item_type}/{action}"


# ---------------------------------------------------------------------------
# 11. Auth — no token required
# ---------------------------------------------------------------------------

class TestAuthPattern:
    def test_get_review_queue_no_token_needed(self, client):
        ms = MagicMock()
        ms.fetch_all.return_value = []
        with patch("routes.admin.config.get_metadata_store", return_value=ms):
            resp = client.get("/review-queue")
        assert resp.status_code == 200

    def test_get_unified_queue_no_token_needed(self, client):
        ms = _ms_returning([], [], [], [])
        with patch("routes.admin.config.get_metadata_store", return_value=ms):
            resp = client.get("/review-queue?source=all")
        assert resp.status_code == 200

    def test_decide_no_token_needed(self, client):
        ms = MagicMock()
        ms.fetch_one.return_value = {"violation_id": VIO_ID}
        ms.execute.return_value = None
        with patch("routes.admin.config.get_metadata_store", return_value=ms):
            resp = client.post("/review-queue/decide", json={
                "item_type": "constraint_violation",
                "item_id":   VIO_ID,
                "action":    "suppress",
            })
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 12. item not found returns 404
# ---------------------------------------------------------------------------

class TestItemNotFound:
    def test_review_queue_item_not_found(self, client):
        ms = MagicMock()
        ms.fetch_one.return_value = None  # rq_row not found
        with patch("routes.admin.config.get_metadata_store", return_value=ms):
            resp = client.post("/review-queue/decide", json={
                "item_type": "ambiguous_entity",
                "item_id":   "99999",
                "action":    "merge",
            })
        assert resp.status_code == 404

    def test_staged_entity_not_found(self, client):
        ms = MagicMock()
        ms.fetch_one.return_value = None
        with patch("routes.admin.config.get_metadata_store", return_value=ms):
            resp = client.post("/review-queue/decide", json={
                "item_type": "staged_entity",
                "item_id":   ENTITY_ID,
                "action":    "promote",
            })
        assert resp.status_code == 404

    def test_violation_not_found(self, client):
        ms = MagicMock()
        ms.fetch_one.return_value = None
        with patch("routes.admin.config.get_metadata_store", return_value=ms):
            resp = client.post("/review-queue/decide", json={
                "item_type": "constraint_violation",
                "item_id":   VIO_ID,
                "action":    "suppress",
            })
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 13. B9 — review_queue_per_user_approval_scope
#
# Audit B9 (`docs/private/MULTI-USER-AUDIT-RESPONSE.md` row B9): only the
# *originating user* OR an admin may approve a review-queue item.
# Non-admin callers acting on someone else's item must be refused with 403.
# Admins may act on behalf of any user; the admin identity is recorded in
# the `review_decisions.payload` JSONB as `acted_by_user_id` so the audit
# trail distinguishes self-approval from admin-on-behalf approval without
# a schema change.
# ---------------------------------------------------------------------------

_B9_AUTH_SECRET = "b9-review-queue-test-secret-please-32-bytes-min"


@pytest.fixture
def auth_app(monkeypatch):
    """FastAPI app with the real ``auth.auth_middleware`` mounted, so JWT
    Bearer tokens populate ``request.state.user``. Mirrors the pattern in
    ``test_phase3_1_mcp_bearer_wiring.py::mini_app``."""
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_SECRET", _B9_AUTH_SECRET)
    monkeypatch.setenv("LUMOGIS_JWT_SECRET", _B9_AUTH_SECRET)

    from auth import auth_middleware
    from routes.admin import router

    app = FastAPI()
    app.middleware("http")(auth_middleware)
    app.include_router(router)
    return app


@pytest.fixture
def auth_client(auth_app):
    return TestClient(auth_app, raise_server_exceptions=False)


def _mint(user_id: str, role: str) -> str:
    from auth import mint_access_token
    return mint_access_token(user_id=user_id, role=role)


class TestReviewQueuePerUserApprovalScope:
    """B9 — per-item-owner authorization on POST /review-queue/decide."""

    def test_non_admin_owner_can_approve_own_constraint_violation(self, auth_client):
        """Alice owns the violation; Alice approves; 200."""
        ms = MagicMock()
        executes: list[tuple[str, tuple]] = []

        # Two fetch_one calls: (1) ownership lookup → user_id=alice;
        # (2) handler's own existence check → returns the violation row.
        ms.fetch_one.side_effect = [
            {"user_id": "alice"},
            {"violation_id": VIO_ID},
        ]
        ms.execute.side_effect = lambda sql, params=None: executes.append((sql, params))

        token = _mint("alice", "user")
        with patch("routes.admin.config.get_metadata_store", return_value=ms):
            resp = auth_client.post(
                "/review-queue/decide",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "item_type": "constraint_violation",
                    "item_id":   VIO_ID,
                    "action":    "suppress",
                },
            )

        assert resp.status_code == 200, resp.text
        decision_inserts = [p for sql, p in executes if "review_decisions" in (sql or "")]
        assert decision_inserts, "review_decisions row not inserted"
        owner_in_decision = decision_inserts[0][0]
        payload_json = decision_inserts[0][4]
        assert owner_in_decision == "alice"
        assert "acted_by_user_id" not in json.loads(payload_json), (
            "self-approval must NOT add acted_by_user_id to the payload"
        )

    def test_non_admin_cannot_approve_another_users_item(self, auth_client):
        """Bob tries to suppress Alice's violation → 403."""
        ms = MagicMock()
        ms.fetch_one.return_value = {"user_id": "alice"}

        token = _mint("bob", "user")
        with patch("routes.admin.config.get_metadata_store", return_value=ms):
            resp = auth_client.post(
                "/review-queue/decide",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "item_type": "constraint_violation",
                    "item_id":   VIO_ID,
                    "action":    "suppress",
                },
            )

        assert resp.status_code == 403
        assert "different user" in resp.text
        assert ms.execute.call_count == 0, (
            "no DB writes should occur on an authorization failure"
        )

    def test_non_admin_cannot_act_on_behalf_via_body_user_id(self, auth_client):
        """Bob passes ``user_id: alice`` in the body trying to spoof
        admin-on-behalf semantics → 403, no DB lookup occurs."""
        ms = MagicMock()

        token = _mint("bob", "user")
        with patch("routes.admin.config.get_metadata_store", return_value=ms):
            resp = auth_client.post(
                "/review-queue/decide",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "item_type": "constraint_violation",
                    "item_id":   VIO_ID,
                    "action":    "suppress",
                    "user_id":   "alice",
                },
            )

        assert resp.status_code == 403
        assert "act on behalf" in resp.text
        assert ms.fetch_one.call_count == 0, (
            "spoofed body.user_id must short-circuit before any DB lookup"
        )

    def test_admin_can_approve_any_users_item_and_records_acted_by(self, auth_client):
        """Admin Carol suppresses Alice's violation → 200; the
        `review_decisions` row is scoped to Alice (the originating user)
        and the JSONB payload carries `acted_by_user_id: carol`."""
        ms = MagicMock()
        executes: list[tuple[str, tuple]] = []
        ms.fetch_one.side_effect = [
            {"user_id": "alice"},
            {"violation_id": VIO_ID},
        ]
        ms.execute.side_effect = lambda sql, params=None: executes.append((sql, params))

        token = _mint("carol", "admin")
        with patch("routes.admin.config.get_metadata_store", return_value=ms):
            resp = auth_client.post(
                "/review-queue/decide",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "item_type": "constraint_violation",
                    "item_id":   VIO_ID,
                    "action":    "suppress",
                },
            )

        assert resp.status_code == 200, resp.text
        decision_inserts = [p for sql, p in executes if "review_decisions" in (sql or "")]
        assert decision_inserts
        owner_in_decision = decision_inserts[0][0]
        payload = json.loads(decision_inserts[0][4])
        assert owner_in_decision == "alice", (
            "review_decisions.user_id must be the originating user, not the admin"
        )
        assert payload.get("acted_by_user_id") == "carol", (
            "admin-on-behalf must be recorded in payload.acted_by_user_id"
        )

    def test_admin_target_user_id_body_field_is_ignored_when_item_disagrees(
        self, auth_client
    ):
        """Admin Carol passes body.user_id='bob' but the item belongs to
        alice. Originating user wins (DB writes scope to alice); the
        decision row attributes Carol via acted_by_user_id."""
        ms = MagicMock()
        executes: list[tuple[str, tuple]] = []
        ms.fetch_one.side_effect = [
            {"user_id": "alice"},
            {"violation_id": VIO_ID},
        ]
        ms.execute.side_effect = lambda sql, params=None: executes.append((sql, params))

        token = _mint("carol", "admin")
        with patch("routes.admin.config.get_metadata_store", return_value=ms):
            resp = auth_client.post(
                "/review-queue/decide",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "item_type": "constraint_violation",
                    "item_id":   VIO_ID,
                    "action":    "suppress",
                    "user_id":   "bob",
                },
            )

        assert resp.status_code == 200
        decision_inserts = [p for sql, p in executes if "review_decisions" in (sql or "")]
        assert decision_inserts
        assert decision_inserts[0][0] == "alice"

    def test_unauthenticated_request_returns_401(self, auth_client):
        """No Bearer token under AUTH_ENABLED=true → 401 from the auth
        middleware, never reaches the handler."""
        ms = MagicMock()
        with patch("routes.admin.config.get_metadata_store", return_value=ms):
            resp = auth_client.post(
                "/review-queue/decide",
                json={
                    "item_type": "constraint_violation",
                    "item_id":   VIO_ID,
                    "action":    "suppress",
                },
            )
        assert resp.status_code == 401
        assert ms.fetch_one.call_count == 0

    def test_item_not_found_returns_404_before_authorization_check(
        self, auth_client
    ):
        """Ownership lookup misses → 404. The 404 leaks no information
        about who owns the item (the lookup returned None)."""
        ms = MagicMock()
        ms.fetch_one.return_value = None

        token = _mint("alice", "user")
        with patch("routes.admin.config.get_metadata_store", return_value=ms):
            resp = auth_client.post(
                "/review-queue/decide",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "item_type": "constraint_violation",
                    "item_id":   VIO_ID,
                    "action":    "suppress",
                },
            )
        assert resp.status_code == 404
        assert ms.execute.call_count == 0

    def test_invalid_action_check_runs_before_db_lookup(self, auth_client):
        """Unknown item_type → 400, no DB call (defence-in-depth: a
        malformed request must not even cost a row lookup)."""
        ms = MagicMock()

        token = _mint("alice", "user")
        with patch("routes.admin.config.get_metadata_store", return_value=ms):
            resp = auth_client.post(
                "/review-queue/decide",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "item_type": "unknown_type",
                    "item_id":   VIO_ID,
                    "action":    "suppress",
                },
            )
        assert resp.status_code == 400
        assert ms.fetch_one.call_count == 0
        assert ms.execute.call_count == 0
