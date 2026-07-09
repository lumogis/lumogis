# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Live two-user conversation share + Qdrant retrieval (LUM-582 P1).

Proves ``project_session`` with an edited ``shared_summary`` against real
Postgres + Qdrant: member B lists/reads the shared projection and retrieves
the household-facing text from the ``conversations`` collection — the gap the
in-memory ``test_conversation_share.py`` cannot cover.

Skips when the real stack is unreachable (plain host unit run). Primary gate:
``make compose-test``.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager

import pytest
from auth import UserContext
from fastapi.testclient import TestClient

import config
from services import conversations as conv
from services import projection as proj

pytestmark = pytest.mark.integration

COLLECTION = "conversations"


def _vector_dim() -> int:
    try:
        return int(config.get_embedder().vector_size)
    except Exception:
        return 768


@pytest.fixture
def auth_env(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_SECRET", "conv-share-live-access-secret")
    monkeypatch.setenv("LUMOGIS_JWT_REFRESH_SECRET", "conv-share-live-refresh-secret")
    monkeypatch.setenv("ACCESS_TOKEN_TTL_SECONDS", "900")
    monkeypatch.setenv("REFRESH_TOKEN_TTL_SECONDS", "3600")
    monkeypatch.setenv("LUMOGIS_REFRESH_COOKIE_SECURE", "false")
    yield
    from routes.auth import _reset_rate_limit_for_tests

    _reset_rate_limit_for_tests()


@pytest.fixture
def live_stores(monkeypatch, auth_env):
    import os

    try:
        from adapters.postgres_store import PostgresStore
        from adapters.qdrant_store import QdrantStore

        vs = QdrantStore(url=os.environ.get("QDRANT_URL", "http://qdrant:6333"))
        ms = PostgresStore(
            host=os.environ.get("POSTGRES_HOST", "postgres"),
            port=int(os.environ.get("POSTGRES_PORT", "5432")),
            user=os.environ.get("POSTGRES_USER", "lumogis"),
            password=os.environ.get("POSTGRES_PASSWORD", "lumogis-dev"),
            dbname=os.environ.get("POSTGRES_DB", "lumogis"),
        )
        if not ms.ping():
            raise RuntimeError("postgres ping failed")
        try:
            vs.count(COLLECTION)
        except Exception:
            vs.create_collection(COLLECTION, _vector_dim())
    except Exception as exc:  # pragma: no cover - env-dependent
        pytest.skip(f"real stack not reachable — run under make compose-test: {exc}")

    monkeypatch.setitem(config._instances, "vector_store", vs)
    monkeypatch.setitem(config._instances, "metadata_store", ms)
    return ms, vs


@contextmanager
def _booted_client():
    import main

    with TestClient(main.app) as client:
        yield client


def _create_user(email: str, role: str) -> str:
    import services.users as users_svc

    return users_svc.create_user(email, "verylongpassword12", role).id


def _login(client: TestClient, email: str) -> str:
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "verylongpassword12"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _auth_hdr(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _seed_personal_session(ms, owner_id: str, summary: str) -> str:
    sid = str(uuid.uuid4())
    ms.execute(
        """
        INSERT INTO sessions (
            session_id, summary, topics, entities, entity_ids, user_id, scope
        ) VALUES (%s::uuid, %s, %s, %s, %s, %s, 'personal')
        """,
        (sid, summary, [], [], [], owner_id),
    )
    return sid


def _cleanup(ms, vs, alice_id: str, bob_id: str, session_id: str) -> None:
    proj_id = str(proj.projection_pk("sessions", session_id, "shared"))
    for sid in (session_id, proj_id):
        try:
            ms.execute("DELETE FROM sessions WHERE session_id = %s::uuid", (sid,))
        except Exception:
            pass
    try:
        vs.delete_where(
            COLLECTION,
            {
                "must": [
                    {"key": "published_from", "match": {"value": session_id}},
                ]
            },
        )
    except Exception:
        pass
    for uid in (alice_id, bob_id):
        try:
            ms.execute("DELETE FROM users WHERE id = %s", (uid,))
        except Exception:
            pass


def test_live_conversation_share_member_retrieves_edited_summary(live_stores):
    ms, vs = live_stores
    suffix = uuid.uuid4().hex[:8]
    alice_email = f"itest-alice-share-{suffix}@home.lan"
    bob_email = f"itest-bob-share-{suffix}@home.lan"
    alice_id = _create_user(alice_email, "user")
    bob_id = _create_user(bob_email, "user")

    ai_summary = f"private AI summary {suffix}"
    household_summary = f"Household edit {suffix}"
    session_id: str | None = None

    try:
        session_id = _seed_personal_session(ms, alice_id, ai_summary)

        with _booted_client() as client:
            alice_token = _login(client, alice_email)
            pub = client.post(
                f"/api/v1/sessions/{session_id}/publish",
                json={"scope": "shared", "shared_summary": household_summary},
                headers=_auth_hdr(alice_token),
            )
            assert pub.status_code == 200, pub.text

            bob_token = _login(client, bob_email)
            listing = client.get("/api/v1/conversations", headers=_auth_hdr(bob_token))
            assert listing.status_code == 200
            rows = listing.json()["conversations"]
            proj_id = str(proj.projection_pk("sessions", session_id, "shared"))
            shared_rows = [r for r in rows if r["conversation_id"] == proj_id]
            assert len(shared_rows) == 1
            assert shared_rows[0]["share_status"] == "shared"
            assert shared_rows[0]["is_owner"] is False

            detail = client.get(
                f"/api/v1/conversations/{proj_id}",
                headers=_auth_hdr(bob_token),
            )
            assert detail.status_code == 200
            body = detail.json()
            assert body["shared_summary"] == household_summary
            assert body["is_owner"] is False

        # Source summary unchanged in Postgres.
        src_row = ms.fetch_one(
            "SELECT summary FROM sessions WHERE session_id = %s::uuid AND scope = 'personal'",
            (session_id,),
        )
        assert src_row is not None
        assert src_row["summary"] == ai_summary

        # Qdrant: shared projection exists in the conversations collection.
        proj_id = str(proj.projection_pk("sessions", session_id, "shared"))
        assert (
            vs.count_where(
                COLLECTION,
                {
                    "must": [
                        {"key": "published_from", "match": {"value": session_id}},
                        {"key": "scope", "match": {"value": "shared"}},
                    ]
                },
            )
            >= 1
        )
        stored = [
            p
            for p in vs.scroll_collection(COLLECTION, user_id=alice_id, with_vectors=True)
            if (p.get("payload") or {}).get("scope") == "shared"
            and str((p.get("payload") or {}).get("published_from")) == session_id
        ]
        assert stored, "shared conversation projection missing from conversations collection"
        assert stored[0]["payload"]["summary"] == household_summary

        # Service-layer list mirrors the API proof.
        bob_user = UserContext(user_id=bob_id, is_authenticated=True, role="user")
        svc_rows = conv.list_conversations(bob_user)
        assert any(r.conversation_id == proj_id and r.share_status == "shared" for r in svc_rows)
    finally:
        if session_id:
            _cleanup(ms, vs, alice_id, bob_id, session_id)
