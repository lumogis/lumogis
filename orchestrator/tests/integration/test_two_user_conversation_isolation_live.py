# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Live two-user Postgres conversation isolation (LUM-395).

Novel harness combination: real ``PostgresStore`` + JWT ``TestClient`` login +
``services.users.create_user``. Unlike ``test_document_share_projection_live.py``
(real Postgres without auth login) and ``test_per_user_export_roundtrip.py``
(JWT login with an in-memory fake store).

Unit-level conversation isolation lives in ``test_api_v1_conversations.py``.
Mock-based multi-user harness: ``test_two_user_isolation.py``.

Skips when Postgres is unreachable (plain host without stack). Under
``make compose-test`` Postgres is started via ``depends_on`` so this module
runs for real; ``-x`` makes wiring bugs fatal to the suite.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

import config

pytestmark = pytest.mark.integration


@pytest.fixture
def auth_env(monkeypatch):
    """Family-LAN mode + deterministic auth secrets."""
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_SECRET", "conv-isolation-access-secret")
    monkeypatch.setenv("LUMOGIS_JWT_REFRESH_SECRET", "conv-isolation-refresh-secret")
    monkeypatch.setenv("ACCESS_TOKEN_TTL_SECONDS", "900")
    monkeypatch.setenv("REFRESH_TOKEN_TTL_SECONDS", "3600")
    monkeypatch.setenv("LUMOGIS_REFRESH_COOKIE_SECURE", "false")
    yield
    from routes.auth import _reset_rate_limit_for_tests

    _reset_rate_limit_for_tests()


@pytest.fixture
def live_postgres(monkeypatch, auth_env):
    """Real Postgres metadata store wired before user creation and app startup."""
    import os

    try:
        from adapters.postgres_store import PostgresStore

        ms = PostgresStore(
            host=os.environ.get("POSTGRES_HOST", "postgres"),
            port=int(os.environ.get("POSTGRES_PORT", "5432")),
            user=os.environ.get("POSTGRES_USER", "lumogis"),
            password=os.environ.get("POSTGRES_PASSWORD", "lumogis-dev"),
            dbname=os.environ.get("POSTGRES_DB", "lumogis"),
        )
        if not ms.ping():
            raise RuntimeError("postgres ping failed")
    except Exception as exc:  # pragma: no cover - env-dependent
        pytest.skip(f"postgres unreachable — run under make compose-test for live proof: {exc}")

    monkeypatch.setitem(config._instances, "metadata_store", ms)
    yield ms


@contextmanager
def _booted_client():
    import main

    with TestClient(main.app) as client:
        yield client


def _create_user(email: str, role: str) -> str:
    import services.users as users_svc

    user = users_svc.create_user(email, "verylongpassword12", role)
    return user.id


def _login(client: TestClient, email: str) -> str:
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "verylongpassword12"},
    )
    assert resp.status_code == 200, f"login for {email} failed: {resp.status_code} {resp.text}"
    return resp.json()["access_token"]


def _auth_hdr(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_live_alice_and_bob_conversation_isolation(live_postgres):
    ms = live_postgres
    suffix = uuid.uuid4().hex[:8]
    alice_email = f"itest-alice-{suffix}@home.lan"
    bob_email = f"itest-bob-{suffix}@home.lan"

    alice_id = _create_user(alice_email, "user")
    bob_id = _create_user(bob_email, "user")

    conversation_id_a: str | None = None
    conversation_id_b: str | None = None

    try:
        with _booted_client() as client:
            alice_token = _login(client, alice_email)
            bob_token = _login(client, bob_email)

            create_a = client.post(
                "/api/v1/conversations",
                json={"title": "Alice thread", "model": "m"},
                headers=_auth_hdr(alice_token),
            )
            assert create_a.status_code == 201
            conversation_id_a = create_a.json()["conversation_id"]

            source_refs = [
                {"document_id": 1, "chunk_index": 0, "quote": "live proof"},
            ]
            mid = str(uuid.uuid4())
            append_a = client.post(
                f"/api/v1/conversations/{conversation_id_a}/messages",
                json={
                    "message_id": mid,
                    "role": "user",
                    "content": "alice message",
                    "model": "m",
                    "source_refs": source_refs,
                },
                headers=_auth_hdr(alice_token),
            )
            assert append_a.status_code == 201

            create_b = client.post(
                "/api/v1/conversations",
                json={"title": "Bob thread", "model": "m"},
                headers=_auth_hdr(bob_token),
            )
            assert create_b.status_code == 201
            conversation_id_b = create_b.json()["conversation_id"]

            for method, path, kwargs in (
                ("get", f"/api/v1/conversations/{conversation_id_a}", {}),
                ("delete", f"/api/v1/conversations/{conversation_id_a}", {}),
                (
                    "post",
                    f"/api/v1/conversations/{conversation_id_a}/messages",
                    {
                        "json": {
                            "message_id": str(uuid.uuid4()),
                            "role": "user",
                            "content": "bob attempt",
                        },
                    },
                ),
            ):
                resp = getattr(client, method)(
                    path,
                    headers=_auth_hdr(bob_token),
                    **kwargs,
                )
                assert resp.status_code == 404
                assert resp.json()["detail"]["error"] == "conversation_not_found"

            alice_detail = client.get(
                f"/api/v1/conversations/{conversation_id_a}",
                headers=_auth_hdr(alice_token),
            )
            assert alice_detail.status_code == 200
            body = alice_detail.json()
            assert len(body["messages"]) == 1
            assert body["messages"][0]["source_refs"] == source_refs

            wc_row = ms.fetch_one(
                "SELECT message_count FROM web_conversations "
                "WHERE conversation_id = %s::uuid AND user_id = %s",
                (conversation_id_a, alice_id),
            )
            assert wc_row is not None
            assert wc_row["message_count"] == 1
    finally:
        for cid in (conversation_id_a, conversation_id_b):
            if not cid:
                continue
            for stmt, params in (
                ("DELETE FROM web_messages WHERE conversation_id = %s::uuid", (cid,)),
                ("DELETE FROM web_conversations WHERE conversation_id = %s::uuid", (cid,)),
            ):
                try:
                    ms.execute(stmt, params)
                except Exception:
                    pass
        for uid in (alice_id, bob_id):
            try:
                ms.execute("DELETE FROM users WHERE id = %s", (uid,))
            except Exception:
                pass
