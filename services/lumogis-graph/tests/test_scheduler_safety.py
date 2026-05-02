# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for the scheduler-safety guards in `graph/__init__.py`.

The plan §"Scheduler safety" requires two AND'd guards before the KG
service registers daily reconciliation + weekly quality jobs:
  1. `KG_SCHEDULER_ENABLED=true` (operator opt-out for sidecar deployments).
  2. `GRAPH_MODE != "inprocess"` (defence in depth: when Core thinks IT
     owns the graph, KG must NOT also schedule projection work or both
     processes scan the same Postgres rows).

These tests pin down the contract so a future refactor can't silently
turn either guard off.
"""

from __future__ import annotations


def test_scheduler_does_not_register_when_kg_scheduler_enabled_false(
    mock_scheduler, monkeypatch, caplog
):
    """KG_SCHEDULER_ENABLED=false → no jobs registered, single INFO log."""
    monkeypatch.setenv("KG_SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("GRAPH_MODE", "service")

    import graph

    with caplog.at_level("INFO", logger="graph"):
        graph.register_scheduled_jobs(mock_scheduler)

    assert mock_scheduler.get_jobs() == []
    assert any(
        "scheduler disabled" in rec.message for rec in caplog.records
    ), "expected an INFO log explaining why no jobs were scheduled"


def test_scheduler_does_not_register_when_graph_mode_inprocess(
    mock_scheduler, monkeypatch, caplog
):
    """GRAPH_MODE=inprocess (Core owns the graph) → no jobs registered."""
    monkeypatch.setenv("KG_SCHEDULER_ENABLED", "true")
    monkeypatch.setenv("GRAPH_MODE", "inprocess")

    import graph

    with caplog.at_level("INFO", logger="graph"):
        graph.register_scheduled_jobs(mock_scheduler)

    assert mock_scheduler.get_jobs() == []


def test_scheduler_registers_both_jobs_when_both_guards_pass(
    mock_scheduler, monkeypatch
):
    """Default config: KG_SCHEDULER_ENABLED=true + GRAPH_MODE=service → both jobs added."""
    monkeypatch.setenv("KG_SCHEDULER_ENABLED", "true")
    monkeypatch.setenv("GRAPH_MODE", "service")

    import graph

    graph.register_scheduled_jobs(mock_scheduler)

    job_ids = {j["id"] for j in mock_scheduler.get_jobs()}
    assert "graph_reconciliation" in job_ids
    assert "graph_weekly_quality" in job_ids


def test_scheduler_should_run_reflects_env(monkeypatch):
    """Direct unit test on the predicate so callers other than `register_scheduled_jobs`
    (e.g. future health-check fields) get a stable contract."""
    from graph import _scheduler_should_run

    monkeypatch.setenv("KG_SCHEDULER_ENABLED", "true")
    monkeypatch.setenv("GRAPH_MODE", "service")
    assert _scheduler_should_run() is True

    monkeypatch.setenv("KG_SCHEDULER_ENABLED", "false")
    assert _scheduler_should_run() is False

    monkeypatch.setenv("KG_SCHEDULER_ENABLED", "true")
    monkeypatch.setenv("GRAPH_MODE", "inprocess")
    assert _scheduler_should_run() is False
