# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""``/api/v1/notifications/*`` — VAPID key, subscribe/unsubscribe idempotency.

Phase 0 ships routes + DB schema for Web Push; the actual sender lands
in Phase 4. The CRUD contract (idempotent subscribe, scoped delete)
must be solid so the SPA can wire registration without a re-roll later.

Phase 4B exercises ``GET/PATCH …/subscriptions`` redacted list + prefs updates.
"""

from __future__ import annotations

import json
from datetime import datetime
from datetime import timezone

import pytest
from fastapi.testclient import TestClient

VALID_SUB = {
    "endpoint": "https://push.example.com/abc",
    "keys": {"p256dh": "BNcRdreALRFXTkOuewZbA", "auth": "tBHItJI5svbpez7KI4CCXg"},
    "user_agent": "TestUA/1.0",
}

VALID_SUB_BOB = {
    "endpoint": "https://push.other.example/long-path/token",
    "keys": {"p256dh": "BNcRdreALRFXTkOuewZbA", "auth": "tBHItJI5svbpez7KI4CCXg"},
    "user_agent": "BobUA",
}


class _NotifyStore:
    """Minimal MetadataStore for the notifications router."""

    def __init__(self) -> None:
        self.rows: list[dict] = []
        self._next_id = 1
        self.deleted: list[int] = []

    def ping(self) -> bool:
        return True

    def execute(self, query: str, params: tuple | None = None) -> None:
        q = " ".join(query.split()).lower()
        p = params or ()

        # Idempotent subscribe / key refresh (+ optional prefs via COALESCE).
        if q.startswith(
            "update webpush_subscriptions set last_seen_at"
        ) and "coalesce" in q:
            p256dh, auth, ua, ns, nss = p[0], p[1], p[2], p[3], p[4]
            sid = int(p[5])
            now = self._now()
            for r in self.rows:
                if r["id"] == sid:
                    r["p256dh"] = p256dh
                    r["auth"] = auth
                    r["user_agent"] = ua
                    if ns is not None:
                        r["notify_on_signals"] = ns
                    if nss is not None:
                        r["notify_on_shared_scope"] = nss
                    r["last_seen_at"] = now
            return

        if q.startswith(
            "update webpush_subscriptions set last_error = null where id"
        ):
            sid = int(p[0])
            for row in self.rows:
                if row["id"] == sid:
                    row["last_error"] = None
            return
        if q.startswith(
            "update webpush_subscriptions set last_error = %s where id = %s"
        ):
            err, sid = p[0], int(p[1])
            for row in self.rows:
                if row["id"] == sid:
                    row["last_error"] = err
            return
        if q.startswith("delete from webpush_subscriptions where id = %s") and len(p) == 1:
            sid = int(p[0])
            for i, row in enumerate(self.rows):
                if row["id"] == sid:
                    self.rows.pop(i)
                    return
            return

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    def fetch_one(self, query: str, params: tuple | None = None) -> dict | None:
        q_lower = query.lower().replace("\n", " ")
        ql = " ".join(q_lower.split())
        p = params or ()

        if (
            ql.startswith("update webpush_subscriptions set")
            and "returning" in ql
        ):
            uid = str(p[-1])
            sid = int(p[-2])
            vals = list(p[:-2])
            head = ql.split("where")[0]
            idx = 0
            for row in self.rows:
                if str(row["user_id"]) != uid or int(row["id"]) != sid:
                    continue
                if "notify_on_signals =" in head:
                    row["notify_on_signals"] = bool(vals[idx])
                    idx += 1
                if "notify_on_shared_scope =" in head:
                    row["notify_on_shared_scope"] = bool(vals[idx])
                    idx += 1
                return {
                    "id": row["id"],
                    "endpoint": row["endpoint"],
                    "created_at": row["created_at"],
                    "last_seen_at": row["last_seen_at"],
                    "last_error": row["last_error"],
                    "user_agent": row["user_agent"],
                    "notify_on_signals": row["notify_on_signals"],
                    "notify_on_shared_scope": row["notify_on_shared_scope"],
                }
            return None

        if ql.startswith("select id from webpush_subscriptions where user_id ="):
            for r in self.rows:
                if r["user_id"] == str(p[0]) and r["endpoint"] == p[1]:
                    return {"id": r["id"]}
            return None

        if ql.startswith("insert into webpush_subscriptions"):
            now = self._now()
            uid, endpoint, dh, ah, ua, nf, ns = p[0], p[1], p[2], p[3], p[4], p[5], p[6]
            row = {
                "id": self._next_id,
                "user_id": uid,
                "endpoint": endpoint,
                "p256dh": dh,
                "auth": ah,
                "user_agent": ua,
                "notify_on_signals": bool(nf),
                "notify_on_shared_scope": bool(ns),
                "last_error": None,
                "created_at": now,
                "last_seen_at": now,
            }
            self._next_id += 1
            self.rows.append(row)
            return {"id": row["id"]}

        if ql.startswith("delete from webpush_subscriptions where id ="):
            if len(p) >= 2 and "and user_id" in ql:
                for i, r in enumerate(self.rows):
                    if int(r["id"]) == int(p[0]) and str(r["user_id"]) == str(p[1]):
                        self.deleted.append(r["id"])
                        self.rows.pop(i)
                        return {"id": p[0]}
                return None

        return None

    def fetch_all(self, query: str, params: tuple | None = None) -> list[dict]:
        ql = " ".join(query.lower().replace("\n", " ").split())
        p = params or ()

        if ql.startswith("select count(*) as n from webpush_subscriptions"):
            n = sum(1 for r in self.rows if str(r["user_id"]) == str(p[0]))
            return [{"n": n}]

        # LIST (GET /notifications/subscriptions) includes created_at, not secrets.
        if "created_at" in ql and "from webpush_subscriptions" in ql:
            uid = str(p[0])
            out: list[dict] = []
            for r in sorted(self.rows, key=lambda x: int(x["id"])):
                if str(r["user_id"]) != uid:
                    continue
                out.append({
                    "id": r["id"],
                    "endpoint": r["endpoint"],
                    "created_at": r["created_at"],
                    "last_seen_at": r["last_seen_at"],
                    "last_error": r["last_error"],
                    "user_agent": r["user_agent"],
                    "notify_on_signals": r["notify_on_signals"],
                    "notify_on_shared_scope": r["notify_on_shared_scope"],
                })
            return out

        # Sender/service list (Phase 4A) — excludes created_at list query.
        if (
            "p256dh" in ql
            and "from webpush_subscriptions" in ql
            and "created_at" not in ql
        ):
            uid = str(p[0])
            out = []
            for r in self.rows:
                if str(r["user_id"]) != uid:
                    continue
                out.append({
                    "id": r["id"],
                    "endpoint": r["endpoint"],
                    "p256dh": r["p256dh"],
                    "auth": r["auth"],
                    "notify_on_signals": r["notify_on_signals"],
                    "notify_on_shared_scope": r["notify_on_shared_scope"],
                })
            return out

        return []

    def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def _notifications_routes_default_single_user_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tests expect dev single-user auth unless they opt into ``AUTH_ENABLED``."""
    monkeypatch.setenv("AUTH_ENABLED", "false")


@pytest.fixture
def notify_store(monkeypatch):
    import config as _config
    s = _NotifyStore()
    _config._instances["metadata_store"] = s
    yield s
    _config._instances.pop("metadata_store", None)


@pytest.fixture
def webpush_env(monkeypatch):
    monkeypatch.setenv("WEBPUSH_VAPID_PUBLIC_KEY", "BPubKey-1")
    monkeypatch.setenv("WEBPUSH_VAPID_PRIVATE_KEY", "PrivKey-1")
    monkeypatch.setenv("WEBPUSH_VAPID_SUBJECT", "mailto:notifications-fixture@lumogis.invalid")


@pytest.fixture
def client():
    import main
    with TestClient(main.app) as c:
        yield c


def test_vapid_public_key_returns_503_when_unconfigured(client, monkeypatch):
    monkeypatch.delenv("WEBPUSH_VAPID_PUBLIC_KEY", raising=False)
    resp = client.get("/api/v1/notifications/vapid-public-key")
    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "webpush_not_configured"


def test_vapid_public_key_returns_key(client, webpush_env):
    resp = client.get("/api/v1/notifications/vapid-public-key")
    assert resp.status_code == 200
    assert resp.json()["public_key"] == "BPubKey-1"


def test_subscribe_first_time_returns_201(client, webpush_env, notify_store):
    resp = client.post("/api/v1/notifications/subscribe", json=VALID_SUB)
    assert resp.status_code == 201
    body = resp.json()
    assert body["already_existed"] is False
    assert body["id"] >= 1


def test_subscribe_idempotent_returns_200(client, webpush_env, notify_store):
    first = client.post("/api/v1/notifications/subscribe", json=VALID_SUB)
    assert first.status_code == 201
    again = client.post("/api/v1/notifications/subscribe", json=VALID_SUB)
    assert again.status_code == 200
    body = again.json()
    assert body["already_existed"] is True
    assert body["id"] == first.json()["id"]


def test_subscribe_503_when_unconfigured(client, notify_store, monkeypatch):
    monkeypatch.delenv("WEBPUSH_VAPID_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("WEBPUSH_VAPID_PRIVATE_KEY", raising=False)
    resp = client.post("/api/v1/notifications/subscribe", json=VALID_SUB)
    assert resp.status_code == 503


def test_unsubscribe_returns_204(client, webpush_env, notify_store):
    sub = client.post("/api/v1/notifications/subscribe", json=VALID_SUB).json()
    resp = client.delete(f"/api/v1/notifications/subscriptions/{sub['id']}")
    assert resp.status_code == 204


def test_unsubscribe_unknown_returns_404(client, webpush_env, notify_store):
    resp = client.delete("/api/v1/notifications/subscriptions/9999")
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "subscription_not_found"


def test_test_endpoint_404_when_dev_echo_disabled(client, webpush_env, notify_store, monkeypatch):
    monkeypatch.delenv("WEBPUSH_DEV_ECHO", raising=False)
    resp = client.get("/api/v1/notifications/test")
    assert resp.status_code == 404


def test_test_endpoint_returns_count_when_enabled(
    client, webpush_env, notify_store, monkeypatch
):
    monkeypatch.setenv("WEBPUSH_DEV_ECHO", "true")
    import services.webpush as wp

    monkeypatch.setattr(wp, "_dispatch_web_push", lambda **_: None)
    client.post("/api/v1/notifications/subscribe", json=VALID_SUB)
    resp = client.get("/api/v1/notifications/test")
    assert resp.status_code == 200
    body = resp.json()
    assert body["sent"] == 1
    assert body["failed"] == 0
    assert body["pruned"] == 0
    assert body["skipped"] == 0
    assert body.get("disabled_reason") is None


def _auth_header(
    monkeypatch: pytest.MonkeyPatch,
    user_id: str,
    role: str = "user",
) -> dict[str, str]:
    monkeypatch.setenv("AUTH_SECRET", "test-webpush-phase4b-secret-do-not-use")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    from auth import mint_access_token

    tok = mint_access_token(user_id, role)
    return {"Authorization": f"Bearer {tok}"}


def test_list_subscriptions_401_when_auth_without_token(client, monkeypatch) -> None:
    monkeypatch.setenv("AUTH_SECRET", "test-wp-list-401")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    r = client.get("/api/v1/notifications/subscriptions")
    assert r.status_code == 401


def test_list_subscriptions_redacted_no_secrets(
    client, webpush_env, notify_store, monkeypatch
) -> None:
    monkeypatch.setenv("AUTH_SECRET", "test-wp-redact")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    hdr = _auth_header(monkeypatch, "redact-u1")

    sid = (
        client.post(
            "/api/v1/notifications/subscribe",
            json=VALID_SUB,
            headers=hdr,
        )
        .json()["id"]
    )

    r = client.get("/api/v1/notifications/subscriptions", headers=hdr)
    assert r.status_code == 200
    subs = r.json()["subscriptions"]
    assert len(subs) == 1
    item = subs[0]
    assert item["id"] == sid
    assert item["endpoint_origin"] == "https://push.example.com"
    assert "push.example.com/abc" not in json.dumps(r.json())
    assert "p256dh" not in item
    assert "auth" not in item
    assert "endpoint" not in item


def test_list_patch_multi_user_isolation(
    client,
    webpush_env,
    notify_store,
    monkeypatch,
) -> None:
    h_alice = _auth_header(monkeypatch, "wp-alice-1")
    h_bob = _auth_header(monkeypatch, "wp-bob-1")

    a_resp = client.post(
        "/api/v1/notifications/subscribe",
        json=VALID_SUB,
        headers=h_alice,
    ).json()
    b_resp = client.post(
        "/api/v1/notifications/subscribe",
        json=VALID_SUB_BOB,
        headers=h_bob,
    ).json()

    alice_list = client.get(
        "/api/v1/notifications/subscriptions",
        headers=h_alice,
    ).json()
    assert [s["id"] for s in alice_list["subscriptions"]] == [a_resp["id"]]

    bob_list = client.get(
        "/api/v1/notifications/subscriptions",
        headers=h_bob,
    ).json()
    assert [s["id"] for s in bob_list["subscriptions"]] == [b_resp["id"]]

    nf = json.dumps(bob_list)
    assert "push.example.com" not in nf  # alice host not visible to bob
    nf2 = json.dumps(alice_list)
    assert "push.other.example" not in nf2

    bob_patch_other = client.patch(
        f"/api/v1/notifications/subscriptions/{a_resp['id']}",
        headers=h_bob,
        json={"notify_on_signals": False},
    )
    assert bob_patch_other.status_code == 404

    alice_patch_ok = client.patch(
        f"/api/v1/notifications/subscriptions/{b_resp['id']}",
        headers=h_alice,
        json={"notify_on_signals": True},
    )
    assert alice_patch_ok.status_code == 404


def test_patch_subscription_partial_preserves_pref(
    client,
    webpush_env,
    notify_store,
    monkeypatch,
) -> None:
    hdr = _auth_header(monkeypatch, "wp-partial-u")
    sid = (
        client.post(
            "/api/v1/notifications/subscribe",
            json={
                **VALID_SUB,
                "notify_on_signals": True,
                "notify_on_shared_scope": True,
            },
            headers=hdr,
        )
        .json()["id"]
    )

    r = client.patch(
        f"/api/v1/notifications/subscriptions/{sid}",
        headers=hdr,
        json={"notify_on_shared_scope": False},
    )
    assert r.status_code == 200
    j = r.json()
    assert j["notify_on_signals"] is True
    assert j["notify_on_shared_scope"] is False


def test_patch_subscription_unknown_or_wrong_returns_404(
    client,
    webpush_env,
    notify_store,
    monkeypatch,
) -> None:
    hdr = _auth_header(monkeypatch, "wp-404-u")
    client.post(
        "/api/v1/notifications/subscribe",
        json=VALID_SUB,
        headers=hdr,
    )
    unk = client.patch(
        "/api/v1/notifications/subscriptions/999987",
        headers=hdr,
        json={"notify_on_signals": False},
    )
    assert unk.status_code == 404


def test_patch_unknown_field_returns_422(
    client,
    webpush_env,
    notify_store,
    monkeypatch,
) -> None:
    hdr = _auth_header(monkeypatch, "wp-extra-field")
    sid = (
        client.post(
            "/api/v1/notifications/subscribe",
            json=VALID_SUB,
            headers=hdr,
        )
        .json()["id"]
    )
    bad = client.patch(
        f"/api/v1/notifications/subscriptions/{sid}",
        headers=hdr,
        json={"notify_on_signals": True, "oops": "x"},
    )
    assert bad.status_code == 422


def test_patch_empty_body_returns_422(
    client,
    webpush_env,
    notify_store,
    monkeypatch,
) -> None:
    hdr = _auth_header(monkeypatch, "wp-422-u")
    sid = (
        client.post(
            "/api/v1/notifications/subscribe",
            json=VALID_SUB,
            headers=hdr,
        )
        .json()["id"]
    )
    empty = client.patch(
        f"/api/v1/notifications/subscriptions/{sid}",
        headers=hdr,
        json={},
    )
    assert empty.status_code == 422


def test_subscribe_optional_preferences_insert(
    client,
    webpush_env,
    notify_store,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AUTH_SECRET", "test-wp-sub-pref")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    hdr = _auth_header(monkeypatch, "wp-sub-pref-insert")
    resp = client.post(
        "/api/v1/notifications/subscribe",
        json={
            **VALID_SUB,
            "notify_on_signals": True,
            "notify_on_shared_scope": False,
        },
        headers=hdr,
    )
    assert resp.status_code == 201
    row = notify_store.rows[0]
    assert row["notify_on_signals"] is True
    assert row["notify_on_shared_scope"] is False


def test_subscribe_idempotent_omit_prefs_preserves_prefs(
    client,
    webpush_env,
    notify_store,
    monkeypatch,
) -> None:
    hdr = _auth_header(monkeypatch, "wp-resub-u")
    first = client.post(
        "/api/v1/notifications/subscribe",
        json={
            **VALID_SUB,
            "notify_on_signals": True,
            "notify_on_shared_scope": False,
        },
        headers=hdr,
    )
    assert first.status_code == 201

    row_id = notify_store.rows[0]["id"]
    again = client.post(
        "/api/v1/notifications/subscribe",
        json=VALID_SUB,
        headers=hdr,
    )
    assert again.status_code == 200
    r = notify_store.rows[0]
    assert int(r["id"]) == row_id
    assert r["notify_on_signals"] is True
    assert r["notify_on_shared_scope"] is False


def test_subscribe_idempotent_explicit_prefs_overrides(
    client,
    webpush_env,
    notify_store,
    monkeypatch,
) -> None:
    hdr = _auth_header(monkeypatch, "wp-resub-2")
    first = client.post(
        "/api/v1/notifications/subscribe",
        json={
            **VALID_SUB,
            "notify_on_signals": True,
            "notify_on_shared_scope": True,
        },
        headers=hdr,
    ).json()
    again = client.post(
        "/api/v1/notifications/subscribe",
        json={
            **VALID_SUB,
            "notify_on_signals": False,
            "notify_on_shared_scope": False,
        },
        headers=hdr,
    )
    assert again.status_code == 200
    r = notify_store.rows[0]
    assert int(r["id"]) == int(first["id"])
    assert r["notify_on_signals"] is False
    assert r["notify_on_shared_scope"] is False
