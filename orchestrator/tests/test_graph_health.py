# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Tests for the GET /graph/health endpoint — Pass 2 KG Quality Pipeline.

Coverage:
  1.  Response has correct shape with all required fields
  2.  Empty corpus returns zeros and null ingestion_quality_trend_7d
  3.  constraint_violation_counts reflect open violations
  4.  Orphan percentage computed correctly
  5.  Mean completeness excludes staged entities and NULLs
  6.  ingestion_quality_trend_7d is null when no entities in last 7 days
  7.  Temporal freshness buckets are correct
  8.  Auth: no authentication required (matches existing admin read pattern)
  9.  503 when Postgres unreachable
  10. One metric failure does not fail entire endpoint
"""

from unittest.mock import MagicMock

import main
import pytest
from fastapi.testclient import TestClient

import config as _config

# ---------------------------------------------------------------------------
# Configurable MetadataStore mock for health endpoint tests
# ---------------------------------------------------------------------------


class _HealthMetadataStore:
    """A programmable metadata store for /graph/health tests.

    Callers inject query-keyed results. Any query whose substring matches a
    key in fetch_one_results / fetch_all_results returns the mapped value.
    """

    def __init__(
        self,
        *,
        ping_ok: bool = True,
        fetch_one_results: dict | None = None,
        fetch_all_results: dict | None = None,
    ):
        self._ping_ok = ping_ok
        self._fetch_one: dict = fetch_one_results or {}
        self._fetch_all: dict = fetch_all_results or {}
        self.execute = MagicMock()

    def ping(self) -> bool:
        return self._ping_ok

    def fetch_one(self, query: str, params=None) -> dict | None:
        for key, val in self._fetch_one.items():
            if key in query:
                return val
        return None

    def fetch_all(self, query: str, params=None) -> list[dict]:
        for key, val in self._fetch_all.items():
            if key in query:
                return val
        return []

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    with TestClient(main.app) as c:
        yield c


def _inject_store(monkeypatch, store):
    monkeypatch.setitem(_config._instances, "metadata_store", store)


# ---------------------------------------------------------------------------
# 1. Response has correct shape with all required fields
# ---------------------------------------------------------------------------


def test_graph_health_response_shape(client, monkeypatch):
    store = _HealthMetadataStore(
        fetch_one_results={
            "review_queue": {"cnt": 0},
            "orphan": {"pct": None},
            "AVG(extraction_quality)": {"mean_quality": None},
            "7 days": {"trend": None},
            "last_7d": {"last_7d": 0, "d8_30": 0, "d31_90": 0, "d90_plus": 0},
        }
    )
    _inject_store(monkeypatch, store)

    resp = client.get("/graph/health")
    assert resp.status_code == 200

    data = resp.json()
    assert "duplicate_candidate_count" in data
    assert "orphan_entity_pct" in data
    assert "mean_entity_completeness" in data
    assert "constraint_violation_counts" in data
    assert "ingestion_quality_trend_7d" in data
    assert "temporal_freshness" in data

    vc = data["constraint_violation_counts"]
    assert "CRITICAL" in vc
    assert "WARNING" in vc
    assert "INFO" in vc

    tf = data["temporal_freshness"]
    assert "last_7d" in tf
    assert "8_30d" in tf
    assert "31_90d" in tf
    assert "90d_plus" in tf


# ---------------------------------------------------------------------------
# 2. Empty corpus returns zeros and null trend
# ---------------------------------------------------------------------------


def test_graph_health_empty_corpus(client, monkeypatch):
    store = _HealthMetadataStore(
        fetch_one_results={
            "review_queue": {"cnt": 0},
            "orphan": {"pct": None},
            "AVG": {"mean_quality": None},
            "7 days": {"trend": None},
            "last_7d": {"last_7d": 0, "d8_30": 0, "d31_90": 0, "d90_plus": 0},
        }
    )
    _inject_store(monkeypatch, store)

    data = client.get("/graph/health").json()

    assert data["duplicate_candidate_count"] == 0
    assert data["orphan_entity_pct"] == 0.0
    assert data["mean_entity_completeness"] == 0.0
    assert data["ingestion_quality_trend_7d"] is None
    assert data["constraint_violation_counts"] == {"CRITICAL": 0, "WARNING": 0, "INFO": 0}
    tf = data["temporal_freshness"]
    assert all(v == 0 for v in tf.values())


# ---------------------------------------------------------------------------
# 3. constraint_violation_counts reflect open violations
# ---------------------------------------------------------------------------


def test_graph_health_violation_counts(client, monkeypatch):
    store = _HealthMetadataStore(
        fetch_one_results={
            "review_queue": {"cnt": 2},
            "orphan": {"pct": 0.0},
            "AVG": {"mean_quality": 0.75},
            "7 days": {"trend": 0.8},
            "last_7d": {"last_7d": 5, "d8_30": 3, "d31_90": 1, "d90_plus": 0},
        },
        fetch_all_results={
            "GROUP BY severity": [
                {"severity": "CRITICAL", "cnt": 3},
                {"severity": "WARNING", "cnt": 7},
                {"severity": "INFO", "cnt": 2},
            ]
        },
    )
    _inject_store(monkeypatch, store)

    data = client.get("/graph/health").json()

    vc = data["constraint_violation_counts"]
    assert vc["CRITICAL"] == 3
    assert vc["WARNING"] == 7
    assert vc["INFO"] == 2


# ---------------------------------------------------------------------------
# 4. Orphan percentage computed correctly
# ---------------------------------------------------------------------------


def test_graph_health_orphan_pct(client, monkeypatch):
    store = _HealthMetadataStore(
        fetch_one_results={
            "review_queue": {"cnt": 0},
            "entity_id NOT IN": {"pct": 25.0},
            "AVG(extraction_quality)": {"mean_quality": None},
            "AS trend": {"trend": None},
            "AS last_7d": {"last_7d": 0, "d8_30": 0, "d31_90": 0, "d90_plus": 0},
        }
    )
    _inject_store(monkeypatch, store)

    data = client.get("/graph/health").json()
    assert data["orphan_entity_pct"] == 25.0


# ---------------------------------------------------------------------------
# 5. Mean completeness excludes staged entities and NULLs
# ---------------------------------------------------------------------------


def test_graph_health_mean_completeness_non_staged(client, monkeypatch):
    # The endpoint queries with is_staged = FALSE and extraction_quality IS NOT NULL
    # We verify the returned value is correctly surfaced.
    store = _HealthMetadataStore(
        fetch_one_results={
            "review_queue": {"cnt": 0},
            "orphan": {"pct": None},
            "AVG(extraction_quality)": {"mean_quality": 0.8234},
            "7 days": {"trend": None},
            "last_7d": {"last_7d": 0, "d8_30": 0, "d31_90": 0, "d90_plus": 0},
        }
    )
    _inject_store(monkeypatch, store)

    data = client.get("/graph/health").json()
    assert data["mean_entity_completeness"] == pytest.approx(0.8234, abs=0.0001)


# ---------------------------------------------------------------------------
# 6. ingestion_quality_trend_7d is null when no entities in last 7 days
# ---------------------------------------------------------------------------


def test_graph_health_trend_null_when_no_recent_entities(client, monkeypatch):
    store = _HealthMetadataStore(
        fetch_one_results={
            "review_queue": {"cnt": 0},
            "orphan": {"pct": None},
            "AVG(extraction_quality)": {"mean_quality": 0.7},
            "last_7d": {"last_7d": 0, "d8_30": 0, "d31_90": 0, "d90_plus": 0},
        }
    )
    _inject_store(monkeypatch, store)

    data = client.get("/graph/health").json()
    assert data["ingestion_quality_trend_7d"] is None


# ---------------------------------------------------------------------------
# 7. Temporal freshness buckets are correct
# ---------------------------------------------------------------------------


def test_graph_health_temporal_freshness_buckets(client, monkeypatch):
    store = _HealthMetadataStore(
        fetch_one_results={
            "review_queue": {"cnt": 0},
            "entity_id NOT IN": {"pct": None},
            "AVG(extraction_quality)": {"mean_quality": None},
            "AS trend": {"trend": None},
            "AS last_7d": {"last_7d": 10, "d8_30": 5, "d31_90": 3, "d90_plus": 1},
        }
    )
    _inject_store(monkeypatch, store)

    data = client.get("/graph/health").json()
    tf = data["temporal_freshness"]
    assert tf["last_7d"] == 10
    assert tf["8_30d"] == 5
    assert tf["31_90d"] == 3
    assert tf["90d_plus"] == 1


# ---------------------------------------------------------------------------
# 8. Auth: no authentication required (matches /health endpoint pattern)
# ---------------------------------------------------------------------------


def test_graph_health_no_auth_required(client, monkeypatch):
    """Endpoint must be accessible without any auth header — same as /health."""
    store = _HealthMetadataStore(
        fetch_one_results={
            "review_queue": {"cnt": 0},
            "orphan": {"pct": None},
            "AVG": {"mean_quality": None},
            "7 days": {"trend": None},
            "last_7d": {"last_7d": 0, "d8_30": 0, "d31_90": 0, "d90_plus": 0},
        }
    )
    _inject_store(monkeypatch, store)

    # No Authorization header; should still succeed
    resp = client.get("/graph/health")
    assert resp.status_code == 200


def test_graph_health_no_auth_with_auth_enabled(client, monkeypatch):
    """When AUTH_ENABLED=true the auth middleware enforces a bearer token on all
    routes including /graph/health.  Callers without a token receive 401."""
    store = _HealthMetadataStore(
        fetch_one_results={
            "review_queue": {"cnt": 0},
            "entity_id NOT IN": {"pct": None},
            "AVG(extraction_quality)": {"mean_quality": None},
            "AS trend": {"trend": None},
            "AS last_7d": {"last_7d": 0, "d8_30": 0, "d31_90": 0, "d90_plus": 0},
        }
    )
    _inject_store(monkeypatch, store)
    monkeypatch.setenv("AUTH_ENABLED", "true")

    resp = client.get("/graph/health")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 9. 503 when Postgres unreachable
# ---------------------------------------------------------------------------


def test_graph_health_503_when_postgres_unreachable(client, monkeypatch):
    store = _HealthMetadataStore(ping_ok=False)
    _inject_store(monkeypatch, store)

    resp = client.get("/graph/health")
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# 10. One metric failure does not fail the entire endpoint
# ---------------------------------------------------------------------------


def test_graph_health_partial_failure_does_not_break_endpoint(client, monkeypatch):
    """If one metric query raises, the endpoint still returns 200 with 0/null for that field."""

    class _BrokenStore(_HealthMetadataStore):
        def fetch_one(self, query, params=None):
            if "review_queue" in query:
                raise RuntimeError("simulated DB error for review_queue")
            return super().fetch_one(query, params)

    store = _BrokenStore(
        fetch_one_results={
            "entity_id NOT IN": {"pct": 10.0},
            "AVG(extraction_quality)": {"mean_quality": 0.65},
            "AS trend": {"trend": 0.7},
            "AS last_7d": {"last_7d": 2, "d8_30": 1, "d31_90": 0, "d90_plus": 0},
        }
    )
    _inject_store(monkeypatch, store)

    resp = client.get("/graph/health")
    assert resp.status_code == 200

    data = resp.json()
    # The failing metric should fall back to 0
    assert data["duplicate_candidate_count"] == 0
    # Other metrics should still be populated
    assert data["orphan_entity_pct"] == 10.0
