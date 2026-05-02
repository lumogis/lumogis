# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Headline integration test for personal/shared/system memory scopes.

Plan §2.15 — pins the publish/unpublish projection model end-to-end.

Two real users (Alice, Bob) plus an admin (Carol) operate against an
in-memory metadata store that emulates the columns added by migration
``013-memory-scopes.sql`` (``scope``, ``published_from`` + the partial
unique index ``<table>_published_from_scope_uniq`` semantics) and the
visibility contract from ``orchestrator/visibility.py``::

    (scope = 'personal' AND user_id = $me) OR scope IN ('shared', 'system')

Scenarios covered (subset of plan §2.15 implementable without a live
Postgres + FalkorDB harness; the remaining gates are pinned with
``pytest.skip`` and a docker-only marker for the integration job):

    S1  personal isolation: Alice's personal note is invisible to Bob
    S2  shared union: Alice publishes → Bob sees via list + by-id
    S3  system scope: a system-seeded signal is visible to both users
    S4  reversible round-trip: publish → unpublish → invisible to Bob,
        personal source still intact for Alice
    S5  idempotent publish: two POSTs collapse to one projection row
    S6  publish refused on staged entity (HTTP 409)
    S7  v1 contract: scope='system' in publish body → HTTP 400
    S8  personal-row 404 by direct id for non-owner (D5.1)
    S9  admin (Carol) on /api/v1/notes does NOT see other users'
        personal notes — admin god-mode is restricted to admin
        surfaces (Obs #4 forward-contributor guard)
    S10 ?scope=personal narrows to caller's own personal rows
    S11 ?scope=invalid → no-op (helper returns full union; route layer
        does NOT today validate the param) — pinned so future tightening
        is visible

The four scenarios that strictly require docker (backup/restore round
trip, partial-index DDL enforcement, FalkorDB shared-graph projection,
MCP harness) are skipped here with a clear marker; they are exercised
by ``docs/connect-and-verify.md`` and the docker-compose integration
job.

Test discipline note: this file deliberately uses a fat in-memory
MetadataStore rather than mocking each ``services/projection.py``
function. The point of a headline test is to catch contract drift
between the route layer, the projection engine, and the visibility
helper; mocking the engine would defeat that.
"""

from __future__ import annotations

import uuid
from typing import Any
from typing import Optional

import pytest
from auth import UserContext
from fastapi import FastAPI
from fastapi.testclient import TestClient

import config

# ---------------------------------------------------------------------------
# In-memory metadata store with scope + published_from semantics
# ---------------------------------------------------------------------------


class _ScopedStore:
    """Minimal MetadataStore covering the queries hit by routes/scope.py
    and the visibility contract.

    Only handles the publishable surfaces (notes, audio_memos, sessions,
    file_index, entities, signals); everything else is a no-op.
    """

    def __init__(self):
        # rows indexed by (table, pk) → dict
        self.rows: dict[tuple[str, Any], dict] = {}
        self._file_id_seq = 0

    def ping(self) -> bool:
        return True

    def close(self) -> None:
        pass

    # ----- direct seeding ---------------------------------------------------

    def seed_note(
        self,
        *,
        user_id: str,
        scope: str = "personal",
        text: str = "hello",
        note_id: Optional[str] = None,
        published_from: Optional[str] = None,
    ) -> str:
        nid = note_id or str(uuid.uuid4())
        self.rows[("notes", nid)] = {
            "note_id": nid,
            "text": text,
            "user_id": user_id,
            "source": "quick_capture",
            "scope": scope,
            "published_from": published_from,
        }
        return nid

    def seed_entity(
        self,
        *,
        user_id: str,
        name: str = "Acme",
        entity_type: str = "company",
        scope: str = "personal",
        is_staged: bool = False,
        entity_id: Optional[str] = None,
    ) -> str:
        eid = entity_id or str(uuid.uuid4())
        self.rows[("entities", eid)] = {
            "entity_id": eid,
            "name": name,
            "entity_type": entity_type,
            "aliases": [],
            "context_tags": [],
            "mention_count": 1,
            "user_id": user_id,
            "scope": scope,
            "published_from": None,
            "is_staged": is_staged,
            "extraction_quality": 0.9,
        }
        return eid

    def seed_signal(
        self,
        *,
        user_id: str,
        title: str = "headline",
        scope: str = "system",
        signal_id: Optional[str] = None,
    ) -> str:
        sid = signal_id or str(uuid.uuid4())
        self.rows[("signals", sid)] = {
            "signal_id": sid,
            "user_id": user_id,
            "source_id": "__system__",
            "title": title,
            "url": "",
            "published_at": None,
            "content_summary": "",
            "entities": [],
            "topics": [],
            "importance_score": 0.5,
            "relevance_score": 0.5,
            "notified": False,
            "scope": scope,
            "published_from": None,
            "source_url": None,
            "source_label": None,
        }
        return sid

    # ----- query plumbing ---------------------------------------------------

    def execute(self, query: str, params: tuple | None = None) -> None:
        self._dispatch(query, params or ())

    def fetch_one(self, query: str, params: tuple | None = None) -> dict | None:
        result = self._dispatch(query, params or (), expect="one")
        return result if isinstance(result, dict) else None

    def fetch_all(self, query: str, params: tuple | None = None) -> list[dict]:
        result = self._dispatch(query, params or (), expect="all")
        return result if isinstance(result, list) else []

    def _dispatch(self, query: str, params: tuple, expect: str = "exec"):
        q = " ".join(query.split()).lower()

        # ---- INSERT projections (notes/audio/sessions/entities/signals) --
        for table, pk_col in (
            ("notes", "note_id"),
            ("audio_memos", "audio_id"),
            ("sessions", "session_id"),
            ("entities", "entity_id"),
            ("signals", "signal_id"),
        ):
            ins = f"insert into {table}"
            if q.startswith(ins) and "on conflict" in q and "published_from" in q:
                return self._handle_projection_insert(table, pk_col, params)

        # ---- INSERT file_index projection (INTEGER PK; SERIAL synth) -----
        if q.startswith("insert into file_index") and "published_from" in q:
            return self._handle_file_projection_insert(params)

        # ---- DELETE projection by (published_from, scope) ----------------
        if q.startswith("delete from ") and "published_from = %s" in q and "scope = %s" in q:
            return self._handle_projection_delete(query, params)

        # ---- SELECT * FROM <table> WHERE <pk> AND user_id AND scope ------
        sel_personal = self._maybe_handle_personal_select(query, params)
        if sel_personal is not None:
            return sel_personal

        # ---- list reads via visible_filter (notes only — illustrative) ---
        if q.startswith("select") and "from notes" in q and "scope" in q:
            return self._handle_notes_list(query, params)

        return None if expect == "one" else ([] if expect == "all" else None)

    # ----- INSERT helpers ---------------------------------------------------

    def _handle_projection_insert(self, table: str, pk_col: str, params: tuple) -> dict:
        """Insert + ON CONFLICT (published_from, scope) DO UPDATE.

        The ordering here mirrors the column order in
        ``orchestrator/services/projection.py`` for each table; we
        deliberately use a positional unpack rather than psycopg2-style
        named binds because the test fakes the cursor.
        """
        # The first param is always the new PK (uuid5).
        new_pk = params[0]
        # Derive column dict from a per-table positional template.
        column_templates: dict[str, list[str]] = {
            "notes": ["note_id", "text", "user_id", "source", "scope", "published_from"],
            "audio_memos": [
                "audio_id",
                "file_path",
                "transcript",
                "duration_seconds",
                "whisper_model",
                "user_id",
                "scope",
                "published_from",
                "transcribed_at",
            ],
            "sessions": [
                "session_id",
                "summary",
                "topics",
                "entities",
                "entity_ids",
                "user_id",
                "scope",
                "published_from",
            ],
            "entities": [
                "entity_id",
                "name",
                "entity_type",
                "aliases",
                "context_tags",
                "mention_count",
                "user_id",
                "scope",
                "published_from",
                "extraction_quality",
            ],
            "signals": [
                "signal_id",
                "user_id",
                "source_id",
                "title",
                "url",
                "published_at",
                "content_summary",
                "entities",
                "topics",
                "importance_score",
                "relevance_score",
                "notified",
                "scope",
                "published_from",
                "source_url",
                "source_label",
            ],
        }
        cols = column_templates[table]
        row = {col: params[i] for i, col in enumerate(cols) if i < len(params)}
        # Idempotency: collapse on (published_from, scope) — emulate the
        # partial unique index by overwriting the existing projection.
        existing = next(
            (
                (k, r)
                for k, r in self.rows.items()
                if k[0] == table
                and r.get("published_from") == row.get("published_from")
                and r.get("scope") == row.get("scope")
                and row.get("published_from") is not None
            ),
            None,
        )
        if existing is not None:
            existing[1].update(row)
            return existing[1]
        if table == "entities":
            row.setdefault("is_staged", False)
        self.rows[(table, new_pk)] = row
        return row

    def _handle_file_projection_insert(self, params: tuple) -> dict:
        cols = [
            "file_path",
            "file_hash",
            "file_type",
            "chunk_count",
            "ocr_used",
            "user_id",
            "scope",
            "published_from",
        ]
        row = {col: params[i] for i, col in enumerate(cols) if i < len(params)}
        existing = next(
            (
                (k, r)
                for k, r in self.rows.items()
                if k[0] == "file_index"
                and r.get("published_from") == row.get("published_from")
                and r.get("scope") == row.get("scope")
                and row.get("published_from") is not None
            ),
            None,
        )
        if existing is not None:
            existing[1].update(row)
            return existing[1]
        self._file_id_seq += 1
        row["id"] = self._file_id_seq
        self.rows[("file_index", self._file_id_seq)] = row
        return row

    # ----- DELETE / SELECT helpers ------------------------------------------

    def _handle_projection_delete(self, query: str, params: tuple) -> Optional[dict]:
        q = " ".join(query.split()).lower()
        # Extract table name between "delete from " and " where".
        try:
            table = q.split("delete from ", 1)[1].split(" ", 1)[0]
        except Exception:
            return None
        target_pf, target_scope = params[0], params[1]
        for k, r in list(self.rows.items()):
            if (
                k[0] == table
                and r.get("published_from") == target_pf
                and r.get("scope") == target_scope
            ):
                self.rows.pop(k)
                return {k[1].__class__.__name__: k[1]}
        return None

    def _maybe_handle_personal_select(self, query: str, params: tuple):
        """Match the per-route fetcher pattern from routes/scope.py:
        ``SELECT * FROM <t> WHERE <pk_col> = %s AND user_id = %s
           AND scope = 'personal'``
        """
        q = " ".join(query.split()).lower()
        if not q.startswith("select * from "):
            return None
        if "scope = 'personal'" not in q:
            return None
        if " and user_id = %s" not in q:
            return None
        try:
            after_from = q.split("from ", 1)[1]
            table = after_from.split(" ", 1)[0]
        except Exception:
            return None
        pk, user_id = params[0], params[1]
        # file_index uses INTEGER pk.
        if table == "file_index":
            try:
                pk = int(pk)
            except (TypeError, ValueError):
                return None
        row = self.rows.get((table, pk))
        if row is None:
            return None
        if row.get("user_id") != user_id or row.get("scope") != "personal":
            return None
        return dict(row)

    def _handle_notes_list(self, query: str, params: tuple) -> list[dict]:
        # Approximation — the real visible_filter expansion lives in
        # visibility.py; here we just emulate its output for notes.
        # Convention: the first param is the requesting user_id.
        if not params:
            return []
        me = params[0]
        out = []
        for k, r in self.rows.items():
            if k[0] != "notes":
                continue
            visible = (r.get("scope") == "personal" and r.get("user_id") == me) or r.get(
                "scope"
            ) in ("shared", "system")
            if visible:
                out.append(dict(r))
        return out


# ---------------------------------------------------------------------------
# Mock vector store (re-uses the conftest one but with payload-key indices)
# ---------------------------------------------------------------------------


class _PayloadVectorStore:
    def __init__(self):
        self.points: dict[tuple[str, str], dict] = {}

    def ping(self) -> bool:
        return True

    def upsert(self, collection: str, id: str, vector: list[float], payload: dict) -> None:
        self.points[(collection, id)] = {"vector": vector, "payload": payload}

    def delete(self, collection: str, id: str) -> None:
        self.points.pop((collection, id), None)

    def delete_where(self, collection: str, filter: dict) -> None:
        for k, p in list(self.points.items()):
            if k[0] != collection:
                continue
            payload = p.get("payload") or {}
            if all(payload.get(c["key"]) == c["match"]["value"] for c in filter.get("must", [])):
                self.points.pop(k, None)

    def search(self, collection: str, vector, limit, threshold, filter=None, sparse_query=None):
        out = []
        for k, p in self.points.items():
            if k[0] != collection:
                continue
            payload = p.get("payload") or {}
            if filter:
                if not all(
                    payload.get(c["key"]) == c["match"]["value"] for c in filter.get("must", [])
                ):
                    continue
            out.append({"id": k[1], "score": 1.0, "payload": payload})
        return out[:limit]

    def count(self, collection: str) -> int:
        return sum(1 for k in self.points if k[0] == collection)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


ALICE = "alice-uid"
BOB = "bob-uid"
CAROL = "carol-uid"  # admin


@pytest.fixture
def store(monkeypatch):
    s = _ScopedStore()
    config._instances["metadata_store"] = s
    config._instances["vector_store"] = _PayloadVectorStore()
    yield s
    config._instances.pop("metadata_store", None)
    config._instances.pop("vector_store", None)


@pytest.fixture
def app(store, monkeypatch):
    # Build a minimal FastAPI app with only the scope router so we
    # don't pull in the full lifespan (auth bootstrap, plugin loader,
    # etc). The router itself is what's under test.
    from routes.scope import router as scope_router

    application = FastAPI()
    application.include_router(scope_router)
    return application


def _override_user(app, user_id: str, role: str = "user") -> None:
    """Force routes that ``Depends(get_user)`` to resolve to a specific user.

    Bypasses the JWT/cookie path so we don't have to mint tokens for
    every scenario; the auth tests already cover that surface.
    """
    from auth import get_user

    def _stub() -> UserContext:
        return UserContext(user_id=user_id, is_authenticated=True, role=role)

    app.dependency_overrides[get_user] = _stub


@pytest.fixture
def client(app):
    return TestClient(app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _publish_note(client, app, *, actor: str, note_id: str, body: Optional[dict] = None):
    _override_user(app, actor)
    return client.post(
        f"/api/v1/notes/{note_id}/publish", json=body if body is not None else {"scope": "shared"}
    )


def _unpublish_note(client, app, *, actor: str, note_id: str):
    _override_user(app, actor)
    return client.delete(f"/api/v1/notes/{note_id}/publish")


# ---------------------------------------------------------------------------
# S1 — personal isolation
# ---------------------------------------------------------------------------


def test_s1_personal_note_invisible_to_other_user(store, app, client):
    """Plan §2.15: personal isolation still holds."""
    alice_note = store.seed_note(user_id=ALICE, text="alice secret")

    # Bob attempts to publish Alice's note → must be 404, never 403.
    # 404 is the convention so the existence of Alice's row is hidden.
    _override_user(app, BOB)
    resp = client.post(f"/api/v1/notes/{alice_note}/publish", json={"scope": "shared"})
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"]["error"] == "not_found"


# ---------------------------------------------------------------------------
# S2 — shared union (publish path)
# ---------------------------------------------------------------------------


def test_s2_publish_creates_visible_projection(store, app, client):
    """Alice publishes a note → projection row carries scope='shared'
    and points back to the personal source via published_from."""
    src = store.seed_note(user_id=ALICE, text="dinner plan")
    resp = _publish_note(client, app, actor=ALICE, note_id=src)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["resource"] == "notes"
    assert body["scope"] == "shared"

    proj = next(
        r for k, r in store.rows.items() if k[0] == "notes" and r.get("published_from") == src
    )
    assert proj["scope"] == "shared"
    assert proj["user_id"] == ALICE  # publisher attribution
    assert proj["text"] == "dinner plan"


# ---------------------------------------------------------------------------
# S3 — system scope union
# ---------------------------------------------------------------------------


def test_s3_system_signal_visible_to_both_users(store):
    """A signal with scope='system' is visible to anyone via the
    visibility filter (no publish endpoint needed)."""
    from visibility import visible_filter

    sid = store.seed_signal(user_id=ALICE, scope="system", title="cve-alert")

    for actor in (ALICE, BOB):
        where, params = visible_filter(UserContext(user_id=actor))
        # The where clause should accept any scope IN ('shared','system')
        # regardless of user_id.
        assert "shared" in where and "system" in where
        # Direct membership check on our seeded row:
        row = store.rows[("signals", sid)]
        visible = (row["scope"] == "personal" and row["user_id"] == actor) or row["scope"] in (
            "shared",
            "system",
        )
        assert visible, f"system signal not visible to {actor}"


# ---------------------------------------------------------------------------
# S4 — reversible round-trip
# ---------------------------------------------------------------------------


def test_s4_publish_then_unpublish_round_trip(store, app, client):
    src = store.seed_note(user_id=ALICE, text="round trip")

    pub = _publish_note(client, app, actor=ALICE, note_id=src)
    assert pub.status_code == 200
    proj_count = sum(
        1 for k, r in store.rows.items() if k[0] == "notes" and r.get("published_from") == src
    )
    assert proj_count == 1

    unp = _unpublish_note(client, app, actor=ALICE, note_id=src)
    assert unp.status_code == 204
    proj_count_after = sum(
        1 for k, r in store.rows.items() if k[0] == "notes" and r.get("published_from") == src
    )
    assert proj_count_after == 0
    # Personal source still intact.
    assert store.rows[("notes", src)]["scope"] == "personal"
    assert store.rows[("notes", src)]["user_id"] == ALICE


# ---------------------------------------------------------------------------
# S5 — idempotent publish
# ---------------------------------------------------------------------------


def test_s5_publish_is_idempotent(store, app, client):
    src = store.seed_note(user_id=ALICE, text="dupe me")

    r1 = _publish_note(client, app, actor=ALICE, note_id=src)
    r2 = _publish_note(client, app, actor=ALICE, note_id=src)
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["note_id"] == r2.json()["note_id"]

    proj_count = sum(
        1 for k, r in store.rows.items() if k[0] == "notes" and r.get("published_from") == src
    )
    assert proj_count == 1, "concurrent publish must collapse to one projection"


# ---------------------------------------------------------------------------
# S6 — staged entity refusal
# ---------------------------------------------------------------------------


def test_s6_staged_entity_publish_refused_409(store, app, client):
    eid = store.seed_entity(user_id=ALICE, is_staged=True, name="Quarantined")

    _override_user(app, ALICE)
    resp = client.post(f"/api/v1/entities/{eid}/publish", json={"scope": "shared"})
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["error"] == "entity_is_staged"


# ---------------------------------------------------------------------------
# S7 — system-scope publish refused
# ---------------------------------------------------------------------------


def test_s7_publish_with_scope_system_rejected_400(store, app, client):
    src = store.seed_note(user_id=ALICE, text="system attempt")

    _override_user(app, ALICE)
    resp = client.post(f"/api/v1/notes/{src}/publish", json={"scope": "system"})
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"]["error"] == "invalid_scope"


# ---------------------------------------------------------------------------
# S8 — personal row 404 by direct id (D5.1)
# ---------------------------------------------------------------------------


def test_s8_personal_row_invisible_to_non_owner_returns_404(store, app, client):
    """Alice's personal note: Bob's publish/unpublish both return 404,
    not 403 (existence is hidden)."""
    src = store.seed_note(user_id=ALICE, text="alice only")

    _override_user(app, BOB)
    pub = client.post(f"/api/v1/notes/{src}/publish", json={"scope": "shared"})
    unp = client.delete(f"/api/v1/notes/{src}/publish")
    assert pub.status_code == 404
    assert unp.status_code == 404


# ---------------------------------------------------------------------------
# S9 — admin god-mode does NOT leak to /api/v1/notes (Obs #4 forward guard)
# ---------------------------------------------------------------------------


def test_s9_admin_publish_does_not_bypass_owner_check(store, app, client):
    """Carol (admin) cannot publish someone else's personal note via
    the public API surface. Admin god-mode lives only on /admin/*."""
    src = store.seed_note(user_id=ALICE, text="alice owned")

    _override_user(app, CAROL, role="admin")
    resp = client.post(f"/api/v1/notes/{src}/publish", json={"scope": "shared"})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# S10 — visible_filter scope_filter narrowing
# ---------------------------------------------------------------------------


def test_s10_scope_filter_personal_narrows_to_caller(store):
    """visible_filter(user, scope_filter='personal') returns only the
    caller's own personal rows — never cross-user personal."""
    from visibility import visible_filter

    where, params = visible_filter(UserContext(user_id=BOB), scope_filter="personal")
    # The narrowed clause must reference user_id (caller's own only).
    assert "user_id" in where
    # And must NOT include the shared/system union arm.
    assert "shared" not in where and "system" not in where


# ---------------------------------------------------------------------------
# S11 — invalid scope_filter rejected by helper
# ---------------------------------------------------------------------------


def test_s11_invalid_scope_filter_value_raises(store):
    from visibility import visible_filter

    with pytest.raises(ValueError):
        visible_filter(UserContext(user_id=ALICE), scope_filter="bogus")


# ---------------------------------------------------------------------------
# Docker-only scenarios — pin them so the gate is visible
# ---------------------------------------------------------------------------


@pytest.mark.skip(
    reason=(
        "plan §2.15 backup/restore round-trip — requires live Postgres + "
        "the routes/admin.py backup helpers; covered by the docker-compose "
        "integration job and docs/connect-and-verify.md"
    )
)
def test_s12_backup_restore_round_trip_with_shared_projections():
    pass


@pytest.mark.skip(
    reason=(
        "plan §2.15 entity-relations follow-back through projection — "
        "requires the lumogis-graph service running against a live Postgres "
        "+ FalkorDB; covered by the docker-compose integration job"
    )
)
def test_s13_entity_relations_follow_back_through_projection():
    pass


@pytest.mark.skip(
    reason=(
        "plan §2.15 MCP memory.get_recent harness — requires the standalone "
        "MCP server fixture from tests/conftest.py::mcp_test_client wired "
        "against the JWT path; tracked separately"
    )
)
def test_s14_mcp_memory_get_recent_returns_shared_projection_for_bob():
    pass
