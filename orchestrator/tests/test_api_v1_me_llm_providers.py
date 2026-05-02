# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""``GET /api/v1/me/llm-providers`` — read-only LLM credential façade (Phase 4)."""

from __future__ import annotations

import json
from datetime import datetime
from datetime import timezone
from unittest.mock import patch

import pytest
from connectors.registry import LLM_OPENAI
from connectors.registry import LLM_PERPLEXITY
from fastapi.testclient import TestClient

from services import connector_credentials as ccs
from services import credential_tiers as ct


@pytest.fixture
def client():
    import main

    with TestClient(main.app) as c:
        yield c


def _auth_header(
    monkeypatch: pytest.MonkeyPatch, user_id: str, role: str = "user"
) -> dict[str, str]:
    monkeypatch.setenv("AUTH_SECRET", "test-me-llm-prov-secret-do-not-use")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    from auth import mint_access_token

    tok = mint_access_token(user_id, role)
    return {"Authorization": f"Bearer {tok}"}


def test_me_llm_providers_401_when_auth_enabled_without_token(client, monkeypatch) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_SECRET", "test-me-llm-prov-401")
    r = client.get("/api/v1/me/llm-providers")
    assert r.status_code == 401


def test_me_llm_providers_200_authenticated_when_auth_enabled(client, monkeypatch) -> None:
    hdr = _auth_header(monkeypatch, "alice-llm-1", "user")
    r = client.get("/api/v1/me/llm-providers", headers=hdr)
    assert r.status_code == 200
    body = r.json()
    assert "providers" in body and "summary" in body
    assert body["summary"]["total"] == len(body["providers"])


def test_me_llm_providers_200_default_user_when_auth_disabled(client) -> None:
    r = client.get("/api/v1/me/llm-providers")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["providers"], list)
    assert body["summary"]["total"] == len(body["providers"])


def test_me_llm_providers_lists_all_llm_connectors_stable_order(client) -> None:
    from services.llm_connector_map import LLM_CONNECTOR_BY_ENV
    from services.me_llm_providers import llm_connector_ids

    r = client.get("/api/v1/me/llm-providers")
    assert r.status_code == 200
    ids = [p["connector"] for p in r.json()["providers"]]
    assert ids == list(llm_connector_ids())
    assert set(ids) == set(LLM_CONNECTOR_BY_ENV.values())


def test_me_llm_providers_safe_json_no_secret_material(client, monkeypatch) -> None:
    hdr = _auth_header(monkeypatch, "safe-json-user", "user")
    r = client.get("/api/v1/me/llm-providers", headers=hdr)
    assert r.status_code == 200
    body = r.json()
    required = {
        "connector",
        "label",
        "description",
        "configured",
        "active_tier",
        "user_credential_present",
        "household_credential_available",
        "system_credential_available",
        "env_fallback_available",
        "updated_at",
        "key_version",
        "status",
        "why_not_available",
    }
    sum_required = {"total", "configured", "not_configured", "by_active_tier"}
    assert set(body["summary"].keys()) == sum_required
    for p in body["providers"]:
        assert set(p.keys()) == required

    raw = json.dumps(body)
    lowered = raw.lower()
    assert "ciphertext" not in lowered
    assert "sk-proj-" not in raw
    assert '"api_key"' not in lowered
    assert '"payload"' not in lowered
    assert "bearer " not in lowered


def test_me_llm_providers_summary_counts(client, monkeypatch) -> None:
    hdr = _auth_header(monkeypatch, "summary-user", "user")
    r = client.get("/api/v1/me/llm-providers", headers=hdr)
    assert r.status_code == 200
    b = r.json()
    s = b["summary"]
    assert s["total"] == len(b["providers"])
    assert s["configured"] + s["not_configured"] == s["total"]
    tier_sum = sum(s["by_active_tier"].values())
    assert tier_sum == s["total"]


def test_me_llm_providers_user_tier_when_metadata_present(client, monkeypatch) -> None:
    """Mocked metadata — unit tests use MockMetadataStore (no real UPSERT)."""
    uid = "llm-prov-user-row-test"
    hdr = _auth_header(monkeypatch, uid, "user")
    ts = datetime(2026, 4, 25, 12, 0, 0, tzinfo=timezone.utc)
    rec = ccs.CredentialRecord(
        user_id=uid,
        connector=LLM_OPENAI,
        created_at=ts,
        updated_at=ts,
        created_by="self",
        updated_by="self",
        key_version=2,
    )

    def _get_record(u: str, c: str):
        return rec if (u, c) == (uid, LLM_OPENAI) else None

    with (
        patch("services.me_llm_providers.ccs.get_record", _get_record),
        patch("services.me_llm_providers.ct.household_get_record", lambda _c: None),
        patch("services.me_llm_providers.ct.system_get_record", lambda _c: None),
        patch("services.me_llm_providers._env_fallback_configured", lambda _c: False),
    ):
        r = client.get("/api/v1/me/llm-providers", headers=hdr)
    assert r.status_code == 200
    by_c = {p["connector"]: p for p in r.json()["providers"]}
    row = by_c[LLM_OPENAI]
    assert row["configured"] is True
    assert row["active_tier"] == "user"
    assert row["user_credential_present"] is True
    assert row["status"] == "configured"
    assert row["why_not_available"] is None
    assert row["key_version"] == 2
    assert "super-secret" not in json.dumps(r.json())


def test_me_llm_providers_household_tier_when_metadata_present(client, monkeypatch) -> None:
    uid = "llm-prov-household-viewer"
    hdr = _auth_header(monkeypatch, uid, "user")
    ts = datetime(2026, 4, 25, 12, 0, 0, tzinfo=timezone.utc)
    hh = ct.HouseholdCredentialRecord(
        connector=LLM_PERPLEXITY,
        created_at=ts,
        updated_at=ts,
        created_by="admin:test",
        updated_by="admin:test",
        key_version=1,
    )

    def _hh(c: str):
        return hh if c == LLM_PERPLEXITY else None

    with (
        patch("services.me_llm_providers.ccs.get_record", lambda _u, _c: None),
        patch("services.me_llm_providers.ct.household_get_record", _hh),
        patch("services.me_llm_providers.ct.system_get_record", lambda _c: None),
        patch("services.me_llm_providers._env_fallback_configured", lambda _c: False),
    ):
        r = client.get("/api/v1/me/llm-providers", headers=hdr)
    assert r.status_code == 200
    by_c = {p["connector"]: p for p in r.json()["providers"]}
    row = by_c[LLM_PERPLEXITY]
    assert row["active_tier"] == "household"
    assert row["household_credential_available"] is True
    assert row["user_credential_present"] is False


def test_me_llm_providers_env_fallback_boolean_only(client, monkeypatch) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "false")
    secret_val = "env-fallback-secret-value-xyz"
    monkeypatch.setenv("OPENAI_API_KEY", secret_val)
    try:
        r = client.get("/api/v1/me/llm-providers")
        assert r.status_code == 200
        by_c = {p["connector"]: p for p in r.json()["providers"]}
        openai = by_c[LLM_OPENAI]
        assert openai["env_fallback_available"] is True
        assert openai["active_tier"] == "env"
        assert secret_val not in json.dumps(r.json())
    finally:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
