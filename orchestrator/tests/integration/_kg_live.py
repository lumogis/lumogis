# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Shared fixtures for the live ``/api/v1/kg/*`` suites (LUM-964, LUM-161).

``live_pg`` (real Postgres, or a hard failure under
``LUMOGIS_REQUIRE_INTEGRATION_DB=1``), ``kg_app`` (the KG router alone),
``_as`` (sign in as a member), ``graph_mode`` (parametrised over the three
effective ``GRAPH_MODE``s) and ``_insert_entity``.

Visibility here comes from the ``users`` table, not from the dependency
override: the handlers resolve ``allows_shared`` through the users service,
which returns ``True`` for a missing row.
"""

from __future__ import annotations

import threading
import time
import uuid

import pytest
import routes.api_v1.kg as kgmod
from auth import UserContext
from authz import require_user
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.conftest import MockEmbedder
from tests.conftest import MockVectorStore
from tests.integration._pgguard import require_postgres

import config
from services import entity_edit

MODES = ["disabled", "inprocess", "service"]


@pytest.fixture
def live_pg(monkeypatch):
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
        require_postgres(exc, what="Postgres")

    monkeypatch.setitem(config._instances, "metadata_store", ms)
    yield ms
    ms.close()


def _insert_entity(ms, *, name, owner, scope="personal", published_from=None) -> str:
    row = ms.fetch_one(
        "INSERT INTO entities (name, entity_type, user_id, scope, published_from) "
        "VALUES (%s, 'PERSON', %s, %s, %s) RETURNING entity_id",
        (name, owner, scope, published_from),
    )
    return str(row["entity_id"])


@pytest.fixture
def kg_app():
    app = FastAPI()
    app.include_router(kgmod.router)
    yield app
    app.dependency_overrides.clear()


def _as(app, monkeypatch, user_id: str) -> TestClient:
    ctx = UserContext(user_id=user_id, is_authenticated=True, role="user", allows_shared=True)
    app.dependency_overrides[require_user] = lambda: ctx
    monkeypatch.setattr(kgmod, "get_user", lambda request: ctx)
    return TestClient(app)


@pytest.fixture(params=MODES)
def graph_mode(request):
    config.set_effective_graph_mode_for_process(request.param)
    yield request.param
    config.set_effective_graph_mode_for_process(None)
    config.clear_graph_mode_env_cache()


# ── seeding helpers shared by the entity-browser and kind-change suites ───────


def _ent(
    ms,
    name,
    owner,
    *,
    etype="PERSON",
    aliases=(),
    tags=(),
    scope="personal",
    published_from=None,
    mention_count=1,
    is_staged=False,
) -> str:
    row = ms.fetch_one(
        "INSERT INTO entities (name, entity_type, aliases, context_tags, mention_count, "
        "user_id, scope, published_from, is_staged) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING entity_id",
        (
            name,
            etype,
            list(aliases),
            list(tags),
            mention_count,
            owner,
            scope,
            published_from,
            is_staged,
        ),
    )
    return str(row["entity_id"])


def _version(ms, entity_id) -> int:
    return int(
        ms.fetch_one("SELECT version FROM entities WHERE entity_id = %s", (entity_id,))["version"]
    )


@pytest.fixture
def world(live_pg, monkeypatch):  # noqa: F811
    """Two members (+ one that opted out of sharing), cleaned up afterwards."""
    ms = live_pg
    suffix = uuid.uuid4().hex[:8]
    w = {
        "ms": ms,
        "sfx": suffix,
        "alice": f"lum161-alice-{suffix}",
        "bob": f"lum161-bob-{suffix}",
        "nosharing": f"lum161-nosharing-{suffix}",
    }
    ms.execute(
        "INSERT INTO users (id, email, password_hash, role, disabled, allows_shared) "
        "VALUES (%s, %s, 'not-a-real-hash', 'user', FALSE, FALSE)",
        (w["nosharing"], f"{w['nosharing']}@home.lan"),
    )
    # Qdrant / embedder are not under test here; rename's refresh is best-effort.
    monkeypatch.setitem(config._instances, "vector_store", MockVectorStore())
    monkeypatch.setitem(config._instances, "embedder", MockEmbedder())
    try:
        yield w
    finally:
        members = (w["alice"], w["bob"], w["nosharing"])
        for table in ("entities", "file_index", "sessions"):
            ms.execute(f"DELETE FROM {table} WHERE user_id IN (%s, %s, %s)", members)  # noqa: S608
        ms.execute("DELETE FROM users WHERE id = %s", (w["nosharing"],))


def _race(kg_app, ms, monkeypatch, alice, e1, e2, target=None, *, body=None):
    """Entry 1 sits inside its transaction (before commit) while entry 2 sends the
    same kind of edit from a second request.

    ``body(entity_id)`` builds the PATCH body; the default renames onto ``target``
    (LUM-161). LUM-967 passes a body that changes the kind.
    """
    from fastapi.testclient import TestClient

    a_in_txn = threading.Event()
    state = {"first": True}
    guard = threading.Lock()

    def hook():
        with guard:
            mine, state["first"] = state["first"], False
        if mine:
            a_in_txn.set()
            time.sleep(0.8)

    if body is None:

        def body(eid):
            return {"name": target, "version": _version(ms, eid)}

    monkeypatch.setattr(entity_edit, "_before_commit", hook)
    client_a = _as(kg_app, monkeypatch, alice)
    client_b = TestClient(kg_app)
    out = {}

    def go(key, client, eid):
        out[key] = client.patch(f"/api/v1/kg/entities/{eid}", json=body(eid))

    ta = threading.Thread(target=go, args=("a", client_a, e1))
    tb = threading.Thread(target=go, args=("b", client_b, e2))
    ta.start()
    assert a_in_txn.wait(10)
    tb.start()
    ta.join(30)
    tb.join(30)
    return out["a"].status_code, out["b"].status_code
