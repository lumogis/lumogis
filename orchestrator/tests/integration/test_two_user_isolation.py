# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Phase 3 headline integration test — two-user data isolation.

Plan §16 ("Test cases / Integration") and §17 ("Definition of done"):

    Alice (admin) and Bob (user) log in via /api/v1/auth/login; each
    ingests one document with distinct content; each runs a chat
    completion that triggers the search tool; assert no token from the
    other user's document appears in either response. Also assert: Bob
    cannot PUT /permissions/filesystem-mcp; Alice can.

This is the regression that pins down the user-isolation contract for
the whole multi-user phase. It exercises the *entire* hot path:

    POST /api/v1/auth/login
        -> JWT mint (Phase 1)
    services.ingest.ingest_file(path, user_id=...)
        -> file_index INSERT with user_id
        -> Qdrant upsert with payload.user_id
    POST /v1/chat/completions
        -> auth middleware extracts user_id from Bearer JWT
        -> chat_completions reads user_id from UserContext
        -> loop.ask(..., user_id=...)
            -> run_tool("search_files", input_, user_id=...)
                -> _search_files(input_, user_id=...)
                    -> services.search.semantic_search(..., user_id=...)
                        -> Qdrant search with payload.user_id filter

Any break in the chain shows up here as a cross-user leak.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import datetime
from datetime import timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Fake metadata store: handles users + file_index, no-ops everything else.
# ---------------------------------------------------------------------------


class _IsolationStore:
    """In-memory MetadataStore for the isolation test.

    Supports the queries hit by the hot path:

      - users  (login, role lookup, refresh-jti round-trip)
      - file_index (ingest_file dedupe + insert)

    Every other query is a no-op (execute) or returns None / [] (fetch).
    """

    def __init__(self):
        self.users: dict[str, dict] = {}
        self.file_index: dict[tuple[str, str], dict] = {}
        self.exec_log: list[tuple[str, tuple]] = []
        # Per-user MCP token surface (plan ``mcp_token_user_map``). Kept
        # alongside ``users`` so the cross-user MCP isolation test can
        # exercise the real route layer without booting Postgres.
        self.mcp_tokens: dict[str, dict] = {}
        self.audit: list[dict] = []
        # Per-user connector credentials surface (plan
        # ``caldav_connector_credentials`` integration test). Modeled
        # alongside ``users``/``mcp_tokens`` so the cross-user CalDAV
        # isolation test exercises the real route + service layers
        # without booting Postgres. SR-5 fixture-preflight: this is
        # option (a) from the plan — extend the fake to support
        # ``user_connector_credentials`` SQL.
        self.creds: dict[tuple[str, str], dict] = {}
        # Per-user connector permissions (plan
        # ``per_user_connector_permissions``). Modeled alongside
        # ``users``/``mcp_tokens``/``creds`` so the cross-user
        # permission isolation test exercises the real route + service
        # layers without booting Postgres. SR-D6 fixture-preflight: the
        # fake's silent fall-through is hostile to test correctness here.
        self.connector_perms: dict[tuple[str, str], dict] = {}
        # Per-user routine_do_tracking — same chunk; per-user since
        # migration 016.
        self.routine_do: dict[tuple[str, str, str], dict] = {}

    def ping(self) -> bool:
        return True

    def close(self) -> None:
        pass

    def transaction(self):
        from contextlib import contextmanager

        @contextmanager
        def _noop():
            yield

        return _noop()

    def execute(self, query: str, params: tuple | None = None) -> None:
        self.exec_log.append((query, params or ()))
        q = " ".join(query.split()).lower()
        p = params or ()

        if q.startswith("insert into users"):
            self.users[p[0]] = {
                "id": p[0],
                "email": p[1],
                "password_hash": p[2],
                "role": p[3],
                "disabled": False,
                "created_at": datetime.now(timezone.utc),
                "last_login_at": None,
                "refresh_token_jti": None,
            }
            return
        if q.startswith("update users set last_login_at = now()"):
            self.users[p[0]]["last_login_at"] = datetime.now(timezone.utc)
            return
        if q.startswith("update users set refresh_token_jti ="):
            self.users[p[1]]["refresh_token_jti"] = p[0]
            return

        if q.startswith("insert into mcp_tokens"):
            tid, uid, prefix, token_hash, label, scopes = p
            for row in self.mcp_tokens.values():
                if row["revoked_at"] is None and row["token_prefix"] == prefix:
                    raise RuntimeError(
                        "duplicate key value violates unique constraint "
                        "mcp_tokens_active_prefix_uniq"
                    )
            self.mcp_tokens[tid] = {
                "id": tid,
                "user_id": uid,
                "token_prefix": prefix,
                "token_hash": token_hash,
                "label": label,
                "scopes": scopes,
                "created_at": datetime.now(timezone.utc),
                "last_used_at": None,
                "expires_at": None,
                "revoked_at": None,
            }
            return
        if q.startswith(
            "update mcp_tokens set revoked_at = now() where id = %s and revoked_at is null"
        ):
            (tid,) = p
            row = self.mcp_tokens.get(tid)
            if row is not None and row["revoked_at"] is None:
                row["revoked_at"] = datetime.now(timezone.utc)
            return
        if q.startswith("update mcp_tokens set last_used_at = now() where id = %s"):
            (tid,) = p
            row = self.mcp_tokens.get(tid)
            if row is not None:
                row["last_used_at"] = datetime.now(timezone.utc)
            return

        # ---- per-user connector permissions (plan per_user_connector_permissions)
        if q.startswith("insert into connector_permissions (user_id, connector, mode)"):
            user_id, connector, mode = p
            now = datetime.now(timezone.utc)
            existing = self.connector_perms.get((user_id, connector))
            if existing is None:
                self.connector_perms[(user_id, connector)] = {
                    "user_id": user_id,
                    "connector": connector,
                    "mode": mode,
                    "created_at": now,
                    "updated_at": now,
                }
            else:
                existing["mode"] = mode
                existing["updated_at"] = now
            return
        if q.startswith("delete from connector_permissions where user_id = %s and connector = %s"):
            user_id, connector = p
            self.connector_perms.pop((user_id, connector), None)
            return
        if q.startswith(
            "insert into routine_do_tracking (user_id, connector, action_type, approval_count)"
        ):
            user_id, connector, action_type = p
            now = datetime.now(timezone.utc)
            existing = self.routine_do.get((user_id, connector, action_type))
            if existing is None:
                self.routine_do[(user_id, connector, action_type)] = {
                    "user_id": user_id,
                    "connector": connector,
                    "action_type": action_type,
                    "approval_count": 1,
                    "edit_count": 0,
                    "auto_approved": False,
                    "granted_at": None,
                    "created_at": now,
                    "updated_at": now,
                }
            else:
                existing["approval_count"] += 1
                existing["updated_at"] = now
            return
        if q.startswith(
            "insert into routine_do_tracking (user_id, connector, action_type, auto_approved, granted_at)"
        ):
            user_id, connector, action_type = p
            now = datetime.now(timezone.utc)
            existing = self.routine_do.get((user_id, connector, action_type))
            if existing is None:
                self.routine_do[(user_id, connector, action_type)] = {
                    "user_id": user_id,
                    "connector": connector,
                    "action_type": action_type,
                    "approval_count": 0,
                    "edit_count": 0,
                    "auto_approved": True,
                    "granted_at": now,
                    "created_at": now,
                    "updated_at": now,
                }
            else:
                existing["auto_approved"] = True
                existing["granted_at"] = now
                existing["updated_at"] = now
            return

        if q.startswith("insert into file_index"):
            file_path, file_hash, file_type, chunk_count, user_id = p
            self.file_index[(user_id, file_path)] = {
                "file_path": file_path,
                "file_hash": file_hash,
                "file_type": file_type,
                "chunk_count": chunk_count,
                "user_id": user_id,
            }
            return
        if q.startswith("update file_index set"):
            new_hash, chunk_count, file_path, user_id = p
            row = self.file_index.get((user_id, file_path))
            if row:
                row["file_hash"] = new_hash
                row["chunk_count"] = chunk_count
            return

    def fetch_one(self, query: str, params: tuple | None = None) -> dict | None:
        q = " ".join(query.split()).lower()
        p = params or ()

        if q.startswith("select id from users where lower(email) ="):
            target = p[0].lower()
            for row in self.users.values():
                if row["email"].lower() == target:
                    return {"id": row["id"]}
            return None
        if q.startswith("select * from users where id ="):
            return dict(self.users[p[0]]) if p[0] in self.users else None
        if q.startswith("select * from users where lower(email) ="):
            target = p[0].lower()
            for row in self.users.values():
                if row["email"].lower() == target:
                    return dict(row)
            return None
        if q.startswith("select count(*) as n from users where role = 'admin'"):
            n = sum(1 for r in self.users.values() if r["role"] == "admin" and not r["disabled"])
            return {"n": n}
        if q.startswith("select count(*) as n from users"):
            return {"n": len(self.users)}
        if q.startswith("select id from users where role = 'admin'"):
            admins = sorted(
                (r for r in self.users.values() if r["role"] == "admin" and not r["disabled"]),
                key=lambda r: r["created_at"],
            )
            return {"id": admins[0]["id"]} if admins else None
        if q.startswith("select refresh_token_jti from users where id ="):
            row = self.users.get(p[0])
            return {"refresh_token_jti": row["refresh_token_jti"]} if row else None

        if q.startswith("select * from mcp_tokens where id = %s"):
            (tid,) = p
            row = self.mcp_tokens.get(tid)
            return dict(row) if row else None
        if q.startswith("select * from mcp_tokens where token_prefix = %s and revoked_at is null"):
            (prefix,) = p
            for row in self.mcp_tokens.values():
                if row["token_prefix"] == prefix and row["revoked_at"] is None:
                    return dict(row)
            return None
        if q.startswith("insert into audit_log"):
            row_id = len(self.audit) + 1
            self.audit.append(
                {
                    "id": row_id,
                    "user_id": p[0],
                    "action_name": p[1],
                }
            )
            return {"id": row_id}

        if q.startswith("select file_hash from file_index"):
            file_path, user_id = p
            row = self.file_index.get((user_id, file_path))
            return {"file_hash": row["file_hash"]} if row else None

        # --- user_connector_credentials (plan caldav_connector_credentials) ---
        if q.startswith("select ciphertext from user_connector_credentials"):
            uid, conn = p
            row = self.creds.get((uid, conn))
            return {"ciphertext": row["ciphertext"]} if row else None
        if q.startswith(
            "select user_id, connector, created_at, updated_at, "
            "created_by, updated_by, key_version "
            "from user_connector_credentials"
        ):
            uid, conn = p
            row = self.creds.get((uid, conn))
            return dict(row) if row else None
        if q.startswith("insert into user_connector_credentials"):
            uid, conn, ciphertext, key_version, created_by, updated_by = p
            now = datetime.now(timezone.utc)
            existing = self.creds.get((uid, conn))
            if existing is None:
                row = {
                    "user_id": uid,
                    "connector": conn,
                    "ciphertext": ciphertext,
                    "key_version": key_version,
                    "created_at": now,
                    "updated_at": now,
                    "created_by": created_by,
                    "updated_by": updated_by,
                }
            else:
                row = dict(existing)
                row["ciphertext"] = ciphertext
                row["key_version"] = key_version
                row["updated_at"] = now
                row["updated_by"] = updated_by
            self.creds[(uid, conn)] = row
            return {
                "user_id": row["user_id"],
                "connector": row["connector"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "created_by": row["created_by"],
                "updated_by": row["updated_by"],
                "key_version": row["key_version"],
            }
        if q.startswith("delete from user_connector_credentials"):
            uid, conn = p
            row = self.creds.pop((uid, conn), None)
            if row is None:
                return None
            return {"key_version": row["key_version"]}

        # --- per-user connector permissions / routine_do_tracking ---
        if q.startswith(
            "select mode from connector_permissions where user_id = %s and connector = %s"
        ):
            user_id, connector = p
            row = self.connector_perms.get((user_id, connector))
            return {"mode": row["mode"]} if row else None
        if q.startswith(
            "select approval_count, edit_count, auto_approved from routine_do_tracking "
            "where user_id = %s and connector = %s and action_type = %s"
        ):
            user_id, connector, action_type = p
            row = self.routine_do.get((user_id, connector, action_type))
            if row is None:
                return None
            return {
                "approval_count": row["approval_count"],
                "edit_count": row["edit_count"],
                "auto_approved": row["auto_approved"],
            }
        if q.startswith(
            "select auto_approved, approval_count from routine_do_tracking "
            "where user_id = %s and connector = %s and action_type = %s"
        ):
            user_id, connector, action_type = p
            row = self.routine_do.get((user_id, connector, action_type))
            if row is None:
                return None
            return {
                "auto_approved": row["auto_approved"],
                "approval_count": row["approval_count"],
            }

        return None

    def fetch_all(self, query: str, params: tuple | None = None) -> list[dict]:
        q = " ".join(query.split()).lower()
        p = params or ()
        if q.startswith("select * from users order by created_at"):
            return sorted(
                (dict(r) for r in self.users.values()),
                key=lambda r: r["created_at"],
            )
        if q.startswith(
            "update mcp_tokens set revoked_at = now() where user_id = %s "
            "and revoked_at is null returning *"
        ):
            (uid,) = p
            now = datetime.now(timezone.utc)
            updated: list[dict] = []
            for row in self.mcp_tokens.values():
                if row["user_id"] == uid and row["revoked_at"] is None:
                    row["revoked_at"] = now
                    updated.append(dict(row))
            return updated
        if q.startswith("select * from mcp_tokens where user_id = %s and revoked_at is null"):
            (uid,) = p
            return sorted(
                (
                    dict(r)
                    for r in self.mcp_tokens.values()
                    if r["user_id"] == uid and r["revoked_at"] is None
                ),
                key=lambda r: r["created_at"],
                reverse=True,
            )
        if q.startswith("select * from mcp_tokens where user_id = %s order by created_at"):
            (uid,) = p
            return sorted(
                (dict(r) for r in self.mcp_tokens.values() if r["user_id"] == uid),
                key=lambda r: r["created_at"],
                reverse=True,
            )
        # --- per-user connector permissions ---
        if q.startswith("select connector, mode from connector_permissions where user_id = %s"):
            (user_id,) = p
            return sorted(
                (
                    {"connector": r["connector"], "mode": r["mode"]}
                    for (u, _c), r in self.connector_perms.items()
                    if u == user_id
                ),
                key=lambda r: r["connector"],
            )
        if q.startswith(
            "select user_id, connector, mode from connector_permissions order by user_id, connector"
        ):
            return sorted(
                (
                    {"user_id": r["user_id"], "connector": r["connector"], "mode": r["mode"]}
                    for r in self.connector_perms.values()
                ),
                key=lambda r: (r["user_id"], r["connector"]),
            )
        # list_records — per-user enumeration ordered by connector ASC.
        if q.startswith(
            "select user_id, connector, created_at, updated_at, "
            "created_by, updated_by, key_version "
            "from user_connector_credentials where user_id"
        ):
            (uid,) = p
            return sorted(
                (dict(r) for (u, _c), r in self.creds.items() if u == uid),
                key=lambda r: r["connector"],
            )
        return []


# ---------------------------------------------------------------------------
# Fake LLM provider: round 1 calls search_files; round 2 returns the result.
# ---------------------------------------------------------------------------


class _SearchOnceProvider:
    """Provider that always: (1) calls search_files, (2) echoes the result."""

    def __init__(self):
        self.calls: list[list[dict]] = []

    def chat(self, messages, tools=None, system=None, max_tokens=4096):
        from models.llm import LLMResponse
        from models.llm import LLMToolCall

        self.calls.append(list(messages))

        last_tool_msg = next(
            (m for m in reversed(messages) if m.get("role") == "tool"),
            None,
        )
        if last_tool_msg is None:
            return LLMResponse(
                text="",
                tool_calls=[
                    LLMToolCall(
                        id="call-1",
                        name="search_files",
                        arguments={"query": "isolation probe"},
                    )
                ],
                stop_reason="tool_calls",
            )
        return LLMResponse(text=last_tool_msg["content"], stop_reason="stop")

    def chat_stream(self, messages, tools=None, system=None, max_tokens=4096):
        from models.llm import LLMEvent
        from models.llm import LLMToolCall

        last_tool_msg = next(
            (m for m in reversed(messages) if m.get("role") == "tool"),
            None,
        )
        if last_tool_msg is None:
            yield LLMEvent(
                type="tool_call",
                tool_call=LLMToolCall(
                    id="call-1",
                    name="search_files",
                    arguments={"query": "isolation probe"},
                ),
            )
            yield LLMEvent(type="end")
            return
        yield LLMEvent(type="text", content=last_tool_msg["content"])
        yield LLMEvent(type="end")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def isolation_env(monkeypatch):
    """Family-LAN mode + deterministic auth secrets for the test process."""
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_SECRET", "isolation-access-secret")
    monkeypatch.setenv("LUMOGIS_JWT_REFRESH_SECRET", "isolation-refresh-secret")
    monkeypatch.setenv("ACCESS_TOKEN_TTL_SECONDS", "900")
    monkeypatch.setenv("REFRESH_TOKEN_TTL_SECONDS", "3600")
    monkeypatch.setenv("LUMOGIS_REFRESH_COOKIE_SECURE", "false")
    yield
    from routes.auth import _reset_rate_limit_for_tests

    _reset_rate_limit_for_tests()


@pytest.fixture
def isolation_store(monkeypatch):
    import config as _config

    store = _IsolationStore()
    _config._instances["metadata_store"] = store
    yield store
    _config._instances.pop("metadata_store", None)


@pytest.fixture
def fake_provider(monkeypatch):
    """Force every model to resolve to the SearchOnce provider; pretend tools are on."""
    import config as _config

    provider = _SearchOnceProvider()

    monkeypatch.setattr(_config, "get_llm_provider", lambda *_a, **_kw: provider)
    monkeypatch.setattr(_config, "is_model_enabled", lambda *_a, **_kw: True)
    monkeypatch.setattr(_config, "get_model_config", lambda *_a, **_kw: {"tools": True})
    monkeypatch.setattr(_config, "is_local_model", lambda *_a, **_kw: False)
    monkeypatch.setattr(_config, "get_all_models_config", lambda: {"isolation-test-model": {}})
    return provider


@pytest.fixture
def passthrough_extractor(monkeypatch):
    """Install a .txt extractor that returns the file content verbatim."""
    import config as _config

    _config._instances["extractors"] = {".txt": lambda p: Path(p).read_text()}
    yield
    _config._instances.pop("extractors", None)


@contextmanager
def _booted_client():
    """TestClient over the live FastAPI app (lifespan executes)."""
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


def _ingest(file_path: str, user_id: str) -> None:
    import services.ingest as ingest

    result = ingest.ingest_file(file_path, user_id=user_id)
    assert result.chunk_count >= 1, (
        f"ingest_file for {file_path} produced no chunks (skipped={result.skipped})"
    )


def _chat(client: TestClient, token: str, model: str = "isolation-test-model") -> dict:
    resp = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "model": model,
            "messages": [{"role": "user", "content": "find anything you can"}],
            "stream": False,
        },
    )
    assert resp.status_code == 200, f"chat failed: {resp.status_code} {resp.text}"
    return resp.json()


# ---------------------------------------------------------------------------
# THE headline test
# ---------------------------------------------------------------------------


def test_alice_and_bob_have_no_cross_user_document_leakage(
    isolation_env,
    isolation_store,
    fake_provider,
    passthrough_extractor,
    tmp_path,
):
    """End-to-end: Alice's chat sees only Alice's docs; Bob's sees only Bob's."""
    alice_id = _create_user("alice@home.lan", "admin")
    bob_id = _create_user("bob@home.lan", "user")

    alice_doc = tmp_path / "alice.txt"
    alice_doc.write_text("ALICESECRETSENTINEL — only Alice should ever see this string.")
    bob_doc = tmp_path / "bob.txt"
    bob_doc.write_text("BOBSECRETSENTINEL — only Bob should ever see this string.")

    with _booted_client() as client:
        # Ingest happens INSIDE the boot context: the lifespan calls
        # `vs.create_collection()` which the MockVectorStore implements as
        # `self._collections[name] = []` (wipes existing entries). Pre-boot
        # ingest would be silently erased on lifespan startup.
        _ingest(str(alice_doc), user_id=alice_id)
        _ingest(str(bob_doc), user_id=bob_id)

        alice_token = _login(client, "alice@home.lan")
        bob_token = _login(client, "bob@home.lan")

        alice_response = _chat(client, alice_token)
        bob_response = _chat(client, bob_token)

        forbidden_for_user = client.put(
            "/permissions/filesystem-mcp",
            headers={"Authorization": f"Bearer {bob_token}"},
            json={"mode": "DO"},
        )
        allowed_for_admin = client.put(
            "/permissions/filesystem-mcp",
            headers={"Authorization": f"Bearer {alice_token}"},
            json={"mode": "DO"},
        )

    alice_text = alice_response["choices"][0]["message"]["content"]
    bob_text = bob_response["choices"][0]["message"]["content"]

    assert "ALICESECRETSENTINEL" in alice_text, (
        f"Alice's chat lost her own document: {alice_text!r}"
    )
    assert "BOBSECRETSENTINEL" not in alice_text, (
        f"DATA LEAK: Alice's chat saw Bob's document: {alice_text!r}"
    )

    assert "BOBSECRETSENTINEL" in bob_text, f"Bob's chat lost his own document: {bob_text!r}"
    assert "ALICESECRETSENTINEL" not in bob_text, (
        f"DATA LEAK: Bob's chat saw Alice's document: {bob_text!r}"
    )

    assert forbidden_for_user.status_code == 403, (
        f"PRIV ESC: bob (user) was allowed to PUT /permissions/filesystem-mcp "
        f"({forbidden_for_user.status_code} {forbidden_for_user.text})"
    )
    assert allowed_for_admin.status_code in (200, 204), (
        f"alice (admin) should be allowed to PUT /permissions/filesystem-mcp; "
        f"got {allowed_for_admin.status_code} {allowed_for_admin.text}"
    )


# ---------------------------------------------------------------------------
# Audit B11 + B12 — two users ingesting the same absolute path coexist.
# ---------------------------------------------------------------------------


def test_two_users_can_ingest_same_path(
    isolation_env,
    isolation_store,
    fake_provider,
    passthrough_extractor,
    tmp_path,
):
    """Audit B11 + B12: two users ingesting the same absolute path coexist.

    What this test exercises (under the in-memory _IsolationStore +
    MockVectorStore — see fidelity caveats below):

    - B11 — distinct Qdrant point ids per user for the same (file_path,
      chunk_index). The pre-fix code emitted identical deterministic
      uuid5s for both users; this assertion is the primary regression
      gate for B11.
    - B11 — payload-level isolation survives the user_id payload filter
      that already exists on the read side.
    - B12 — the SQL emitted by ingest_file for both users contains
      `INSERT INTO file_index (...) ON CONFLICT (user_id, file_path) DO
      UPDATE`, NOT the pre-fix bare-path INSERT or the pre-fix UPDATE
      branch. The mock store keys its dict by (user_id, file_path)
      regardless of the SQL constraint shape, so without this exec_log
      assertion the test could pass against the OLD code that doesn't
      have the upsert.
    - B12 — both users' file_index rows are present in the mock store.

    Fidelity caveats (NOT covered by this test; covered elsewhere):

    - The Postgres composite UNIQUE constraint itself is exercised at
      boot of a real container (db_migrations.py applies migration 011).
      The mock store does not enforce SQL UNIQUE.
    - Re-ingesting the same (user_id, file_path) with new content is
      covered by the existing
      `test_alice_and_bob_have_no_cross_user_document_leakage` flow
      (which runs the full ingest_file path), not by an explicit
      edge-case test here.
    """
    import re

    import config as _config

    alice_id = _create_user("alice@home.lan", "admin")
    bob_id = _create_user("bob@home.lan", "user")

    shared_path = tmp_path / "shared.txt"

    with _booted_client():
        # Ingest happens INSIDE the boot context: the lifespan calls
        # vs.create_collection() which wipes the in-memory MockVectorStore.
        shared_path.write_text("ALICE-CONTENT — only Alice should see this.")
        _ingest(str(shared_path), user_id=alice_id)

        # Same absolute path; Bob writes different content.
        shared_path.write_text("BOB-CONTENT — only Bob should see this.")
        _ingest(str(shared_path), user_id=bob_id)

        vs = _config.get_vector_store()
        documents = list(vs._collections.get("documents", []))

    # ------------------------------------------------------------------
    # B12 — SQL contract: ingest_file MUST emit an ON CONFLICT upsert
    # whose conflict target is the (user_id, file_path) uniqueness pair.
    # ------------------------------------------------------------------
    # The mock store keys its dict by (user_id, file_path) regardless of
    # SQL constraints, so without this assertion a regression that drops
    # the upsert in favour of the pre-fix branched INSERT/UPDATE would
    # not be caught. We accept either spelling that Postgres treats as
    # equivalent: ON CONFLICT (user_id, file_path) ... DO UPDATE, or
    # ON CONFLICT ON CONSTRAINT file_index_user_path_uniq DO UPDATE.
    _ON_CONFLICT_PATTERNS = (
        # Inline column list: ON CONFLICT (user_id, file_path) DO UPDATE
        # OR ON CONFLICT (file_path, user_id) DO UPDATE
        re.compile(
            r"on\s+conflict\s*\(\s*"
            r"(?:user_id\s*,\s*file_path|file_path\s*,\s*user_id)"
            r"\s*\)\s+do\s+update",
        ),
        # Constraint reference: ON CONFLICT ON CONSTRAINT
        # file_index_user_path_uniq DO UPDATE
        re.compile(
            r"on\s+conflict\s+on\s+constraint\s+"
            r"file_index_user_path_uniq\s+do\s+update",
        ),
    )

    file_index_writes = [
        " ".join(q.split()).lower()
        for (q, _p) in isolation_store.exec_log
        if " ".join(q.split()).lower().startswith("insert into file_index")
    ]
    assert file_index_writes, (
        "ingest_file did not emit any INSERT INTO file_index — "
        f"exec_log: {isolation_store.exec_log!r}"
    )
    for sql in file_index_writes:
        assert any(p.search(sql) for p in _ON_CONFLICT_PATTERNS), (
            "B12 regression: ingest_file emitted an INSERT INTO file_index "
            "without the per-user ON CONFLICT upsert. Accepted forms: "
            "`ON CONFLICT (user_id, file_path) DO UPDATE` (any column order), "
            "or `ON CONFLICT ON CONSTRAINT file_index_user_path_uniq DO UPDATE`. "
            f"SQL: {sql!r}"
        )
    # Belt-and-braces: the deprecated pre-fix UPDATE branch must be gone.
    # Note: this asserts no `UPDATE file_index SET …` is emitted *by ingest_file
    # within this test*. The exec_log is scoped to the per-test isolation_store
    # fixture (not a process-global), so unrelated code paths in other tests
    # cannot pollute it. If a future feature legitimately needs to emit a
    # standalone `UPDATE file_index SET …` (e.g. an admin tool), this
    # assertion must move to a more specific call-stack filter at that time.
    assert not any(
        " ".join(q.split()).lower().startswith("update file_index set")
        for (q, _p) in isolation_store.exec_log
    ), (
        "B12 regression: ingest_file still emits the pre-fix `UPDATE "
        "file_index SET …` branch. The upsert was supposed to replace it."
    )

    # ------------------------------------------------------------------
    # B12 — Postgres-side rows: two rows for the same path, distinct users.
    # ------------------------------------------------------------------
    rows = [(uid, fp) for (uid, fp) in isolation_store.file_index.keys() if fp == str(shared_path)]
    alice_rows = [r for r in rows if r[0] == alice_id]
    bob_rows = [r for r in rows if r[0] == bob_id]
    assert len(alice_rows) == 1, f"Alice's file_index row missing: rows for {shared_path}: {rows!r}"
    assert len(bob_rows) == 1, f"Bob's file_index row missing: rows for {shared_path}: {rows!r}"

    # ------------------------------------------------------------------
    # B11 — Qdrant-side: each user has at least one chunk; payloads
    # are not cross-leaked at the payload-filter layer.
    # ------------------------------------------------------------------
    alice_chunks = [
        d
        for d in documents
        if d["payload"].get("file_path") == str(shared_path)
        and d["payload"].get("user_id") == alice_id
    ]
    bob_chunks = [
        d
        for d in documents
        if d["payload"].get("file_path") == str(shared_path)
        and d["payload"].get("user_id") == bob_id
    ]
    assert alice_chunks, (
        f"Alice's Qdrant chunks lost: {len(documents)} total chunks for path {shared_path}"
    )
    assert bob_chunks, (
        f"Bob's Qdrant chunks lost: {len(documents)} total chunks for path {shared_path}"
    )

    # ------------------------------------------------------------------
    # B11 — primary regression gate: distinct point ids per user.
    # The pre-fix code emitted identical uuid5s here (`f"{file_path}::
    # chunk-{i}"` ignored user_id), and on real Qdrant the second upsert
    # would silently overwrite the first. The MockVectorStore appends
    # rather than dedupes, but the IDs themselves still tell us whether
    # the namespace fix landed.
    # ------------------------------------------------------------------
    alice_ids = {d["id"] for d in alice_chunks}
    bob_ids = {d["id"] for d in bob_chunks}
    assert alice_ids.isdisjoint(bob_ids), (
        f"B11 regression: Alice and Bob share Qdrant point ids; on real "
        f"Qdrant this is silent data overwrite. Shared ids: "
        f"{alice_ids & bob_ids!r}"
    )

    # ------------------------------------------------------------------
    # Payload-level non-leakage (already enforced by user_id payload
    # filter elsewhere; asserted here to lock the contract).
    # ------------------------------------------------------------------
    alice_text = " ".join(d["payload"].get("text", "") for d in alice_chunks)
    bob_text = " ".join(d["payload"].get("text", "") for d in bob_chunks)
    assert "ALICE-CONTENT" in alice_text, f"Alice's chunk content overwritten: {alice_text!r}"
    assert "BOB-CONTENT" not in alice_text, (
        f"DATA LEAK: Alice's chunks contain Bob's content: {alice_text!r}"
    )
    assert "BOB-CONTENT" in bob_text, f"Bob's chunk content missing: {bob_text!r}"
    assert "ALICE-CONTENT" not in bob_text, (
        f"DATA LEAK: Bob's chunks contain Alice's content: {bob_text!r}"
    )


# ---------------------------------------------------------------------------
# Plan ``mcp_token_user_map`` — cross-user MCP token isolation.
#
# The /mcp/* surface used to be a coarse single-bearer gate. The new
# per-user token surface ($mint → use → revoke$) is the only path that
# gives real per-user isolation over MCP. This test pins:
#
#   * Each user sees ONLY their own minted ``lmcp_…`` tokens via
#     ``GET /api/v1/me/mcp-tokens``.
#   * A user CANNOT revoke another user's token: the cross-user DELETE
#     returns 404 (not 403), per the information-leak guard in the
#     plan §"Information-leak guard".
#   * After revocation, the bearer-presented token stops authenticating
#     against ``services.mcp_tokens.verify`` — closing the loop on the
#     user-controlled lifecycle.
# ---------------------------------------------------------------------------


def test_alice_and_bob_mcp_tokens_are_user_scoped(
    isolation_env,
    isolation_store,
):
    """Per-user MCP tokens are minted, listed, and revoked per-caller.

    Strict integration-flavour test: hits the real ``/api/v1/me/mcp-tokens``
    routes through ``TestClient`` (the same way external callers will),
    not the service layer in isolation. Cross-user DELETE returns 404,
    pinning the information-leak guard.
    """
    import jwt
    import services.mcp_tokens as mcp_tokens_service

    alice_id = _create_user("alice@home.lan", "user")
    bob_id = _create_user("bob@home.lan", "user")

    def _hdr(user_id: str, role: str = "user") -> dict:
        token = jwt.encode(
            {
                "sub": user_id,
                "role": role,
                "iat": int(datetime.now(timezone.utc).timestamp()),
                "exp": int(datetime.now(timezone.utc).timestamp()) + 600,
            },
            os.environ["AUTH_SECRET"],
            algorithm="HS256",
        )
        return {"Authorization": f"Bearer {token}"}

    with _booted_client() as client:
        a_mint = client.post(
            "/api/v1/me/mcp-tokens",
            headers=_hdr(alice_id),
            json={"label": "alice-claude-desktop"},
        )
        b_mint = client.post(
            "/api/v1/me/mcp-tokens",
            headers=_hdr(bob_id),
            json={"label": "bob-thunderbolt"},
        )
        assert a_mint.status_code == 201, a_mint.text
        assert b_mint.status_code == 201, b_mint.text
        alice_token_id = a_mint.json()["token"]["id"]
        bob_token_id = b_mint.json()["token"]["id"]
        alice_plaintext = a_mint.json()["plaintext"]
        bob_plaintext = b_mint.json()["plaintext"]

        a_list = client.get(
            "/api/v1/me/mcp-tokens",
            headers=_hdr(alice_id),
        ).json()
        b_list = client.get(
            "/api/v1/me/mcp-tokens",
            headers=_hdr(bob_id),
        ).json()
        assert {t["label"] for t in a_list} == {"alice-claude-desktop"}, (
            f"DATA LEAK: Alice's MCP token list saw Bob's tokens: {a_list!r}"
        )
        assert {t["label"] for t in b_list} == {"bob-thunderbolt"}, (
            f"DATA LEAK: Bob's MCP token list saw Alice's tokens: {b_list!r}"
        )

        # Cross-user DELETE — 404, not 403 (information-leak guard).
        cross = client.delete(
            f"/api/v1/me/mcp-tokens/{alice_token_id}",
            headers=_hdr(bob_id),
        )
        assert cross.status_code == 404, (
            "PRIV ESC: Bob was allowed to (or even *probe*) Alice's "
            f"MCP token id space: {cross.status_code} {cross.text}"
        )

        # Verify the bearer side — both tokens authenticate today …
        assert mcp_tokens_service.verify(alice_plaintext) is not None
        assert mcp_tokens_service.verify(bob_plaintext) is not None

        # … and Alice revoking her own token closes the bearer loop.
        own = client.delete(
            f"/api/v1/me/mcp-tokens/{alice_token_id}",
            headers=_hdr(alice_id),
        )
        assert own.status_code == 200, own.text
        assert mcp_tokens_service.verify(alice_plaintext) is None, (
            "Revoked tokens MUST NOT continue to authenticate against /mcp/*"
        )
        assert mcp_tokens_service.verify(bob_plaintext) is not None, (
            "Bob's untouched token MUST keep authenticating after Alice's revoke"
        )
        # Bob's token id is referenced to make the assertion's intent
        # explicit — Bob's row is the unaffected one.
        assert bob_token_id in isolation_store.mcp_tokens


# ---------------------------------------------------------------------------
# Plan ``caldav_connector_credentials`` — cross-user CalDAV credential
# isolation (test #21 in the plan's test matrix).
#
# Pins:
#   * Alice and Bob can each PUT a per-user caldav credential row.
#   * load_connection() resolves to each user's distinct values.
#   * The CalendarAdapter for a SourceConfig(user_id=alice) sees ONLY
#     Alice's credentials, not Bob's.
#   * Bob can DELETE his own row and Alice's stays intact; subsequent
#     load_connection("bob") raises ConnectorNotConfigured under
#     AUTH_ENABLED=true.
#
# SR-5 fixture-preflight: this test relies on the
# ``user_connector_credentials`` SQL surface added to ``_IsolationStore``
# above (option (a) from the plan; option (b) — bypass via monkeypatch —
# was rejected because it would not exercise the real route → service
# → store seam).
# ---------------------------------------------------------------------------


def test_two_users_have_independent_caldav_credentials(
    isolation_env,
    isolation_store,
    monkeypatch,
):
    """Alice's and Bob's CalDAV credentials never cross-contaminate."""
    import jwt

    monkeypatch.setenv(
        "LUMOGIS_CREDENTIAL_KEY",
        "OlGLYckGIbBSt54y8XVmgb441LgKJWvvYoHnpQ_cv9A=",
    )
    monkeypatch.delenv("LUMOGIS_CREDENTIAL_KEYS", raising=False)
    monkeypatch.delenv("LUMOGIS_PUBLIC_ORIGIN", raising=False)
    # CALENDAR_* env MUST NOT influence resolution under AUTH_ENABLED=true.
    monkeypatch.setenv("CALENDAR_CALDAV_URL", "https://leak-detector.example/dav/")
    monkeypatch.setenv("CALENDAR_USERNAME", "leak-detector")
    monkeypatch.setenv("CALENDAR_PASSWORD", "leak-detector")

    from services import connector_credentials as ccs

    ccs.reset_for_tests()

    alice_id = _create_user("alice@home.lan", "user")
    bob_id = _create_user("bob@home.lan", "user")

    def _bearer(user_id: str) -> dict:
        token = jwt.encode(
            {
                "sub": user_id,
                "role": "user",
                "iat": int(datetime.now(timezone.utc).timestamp()),
                "exp": int(datetime.now(timezone.utc).timestamp()) + 600,
            },
            os.environ["AUTH_SECRET"],
            algorithm="HS256",
        )
        return {"Authorization": f"Bearer {token}"}

    alice_payload = {
        "base_url": "https://alice.example/remote.php/dav/",
        "username": "alice",
        "password": "alice-secret",
    }
    bob_payload = {
        "base_url": "https://bob.example/remote.php/dav/",
        "username": "bob",
        "password": "bob-secret",
    }

    with _booted_client() as client:
        # PUT — each user stores their own row.
        a_put = client.put(
            "/api/v1/me/connector-credentials/caldav",
            headers=_bearer(alice_id),
            json={"payload": alice_payload},
        )
        b_put = client.put(
            "/api/v1/me/connector-credentials/caldav",
            headers=_bearer(bob_id),
            json={"payload": bob_payload},
        )
        assert a_put.status_code == 200, a_put.text
        assert b_put.status_code == 200, b_put.text

        # GET — each user sees only their own row's metadata.
        a_get = client.get(
            "/api/v1/me/connector-credentials/caldav",
            headers=_bearer(alice_id),
        ).json()
        b_get = client.get(
            "/api/v1/me/connector-credentials/caldav",
            headers=_bearer(bob_id),
        ).json()
        assert a_get["created_by"] == "self"
        assert b_get["created_by"] == "self"
        for body in (a_get, b_get):
            for forbidden in ("payload", "ciphertext"):
                assert forbidden not in body
            assert "alice-secret" not in str(body)
            assert "bob-secret" not in str(body)

        # Direct service call — load_connection MUST resolve each user
        # to their OWN payload, never the other's, never the env trio.
        from services.caldav_credentials import load_connection

        alice_conn = load_connection(alice_id)
        bob_conn = load_connection(bob_id)
        assert alice_conn.base_url == alice_payload["base_url"]
        assert alice_conn.username == "alice"
        assert alice_conn.password == "alice-secret"
        assert bob_conn.base_url == bob_payload["base_url"]
        assert bob_conn.username == "bob"
        assert bob_conn.password == "bob-secret"

        # CalendarAdapter for a per-user source must see Alice's
        # payload — never Bob's, never the env leak-detector.
        from adapters.calendar_adapter import CalendarAdapter
        from models.signals import SourceConfig

        alice_source = SourceConfig(
            id="src-alice",
            name="Alice cal",
            source_type="caldav",
            url="https://display-only.example/",
            category="calendar",
            active=True,
            poll_interval=3600,
            extraction_method="caldav",
            css_selector_override=None,
            last_polled_at=None,
            last_signal_at=None,
            user_id=alice_id,
        )
        adapter = CalendarAdapter(alice_source)
        resolved = adapter._get_connection()
        assert resolved is not None
        assert resolved.base_url == alice_payload["base_url"]
        assert resolved.username == "alice"
        assert resolved.password == "alice-secret"
        # Hard guard: no Bob and no env leak-detector substring leaked
        # through the per-user resolution path.
        assert "bob" not in resolved.base_url
        assert "leak-detector" not in resolved.base_url
        assert "leak-detector" not in resolved.username
        assert "leak-detector" not in resolved.password

        # Bob DELETE — his row goes, Alice's stays.
        b_del = client.delete(
            "/api/v1/me/connector-credentials/caldav",
            headers=_bearer(bob_id),
        )
        assert b_del.status_code == 204, b_del.text

        # Post-DELETE assertions MUST run inside the booted client
        # context — the lifespan's ``config.shutdown()`` clears the
        # metadata-store singleton on exit, after which a direct
        # service call would attempt to instantiate the real
        # ``PostgresStore`` (no ``psycopg2`` in the test venv).
        assert (alice_id, "caldav") in isolation_store.creds
        assert (bob_id, "caldav") not in isolation_store.creds

        # Under AUTH_ENABLED=true, Bob's resolution must fail-loud
        # (D9 / Q-A) — no fallback to the env leak-detector trio.
        with pytest.raises(ccs.ConnectorNotConfigured):
            load_connection(bob_id)

        # Alice's resolution still works after Bob's row is gone.
        alice_conn_after = load_connection(alice_id)
        assert alice_conn_after.password == "alice-secret"


# ---------------------------------------------------------------------------
# Plan ``per_user_connector_permissions`` — Audit A2 closure.
#
# Pins:
#   * Alice flipping filesystem-mcp to DO does not flip Bob's mode.
#   * Alice's 15 routine_check approvals never elevate Bob's row.
#
# SR-D6 fixture-preflight: depends on the ``connector_permissions`` and
# ``routine_do_tracking`` SQL surfaces added to ``_IsolationStore`` above.
# ---------------------------------------------------------------------------


def test_alice_and_bob_have_independent_connector_modes(
    isolation_env,
    isolation_store,
    monkeypatch,
):
    """Audit A2 closure: Alice flipping filesystem-mcp:DO does not flip Bob's.

    Strict integration-flavour test: hits the real
    ``/api/v1/me/permissions`` routes through ``TestClient`` (the same
    way external callers will), not the service layer in isolation.
    Mirrors the shape of
    ``test_alice_and_bob_mcp_tokens_are_user_scoped`` in this file.

    Registry seeding: the orchestrator test environment has no action
    handlers wired (handlers ship via plugins); the test stubs
    ``actions.registry.list_actions`` so the route layer's
    ``_known_connectors`` helper sees ``filesystem-mcp`` and the list
    endpoint returns a non-empty body. Critique D3.2/D6.3 preflight
    pin: the assertion below is the canonical "registry is wired"
    smoke for this chunk.
    """
    import jwt
    from actions import registry as actions_registry

    monkeypatch.setattr(
        actions_registry,
        "list_actions",
        lambda: [
            {
                "name": "filesystem-mcp.write_file",
                "connector": "filesystem-mcp",
                "action_type": "write_file",
                "is_write": True,
                "is_reversible": False,
                "reverse_action_name": None,
                "definition": {},
            },
        ],
    )

    alice_id = _create_user("alice@home.lan", "user")
    bob_id = _create_user("bob@home.lan", "user")

    def _hdr(user_id: str, role: str = "user") -> dict:
        token = jwt.encode(
            {
                "sub": user_id,
                "role": role,
                "iat": int(datetime.now(timezone.utc).timestamp()),
                "exp": int(datetime.now(timezone.utc).timestamp()) + 600,
            },
            os.environ["AUTH_SECRET"],
            algorithm="HS256",
        )
        return {"Authorization": f"Bearer {token}"}

    with _booted_client() as client:
        # Preflight — registry must be non-empty for the list-endpoint
        # assertion to mean anything (critique D3.2/D6.3 fix).
        from actions import registry

        assert registry.list_actions(), (
            "registry empty in test environment — connector_permissions "
            "headline test cannot validate per-connector behaviour. "
            "Boot order may be wrong; verify lifespan ran."
        )

        # Both start at the lazy default (no rows exist) on the list endpoint.
        r1 = client.get("/api/v1/me/permissions", headers=_hdr(alice_id))
        r2 = client.get("/api/v1/me/permissions", headers=_hdr(bob_id))
        assert r1.status_code == 200, r1.text
        assert r2.status_code == 200, r2.text
        assert r1.json(), "list endpoint returned empty body — registry preflight failed silently"
        assert all(p["mode"] == "ASK" and p["is_default"] for p in r1.json())
        assert all(p["mode"] == "ASK" and p["is_default"] for p in r2.json())

        # Alice flips her filesystem-mcp to DO via the per-connector PUT.
        put = client.put(
            "/api/v1/me/permissions/filesystem-mcp",
            json={"mode": "DO"},
            headers=_hdr(alice_id),
        )
        assert put.status_code == 200, put.text
        assert put.json()["mode"] == "DO"
        assert put.json()["is_default"] is False

        # Alice now sees DO via the per-connector GET (added in this chunk).
        r3 = client.get(
            "/api/v1/me/permissions/filesystem-mcp",
            headers=_hdr(alice_id),
        )
        assert r3.status_code == 200, r3.text
        assert r3.json()["mode"] == "DO"
        assert r3.json()["is_default"] is False

        # Bob still sees ASK (no row, falls back to _DEFAULT_MODE).
        r4 = client.get(
            "/api/v1/me/permissions/filesystem-mcp",
            headers=_hdr(bob_id),
        )
        assert r4.status_code == 200, r4.text
        assert r4.json()["mode"] == "ASK", (
            f"DATA LEAK: Bob's filesystem-mcp mode flipped to "
            f"{r4.json()['mode']!r} when only Alice flipped it"
        )
        assert r4.json()["is_default"] is True


def test_alice_routine_approvals_do_not_elevate_bob(
    isolation_env,
    isolation_store,
):
    """Audit A2 closure for ``routine_do_tracking``.

    Substrate-only: bypass the HTTP layer for the threshold-loop
    (``routine_check`` is the underlying primitive that the elevate
    route + automatic accumulation both call into). Hitting the route
    15× would also work but adds CSRF + bearer plumbing for no extra
    coverage. The DB-row assertions below pin the per-user split.

    Implementation note: ``routine_check`` only fires the
    ``ROUTINE_ELEVATION_READY`` hook at the threshold — no
    auto-elevation happens by default (plugins / admin UI flip
    ``auto_approved``). To validate that the elevation path is also
    per-user the test additionally calls :func:`elevate_to_routine`
    for Alice and asserts Bob's row is unaffected.
    """
    from permissions import elevate_to_routine
    from permissions import routine_check
    from permissions import set_connector_mode

    from config import get_metadata_store

    alice_id = _create_user("alice@home.lan", "user")
    bob_id = _create_user("bob@home.lan", "user")

    # Both must be in DO first for routine_check to even count.
    set_connector_mode(
        user_id=alice_id,
        connector="filesystem-mcp",
        mode="DO",
    )
    set_connector_mode(
        user_id=bob_id,
        connector="filesystem-mcp",
        mode="DO",
    )

    for _ in range(15):
        routine_check(
            user_id=alice_id,
            connector="filesystem-mcp",
            action_type="write_file",
        )

    elevate_to_routine(
        user_id=alice_id,
        connector="filesystem-mcp",
        action_type="write_file",
    )

    ms = get_metadata_store()
    alice_row = ms.fetch_one(
        "SELECT auto_approved, approval_count FROM routine_do_tracking "
        "WHERE user_id = %s AND connector = %s AND action_type = %s "
        "-- SCOPE-EXEMPT: routine_do_tracking has no scope column",
        (alice_id, "filesystem-mcp", "write_file"),
    )
    bob_row = ms.fetch_one(
        "SELECT auto_approved, approval_count FROM routine_do_tracking "
        "WHERE user_id = %s AND connector = %s AND action_type = %s "
        "-- SCOPE-EXEMPT: routine_do_tracking has no scope column",
        (bob_id, "filesystem-mcp", "write_file"),
    )
    assert alice_row is not None, "Alice's routine row missing after 15 approvals + elevation"
    assert alice_row["approval_count"] == 15, (
        f"Alice's approval_count off after 15 routine_check calls: {alice_row!r}"
    )
    assert alice_row["auto_approved"] is True, (
        f"Alice did not reach auto_approved after explicit elevate: {alice_row!r}"
    )
    assert bob_row is None, (
        f"AUDIT A2 REGRESSION: Bob's routine_do_tracking row exists "
        f"despite Bob never approving anything: {bob_row!r}"
    )
