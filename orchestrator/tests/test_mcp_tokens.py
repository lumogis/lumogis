# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for ``services/mcp_tokens.py``.

Covers every public-API entry point + the throttle behaviour + the
collision-budget guard + the SHA-256 contract + the ``_emit_audit``
writes-go-to-audit_log-not-action_log assertion. Pinned by the test
matrix in plan ``mcp_token_user_map`` §"Unit tests".

The tests use a small in-memory :class:`_FakeStore` (mirrors the
``_IsolationStore`` in ``tests/integration/test_two_user_isolation.py``
but scoped to ``mcp_tokens`` only, plus the ``audit_log`` writes the
service emits via :func:`actions.audit.write_audit`).
"""

from __future__ import annotations

import contextlib
import logging
import re
import uuid
from datetime import datetime
from datetime import timedelta
from datetime import timezone

import pytest

# ---------------------------------------------------------------------------
# In-memory store: knows about `mcp_tokens` rows and `audit_log` rows.
# ---------------------------------------------------------------------------


class _FakeStore:
    """In-memory MetadataStore covering the queries `mcp_tokens` issues.

    Recognised statements (normalised: lowercased, single-spaced):

      * INSERT INTO mcp_tokens (...)
      * SELECT * FROM mcp_tokens WHERE id = %s
      * SELECT * FROM mcp_tokens WHERE token_prefix = %s AND revoked_at IS NULL
      * SELECT * FROM mcp_tokens WHERE user_id = %s [AND revoked_at IS NULL] ORDER BY ...
      * SELECT * FROM mcp_tokens [WHERE revoked_at IS NULL] ORDER BY ...
      * UPDATE mcp_tokens SET revoked_at = NOW() WHERE id = %s AND revoked_at IS NULL
      * UPDATE mcp_tokens SET revoked_at = NOW() WHERE user_id = %s AND revoked_at IS NULL RETURNING *
      * UPDATE mcp_tokens SET last_used_at = NOW() WHERE id = %s
      * INSERT INTO audit_log (...) RETURNING id
    """

    def __init__(self) -> None:
        self.tokens: dict[str, dict] = {}
        self.audit: list[dict] = []
        self.action_log: list[dict] = []
        self.exec_log: list[tuple[str, tuple]] = []
        self._fail_on_update_last_used: bool = False
        self._fail_on_insert: bool = False
        self._force_unique_violation_on_insert: bool = False

    def ping(self) -> bool:
        return True

    def close(self) -> None:
        pass

    @contextlib.contextmanager
    def transaction(self):
        yield

    @staticmethod
    def _norm(query: str) -> str:
        return " ".join(query.split()).lower()

    def execute(self, query: str, params: tuple | None = None) -> None:
        self.exec_log.append((query, params or ()))
        q = self._norm(query)
        p = params or ()

        if q.startswith("insert into mcp_tokens"):
            if self._force_unique_violation_on_insert:
                raise RuntimeError(
                    "duplicate key value violates unique constraint mcp_tokens_active_prefix_uniq"
                )
            if self._fail_on_insert:
                raise RuntimeError("simulated mcp_tokens insert failure")
            token_id, user_id, token_prefix, token_hash, label, scopes = p
            # Active-prefix uniqueness guard mirroring the partial unique index.
            for row in self.tokens.values():
                if row["revoked_at"] is None and row["token_prefix"] == token_prefix:
                    raise RuntimeError(
                        "duplicate key value violates unique constraint "
                        "mcp_tokens_active_prefix_uniq"
                    )
            self.tokens[token_id] = {
                "id": token_id,
                "user_id": user_id,
                "token_prefix": token_prefix,
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
            row = self.tokens.get(tid)
            if row is not None and row["revoked_at"] is None:
                row["revoked_at"] = datetime.now(timezone.utc)
            return

        if q.startswith("update mcp_tokens set last_used_at = now() where id = %s"):
            (tid,) = p
            if self._fail_on_update_last_used:
                raise RuntimeError("simulated last_used_at update failure")
            row = self.tokens.get(tid)
            if row is not None:
                row["last_used_at"] = datetime.now(timezone.utc)
            return

        # No-op for unknown statements; tests that care assert on the exec log.

    def fetch_one(self, query: str, params: tuple | None = None) -> dict | None:
        q = self._norm(query)
        p = params or ()

        if q.startswith("select * from mcp_tokens where id = %s"):
            (tid,) = p
            row = self.tokens.get(tid)
            return dict(row) if row else None

        if q.startswith("select * from mcp_tokens where token_prefix = %s and revoked_at is null"):
            (prefix,) = p
            for row in self.tokens.values():
                if row["token_prefix"] == prefix and row["revoked_at"] is None:
                    return dict(row)
            return None

        return None

    def fetch_all(self, query: str, params: tuple | None = None) -> list[dict]:
        q = self._norm(query)
        p = params or ()

        if q.startswith(
            "update mcp_tokens set revoked_at = now() where user_id = %s and revoked_at is null returning *"
        ):
            (uid,) = p
            now = datetime.now(timezone.utc)
            updated: list[dict] = []
            for row in self.tokens.values():
                if row["user_id"] == uid and row["revoked_at"] is None:
                    row["revoked_at"] = now
                    updated.append(dict(row))
            return updated

        if q.startswith("select * from mcp_tokens where user_id = %s and revoked_at is null"):
            (uid,) = p
            return sorted(
                (
                    dict(r)
                    for r in self.tokens.values()
                    if r["user_id"] == uid and r["revoked_at"] is None
                ),
                key=lambda r: r["created_at"],
                reverse=True,
            )

        if q.startswith("select * from mcp_tokens where user_id = %s order by created_at"):
            (uid,) = p
            return sorted(
                (dict(r) for r in self.tokens.values() if r["user_id"] == uid),
                key=lambda r: r["created_at"],
                reverse=True,
            )

        if q.startswith("select * from mcp_tokens where revoked_at is null order by"):
            return sorted(
                (dict(r) for r in self.tokens.values() if r["revoked_at"] is None),
                key=lambda r: r["created_at"],
                reverse=True,
            )

        if q.startswith("select * from mcp_tokens order by created_at"):
            return sorted(
                (dict(r) for r in self.tokens.values()),
                key=lambda r: r["created_at"],
                reverse=True,
            )

        return []


class _AuditAwareStore(_FakeStore):
    """`_FakeStore` plus an `INSERT INTO audit_log ... RETURNING id` handler.

    The plain-fetch_one path of `actions/audit.py::write_audit` issues
    a single `INSERT … RETURNING id`. We mock that here so the audit
    write actually lands somewhere our tests can inspect.
    """

    def fetch_one(self, query: str, params: tuple | None = None) -> dict | None:
        q = self._norm(query)
        if q.startswith("insert into audit_log"):
            row_id = len(self.audit) + 1
            self.audit.append(
                {
                    "id": row_id,
                    "user_id": params[0],
                    "action_name": params[1],
                    "connector": params[2],
                    "mode": params[3],
                    "input_summary": params[4],
                    "result_summary": params[5],
                    # remaining fields ignored
                }
            )
            return {"id": row_id}
        return super().fetch_one(query, params)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store(monkeypatch):
    """Install an `_AuditAwareStore` as the metadata-store singleton.

    Also clears the module-level `_LAST_STAMP_CACHE` so per-test state
    doesn't leak (the LRU is process-scoped by design — see D5).
    """
    import config as _config
    from services import mcp_tokens

    s = _AuditAwareStore()
    _config._instances["metadata_store"] = s
    mcp_tokens._LAST_STAMP_CACHE.clear()
    yield s
    _config._instances.pop("metadata_store", None)
    mcp_tokens._LAST_STAMP_CACHE.clear()


# ---------------------------------------------------------------------------
# Mint tests
# ---------------------------------------------------------------------------


def test_mint_returns_lmcp_prefixed_50_char_token(store):
    """D2: minted bearer is `lmcp_` + 45 base32 lowercase chars (no padding)."""
    from services.mcp_tokens import mint

    row, plaintext = mint("alice", "Claude Desktop")

    assert plaintext.startswith("lmcp_")
    assert len(plaintext) == 50
    body = plaintext[len("lmcp_") :]
    assert len(body) == 45
    assert re.fullmatch(r"[a-z2-7]+", body), f"body must be base32 lowercase: {body!r}"
    assert "=" not in body, "no padding per D2"
    assert row.label == "Claude Desktop"
    assert row.user_id == "alice"
    assert row.scopes is None  # D3 default unrestricted
    assert row.revoked_at is None


def test_mint_token_prefix_is_first_16_chars_of_body(store):
    """D2: stored `token_prefix` equals the first 16 chars of the body."""
    from services.mcp_tokens import mint

    row, plaintext = mint("alice", "lbl")
    body = plaintext[len("lmcp_") :]
    assert row.token_prefix == body[:16]
    assert len(row.token_prefix) == 16


def test_mcp_token_hash_is_sha256(store):
    """D9: token_hash = SHA-256 hex of the plaintext."""
    import hashlib

    from services.mcp_tokens import mint

    row, plaintext = mint("alice", "lbl")
    expected = hashlib.sha256(plaintext.encode("ascii")).hexdigest()
    assert row.token_hash == expected
    assert len(row.token_hash) == 64


def test_mint_inserts_scopes_as_null_not_empty_array(store):
    """D3: the INSERT MUST pass `scopes=None` (NOT `[]`) so SQL records NULL."""
    from services.mcp_tokens import mint

    mint("alice", "lbl")
    inserts = [
        (q, p)
        for (q, p) in store.exec_log
        if "insert into mcp_tokens" in " ".join(q.split()).lower()
    ]
    assert len(inserts) == 1
    _, params = inserts[0]
    # params order: (id, user_id, token_prefix, token_hash, label, scopes)
    assert params[5] is None, (
        "D3: scopes column MUST be inserted as Python None (SQL NULL); "
        f"got {params[5]!r} which would be misinterpreted as 'no access'"
    )


def test_mint_collision_regenerates(store, monkeypatch):
    """A one-time prefix collision triggers regeneration; second attempt wins.

    Strategy: seed an active token, then patch ``secrets.token_bytes`` so
    the FIRST call inside the mint-under-test returns bytes that base32
    to the same first-16-char prefix as the seed (so the partial unique
    index throws), and the SECOND call returns fresh CSPRNG output.
    """
    import services.mcp_tokens as svc

    real_token_bytes = svc.secrets.token_bytes
    seed, _ = svc.mint("seed-user", "seed")

    # Reverse-engineer 28 raw bytes whose base32 lowercase no-pad rendering
    # has the same first-16-char prefix as `seed.token_prefix`. Since base32
    # encodes 5 bits per char, the first 16 chars cover the first 80 bits =
    # 10 bytes, plus the upper 0 bits of byte 11 (no — actually exactly 10
    # bytes since 16*5 = 80 bits). So copying the seed's first 10 raw bytes
    # is sufficient; the remaining 18 bytes can be anything fresh.
    # 16 base32 chars decodes to exactly 10 bytes (16*5 = 80 bits) and is
    # already a multiple of 8 — no padding required.
    seed_raw_first10 = svc.base64.b32decode(seed.token_prefix.upper().encode("ascii"))
    assert len(seed_raw_first10) == 10
    colliding = seed_raw_first10 + real_token_bytes(svc._TOKEN_BODY_BYTES - 10)

    canned = iter([colliding])

    def _fake_token_bytes(n):
        try:
            return next(canned)
        except StopIteration:
            return real_token_bytes(n)

    monkeypatch.setattr(svc.secrets, "token_bytes", _fake_token_bytes)

    fresh, _ = svc.mint("alice", "lbl")
    assert fresh.token_prefix != seed.token_prefix
    assert fresh.user_id == "alice"


def test_mint_collision_budget_exhausts_loud(store, monkeypatch):
    """Every regeneration colliding raises RuntimeError, NOT an infinite loop."""
    import services.mcp_tokens as svc

    monkeypatch.setattr(store, "_force_unique_violation_on_insert", True, raising=False)
    with pytest.raises(RuntimeError, match="collision retry budget exhausted"):
        svc.mint("alice", "lbl")


# ---------------------------------------------------------------------------
# Verify tests
# ---------------------------------------------------------------------------


def test_verify_returns_row_for_active_token(store):
    from services.mcp_tokens import mint
    from services.mcp_tokens import verify

    row, plaintext = mint("alice", "lbl")
    got = verify(plaintext)
    assert got is not None
    assert got.id == row.id


def test_verify_returns_none_for_unknown_prefix(store):
    from services.mcp_tokens import verify

    fabricated = "lmcp_" + "a" * 45
    assert verify(fabricated) is None


def test_verify_returns_none_for_known_prefix_wrong_hash(store, caplog):
    """Known prefix + flipped char in the body returns None AND logs WARNING."""
    from services.mcp_tokens import mint
    from services.mcp_tokens import verify

    _, plaintext = mint("alice", "lbl")
    # Flip a char AFTER the 16-char prefix so the prefix still hits but the
    # hash mismatches.
    body = plaintext[len("lmcp_") :]
    head, tail = body[:16], body[16:]
    flipped = ("3" if tail[0] != "3" else "4") + tail[1:]
    bogus = "lmcp_" + head + flipped

    caplog.set_level(logging.WARNING, logger="services.mcp_tokens")
    assert verify(bogus) is None
    msgs = " ".join(rec.getMessage() for rec in caplog.records)
    assert "hash mismatch" in msgs


def test_verify_returns_none_for_revoked_token(store):
    """Partial unique index hides revoked rows; `verify()` returns None."""
    from services.mcp_tokens import mint
    from services.mcp_tokens import revoke
    from services.mcp_tokens import verify

    row, plaintext = mint("alice", "lbl")
    revoke(row.id, by_user_id="alice", by_role="user")
    assert verify(plaintext) is None


def test_verify_does_not_leak_plaintext_in_warning(store, caplog):
    """Mismatch WARNING never includes the bearer or its prefix."""
    from services.mcp_tokens import mint
    from services.mcp_tokens import verify

    _, plaintext = mint("alice", "lbl")
    body = plaintext[len("lmcp_") :]
    head, tail = body[:16], body[16:]
    bogus = "lmcp_" + head + ("4" if tail[0] != "4" else "5") + tail[1:]

    caplog.set_level(logging.WARNING, logger="services.mcp_tokens")
    verify(bogus)
    for rec in caplog.records:
        msg = rec.getMessage()
        assert plaintext not in msg
        assert bogus not in msg
        assert head not in msg, "even the 16-char prefix must not appear in mismatch logs"


def test_verify_returns_none_for_non_lmcp_bearer(store):
    """Bearers that are not `lmcp_…` shape return None without DB hit."""
    from services.mcp_tokens import verify

    assert verify("Bearer xyz") is None
    assert verify("") is None
    assert verify("lmcp_short") is None  # body too short


# ---------------------------------------------------------------------------
# Throttle tests (D5)
# ---------------------------------------------------------------------------


def test_stamp_used_throttles_to_5_minutes(store):
    """Two verifies in quick succession produce exactly one UPDATE last_used_at."""
    from services.mcp_tokens import mint
    from services.mcp_tokens import verify

    _, plaintext = mint("alice", "lbl")
    store.exec_log.clear()  # ignore the INSERT from mint

    verify(plaintext)
    verify(plaintext)
    updates = [
        (q, p)
        for (q, p) in store.exec_log
        if "update mcp_tokens set last_used_at" in " ".join(q.split()).lower()
    ]
    assert len(updates) == 1, (
        f"throttle should suppress the second `last_used_at` UPDATE; got {len(updates)}"
    )


def test_stamp_used_writes_after_throttle_window(store, monkeypatch):
    """Fast-forwarding the cache by 6 minutes triggers a second UPDATE."""
    import services.mcp_tokens as svc

    _, plaintext = svc.mint("alice", "lbl")
    store.exec_log.clear()
    svc.verify(plaintext)

    # Backdate the cache entry by 6 minutes so the throttle window has passed.
    cache_key = next(iter(svc._LAST_STAMP_CACHE._data))
    svc._LAST_STAMP_CACHE._data[cache_key] = datetime.now(timezone.utc) - timedelta(minutes=6)

    svc.verify(plaintext)
    updates = [
        (q, p)
        for (q, p) in store.exec_log
        if "update mcp_tokens set last_used_at" in " ".join(q.split()).lower()
    ]
    assert len(updates) == 2


def test_stamp_used_failure_does_not_propagate(store, caplog):
    """A failing UPDATE is logged at WARNING; verify() still returns the row."""
    from services.mcp_tokens import mint
    from services.mcp_tokens import verify

    row, plaintext = mint("alice", "lbl")
    store._fail_on_update_last_used = True

    caplog.set_level(logging.WARNING, logger="services.mcp_tokens")
    got = verify(plaintext)
    assert got is not None
    assert got.id == row.id
    msgs = " ".join(rec.getMessage() for rec in caplog.records)
    assert "stamp_used failed" in msgs


# ---------------------------------------------------------------------------
# List / get / revoke / cascade tests
# ---------------------------------------------------------------------------


def test_list_for_user_excludes_revoked_by_default(store):
    from services.mcp_tokens import list_for_user
    from services.mcp_tokens import mint
    from services.mcp_tokens import revoke

    a1, _ = mint("alice", "a1")
    a2, _ = mint("alice", "a2")
    revoke(a1.id, by_user_id="alice", by_role="user")

    active = list_for_user("alice")
    assert {t.id for t in active} == {a2.id}

    everything = list_for_user("alice", include_revoked=True)
    assert {t.id for t in everything} == {a1.id, a2.id}


def test_list_for_user_filters_by_user_id(store):
    from services.mcp_tokens import list_for_user
    from services.mcp_tokens import mint

    alice, _ = mint("alice", "a")
    bob, _ = mint("bob", "b")

    assert {t.id for t in list_for_user("alice")} == {alice.id}
    assert {t.id for t in list_for_user("bob")} == {bob.id}


def test_list_all_excludes_revoked_by_default(store):
    """`list_all` mirrors `list_for_user` semantics for the include_revoked toggle."""
    from services.mcp_tokens import list_all
    from services.mcp_tokens import mint
    from services.mcp_tokens import revoke

    a, _ = mint("alice", "a")
    b, _ = mint("bob", "b")
    revoke(a.id, by_user_id="alice", by_role="user")

    assert {t.id for t in list_all()} == {b.id}
    assert {t.id for t in list_all(include_revoked=True)} == {a.id, b.id}


def test_get_by_id_returns_revoked_rows(store):
    """`get_by_id` does NOT filter by `revoked_at` (route-layer ownership check)."""
    from services.mcp_tokens import get_by_id
    from services.mcp_tokens import mint
    from services.mcp_tokens import revoke

    row, _ = mint("alice", "a")
    revoke(row.id, by_user_id="alice", by_role="user")

    fetched = get_by_id(row.id)
    assert fetched is not None
    assert fetched.id == row.id
    assert fetched.revoked_at is not None


def test_get_by_id_returns_none_for_unknown(store):
    from services.mcp_tokens import get_by_id

    assert get_by_id(uuid.uuid4().hex) is None


def test_revoke_is_idempotent(store):
    """Re-revoking returns the same row with the original `revoked_at`."""
    from services.mcp_tokens import mint
    from services.mcp_tokens import revoke

    row, _ = mint("alice", "a")

    first = revoke(row.id, by_user_id="alice", by_role="user")
    assert first is not None
    assert first.revoked_at is not None
    first_revoked_at = first.revoked_at

    second = revoke(row.id, by_user_id="alice", by_role="user")
    assert second is not None
    assert second.revoked_at == first_revoked_at


def test_revoke_returns_none_for_unknown(store):
    from services.mcp_tokens import revoke

    assert revoke(uuid.uuid4().hex, by_user_id="alice", by_role="user") is None


def test_cascade_revoke_for_user_revokes_only_active_tokens(store):
    """Already-revoked rows are untouched; only active rows for the user flip."""
    from services.mcp_tokens import cascade_revoke_for_user
    from services.mcp_tokens import list_for_user
    from services.mcp_tokens import mint
    from services.mcp_tokens import revoke

    a1, _ = mint("alice", "a1")
    a2, _ = mint("alice", "a2")
    b, _ = mint("bob", "b")

    revoke(a1.id, by_user_id="alice", by_role="user")  # already revoked
    a1_revoked_at = next(
        t for t in list_for_user("alice", include_revoked=True) if t.id == a1.id
    ).revoked_at

    cascaded = cascade_revoke_for_user("alice", by_admin_user_id="admin1")

    assert {t.id for t in cascaded} == {a2.id}, (
        "cascade should ONLY return previously-active rows it just revoked"
    )

    # Bob's token unaffected.
    assert list_for_user("bob")
    # Alice's earlier-revoked token's revoked_at unchanged.
    a1_after = next(t for t in list_for_user("alice", include_revoked=True) if t.id == a1.id)
    assert a1_after.revoked_at == a1_revoked_at


def test_cascade_revoke_for_user_empty_when_no_active_tokens(store):
    from services.mcp_tokens import cascade_revoke_for_user

    assert cascade_revoke_for_user("nobody", by_admin_user_id="admin1") == []


# ---------------------------------------------------------------------------
# Audit emission contract (D14)
# ---------------------------------------------------------------------------


def test_emit_audit_writes_to_audit_log_not_action_log(store):
    """D14: `_emit_audit` lands in `audit_log`, never `action_log`."""
    from services.mcp_tokens import _emit_audit

    _emit_audit(
        "__mcp_token__.minted",
        user_id="alice",
        input_summary={"label": "x", "mcp_token_id": "t1"},
        result_summary={"token_prefix": "abcdef0123456789"},
    )

    assert len(store.audit) == 1
    row = store.audit[0]
    assert row["action_name"] == "__mcp_token__.minted"
    assert row["connector"] == "auth"
    assert row["mode"] == "system"
    assert row["user_id"] == "alice"
    # action_log gets nothing — the writer is `permissions.log_action`, NOT us.
    assert store.action_log == []


def test_emit_audit_failure_is_swallowed(store, monkeypatch, caplog):
    """A failing audit write logs `_log.exception` and never re-raises."""
    from services import mcp_tokens

    def _boom(*_a, **_kw):
        raise RuntimeError("audit table is on fire")

    monkeypatch.setattr(mcp_tokens, "write_audit", _boom)
    caplog.set_level(logging.ERROR, logger="services.mcp_tokens")

    # Should NOT raise.
    mcp_tokens._emit_audit(
        "__mcp_token__.minted",
        user_id="alice",
        input_summary={},
        result_summary={},
    )

    msgs = " ".join(rec.getMessage() for rec in caplog.records)
    assert "audit write" in msgs.lower()


# ---------------------------------------------------------------------------
# Pydantic redaction (D15)
# ---------------------------------------------------------------------------


def test_pydantic_repr_does_not_leak_plaintext(store):
    """D15: token_hash, token_prefix, plaintext are `Field(repr=False)`."""
    from models.mcp_token import McpTokenPublic
    from models.mcp_token import MintMcpTokenResponse
    from services.mcp_tokens import mint

    row, plaintext = mint("alice", "lbl")
    rep = repr(row)
    assert row.token_hash not in rep
    assert row.token_prefix not in rep

    response = MintMcpTokenResponse(
        token=McpTokenPublic(
            id=row.id,
            label=row.label,
            scopes=row.scopes,
            created_at=row.created_at,
            last_used_at=None,
            expires_at=None,
            revoked_at=None,
        ),
        plaintext=plaintext,
    )
    assert plaintext not in repr(response)


# ---------------------------------------------------------------------------
# D16: MintMcpTokenRequest forbids unknown fields
# ---------------------------------------------------------------------------


def test_mint_request_rejects_expires_at_field():
    """D4 + D16: `expires_at` in the body is a 422 (Pydantic `extra_forbidden`)."""
    from models.mcp_token import MintMcpTokenRequest
    from pydantic import ValidationError

    with pytest.raises(ValidationError) as exc_info:
        MintMcpTokenRequest(label="x", expires_at="2027-01-01T00:00:00Z")
    errors = exc_info.value.errors()
    assert any(e["type"] == "extra_forbidden" for e in errors)


def test_mint_request_rejects_arbitrary_unknown_fields():
    """D16: any unknown field is 422 — defends D4 explicitly."""
    from models.mcp_token import MintMcpTokenRequest
    from pydantic import ValidationError

    with pytest.raises(ValidationError) as exc_info:
        MintMcpTokenRequest(label="x", scopes=["memory.search"])
    errors = exc_info.value.errors()
    assert any(e["type"] == "extra_forbidden" for e in errors)


# ---------------------------------------------------------------------------
# D9 contract pin: hmac.compare_digest is on the verify path
# ---------------------------------------------------------------------------


def test_check_mcp_bearer_uses_compare_digest():
    """Regex against source: D9 mandates constant-time compare in verify()."""
    import inspect

    from services import mcp_tokens

    src = inspect.getsource(mcp_tokens)
    assert "hmac.compare_digest" in src
