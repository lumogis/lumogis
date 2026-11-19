# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Boot warning when saved credentials do not match the loaded key (LUM-981, K1-K6).

A re-copied ``.env`` brings a freshly generated ``LUMOGIS_CREDENTIAL_KEY``. The
check warns (it never refuses): losing saved passwords is bounded, and refusing
to boot would be a total outage. Counts come from the three real
``count_rows_by_key_version`` readers, with the store behind them faked.
"""

from __future__ import annotations

import logging

import main
import pytest
from cryptography.fernet import Fernet

import config
from services import _credential_internals as internals

KEY_A = Fernet.generate_key().decode()
KEY_B = Fernet.generate_key().decode()
FP_A = internals._key_fingerprint(KEY_A.encode())
FP_B = internals._key_fingerprint(KEY_B.encode())
FP_OLD = (FP_A + 1) % (2**32)


class _CountingStore:
    """Answers the three ``GROUP BY key_version`` queries from per-table counts."""

    def __init__(self, user=None, household=None, system=None, boom=False):
        self.by_table = {
            "user_connector_credentials": user or {},
            "household_connector_credentials": household or {},
            "instance_system_connector_credentials": system or {},
        }
        self.boom = boom

    def fetch_all(self, query, params=None):
        if self.boom:
            raise RuntimeError("relation does not exist")
        for table, counts in self.by_table.items():
            if table in query:
                return [{"key_version": v, "n": n} for v, n in counts.items()]
        raise AssertionError(f"unexpected query: {query}")


@pytest.fixture
def store():
    def install(**kw):
        config._instances["metadata_store"] = _CountingStore(**kw)

    yield install
    config._instances.pop("metadata_store", None)


@pytest.fixture(autouse=True)
def _keys(monkeypatch):
    monkeypatch.delenv("LUMOGIS_CREDENTIAL_KEYS", raising=False)
    monkeypatch.setenv("LUMOGIS_CREDENTIAL_KEY", KEY_A)
    internals.reset_for_tests()
    yield
    internals.reset_for_tests()


def _warnings(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]


def test_k1_no_rows_no_warning(store, caplog):
    store()
    with caplog.at_level(logging.INFO):
        main._enforce_credential_key_match()
    assert _warnings(caplog) == []


def test_k2_rows_sealed_with_the_loaded_key_no_warning(store, caplog):
    store(user={FP_A: 3}, household={FP_A: 1}, system={FP_A: 2})
    with caplog.at_level(logging.INFO):
        main._enforce_credential_key_match()
    assert _warnings(caplog) == []


def test_k3_no_row_matches_the_loaded_key_warns_with_the_row_count(store, caplog):
    store(user={FP_OLD: 3}, household={FP_OLD: 1})
    with caplog.at_level(logging.INFO):
        main._enforce_credential_key_match()  # does not raise
    (msg,) = _warnings(caplog)
    assert "different LUMOGIS_CREDENTIAL_KEY" in msg
    assert "(4 rows)" in msg
    assert "LUMOGIS_CREDENTIAL_KEYS=<the key now in .env>,<the old key>" in msg
    assert KEY_A not in msg and str(FP_OLD) not in msg  # no key material, no fingerprints


def test_k4_some_versions_unloadable_warns_with_counts_and_does_not_raise(store, caplog):
    store(user={FP_A: 2, FP_OLD: 5})
    with caplog.at_level(logging.INFO):
        main._enforce_credential_key_match()
    (msg,) = _warnings(caplog)
    assert "different LUMOGIS_CREDENTIAL_KEY" not in msg
    assert str(FP_OLD) in msg and "5" in msg
    assert KEY_A not in msg


def test_k4_a_second_loaded_key_covers_the_old_rows(store, monkeypatch, caplog):
    monkeypatch.setenv("LUMOGIS_CREDENTIAL_KEYS", f"{KEY_A},{KEY_B}")
    internals.reset_for_tests()
    store(user={FP_A: 2, FP_B: 5})
    with caplog.at_level(logging.INFO):
        main._enforce_credential_key_match()
    assert _warnings(caplog) == []


def test_k5_rows_exist_but_no_key_is_configured_warns_the_same(store, monkeypatch, caplog):
    monkeypatch.delenv("LUMOGIS_CREDENTIAL_KEY", raising=False)
    internals.reset_for_tests()
    store(user={FP_A: 2})
    with caplog.at_level(logging.INFO):
        main._enforce_credential_key_match()
    (msg,) = _warnings(caplog)
    assert "different LUMOGIS_CREDENTIAL_KEY" in msg


def test_k6_a_failing_count_is_logged_and_the_boot_continues(store, caplog):
    store(boom=True)
    with caplog.at_level(logging.INFO):
        main._run_credential_key_check()  # what the lifespan calls; no raise
    assert any("credential key check failed" in r.getMessage() for r in caplog.records)
