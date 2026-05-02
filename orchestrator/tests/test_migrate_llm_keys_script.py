# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Tests for ``scripts/migrate_llm_keys_to_per_user.py``.

Plan: ``llm_provider_keys_per_user_migration`` Pass 4.14.

The script is a thin orchestrator over the substrate
:func:`services.connector_credentials.put_payload`, the legacy
``app_settings`` reader :func:`settings_store.get_setting`, and (with
``--delete-legacy``) a parameterised ``DELETE FROM app_settings``. The
heavy lifting (encrypt, audit, registry-strict membership) is owned
by the substrate and exercised in
``tests/test_connector_credentials_service.py`` — these tests only
prove the wrapper:

* Defines its CLI surface as the plan describes.
* Argparse-level rejects malformed ``--user-id`` / ``--actor`` values
  **before** any DB write (exit 2).
* Honours the dry-run / live mode toggle and the ``--delete-legacy``
  flag with the documented exit-code matrix and JSON envelope shapes.
* Never logs plaintext secrets — per-pair stderr lines carry only
  ``key_present`` / ``error_class`` and the live-mode summary
  aggregates counts only.
* Multi ``--user-id`` writes one row per (env, user) pair.
* Partial failures don't stop subsequent pairs; final exit is the OR.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest
from cryptography.fernet import Fernet

from tests.test_connector_credentials_service import _FakeStore


class _MigrationFakeStore(_FakeStore):
    """``_FakeStore`` + the ``app_settings`` SQL surface the script touches.

    The substrate ``_FakeStore`` only knows ``user_connector_credentials``
    + ``audit_log``; the migration script also reads/deletes
    ``app_settings`` rows. Composing the surfaces here keeps the tests
    in lock-step with the substrate test fakes (so a future SQL change
    in the service still passes through these tests) without forking
    the credential SQL.
    """

    def __init__(self) -> None:
        super().__init__()
        self.app_settings: dict[str, str] = {}
        self.deleted_legacy: list[str] = []

    def fetch_one(self, query: str, params: tuple | None = None) -> dict | None:
        q = self._norm(query)
        p = params or ()
        if q.startswith("select value from app_settings where key"):
            (key,) = p
            v = self.app_settings.get(key)
            return {"value": v} if v is not None else None
        return super().fetch_one(query, params)

    def execute(self, query: str, params: tuple | None = None) -> None:
        q = self._norm(query)
        if q.startswith("delete from app_settings where key"):
            (key,) = params or ()  # type: ignore[misc]
            self.app_settings.pop(key, None)
            self.deleted_legacy.append(key)
            return None
        return super().execute(query, params)


@pytest.fixture
def store(monkeypatch):
    import config as _config
    from services import connector_credentials as svc

    k1 = Fernet.generate_key().decode()
    monkeypatch.setenv("LUMOGIS_CREDENTIAL_KEY", k1)
    monkeypatch.delenv("LUMOGIS_CREDENTIAL_KEYS", raising=False)
    monkeypatch.setenv("AUTH_ENABLED", "true")
    svc.reset_for_tests()

    s = _MigrationFakeStore()
    _config._instances["metadata_store"] = s
    yield s
    _config._instances.pop("metadata_store", None)
    svc.reset_for_tests()


def _seed_all_six(store):
    """Populate every legacy plaintext row the script knows how to read."""
    store.app_settings.update({
        "ANTHROPIC_API_KEY": "sk-ant-fake",
        "OPENAI_API_KEY": "sk-openai-fake",
        "XAI_API_KEY": "xai-fake",
        "PERPLEXITY_API_KEY": "pplx-fake",
        "GEMINI_API_KEY": "gemini-fake",
        "MISTRAL_API_KEY": "mistral-fake",
    })


# ---------------------------------------------------------------------------
# Argparse-level rejections (exit 2 — must NEVER reach the DB).
# ---------------------------------------------------------------------------


def test_no_user_id_exits_two(store, capsys):
    from scripts.migrate_llm_keys_to_per_user import main

    with pytest.raises(SystemExit) as excinfo:
        main([])
    assert excinfo.value.code == 2
    # Argparse error went to stderr; nothing was written to the DB.
    assert store.rows == {}
    assert store.audit == []


def test_bad_user_id_format_exits_two_before_any_write(store, capsys):
    from scripts.migrate_llm_keys_to_per_user import main

    _seed_all_six(store)
    with pytest.raises(SystemExit) as excinfo:
        main(["--user-id", "alice; DROP TABLE users"])
    assert excinfo.value.code == 2
    assert store.rows == {}
    assert store.audit == []
    assert store.app_settings  # legacy plaintext untouched


def test_bad_actor_format_exits_two_before_any_write(store, capsys):
    from scripts.migrate_llm_keys_to_per_user import main

    _seed_all_six(store)
    with pytest.raises(SystemExit) as excinfo:
        main(["--user-id", "alice", "--actor", "not-a-valid-actor!"])
    assert excinfo.value.code == 2
    assert store.rows == {}
    assert store.audit == []


# ---------------------------------------------------------------------------
# Dry-run mode.
# ---------------------------------------------------------------------------


def test_dry_run_does_not_write(store, capsys):
    from scripts.migrate_llm_keys_to_per_user import main

    _seed_all_six(store)
    rc = main(["--dry-run", "--user-id", "alice"])
    assert rc == 0

    captured = capsys.readouterr()
    summary = json.loads(captured.out.strip())
    assert summary == {
        "mode": "dry_run",
        "would_migrate": 6,
        "skipped_no_source": 0,
        "would_delete_legacy": 0,
        "users": ["alice"],
    }
    # No rows / audits / deletes happened.
    assert store.rows == {}
    assert store.audit == []
    assert store.deleted_legacy == []
    # Legacy plaintext intact.
    assert store.app_settings.get("ANTHROPIC_API_KEY") == "sk-ant-fake"


def test_dry_run_with_delete_legacy_reports_would_delete(store, capsys):
    from scripts.migrate_llm_keys_to_per_user import main

    _seed_all_six(store)
    rc = main(["--dry-run", "--delete-legacy", "--user-id", "alice"])
    assert rc == 0

    summary = json.loads(capsys.readouterr().out.strip())
    assert summary["mode"] == "dry_run"
    assert summary["would_delete_legacy"] == 6
    assert summary["would_migrate"] == 6
    assert store.app_settings  # nothing actually deleted


def test_dry_run_skips_missing_sources(store, capsys):
    """Sources missing from app_settings increment skipped_no_source, not migrated."""
    from scripts.migrate_llm_keys_to_per_user import main

    # Only seed two of the six.
    store.app_settings["ANTHROPIC_API_KEY"] = "sk-ant-fake"
    store.app_settings["OPENAI_API_KEY"] = "sk-openai-fake"

    rc = main(["--dry-run", "--user-id", "alice"])
    assert rc == 0
    summary = json.loads(capsys.readouterr().out.strip())
    assert summary["would_migrate"] == 2
    assert summary["skipped_no_source"] == 4


def test_dry_run_skips_blank_after_strip(store, capsys):
    """Whitespace-only legacy values count as no source (not migrated)."""
    from scripts.migrate_llm_keys_to_per_user import main

    store.app_settings["ANTHROPIC_API_KEY"] = "   "
    rc = main(["--dry-run", "--user-id", "alice"])
    assert rc == 0
    summary = json.loads(capsys.readouterr().out.strip())
    assert summary["would_migrate"] == 0
    assert summary["skipped_no_source"] == 6


# ---------------------------------------------------------------------------
# Live mode — single + multi user, audit emission, plaintext-never-logged.
# ---------------------------------------------------------------------------


def test_single_user_migration_writes_rows_and_audit(store, capsys):
    from scripts.migrate_llm_keys_to_per_user import main

    _seed_all_six(store)
    rc = main(["--user-id", "alice"])
    assert rc == 0

    summary = json.loads(capsys.readouterr().out.strip())
    assert summary["mode"] == "live"
    assert summary["migrated"] == 6
    assert summary["failed"] == 0
    assert summary["deleted_legacy"] == 0
    assert summary["users"] == ["alice"]

    # One encrypted row per llm_<vendor> connector for alice.
    alice_rows = [k for k in store.rows if k[0] == "alice"]
    assert len(alice_rows) == 6
    for (uid, conn) in alice_rows:
        assert conn.startswith("llm_")

    # Audit emission: one __connector_credential__.put per write.
    put_audits = [a for a in store.audit
                  if a["action_name"] == "__connector_credential__.put"]
    assert len(put_audits) == 6
    for a in put_audits:
        assert a["user_id"] == "alice"


def test_multi_user_migration_writes_rows_for_each(store, capsys):
    from scripts.migrate_llm_keys_to_per_user import main

    _seed_all_six(store)
    rc = main(["--user-id", "alice", "--user-id", "bob"])
    assert rc == 0

    summary = json.loads(capsys.readouterr().out.strip())
    assert summary["mode"] == "live"
    assert summary["migrated"] == 12
    assert summary["users"] == ["alice", "bob"]

    alice_rows = [k for k in store.rows if k[0] == "alice"]
    bob_rows = [k for k in store.rows if k[0] == "bob"]
    assert len(alice_rows) == 6
    assert len(bob_rows) == 6


def test_delete_legacy_removes_app_settings_row_after_success(store, capsys):
    from scripts.migrate_llm_keys_to_per_user import main

    _seed_all_six(store)
    rc = main(["--delete-legacy", "--user-id", "alice"])
    assert rc == 0
    summary = json.loads(capsys.readouterr().out.strip())
    assert summary["deleted_legacy"] == 6
    # Plaintext app_settings rows are gone.
    for env_key in (
        "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "XAI_API_KEY",
        "PERPLEXITY_API_KEY", "GEMINI_API_KEY", "MISTRAL_API_KEY",
    ):
        assert env_key not in store.app_settings, env_key


def test_plaintext_never_logged_anywhere(store, capsys):
    """Per-pair stderr + summary stdout MUST NOT echo any secret bytes."""
    from scripts.migrate_llm_keys_to_per_user import main

    secret_substrings = [
        "sk-ant-fake", "sk-openai-fake", "xai-fake",
        "pplx-fake", "gemini-fake", "mistral-fake",
    ]
    _seed_all_six(store)
    rc = main(["--user-id", "alice"])
    assert rc == 0
    captured = capsys.readouterr()
    blob = captured.out + captured.err
    for secret in secret_substrings:
        assert secret not in blob, (
            f"secret substring {secret!r} leaked into script output"
        )

    # And every per-pair stderr line should be a JSON object with the
    # documented schema (no plaintext field).
    for line in captured.err.strip().splitlines():
        if not line.startswith("{"):
            continue  # skip the "Note: restart..." trailing line
        rec = json.loads(line)
        assert set(rec.keys()) == {
            "user_id", "connector", "outcome", "error_class", "key_present"
        }, rec
        assert isinstance(rec["key_present"], bool)


def test_partial_failure_does_not_stop_subsequent_pairs(store, capsys, monkeypatch):
    """One ``put_payload`` exception → exit 1, but every other pair still runs."""
    from scripts import migrate_llm_keys_to_per_user as script

    _seed_all_six(store)
    real_put = script.ccs.put_payload
    seen: list[tuple[str, str]] = []

    def _put(user_id, connector, payload, *, actor):
        seen.append((user_id, connector))
        if connector == "llm_openai":
            raise RuntimeError("simulated transient DB hiccup")
        return real_put(user_id, connector, payload, actor=actor)

    monkeypatch.setattr(script.ccs, "put_payload", _put)

    rc = script.main(["--user-id", "alice"])
    assert rc == 1
    summary = json.loads(capsys.readouterr().out.strip())
    assert summary["mode"] == "live"
    assert summary["failed"] == 1
    assert summary["migrated"] == 5
    # Every connector was attempted (no early bail-out).
    attempted_connectors = {c for (_uid, c) in seen}
    assert attempted_connectors == {
        "llm_anthropic", "llm_openai", "llm_xai",
        "llm_perplexity", "llm_gemini", "llm_mistral",
    }


def test_credential_key_unset_exits_two_before_any_write(store, monkeypatch, capsys):
    """``LUMOGIS_CREDENTIAL_KEY[S]`` unset → exit 2 with remediation hint."""
    from scripts.migrate_llm_keys_to_per_user import main
    from services import connector_credentials as svc

    monkeypatch.delenv("LUMOGIS_CREDENTIAL_KEY", raising=False)
    monkeypatch.delenv("LUMOGIS_CREDENTIAL_KEYS", raising=False)
    svc.reset_for_tests()

    _seed_all_six(store)
    rc = main(["--user-id", "alice"])
    assert rc == 2
    captured = capsys.readouterr()
    assert "LUMOGIS_CREDENTIAL_KEY" in captured.err
    assert store.rows == {}
    assert store.audit == []
