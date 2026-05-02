# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for :mod:`services.llm_connector_map`.

Pinned by plan ``llm_provider_keys_per_user_migration`` §Implementation
sequence Pass 1.2.

Covers:

* The frozen env-string → connector-id mapping
  (:data:`LLM_CONNECTOR_BY_ENV`) and its drift guard against
  ``config/models.yaml``.
* :func:`vendor_label_for_connector` — known + fallback.
* :func:`has_credential` — registry-strict existence (no decrypt).
* :func:`get_user_credentials_snapshot` — single SELECT, ``llm_*``
  filter, escapes ``_`` literal.
* :func:`effective_api_key` — every branch in the exception contract:
  ``ConnectorNotConfigured`` for missing rows / blank values,
  ``CredentialUnavailable`` for malformed payloads / wrong shapes,
  env-fallback under ``AUTH_ENABLED=false`` reading the ``"value"``
  payload key, registered + auth-on path reading the ``"api_key"``
  payload key.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from cryptography.fernet import Fernet

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fernet_key() -> str:
    return Fernet.generate_key().decode()


class _FakeStore:
    """Minimal MetadataStore stub for the queries this module issues.

    Recognises:

    * ``SELECT ciphertext FROM user_connector_credentials …`` (used by
      ``connector_credentials.resolve``).
    * ``SELECT user_id, connector, … FROM user_connector_credentials
      WHERE user_id = %s AND connector = %s`` (used by
      ``connector_credentials.get_record`` via ``has_credential``).
    * ``SELECT connector FROM user_connector_credentials WHERE user_id
      = %s AND connector LIKE 'llm\\_%%' ESCAPE '\\'`` (used by
      ``get_user_credentials_snapshot``).
    """

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], dict[str, Any]] = {}
        self.fetch_all_calls: list[tuple[str, tuple]] = []

    def ping(self) -> bool:  # pragma: no cover — protocol completeness
        return True

    def execute(self, query: str, params: tuple | None = None) -> None:
        return None

    @staticmethod
    def _norm(query: str) -> str:
        return " ".join(query.split()).lower()

    def fetch_one(self, query: str, params: tuple | None = None) -> dict | None:
        q = self._norm(query)
        p = params or ()
        if q.startswith("select ciphertext from user_connector_credentials"):
            user_id, connector = p
            row = self.rows.get((user_id, connector))
            return {"ciphertext": row["ciphertext"]} if row else None
        if (
            q.startswith("select user_id, connector,")
            and "where user_id = %s and connector = %s" in q
        ):
            user_id, connector = p
            row = self.rows.get((user_id, connector))
            if row is None:
                return None
            return {
                "user_id": user_id,
                "connector": connector,
                "ciphertext": row["ciphertext"],
                "key_version": row["key_version"],
                "created_at": row.get("created_at"),
                "updated_at": row.get("updated_at"),
                "created_by": row.get("created_by", "self"),
                "updated_by": row.get("updated_by", "self"),
            }
        return None

    def fetch_all(self, query: str, params: tuple | None = None) -> list[dict]:
        self.fetch_all_calls.append((query, params or ()))
        q = self._norm(query)
        p = params or ()
        # Match the snapshot SQL by its distinguishing prefix + LIKE pattern.
        # The literal SQL uses ``LIKE 'llm\\_%%' ESCAPE '\\'`` so psycopg's
        # ``%`` substitution emits ``LIKE 'llm\_%' ESCAPE '\'`` — the
        # ``llm\_`` token is the stable thing to look for.
        if q.startswith("select connector from user_connector_credentials") and "like 'llm\\_" in q:
            (user_id,) = p
            return [
                {"connector": conn}
                for (uid, conn) in self.rows.keys()
                if uid == user_id and conn.startswith("llm_")
            ]
        return []


@pytest.fixture
def store(monkeypatch, fernet_key):
    """Install a `_FakeStore`, seed AUTH_ENABLED=true + a fresh credential key."""
    import config as _config
    from services import connector_credentials as ccs

    monkeypatch.setenv("LUMOGIS_CREDENTIAL_KEY", fernet_key)
    monkeypatch.delenv("LUMOGIS_CREDENTIAL_KEYS", raising=False)
    monkeypatch.setenv("AUTH_ENABLED", "true")
    ccs.reset_for_tests()

    s = _FakeStore()
    _config._instances["metadata_store"] = s
    yield s
    _config._instances.pop("metadata_store", None)
    ccs.reset_for_tests()


def _seed_payload(store: _FakeStore, *, user_id: str, connector: str, payload: dict) -> None:
    """Encrypt ``payload`` with the live MultiFernet and seed a row."""
    from services.connector_credentials import _current_key_version
    from services.connector_credentials import _encrypt_payload

    store.rows[(user_id, connector)] = {
        "ciphertext": _encrypt_payload(payload),
        "key_version": _current_key_version(),
    }


# ---------------------------------------------------------------------------
# Mapping + drift guard
# ---------------------------------------------------------------------------


def test_mapping_contains_all_six_known_envs():
    from services.llm_connector_map import LLM_CONNECTOR_BY_ENV

    assert set(LLM_CONNECTOR_BY_ENV) == {
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "XAI_API_KEY",
        "PERPLEXITY_API_KEY",
        "GEMINI_API_KEY",
        "MISTRAL_API_KEY",
    }


def test_mapping_values_match_registry_constants():
    from connectors.registry import LLM_ANTHROPIC
    from connectors.registry import LLM_GEMINI
    from connectors.registry import LLM_MISTRAL
    from connectors.registry import LLM_OPENAI
    from connectors.registry import LLM_PERPLEXITY
    from connectors.registry import LLM_XAI
    from services.llm_connector_map import LLM_CONNECTOR_BY_ENV

    assert LLM_CONNECTOR_BY_ENV["ANTHROPIC_API_KEY"] == LLM_ANTHROPIC
    assert LLM_CONNECTOR_BY_ENV["OPENAI_API_KEY"] == LLM_OPENAI
    assert LLM_CONNECTOR_BY_ENV["XAI_API_KEY"] == LLM_XAI
    assert LLM_CONNECTOR_BY_ENV["PERPLEXITY_API_KEY"] == LLM_PERPLEXITY
    assert LLM_CONNECTOR_BY_ENV["GEMINI_API_KEY"] == LLM_GEMINI
    assert LLM_CONNECTOR_BY_ENV["MISTRAL_API_KEY"] == LLM_MISTRAL


def test_mapping_covers_models_yaml_envs():
    """Drift guard: every ``api_key_env`` in models.yaml is mapped here.

    Catches the case where a new cloud model is added to ``models.yaml``
    without registering its env-string → connector-id pair, which would
    silently leave the new vendor on the legacy app_settings/env path.
    """
    from services.llm_connector_map import LLM_CONNECTOR_BY_ENV

    candidates = [
        Path(__file__).parent.parent.parent / "config" / "models.yaml",
        Path("/opt/lumogis/config/models.yaml"),
    ]
    yaml_path = next((p for p in candidates if p.is_file()), None)
    if yaml_path is None:
        pytest.skip("models.yaml not found in any known location")

    data = yaml.safe_load(yaml_path.read_text())
    envs = {
        entry["api_key_env"]
        for entry in data.get("models", {}).values()
        if isinstance(entry, dict) and "api_key_env" in entry
    }
    missing = envs - set(LLM_CONNECTOR_BY_ENV)
    assert not missing, (
        f"models.yaml lists api_key_env values that LLM_CONNECTOR_BY_ENV does not "
        f"map: {sorted(missing)}. Add them to services/llm_connector_map.py + "
        f"connectors/registry.py."
    )


def test_connector_for_api_key_env_returns_none_for_unmapped():
    from services.llm_connector_map import connector_for_api_key_env

    assert connector_for_api_key_env("FUTURE_VENDOR_KEY") is None
    assert connector_for_api_key_env("") is None
    assert connector_for_api_key_env(None) is None  # type: ignore[arg-type]


def test_vendor_label_known_and_fallback():
    from connectors.registry import LLM_ANTHROPIC
    from services.llm_connector_map import vendor_label_for_connector

    assert vendor_label_for_connector(LLM_ANTHROPIC) == "Anthropic"
    assert vendor_label_for_connector("llm_unknown_future") == "llm_unknown_future"


# ---------------------------------------------------------------------------
# has_credential
# ---------------------------------------------------------------------------


def test_has_credential_true_when_row_present(store):
    from services.llm_connector_map import has_credential

    _seed_payload(store, user_id="alice", connector="llm_anthropic", payload={"api_key": "sk-x"})
    assert has_credential("alice", "ANTHROPIC_API_KEY") is True


def test_has_credential_false_when_row_missing(store):
    from services.llm_connector_map import has_credential

    assert has_credential("alice", "ANTHROPIC_API_KEY") is False


def test_has_credential_false_for_unmapped_env(store):
    from services.llm_connector_map import has_credential

    _seed_payload(store, user_id="alice", connector="llm_anthropic", payload={"api_key": "sk-x"})
    assert has_credential("alice", "FUTURE_VENDOR_KEY") is False


def test_has_credential_false_when_user_id_none(store):
    from services.llm_connector_map import has_credential

    _seed_payload(store, user_id="alice", connector="llm_anthropic", payload={"api_key": "sk-x"})
    assert has_credential(None, "ANTHROPIC_API_KEY") is False


# ---------------------------------------------------------------------------
# get_user_credentials_snapshot
# ---------------------------------------------------------------------------


def test_snapshot_returns_set_of_llm_connectors(store):
    from services.llm_connector_map import get_user_credentials_snapshot

    _seed_payload(store, user_id="alice", connector="llm_anthropic", payload={"api_key": "x"})
    _seed_payload(store, user_id="alice", connector="llm_openai", payload={"api_key": "y"})
    # Non-llm row must NOT appear.
    _seed_payload(
        store, user_id="alice", connector="ntfy", payload={"url": "https://n", "topic": "t"}
    )

    snap = get_user_credentials_snapshot("alice")
    assert snap == {"llm_anthropic", "llm_openai"}


def test_snapshot_empty_for_unknown_user(store):
    from services.llm_connector_map import get_user_credentials_snapshot

    assert get_user_credentials_snapshot("nobody") == set()


def test_snapshot_empty_for_none_user(store):
    from services.llm_connector_map import get_user_credentials_snapshot

    assert get_user_credentials_snapshot(None) == set()


def test_snapshot_uses_single_query(store):
    from services.llm_connector_map import get_user_credentials_snapshot

    get_user_credentials_snapshot("alice")
    assert len(store.fetch_all_calls) == 1
    sql, params = store.fetch_all_calls[0]
    assert "user_connector_credentials" in sql
    # Literal psycopg-aware SQL: percent is doubled to survive parameter
    # substitution; backslash escapes the underscore literal so a future
    # connector id like ``llmbroker`` cannot leak in.
    assert "LIKE 'llm\\_%%' ESCAPE '\\'" in sql
    assert params == ("alice",)


# ---------------------------------------------------------------------------
# effective_api_key — happy paths
# ---------------------------------------------------------------------------


def test_effective_api_key_per_user_row_under_auth_on(store):
    from services.llm_connector_map import effective_api_key

    _seed_payload(
        store, user_id="alice", connector="llm_anthropic", payload={"api_key": "  sk-secret  "}
    )
    assert effective_api_key("alice", "ANTHROPIC_API_KEY") == "sk-secret"


def test_effective_api_key_env_fallback_under_auth_off(monkeypatch, store):
    """Auth off, no per-user row, env var present → returns env value."""
    from services.llm_connector_map import effective_api_key

    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key-value")
    assert effective_api_key("alice", "ANTHROPIC_API_KEY") == "env-key-value"


def test_effective_api_key_unmapped_env_under_auth_off_reads_environ(monkeypatch, store):
    from services.llm_connector_map import effective_api_key

    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("FUTURE_VENDOR_KEY", "future-secret")
    assert effective_api_key("alice", "FUTURE_VENDOR_KEY") == "future-secret"


# ---------------------------------------------------------------------------
# effective_api_key — ConnectorNotConfigured (424)
# ---------------------------------------------------------------------------


def test_effective_api_key_missing_row_under_auth_on_raises_not_configured(store):
    from services.connector_credentials import ConnectorNotConfigured
    from services.llm_connector_map import effective_api_key

    with pytest.raises(ConnectorNotConfigured):
        effective_api_key("alice", "ANTHROPIC_API_KEY")


def test_effective_api_key_unmapped_env_under_auth_on_raises_not_configured(monkeypatch, store):
    from services.connector_credentials import ConnectorNotConfigured
    from services.llm_connector_map import effective_api_key

    monkeypatch.setenv("FUTURE_VENDOR_KEY", "ignored-because-unmapped")
    with pytest.raises(ConnectorNotConfigured):
        effective_api_key("alice", "FUTURE_VENDOR_KEY")


def test_effective_api_key_unmapped_env_under_auth_off_blank_raises_not_configured(
    monkeypatch, store
):
    from services.connector_credentials import ConnectorNotConfigured
    from services.llm_connector_map import effective_api_key

    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.delenv("FUTURE_VENDOR_KEY", raising=False)
    with pytest.raises(ConnectorNotConfigured):
        effective_api_key("alice", "FUTURE_VENDOR_KEY")


def test_effective_api_key_blank_after_strip_raises_not_configured(store):
    from services.connector_credentials import ConnectorNotConfigured
    from services.llm_connector_map import effective_api_key

    _seed_payload(store, user_id="alice", connector="llm_anthropic", payload={"api_key": "    "})
    with pytest.raises(ConnectorNotConfigured):
        effective_api_key("alice", "ANTHROPIC_API_KEY")


def test_effective_api_key_env_fallback_blank_raises_not_configured(monkeypatch, store):
    from services.connector_credentials import ConnectorNotConfigured
    from services.llm_connector_map import effective_api_key

    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ConnectorNotConfigured):
        effective_api_key("alice", "ANTHROPIC_API_KEY")


# ---------------------------------------------------------------------------
# effective_api_key — CredentialUnavailable (503)
# ---------------------------------------------------------------------------


def test_effective_api_key_missing_api_key_field_raises_unavailable(store):
    from services.connector_credentials import CredentialUnavailable
    from services.llm_connector_map import effective_api_key

    _seed_payload(
        store, user_id="alice", connector="llm_anthropic", payload={"wrong_field": "sk-x"}
    )
    with pytest.raises(CredentialUnavailable):
        effective_api_key("alice", "ANTHROPIC_API_KEY")


def test_effective_api_key_non_string_api_key_raises_unavailable(store):
    from services.connector_credentials import CredentialUnavailable
    from services.llm_connector_map import effective_api_key

    _seed_payload(store, user_id="alice", connector="llm_anthropic", payload={"api_key": 12345})
    with pytest.raises(CredentialUnavailable):
        effective_api_key("alice", "ANTHROPIC_API_KEY")
