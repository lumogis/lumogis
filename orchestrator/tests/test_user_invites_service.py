# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for ``services/user_invites.py`` (LUM-186)."""

from __future__ import annotations

import contextlib
import re
from datetime import datetime
from datetime import timedelta
from datetime import timezone

import pytest
from models.user_invite import DuplicateEmailError
from models.user_invite import InviteInvalidError

from services import user_invites as svc


class _FakeStore:
    def __init__(self) -> None:
        self.invites: dict[str, dict] = {}
        self.users: dict[str, dict] = {}
        # LUM-885: invite creators live apart from ``users`` (the redeemed
        # accounts) so existing user-count assertions keep their meaning. The
        # creator predicate (exists, active, admin) is applied against this.
        self.creators: dict[str, dict] = {"admin1": {"role": "admin", "disabled": False}}
        self._fail_user_insert_unique = False
        self._in_txn = False

    def ping(self) -> bool:
        return True

    def close(self) -> None:
        pass

    @contextlib.contextmanager
    def transaction(self):
        self._in_txn = True
        try:
            yield
        finally:
            self._in_txn = False

    @staticmethod
    def _norm(query: str) -> str:
        return " ".join(query.split()).lower()

    def _creator_is_active_admin(self, row: dict) -> bool:
        creator = self.creators.get(row["created_by"])
        return bool(creator) and creator["role"] == "admin" and not creator["disabled"]

    def execute(self, query: str, params: tuple | None = None) -> None:
        q = self._norm(query)
        p = params or ()

        if q.startswith("insert into user_invites"):
            iid, prefix, thash, role, allows, created_by, expires = p
            for row in self.invites.values():
                if (
                    row["used_at"] is None
                    and row["revoked_at"] is None
                    and row["token_prefix"] == prefix
                ):
                    raise RuntimeError(
                        "duplicate key value violates unique constraint "
                        "user_invites_active_prefix_uniq"
                    )
            self.invites[iid] = {
                "id": iid,
                "token_prefix": prefix,
                "token_hash": thash,
                "role": role,
                "allows_shared": allows,
                "created_by": created_by,
                "created_at": datetime.now(timezone.utc),
                "expires_at": expires,
                "used_at": None,
                "used_by": None,
                "revoked_at": None,
            }
            return

        if q.startswith("insert into users"):
            if len(p) >= 5:
                uid, email, pw_hash, role, allows_shared = p[0], p[1], p[2], p[3], p[4]
            else:
                uid, email, pw_hash, role = p[:4]
                allows_shared = True
            for row in self.users.values():
                if row["email"].lower() == email.lower():
                    raise RuntimeError(
                        "duplicate key value violates unique constraint users_email_key"
                    )
            if self._fail_user_insert_unique:
                raise RuntimeError("duplicate key value violates unique constraint users_email_key")
            self.users[uid] = {
                "id": uid,
                "email": email,
                "password_hash": pw_hash,
                "role": role,
                "disabled": False,
                "created_at": datetime.now(timezone.utc),
                "token_version": 1,
                "allows_shared": allows_shared,
            }
            return

        if q.startswith("update user_invites set revoked_at"):
            (iid,) = p
            row = self.invites.get(iid)
            if row and row["used_at"] is None and row["revoked_at"] is None:
                row["revoked_at"] = datetime.now(timezone.utc)
            return

    def fetch_one(self, query: str, params: tuple | None = None) -> dict | None:
        q = self._norm(query)
        p = params or ()

        if q.startswith("select * from user_invites where id ="):
            (iid,) = p
            row = self.invites.get(iid)
            return dict(row) if row else None

        if q.startswith("select id from user_invites where id ="):
            (iid,) = p
            row = self.invites.get(iid)
            if row and row["used_at"] is None and row["revoked_at"] is None:
                return {"id": iid}
            return None

        if q.startswith("select i.* from user_invites i join users c on c.id = i.created_by"):
            (prefix,) = p
            now = datetime.now(timezone.utc)
            for row in self.invites.values():
                if (
                    row["token_prefix"] == prefix
                    and row["used_at"] is None
                    and row["revoked_at"] is None
                    and row["expires_at"] > now
                    and self._creator_is_active_admin(row)
                ):
                    return dict(row)
            return None

        if q.startswith("select id from users where lower(email)"):
            (email,) = p
            for row in self.users.values():
                if row["email"].lower() == email.lower():
                    return {"id": row["id"]}
            return None

        if q.startswith("select * from users where id ="):
            (uid,) = p
            row = self.users.get(uid)
            return dict(row) if row else None

        if q.startswith("select allows_shared from users where id ="):
            (uid,) = p
            row = self.users.get(uid)
            if row is None:
                return None
            return {"allows_shared": row.get("allows_shared", True)}

        if q.startswith("update user_invites i set used_at = now()") and "returning" in q:
            used_by, iid = p
            row = self.invites.get(iid)
            now = datetime.now(timezone.utc)
            if (
                row
                and row["used_at"] is None
                and row["revoked_at"] is None
                and row["expires_at"] > now
                and self._creator_is_active_admin(row)
            ):
                row["used_at"] = now
                row["used_by"] = used_by
                return dict(row)
            return None

        return None

    def fetch_all(self, query: str, params: tuple | None = None) -> list[dict]:
        q = self._norm(query)
        if q.startswith("select * from user_invites"):
            rows = sorted(self.invites.values(), key=lambda r: r["created_at"], reverse=True)
            return [dict(r) for r in rows]
        return []


@pytest.fixture
def invite_store(monkeypatch):
    import config as _config

    store = _FakeStore()
    _config._instances["metadata_store"] = store
    yield store
    _config._instances.pop("metadata_store", None)


def test_mint_returns_plaintext_once_and_stores_hash(invite_store):
    internal, plaintext = svc.mint_invite(role="user", allows_shared=True, created_by="admin1")
    assert plaintext.startswith("linv_")
    assert len(plaintext) >= 50
    assert re.fullmatch(r"linv_[a-z2-7]+", plaintext)
    row = invite_store.invites[internal.id]
    assert row["token_hash"] != plaintext
    assert row["token_prefix"] == plaintext[5 : 5 + 16]


def test_peek_valid_token_returns_metadata(invite_store):
    _, plaintext = svc.mint_invite(role="user", allows_shared=False, created_by="admin1")
    peek = svc.peek_invite(plaintext)
    assert peek is not None
    assert peek.allows_shared is False
    assert peek.expires_at is not None


def test_peek_expired_returns_none(invite_store):
    _, plaintext = svc.mint_invite(role="user", allows_shared=True, created_by="admin1")
    row = next(iter(invite_store.invites.values()))
    row["expires_at"] = datetime.now(timezone.utc) - timedelta(hours=1)
    assert svc.peek_invite(plaintext) is None


def test_redeem_consumes_and_creates_user(invite_store):
    _, plaintext = svc.mint_invite(role="user", allows_shared=True, created_by="admin1")
    result = svc.redeem_invite(plaintext, "member@example.com", "securepass1234")
    assert result.user.email == "member@example.com"
    assert result.user.role == "user"
    assert result.user.allows_shared is True
    assert len(invite_store.users) == 1
    invite_row = next(iter(invite_store.invites.values()))
    assert invite_row["used_at"] is not None


def test_redeem_personal_only_sets_allows_shared_false(invite_store):
    _, plaintext = svc.mint_invite(role="user", allows_shared=False, created_by="admin1")
    result = svc.redeem_invite(plaintext, "solo@example.com", "securepass1234")
    assert result.user.allows_shared is False
    user_row = invite_store.users[result.user.id]
    assert user_row["allows_shared"] is False


def test_mint_redeem_revoke_emit_audit(invite_store, monkeypatch):
    audits: list[str] = []

    def _capture(entry, **kwargs):
        audits.append(entry.action_name)
        return "audit-1"

    monkeypatch.setattr("services.user_invites.write_audit", _capture)

    internal, plaintext = svc.mint_invite(role="user", allows_shared=True, created_by="admin1")
    assert "__user_invite__.minted" in audits

    svc.redeem_invite(plaintext, "member@example.com", "securepass1234")
    assert "__user_invite__.redeemed" in audits

    internal2, _ = svc.mint_invite(role="user", allows_shared=True, created_by="admin1")
    assert svc.revoke_invite(internal2.id, actor_admin_id="admin1")
    assert "__user_invite__.revoked" in audits


def test_redeem_second_attempt_fails(invite_store):
    _, plaintext = svc.mint_invite(role="user", allows_shared=True, created_by="admin1")
    svc.redeem_invite(plaintext, "member@example.com", "securepass1234")
    with pytest.raises(InviteInvalidError):
        svc.redeem_invite(plaintext, "other@example.com", "securepass1234")


def test_revoke_prevents_redeem(invite_store):
    internal, plaintext = svc.mint_invite(role="user", allows_shared=True, created_by="admin1")
    assert svc.revoke_invite(internal.id, actor_admin_id="admin1")
    assert svc.peek_invite(plaintext) is None
    with pytest.raises(InviteInvalidError):
        svc.redeem_invite(plaintext, "member@example.com", "securepass1234")


def test_redeem_admin_role_creates_admin_user(invite_store):
    _, plaintext = svc.mint_invite(role="admin", allows_shared=True, created_by="admin1")
    result = svc.redeem_invite(plaintext, "newadmin@example.com", "securepass1234")
    assert result.user.role == "admin"


def test_redeem_duplicate_email_before_consume(invite_store):
    _, plaintext = svc.mint_invite(role="user", allows_shared=True, created_by="admin1")
    invite_store.users["existing"] = {
        "id": "existing",
        "email": "member@example.com",
        "password_hash": "x",
        "role": "user",
        "disabled": False,
        "created_at": datetime.now(timezone.utc),
        "token_version": 1,
    }
    with pytest.raises(DuplicateEmailError):
        svc.redeem_invite(plaintext, "member@example.com", "securepass1234")
    invite_row = next(iter(invite_store.invites.values()))
    assert invite_row["used_at"] is None


# --- LUM-885: an invite redeems only while its creator is an active admin -----


def test_redeem_rejects_invite_of_demoted_creator(invite_store):
    _, plaintext = svc.mint_invite(role="admin", allows_shared=True, created_by="admin1")

    # Negative control: while the creator is still an admin the invite is live.
    assert svc.peek_invite(plaintext) is not None

    invite_store.creators["admin1"]["role"] = "user"
    with pytest.raises(InviteInvalidError):
        svc.redeem_invite(plaintext, "intruder@example.com", "securepass1234")
    assert invite_store.users == {}
    assert next(iter(invite_store.invites.values()))["used_at"] is None


def test_redeem_of_live_invite_still_works_with_active_admin_creator(invite_store):
    _, plaintext = svc.mint_invite(role="admin", allows_shared=True, created_by="admin1")
    result = svc.redeem_invite(plaintext, "second-admin@example.com", "securepass1234")
    assert result.role == "admin"


@pytest.mark.parametrize("how", ["disabled", "deleted"])
def test_peek_and_redeem_reject_invite_of_disabled_or_deleted_creator(invite_store, how):
    _, plaintext = svc.mint_invite(role="user", allows_shared=True, created_by="admin1")
    if how == "disabled":
        invite_store.creators["admin1"]["disabled"] = True
    else:
        del invite_store.creators["admin1"]

    assert svc.peek_invite(plaintext) is None
    with pytest.raises(InviteInvalidError):
        svc.redeem_invite(plaintext, "member@example.com", "securepass1234")


def test_consume_update_rechecks_the_creator_after_the_lookup(invite_store):
    """A demotion that commits between the lookup and the consume still wins:
    the lookup passes, the creator then loses admin, the consume matches no row."""
    _, plaintext = svc.mint_invite(role="admin", allows_shared=True, created_by="admin1")
    real_lookup = svc._lookup_active_by_prefix

    def lookup_then_demote(prefix):
        row = real_lookup(prefix)
        invite_store.creators["admin1"]["role"] = "user"
        return row

    svc._lookup_active_by_prefix = lookup_then_demote
    try:
        with pytest.raises(InviteInvalidError):
            svc.redeem_invite(plaintext, "intruder@example.com", "securepass1234")
    finally:
        svc._lookup_active_by_prefix = real_lookup
    assert invite_store.users == {}
