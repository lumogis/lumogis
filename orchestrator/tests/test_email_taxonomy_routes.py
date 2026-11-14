# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Route tests for the email taxonomy + rules surface (LUM-629).

GET defaults, PUT round-trips, 422 validation, reclassify 404, and cross-user
isolation — all through the HTTP layer with the functional fake store.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from tests.test_email_taxonomy import FakeStore

import config


@pytest.fixture(autouse=True)
def _auth_disabled(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")


@pytest.fixture
def store():
    s = FakeStore()
    config._instances["metadata_store"] = s
    config.invalidate_email_taxonomy_cache()
    yield s
    config._instances.pop("metadata_store", None)
    config.invalidate_email_taxonomy_cache()


@pytest.fixture
def client(store):
    import main

    with TestClient(main.app) as c:
        yield c


def _hdr(monkeypatch, user_id):
    monkeypatch.setenv("AUTH_SECRET", "test-email-taxonomy-secret")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    from auth import mint_access_token

    return {"Authorization": f"Bearer {mint_access_token(user_id, 'user')}"}


def test_get_defaults(client):
    r = client.get("/api/v1/me/email-taxonomy")
    assert r.status_code == 200
    cats = r.json()["categories"]
    assert [c["slug"] for c in cats] == [
        "school",
        "insurance_renewal",
        "bills_finance",
        "family_personal",
        "promotional_bulk",
        "other",
    ]
    assert cats[-1]["is_protected"] is True


def test_put_and_get_roundtrip(client):
    r = client.put(
        "/api/v1/me/email-taxonomy",
        json={"categories": [{"slug": "receipts", "label": "Receipts", "sort": 10}]},
    )
    assert r.status_code == 200
    slugs = [c["slug"] for c in r.json()["categories"]]
    assert "receipts" in slugs


def test_put_protected_422(client):
    r = client.put(
        "/api/v1/me/email-taxonomy", json={"categories": [{"slug": "other", "label": "Junk"}]}
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "protected_category"


def test_put_rules_unknown_field_422(client):
    # An unknown match field → extra=forbid rejects it (Pydantic 422).
    # (body_contains IS valid since LUM-630 — use a genuinely-unknown field.)
    r = client.put(
        "/api/v1/me/email-classification-rules",
        json={"rules": [{"rule_id": "r1", "category": "other", "match": {"body_regex": ["x"]}}]},
    )
    assert r.status_code == 422


def test_put_rules_empty_match_422(client):
    r = client.put(
        "/api/v1/me/email-classification-rules",
        json={"rules": [{"rule_id": "r1", "category": "other", "match": {}}]},
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "rule_empty_match"


def test_reclassify_unknown_slug_404(client):
    r = client.post("/api/v1/me/email-taxonomy/nope/reclassify")
    assert r.status_code == 404


def test_cross_user_isolation(client, monkeypatch):
    a = _hdr(monkeypatch, "alice")
    client.put(
        "/api/v1/me/email-taxonomy",
        json={"categories": [{"slug": "receipts", "label": "Receipts"}]},
        headers=a,
    )
    b = _hdr(monkeypatch, "bob")
    rb = client.get("/api/v1/me/email-taxonomy", headers=b)
    assert "receipts" not in [c["slug"] for c in rb.json()["categories"]]
    ra = client.get("/api/v1/me/email-taxonomy", headers=a)
    assert "receipts" in [c["slug"] for c in ra.json()["categories"]]
