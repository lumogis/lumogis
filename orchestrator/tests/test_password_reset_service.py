# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Service tests for forgot-password tokens (LUM-65).

Covers the token lifecycle through the service's public functions: mint,
redeem, expiry, replay, and the cross-channel invalidation that makes an admin
or CLI password reset kill a live emailed link.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from datetime import timedelta
from datetime import timezone

import pytest
from tests.test_auth_phase1 import FakeUsersStore

import config
from services import password_reset
from services import users as users_service


class _ResetFakeStore(FakeUsersStore):
    """``FakeUsersStore`` + the ``password_reset_tokens`` statements we issue."""

    def __init__(self) -> None:
        super().__init__()
        self.reset_tokens: dict[str, dict] = {}

    @staticmethod
    def _norm(query: str) -> str:
        return " ".join(query.split()).lower()

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    def execute(self, query: str, params: tuple | None = None) -> None:
        q = self._norm(query)
        p = params or ()
        if q.startswith("insert into password_reset_tokens"):
            tid, user_id, prefix, thash, expires_at = p
            for row in self.reset_tokens.values():
                if row["token_prefix"] == prefix:
                    raise RuntimeError(
                        "duplicate key value violates unique constraint "
                        "password_reset_tokens_token_prefix_key"
                    )
            self.reset_tokens[tid] = {
                "id": tid,
                "user_id": user_id,
                "token_prefix": prefix,
                "token_hash": thash,
                "created_at": self._now(),
                "expires_at": expires_at,
                "used_at": None,
            }
            return
        return super().execute(query, params)

    def fetch_all(self, query: str, params: tuple | None = None) -> list[dict]:
        q = self._norm(query)
        p = params or ()
        if q.startswith("update password_reset_tokens set used_at = now() where user_id"):
            (user_id,) = p
            hit = []
            for row in self.reset_tokens.values():
                if row["user_id"] == user_id and row["used_at"] is None:
                    row["used_at"] = self._now()
                    hit.append({"id": row["id"]})
            return hit
        return super().fetch_all(query, params)

    def fetch_one(self, query: str, params: tuple | None = None) -> dict | None:
        q = self._norm(query)
        p = params or ()
        if q.startswith("update password_reset_tokens set used_at = now() where token_prefix"):
            (prefix,) = p
            for row in self.reset_tokens.values():
                if (
                    row["token_prefix"] == prefix
                    and row["used_at"] is None
                    and row["expires_at"] > self._now()
                ):
                    row["used_at"] = self._now()
                    return {
                        "id": row["id"],
                        "user_id": row["user_id"],
                        "token_hash": row["token_hash"],
                    }
            return None
        return super().fetch_one(query, params)


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> _ResetFakeStore:
    s = _ResetFakeStore()
    monkeypatch.setattr(config, "get_metadata_store", lambda: s)
    monkeypatch.setattr("services.users.config.get_metadata_store", lambda: s, raising=False)
    return s


def _make_user(store: _ResetFakeStore, email: str = "alice@example.com") -> str:
    user_id = uuid.uuid4().hex
    store.rows[user_id] = {
        "id": user_id,
        "email": email,
        "password_hash": users_service.hash_password("original-password-1"),
        "role": "user",
        "disabled": False,
        "created_at": datetime.now(timezone.utc),
        "last_login_at": None,
        "token_version": 1,
        "allows_shared": True,
    }
    return user_id


def test_mint_when_called_then_token_carries_prst_prefix_and_is_not_stored_in_clear(
    store: _ResetFakeStore,
) -> None:
    user_id = _make_user(store)

    minted = password_reset.mint(store, user_id)

    assert minted.plaintext.startswith("prst_")
    stored = store.reset_tokens[minted.token_id]
    assert minted.plaintext not in stored.values()
    assert stored["token_hash"] != minted.plaintext
    assert stored["token_prefix"] == minted.plaintext[len("prst_") :][:16]


def test_redeem_when_token_valid_then_password_changes_and_sessions_die(
    store: _ResetFakeStore,
) -> None:
    user_id = _make_user(store)
    minted = password_reset.mint(store, user_id)
    before_version = store.rows[user_id]["token_version"]
    before_hash = store.rows[user_id]["password_hash"]

    returned = password_reset.redeem(minted.plaintext, "a-brand-new-password")

    assert returned == user_id
    assert store.rows[user_id]["password_hash"] != before_hash
    # token_version bump is what invalidates already-issued access JWTs.
    assert store.rows[user_id]["token_version"] == before_version + 1


def test_redeem_when_replayed_then_second_attempt_is_invalid(store: _ResetFakeStore) -> None:
    user_id = _make_user(store)
    minted = password_reset.mint(store, user_id)
    password_reset.redeem(minted.plaintext, "a-brand-new-password")

    with pytest.raises(password_reset.ResetTokenInvalidError):
        password_reset.redeem(minted.plaintext, "another-new-password")


def test_redeem_when_token_expired_then_invalid(store: _ResetFakeStore) -> None:
    user_id = _make_user(store)
    minted = password_reset.mint(store, user_id)
    store.reset_tokens[minted.token_id]["expires_at"] = datetime.now(timezone.utc) - timedelta(
        minutes=1
    )

    with pytest.raises(password_reset.ResetTokenInvalidError):
        password_reset.redeem(minted.plaintext, "a-brand-new-password")


def test_redeem_when_token_unknown_then_invalid(store: _ResetFakeStore) -> None:
    _make_user(store)

    with pytest.raises(password_reset.ResetTokenInvalidError):
        password_reset.redeem("prst_" + "z" * 45, "a-brand-new-password")


def test_redeem_when_password_too_short_then_policy_error_and_token_survives(
    store: _ResetFakeStore,
) -> None:
    """A weak password must not burn the user's only live link."""
    user_id = _make_user(store)
    minted = password_reset.mint(store, user_id)

    with pytest.raises(users_service.PasswordPolicyViolationError):
        password_reset.redeem(minted.plaintext, "short")

    assert store.reset_tokens[minted.token_id]["used_at"] is None
    password_reset.redeem(minted.plaintext, "a-brand-new-password")


def test_mint_when_called_twice_then_only_the_newest_link_works(store: _ResetFakeStore) -> None:
    user_id = _make_user(store)
    first = password_reset.mint(store, user_id)
    second = password_reset.mint(store, user_id)

    with pytest.raises(password_reset.ResetTokenInvalidError):
        password_reset.redeem(first.plaintext, "a-brand-new-password")

    assert password_reset.redeem(second.plaintext, "a-brand-new-password") == user_id


def test_redeem_when_token_belongs_to_another_user_then_only_that_user_changes(
    store: _ResetFakeStore,
) -> None:
    alice = _make_user(store, "alice@example.com")
    bob = _make_user(store, "bob@example.com")
    bob_hash_before = store.rows[bob]["password_hash"]
    minted = password_reset.mint(store, alice)

    password_reset.redeem(minted.plaintext, "a-brand-new-password")

    assert store.rows[bob]["password_hash"] == bob_hash_before
    assert store.rows[bob]["token_version"] == 1


@pytest.mark.parametrize(
    "channel",
    ["admin_reset", "self_service_change", "cli_reset"],
)
def test_outstanding_reset_token_dies_when_password_changes_through_another_channel(
    store: _ResetFakeStore, channel: str
) -> None:
    """LUM-65 R4 P0: the product's remediation paths must know about each other.

    A member requests an email reset, gets impatient, and an admin resets the
    password from Admin → Users instead. The emailed link must not still work.
    """
    user_id = _make_user(store)
    minted = password_reset.mint(store, user_id)

    if channel == "admin_reset":
        users_service.admin_reset_user_password(user_id, "admin-chosen-password")
    elif channel == "self_service_change":
        users_service.change_own_password(user_id, "original-password-1", "self-chosen-password")
    else:
        # scripts/reset_password.py calls the same service entry point.
        users_service._apply_new_password(user_id, "cli-chosen-password")

    with pytest.raises(password_reset.ResetTokenInvalidError):
        password_reset.redeem(minted.plaintext, "attacker-chosen-password")


def test_ttl_minutes_when_env_out_of_range_then_clamped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LUMOGIS_PASSWORD_RESET_TTL_MINUTES", "9999")
    assert password_reset.ttl_minutes() == 120
    monkeypatch.setenv("LUMOGIS_PASSWORD_RESET_TTL_MINUTES", "1")
    assert password_reset.ttl_minutes() == 5
    monkeypatch.setenv("LUMOGIS_PASSWORD_RESET_TTL_MINUTES", "not-a-number")
    assert password_reset.ttl_minutes() == 30
    monkeypatch.delenv("LUMOGIS_PASSWORD_RESET_TTL_MINUTES")
    assert password_reset.ttl_minutes() == 30


def test_build_reset_url_when_public_origin_unset_then_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Never fall back to a relative link or the Host header for a reset URL."""
    monkeypatch.delenv("LUMOGIS_PUBLIC_ORIGIN", raising=False)

    with pytest.raises(ValueError):
        password_reset.build_reset_url("prst_sometoken")


def test_build_reset_url_when_public_origin_set_then_absolute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LUMOGIS_PUBLIC_ORIGIN", "https://home.example.com/")

    url = password_reset.build_reset_url("prst_abc")

    assert url == "https://home.example.com/reset-password?token=prst_abc"
