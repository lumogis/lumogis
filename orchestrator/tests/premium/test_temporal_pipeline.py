# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for plugins/graph/temporal_pipeline.py (LUM-104).

Mocked LLM provider + graph/metadata stores — every guard G1–G8 is pinned
individually, plus the Postgres-first ordering invariant (call-order spy AND
exception-injection variant), the fail-open judge contract, the shared-scope
detect-and-record fold, and the sweep's newest-first ordering.
"""

from __future__ import annotations

import json
from datetime import datetime
from datetime import timezone
from types import SimpleNamespace
from typing import get_args

import features
import pytest
from graph import temporal
from plugins.graph import temporal_pipeline as tp

import config

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeGraphStore:
    def __init__(self):
        self.queries: list[tuple[str, dict]] = []
        self.nodes: dict[tuple[str, str], dict] = {}
        self.edges: list[dict] = []
        self._canned: list[tuple[str, list[dict]]] = []
        self.raise_on: list[str] = []
        self._next_id = 0

    def seed(self, substring: str, rows: list[dict]) -> None:
        self._canned.append((substring, rows))

    def query(self, cypher: str, params: dict | None = None) -> list[dict]:
        self.queries.append((cypher, params or {}))
        for substring in self.raise_on:
            if substring in cypher:
                raise RuntimeError(f"injected failure for {substring!r}")
        for substring, rows in self._canned:
            if substring in cypher:
                return list(rows)
        return []

    def create_node(self, labels, properties) -> str:
        key = (str(properties.get("lumogis_id")), str(properties.get("user_id")))
        if key in self.nodes:
            self.nodes[key]["props"].update(properties)
            return self.nodes[key]["id"]
        self._next_id += 1
        nid = str(self._next_id)
        self.nodes[key] = {"id": nid, "labels": list(labels), "props": dict(properties)}
        return nid

    def create_edge(self, from_id, to_id, rel_type, properties) -> None:
        self.edges.append(
            {"from_id": from_id, "to_id": to_id, "rel_type": rel_type, "props": dict(properties)}
        )


class FakeMetadataStore:
    def __init__(self):
        self.executed: list[tuple[str, tuple]] = []
        self.fetches: list[tuple[str, tuple]] = []
        self._all: list[tuple[str, list[dict]]] = []
        self._one: list[tuple[str, dict | None]] = []
        self.execute_raises = False

    def seed_all(self, substring: str, rows: list[dict]) -> None:
        self._all.append((substring, rows))

    def seed_one(self, substring: str, row: dict | None) -> None:
        self._one.append((substring, row))

    def fetch_all(self, sql: str, params=None) -> list[dict]:
        self.fetches.append((sql, params))
        for substring, rows in self._all:
            if substring in sql:
                return list(rows)
        return []

    def fetch_one(self, sql: str, params=None):
        self.fetches.append((sql, params))
        for substring, row in self._one:
            if substring in sql:
                return dict(row) if row else None
        return None

    def execute(self, sql: str, params=None) -> None:
        if self.execute_raises:
            raise RuntimeError("injected Postgres failure")
        self.executed.append((sql, params))


class FakeProvider:
    def __init__(self, replies: list):
        self.replies = list(replies)
        self.calls: list[dict] = []

    def chat(self, messages, tools=None, system=None, max_tokens=4096):
        self.calls.append({"messages": messages, "system": system})
        reply = self.replies.pop(0) if self.replies else "{}"
        if isinstance(reply, Exception):
            raise reply
        return SimpleNamespace(text=reply, tool_calls=[], stop_reason="stop")


EXTRACT_REPLY = json.dumps({"valid_at": "2026-05-01T00:00:00Z"})
JUDGE_SUPERSEDES = json.dumps(
    [{"edge_uid": "uid1", "verdict": "supersedes", "confidence": 0.95, "reason": "changed"}]
)
REF = datetime(2026, 7, 3, 6, 0, 0, tzinfo=timezone.utc)


# Hermetic model registry: the reference alias (the static ``llama`` entry), a
# second alias on the same Ollama tag, and a judge on other weights. No Ollama.
_REF_TAG = "llama3.2:3b"
_REGISTRY = {
    "llama": {"adapter": "openai", "model": _REF_TAG, "base_url": "http://ollama:11434/v1"},
    "llama-judge": {"adapter": "openai", "model": _REF_TAG, "base_url": "http://ollama:11434/v1"},
    "qwen-judge": {
        "adapter": "openai",
        "model": "qwen2.5:7b",
        "base_url": "http://ollama:11434/v1",
    },
}


@pytest.fixture(autouse=True)
def _model_registry(monkeypatch):
    monkeypatch.setattr(config, "_models_config", {k: dict(v) for k, v in _REGISTRY.items()})
    monkeypatch.setattr(config, "_dynamic_ollama_models", lambda: {})


@pytest.fixture
def gs():
    return FakeGraphStore()


@pytest.fixture
def ms():
    return FakeMetadataStore()


@pytest.fixture
def provider_spy(monkeypatch):
    """Install a FakeProvider factory; returns a dict with provider + calls."""
    state = {"provider": FakeProvider([EXTRACT_REPLY, JUDGE_SUPERSEDES]), "factory_calls": []}

    def _factory(model_name, *, user_id=None):
        state["factory_calls"].append({"model": model_name, "user_id": user_id})
        return state["provider"]

    monkeypatch.setattr(config, "get_llm_provider", _factory)
    monkeypatch.setattr(tp.context_budget, "get_budget", lambda model: 8192)
    return state


def _wire_stores(monkeypatch, gs, ms):
    monkeypatch.setattr(config, "get_graph_store", lambda bank=None: gs)
    monkeypatch.setattr(config, "get_metadata_store", lambda: ms)


def _seed_happy_path(gs, ms, *, tier="personal"):
    """Episode note-1 (user u1) with entities e1<e2 and one valid candidate."""
    ms.seed_all("FROM entity_relations er", [{"source_id": "e1"}, {"source_id": "e2"}])
    ms.seed_one("FROM notes", {"text": "Thomas stopped working with Marcus in March 2025."})
    ms.seed_all(
        "FROM entity_edges",
        [
            {
                "src_entity_id": "e1",
                "dst_entity_id": "e2",
                "relation_type": "WORKS_WITH",
                "evidence_id": "mem1",
            }
        ],
    )
    ms.seed_all(
        "FROM entities WHERE entity_id = ANY",
        [{"entity_id": "e1", "name": "Thomas"}, {"entity_id": "e2", "name": "Marcus"}],
    )
    ms.seed_all("FROM memories", [{"id": "mem1", "content": "Thomas works with Marcus"}])
    ms.seed_one(
        "HAVING count(DISTINCT source_id) = 2",
        {"evidence_id": "note-0", "evidence_type": "NOTE"},
    )
    scope_key = "a.scope = 'personal'" if tier == "personal" else "a.scope = 'shared'"
    gs.seed(
        scope_key,
        [{"edge_uid": "uid1", "a_id": "e1", "b_id": "e2", "strength": 5, "last_seen": None}],
    )
    gs.seed("WHERE r.edge_uid = $edge_uid", [{"rid": 1}])


def _run(monkeypatch, gs, ms, **kw):
    _wire_stores(monkeypatch, gs, ms)
    return tp.on_episode_temporal_check(
        evidence_id="note-1",
        evidence_type="NOTE",
        user_id="u1",
        reference_time=REF,
        **kw,
    )


def _stamp_status(gs) -> str | None:
    node = gs.nodes.get(("note-1", "u1"))
    return node["props"].get("temporal_check_status") if node else None


# ---------------------------------------------------------------------------
# Flag gate + no-LLM negative
# ---------------------------------------------------------------------------


def test_flag_off_no_llm_provider_constructed(monkeypatch, gs, ms, provider_spy):
    monkeypatch.delenv("LUMOGIS_FF_TEMPORAL_KG", raising=False)
    result = _run(monkeypatch, gs, ms)
    assert result is None
    assert provider_spy["factory_calls"] == []  # provider factory never called
    assert gs.queries == []


def test_zero_entities_stamps_skipped_without_llm(monkeypatch, gs, ms, provider_spy):
    monkeypatch.setenv("LUMOGIS_FF_TEMPORAL_KG", "true")
    # no entity_relations rows seeded → zero graph entities
    result = _run(monkeypatch, gs, ms)
    assert result is not None and result.status == "skipped"
    assert provider_spy["factory_calls"] == []
    assert _stamp_status(gs) == "skipped"


def test_sweep_stamps_text_unavailable_and_does_not_loop(monkeypatch, gs, ms, provider_spy):
    monkeypatch.setenv("LUMOGIS_FF_TEMPORAL_KG", "true")
    ms.seed_all("FROM entity_relations er", [{"source_id": "e1"}])
    # no notes row seeded → hydration returns None
    result = _run(monkeypatch, gs, ms)
    assert result is not None and result.status == "text_unavailable"
    assert _stamp_status(gs) == "text_unavailable"  # terminal: sweep skips it
    assert provider_spy["factory_calls"] == []


# ---------------------------------------------------------------------------
# Extraction — fail-safe default + future clamp
# ---------------------------------------------------------------------------


def test_extract_failure_defaults_to_ingestion_time_and_judgment_still_runs(
    monkeypatch, gs, ms, provider_spy
):
    monkeypatch.setenv("LUMOGIS_FF_TEMPORAL_KG", "true")
    _seed_happy_path(gs, ms)
    provider_spy["provider"].replies = [RuntimeError("ollama down"), JUDGE_SUPERSEDES]
    result = _run(monkeypatch, gs, ms)
    assert result is not None
    assert result.status == "extract_failed"  # informational, NOT terminal
    assert result.extracted_valid_at == REF  # ingestion-time default
    assert len(provider_spy["provider"].calls) == 2  # judgment still ran
    assert result.proposed == 1  # shadow default → proposal recorded


def test_future_valid_at_clamped_to_reference_time(monkeypatch, gs, ms, provider_spy):
    monkeypatch.setenv("LUMOGIS_FF_TEMPORAL_KG", "true")
    _seed_happy_path(gs, ms)
    provider_spy["provider"].replies = [
        json.dumps({"valid_at": "2030-01-01T00:00:00Z"}),
        JUDGE_SUPERSEDES,
    ]
    result = _run(monkeypatch, gs, ms)
    assert result.extracted_valid_at == REF  # clamped, WARNING logged


# ---------------------------------------------------------------------------
# Judge — strict schema, fail-open to no action
# ---------------------------------------------------------------------------


def test_judge_parse_failure_applies_nothing_and_stamps_status(monkeypatch, gs, ms, provider_spy):
    monkeypatch.setenv("LUMOGIS_FF_TEMPORAL_KG", "true")
    monkeypatch.setenv("LUMOGIS_TEMPORAL_AUTO_APPLY", "true")
    _seed_happy_path(gs, ms)
    provider_spy["provider"].replies = [EXTRACT_REPLY, "sorry, I cannot help with that"]
    result = _run(monkeypatch, gs, ms)
    assert result.status == "judge_failed"
    assert result.applied == 0 and result.proposed == 0
    assert ms.executed == []  # no Postgres archive
    assert not [c for c, _ in gs.queries if "SET r.invalid_at" in c]  # no graph write
    assert _stamp_status(gs) == "judge_failed"  # countable, sweep won't loop


def test_judge_unknown_edge_uid_fails_whole_episode(monkeypatch, gs, ms, provider_spy):
    monkeypatch.setenv("LUMOGIS_FF_TEMPORAL_KG", "true")
    _seed_happy_path(gs, ms)
    provider_spy["provider"].replies = [
        EXTRACT_REPLY,
        json.dumps(
            [
                {
                    "edge_uid": "not-a-real-uid",
                    "verdict": "supersedes",
                    "confidence": 0.9,
                    "reason": "x",
                }
            ]
        ),
    ]
    result = _run(monkeypatch, gs, ms)
    assert result.status == "judge_failed"


def test_judge_out_of_range_confidence_rejected(monkeypatch, gs, ms, provider_spy):
    monkeypatch.setenv("LUMOGIS_FF_TEMPORAL_KG", "true")
    _seed_happy_path(gs, ms)
    provider_spy["provider"].replies = [
        EXTRACT_REPLY,
        json.dumps(
            [{"edge_uid": "uid1", "verdict": "supersedes", "confidence": 1.7, "reason": "x"}]
        ),
    ]
    result = _run(monkeypatch, gs, ms)
    assert result.status == "judge_failed"  # pydantic ge/le bound


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


def test_candidate_selection_personal_scope_only_for_auto_apply(monkeypatch, gs, ms, provider_spy):
    """G1: the personal-tier candidate query pins scope to endpoint NODES and
    r.user_id to the episode user (cross-user negative: Alice's episode can
    only ever select Alice-owned personal edges for auto-apply)."""
    monkeypatch.setenv("LUMOGIS_FF_TEMPORAL_KG", "true")
    _seed_happy_path(gs, ms)
    _run(monkeypatch, gs, ms)
    personal_q = [
        (c, p) for c, p in gs.queries if "a.scope = 'personal'" in c and "RELATES_TO" in c
    ]
    assert personal_q, "personal-tier candidate query missing"
    cypher, params = personal_q[0]
    assert "r.user_id = $episode_user" in cypher
    assert params["episode_user"] == "u1"
    assert params["vis_me"] == "u1"
    assert "b.scope = 'personal'" in cypher  # both endpoints scope-pinned
    # currently-valid only + targetable only
    assert "(r.invalid_at IS NULL)" in cypher and "(r.expired_at IS NULL)" in cypher
    assert "r.edge_uid IS NOT NULL" in cypher


def test_candidates_without_fact_text_dropped(monkeypatch, gs, ms, provider_spy):
    """G2: bare co-occurrence (no typed relation, no memory) is never judged."""
    monkeypatch.setenv("LUMOGIS_FF_TEMPORAL_KG", "true")
    _seed_happy_path(gs, ms)
    # remove the entity_edges hydration → no fact text anywhere
    ms._all = [(s, r) for s, r in ms._all if "FROM entity_edges" not in s]
    result = _run(monkeypatch, gs, ms)
    assert result.candidates == 0
    assert len(provider_spy["provider"].calls) == 1  # extraction only, no judge


def test_confidence_floor(monkeypatch, gs, ms, provider_spy):
    """G4: supersedes below the floor is ignored entirely."""
    monkeypatch.setenv("LUMOGIS_FF_TEMPORAL_KG", "true")
    monkeypatch.setenv("LUMOGIS_TEMPORAL_AUTO_APPLY", "true")
    _seed_happy_path(gs, ms)
    provider_spy["provider"].replies = [
        EXTRACT_REPLY,
        json.dumps(
            [{"edge_uid": "uid1", "verdict": "supersedes", "confidence": 0.5, "reason": "x"}]
        ),
    ]
    result = _run(monkeypatch, gs, ms)
    assert result.applied == 0 and result.proposed == 0
    assert ms.executed == []


def test_shadow_mode_writes_proposal_only(monkeypatch, gs, ms, provider_spy):
    """G6: auto-apply defaults OFF — verdicts become applied:false proposals;
    nothing is invalidated anywhere."""
    monkeypatch.setenv("LUMOGIS_FF_TEMPORAL_KG", "true")
    monkeypatch.delenv("LUMOGIS_TEMPORAL_AUTO_APPLY", raising=False)
    _seed_happy_path(gs, ms)
    result = _run(monkeypatch, gs, ms)
    assert result.applied == 0 and result.proposed == 1
    assert ms.executed == []  # Postgres untouched
    assert not [c for c, _ in gs.queries if "SET r.invalid_at" in c]
    proposals = [e for e in gs.edges if e["rel_type"] == "SUPERSEDES"]
    assert len(proposals) == 1
    assert proposals[0]["props"]["applied"] is False


def test_per_episode_cap_downgrades_to_proposal(monkeypatch, gs, ms, provider_spy):
    """G5: verdicts beyond the cap downgrade to applied:false proposals."""
    monkeypatch.setenv("LUMOGIS_FF_TEMPORAL_KG", "true")
    monkeypatch.setenv("LUMOGIS_TEMPORAL_AUTO_APPLY", "true")
    monkeypatch.setenv("LUMOGIS_TEMPORAL_MAX_INVALIDATIONS_PER_EPISODE", "1")
    _seed_happy_path(gs, ms)
    ms.seed_all("FROM entity_relations er", [{"source_id": "e1"}, {"source_id": "e2"}])
    # second candidate on the same pair set
    gs._canned = []
    gs.seed(
        "a.scope = 'personal'",
        [
            {"edge_uid": "uid1", "a_id": "e1", "b_id": "e2", "strength": 5, "last_seen": None},
            {"edge_uid": "uid2", "a_id": "e1", "b_id": "e2", "strength": 3, "last_seen": None},
        ],
    )
    gs.seed("WHERE r.edge_uid = $edge_uid", [{"rid": 1}])
    provider_spy["provider"].replies = [
        EXTRACT_REPLY,
        json.dumps(
            [
                {"edge_uid": "uid1", "verdict": "supersedes", "confidence": 0.95, "reason": "a"},
                {"edge_uid": "uid2", "verdict": "supersedes", "confidence": 0.92, "reason": "b"},
            ]
        ),
    ]
    result = _run(monkeypatch, gs, ms)
    assert result.applied == 1
    assert result.proposed == 1  # excess downgraded, not applied
    proposals = [e for e in gs.edges if e["rel_type"] == "SUPERSEDES" and not e["props"]["applied"]]
    assert len(proposals) == 1


def test_shared_scope_recorded_never_applied(monkeypatch, gs, ms, provider_spy):
    """Checkpoint fold 2a + G1: shared-scope contradictions are recorded as
    scope-tagged applied:false proposals even with auto-apply ON."""
    monkeypatch.setenv("LUMOGIS_FF_TEMPORAL_KG", "true")
    monkeypatch.setenv("LUMOGIS_TEMPORAL_AUTO_APPLY", "true")
    _seed_happy_path(gs, ms, tier="shared")
    result = _run(monkeypatch, gs, ms)
    assert result.applied == 0 and result.proposed == 1
    assert ms.executed == []  # shared facts never archived in Postgres
    assert not [c for c, _ in gs.queries if "SET r.invalid_at" in c]
    proposal = [e for e in gs.edges if e["rel_type"] == "SUPERSEDES"][0]
    assert proposal["props"]["applied"] is False
    assert proposal["props"]["scope"] == "shared"


def test_semaphore_caps_concurrent_llm_calls(monkeypatch, gs, ms, provider_spy):
    """G8: every temporal LLM call runs inside the shared semaphore."""
    monkeypatch.setenv("LUMOGIS_FF_TEMPORAL_KG", "true")

    class CountingSem:
        def __init__(self):
            self.held = 0
            self.max_held = 0
            self.acquisitions = 0

        def __enter__(self):
            self.held += 1
            self.acquisitions += 1
            self.max_held = max(self.max_held, self.held)

        def __exit__(self, *a):
            self.held -= 1

    sem = CountingSem()
    monkeypatch.setattr(tp, "_llm_semaphore", sem)
    _seed_happy_path(gs, ms)
    _run(monkeypatch, gs, ms)
    assert sem.acquisitions == 2  # extraction + judge, both guarded
    assert sem.held == 0  # always released


def test_semaphore_size_honours_env(monkeypatch):
    monkeypatch.setattr(tp, "_llm_semaphore", None)
    monkeypatch.setenv("LUMOGIS_TEMPORAL_LLM_CONCURRENCY", "3")
    sem = tp._get_semaphore()
    assert sem._value == 3


# ---------------------------------------------------------------------------
# ADR 151 reference-judge pin (LUM-927)
# ---------------------------------------------------------------------------

NON_REFERENCE_JUDGE = "qwen-judge"


def _pin_judge(monkeypatch, model: str) -> list:
    calls: list = []

    def _resolve(user_id=None):
        calls.append(user_id)
        return model

    monkeypatch.setattr(tp, "_judge_model", _resolve)
    return calls


def test_reference_judge_with_auto_apply_applies(monkeypatch, gs, ms, provider_spy):
    monkeypatch.setenv("LUMOGIS_FF_TEMPORAL_KG", "true")
    monkeypatch.setenv("LUMOGIS_TEMPORAL_AUTO_APPLY", "true")
    calls = _pin_judge(monkeypatch, tp.TEMPORAL_JUDGE_REFERENCE_MODEL)
    _seed_happy_path(gs, ms)
    result = _run(monkeypatch, gs, ms)
    assert result.applied == 1 and result.proposed == 0
    assert ms.executed  # Postgres archived
    assert [c for c, _ in gs.queries if "SET r.invalid_at" in c]
    assert calls == ["u1"]  # resolved once per episode
    # The model that judged is the model the pin checked.
    assert provider_spy["factory_calls"][-1]["model"] == tp.TEMPORAL_JUDGE_REFERENCE_MODEL


def test_non_reference_judge_with_auto_apply_proposes(monkeypatch, gs, ms, provider_spy, caplog):
    monkeypatch.setenv("LUMOGIS_FF_TEMPORAL_KG", "true")
    monkeypatch.setenv("LUMOGIS_TEMPORAL_AUTO_APPLY", "true")
    _pin_judge(monkeypatch, NON_REFERENCE_JUDGE)
    _seed_happy_path(gs, ms)
    with caplog.at_level("WARNING", logger=tp.__name__):
        result = _run(monkeypatch, gs, ms)
    assert result.applied == 0 and result.proposed == 1
    assert ms.executed == []  # Postgres untouched
    assert not [c for c, _ in gs.queries if "SET r.invalid_at" in c]
    proposals = [e for e in gs.edges if e["rel_type"] == "SUPERSEDES"]
    assert len(proposals) == 1 and proposals[0]["props"]["applied"] is False
    assert provider_spy["factory_calls"][-1]["model"] == NON_REFERENCE_JUDGE
    msgs = [r.getMessage() for r in caplog.records]
    assert any(tp.JUDGE_NOT_REFERENCE in m and NON_REFERENCE_JUDGE in m for m in msgs)


def test_other_alias_on_the_reference_tag_counts_as_reference(monkeypatch, gs, ms, provider_spy):
    """The pin compares the resolved Ollama tag, not the alias."""
    monkeypatch.setenv("LUMOGIS_FF_TEMPORAL_KG", "true")
    monkeypatch.setenv("LUMOGIS_TEMPORAL_AUTO_APPLY", "true")
    _pin_judge(monkeypatch, "llama-judge")
    _seed_happy_path(gs, ms)
    result = _run(monkeypatch, gs, ms)
    assert result.applied == 1 and result.proposed == 0


def test_reference_alias_repointed_at_other_weights_proposes(monkeypatch, gs, ms, provider_spy):
    """An operator who points ``llama`` at another model leaves the reference."""
    monkeypatch.setenv("LUMOGIS_FF_TEMPORAL_KG", "true")
    monkeypatch.setenv("LUMOGIS_TEMPORAL_AUTO_APPLY", "true")
    config._models_config["llama"]["model"] = "qwen2.5:7b"
    _pin_judge(monkeypatch, tp.TEMPORAL_JUDGE_REFERENCE_MODEL)
    _seed_happy_path(gs, ms)
    result = _run(monkeypatch, gs, ms)
    assert result.applied == 0 and result.proposed == 1
    assert ms.executed == []


def test_is_reference_judge_fails_closed():
    assert tp.is_reference_judge(tp.TEMPORAL_JUDGE_REFERENCE_MODEL) is True
    assert tp.is_reference_judge("qwen-judge") is False
    assert tp.is_reference_judge("not-configured") is False  # unknown alias
    assert tp.is_reference_judge(_REF_TAG) is False  # a raw tag is not an alias
    assert tp.is_reference_judge(None) is False
    assert tp.is_reference_judge("") is False


@pytest.mark.parametrize("judge", ["reference", NON_REFERENCE_JUDGE])
def test_auto_apply_off_proposes_whatever_the_judge(
    monkeypatch, gs, ms, provider_spy, caplog, judge
):
    monkeypatch.setenv("LUMOGIS_FF_TEMPORAL_KG", "true")
    monkeypatch.delenv("LUMOGIS_TEMPORAL_AUTO_APPLY", raising=False)
    model = tp.TEMPORAL_JUDGE_REFERENCE_MODEL if judge == "reference" else judge
    _pin_judge(monkeypatch, model)
    _seed_happy_path(gs, ms)
    with caplog.at_level("WARNING", logger=tp.__name__):
        result = _run(monkeypatch, gs, ms)
    assert result.applied == 0 and result.proposed == 1
    assert ms.executed == []
    # Shadow mode is the operator's choice, not the pin: no pin warning.
    assert not any(tp.JUDGE_NOT_REFERENCE in r.getMessage() for r in caplog.records)


def test_judge_model_resolution_failure_is_judge_failed(monkeypatch, gs, ms, provider_spy):
    monkeypatch.setenv("LUMOGIS_FF_TEMPORAL_KG", "true")
    monkeypatch.setenv("LUMOGIS_TEMPORAL_AUTO_APPLY", "true")

    def _boom(user_id=None):
        raise RuntimeError("tasks table unreadable")

    monkeypatch.setattr(tp, "_judge_model", _boom)
    _seed_happy_path(gs, ms)
    result = _run(monkeypatch, gs, ms)
    assert result.status == "judge_failed"
    assert result.applied == 0 and result.proposed == 0
    assert ms.executed == []
    assert len(provider_spy["provider"].calls) == 1  # extraction only


# ---------------------------------------------------------------------------
# Postgres-first ordering invariant
# ---------------------------------------------------------------------------


def test_postgres_valid_until_set_before_graph_write(monkeypatch, gs, ms, provider_spy):
    """Call-order spy: the entity_edges UPDATE lands before the graph SET."""
    monkeypatch.setenv("LUMOGIS_FF_TEMPORAL_KG", "true")
    monkeypatch.setenv("LUMOGIS_TEMPORAL_AUTO_APPLY", "true")
    _seed_happy_path(gs, ms)
    order: list[str] = []
    orig_execute = ms.execute
    orig_query = gs.query

    def spy_execute(sql, params=None):
        if "UPDATE entity_edges" in sql:
            order.append("postgres")
        return orig_execute(sql, params)

    def spy_query(cypher, params=None):
        if "SET r.invalid_at" in cypher:
            order.append("graph")
        return orig_query(cypher, params)

    monkeypatch.setattr(ms, "execute", spy_execute)
    monkeypatch.setattr(gs, "query", spy_query)
    result = _run(monkeypatch, gs, ms)
    assert result.applied == 1
    assert order == ["postgres", "graph"]  # invariant: Postgres FIRST
    sql, params = ms.executed[0]
    assert "relation_type = ANY(%s)" in sql  # row-scoped, never pair-wide
    assert "valid_until IS NULL" in sql  # idempotent guard


def test_graph_failure_after_postgres_leaves_evidence_unstamped(monkeypatch, gs, ms, provider_spy):
    """Exception injection: graph write raises AFTER Postgres succeeded →
    recall is already correct (reads Postgres); the evidence node stays
    unstamped so the sweep re-drives the episode (retry idempotent via the
    valid_until IS NULL guard)."""
    monkeypatch.setenv("LUMOGIS_FF_TEMPORAL_KG", "true")
    monkeypatch.setenv("LUMOGIS_TEMPORAL_AUTO_APPLY", "true")
    _seed_happy_path(gs, ms)
    gs.raise_on.append("SET r.invalid_at")
    result = _run(monkeypatch, gs, ms)
    assert result is None  # deliberate: no result, no stamp
    assert ms.executed, "Postgres archive must have happened first"
    assert _stamp_status(gs) is None  # evidence node NOT stamped → sweep retry


def test_invalidation_sync_archives_only_hydrated_relation_types(monkeypatch, gs, ms, provider_spy):
    """Two typed relations on one pair; only the hydrated/judged one is
    archived — the pair-wide UPDATE bug is pinned out."""
    monkeypatch.setenv("LUMOGIS_FF_TEMPORAL_KG", "true")
    monkeypatch.setenv("LUMOGIS_TEMPORAL_AUTO_APPLY", "true")
    _seed_happy_path(gs, ms)
    result = _run(monkeypatch, gs, ms)
    assert result.applied == 1
    sql, params = ms.executed[0]
    assert "relation_type = ANY(%s)" in sql
    rels = params[-1]
    assert rels == ["WORKS_WITH"]  # exactly the hydrated tuple set


# ---------------------------------------------------------------------------
# Enum/flag parity with the KG side (split-brain pins)
# ---------------------------------------------------------------------------


def test_status_domain_identical_to_kg_enum():
    literal = tp.TemporalCheckResult.model_fields["status"].annotation
    assert tuple(get_args(literal)) == temporal.TEMPORAL_CHECK_STATUSES


# Shared truthy fixture — mirrors services/lumogis-graph tests so the two
# parsers cannot drift.
FLAG_PARSE_CASES = [
    ("on", True),
    ("1", True),
    ("TRUE", True),
    ("true", True),
    ("yes", True),
    (" on ", True),
    ("off", False),
    ("0", False),
    ("", False),
    ("false", False),
    ("banana", False),
]


@pytest.mark.parametrize("raw,expected", FLAG_PARSE_CASES)
def test_ff_parse_identical_core_and_kg_side(monkeypatch, raw, expected):
    monkeypatch.setenv("LUMOGIS_FF_TEMPORAL_KG", raw)
    assert features.is_enabled("TEMPORAL_KG") is expected
    assert temporal.temporal_feature_enabled() is expected  # KG-side helper


def test_temporal_kg_flag_registered_and_default_off(monkeypatch):
    monkeypatch.delenv("LUMOGIS_FF_TEMPORAL_KG", raising=False)
    assert "TEMPORAL_KG" in features.all_flag_keys()
    assert features.is_enabled("TEMPORAL_KG") is False


# ---------------------------------------------------------------------------
# Stamp-pass targeting + sweep ordering
# ---------------------------------------------------------------------------


def test_relates_to_stamp_pass_derives_pairs_from_entity_relations(
    monkeypatch, gs, ms, provider_spy
):
    """RELATES_TO targeting goes via the Postgres entity set (those edges
    carry no evidence_id property) — pairs are canonicalised lower→higher."""
    monkeypatch.setenv("LUMOGIS_FF_TEMPORAL_KG", "true")
    _seed_happy_path(gs, ms)
    _run(monkeypatch, gs, ms)
    pair_stamps = [
        (c, p)
        for c, p in gs.queries
        if "MATCH (a)-[r:RELATES_TO]->(b)" in c and "a_id" in p and "RETURN id(r)" in c
    ]
    assert pair_stamps, "expected a RELATES_TO pair stamp read"
    _, params = pair_stamps[0]
    assert params["a_id"] == "e1" and params["b_id"] == "e2"


def test_sweep_orders_candidates_newest_first(monkeypatch, gs, ms):
    monkeypatch.setenv("LUMOGIS_FF_TEMPORAL_KG", "true")
    _wire_stores(monkeypatch, gs, ms)
    gs.seed(
        "MATCH (n:Note)",
        [
            {"eid": "old-note", "uid": "u1", "ts": "2026-01-01T00:00:00Z"},
            {"eid": "new-note", "uid": "u1", "ts": "2026-07-01T00:00:00Z"},
        ],
    )
    gs.seed(
        "MATCH (n:Session)",
        [{"eid": "mid-session", "uid": "u1", "ts": "2026-04-01T00:00:00Z"}],
    )
    processed: list[str] = []
    monkeypatch.setattr(
        tp,
        "on_episode_temporal_check",
        lambda **kw: processed.append(kw["evidence_id"]),
    )
    n = tp.run_temporal_sweep(batch=20)
    assert n == 3
    assert processed == ["new-note", "mid-session", "old-note"]  # newest first


def test_sweep_noop_when_flag_off(monkeypatch, gs, ms):
    monkeypatch.delenv("LUMOGIS_FF_TEMPORAL_KG", raising=False)
    _wire_stores(monkeypatch, gs, ms)
    assert tp.run_temporal_sweep() == 0
    assert gs.queries == []


# ---------------------------------------------------------------------------
# Registration path (plugin wiring) — reload-based, mirrors the mode-guard
# test pattern
# ---------------------------------------------------------------------------


class RecordingScheduler:
    def __init__(self):
        self.jobs: list[dict] = []

    def add_job(self, func, trigger=None, *, id=None, replace_existing=True, **kw):
        self.jobs.append({"id": id, "func": func, "trigger": trigger, **kw})


@pytest.fixture
def reloaded_plugin(monkeypatch):
    import importlib

    import hooks
    import plugins.graph as plugin_pkg

    snapshot_listeners = {k: list(v) for k, v in hooks._listeners.items()}

    def _reload():
        config.clear_graph_mode_env_cache()
        return importlib.reload(plugin_pkg)

    yield _reload

    hooks._listeners.clear()
    hooks._listeners.update({k: list(v) for k, v in snapshot_listeners.items()})
    config.clear_graph_mode_env_cache()


def test_temporal_hooks_and_sweep_registered_when_inprocess_and_flag_on(
    reloaded_plugin, monkeypatch
):
    import hooks
    from events import Event

    scheduler = RecordingScheduler()
    monkeypatch.setattr(config, "get_scheduler", lambda: scheduler)
    monkeypatch.setenv("GRAPH_MODE", "inprocess")
    monkeypatch.setenv("LUMOGIS_FF_TEMPORAL_KG", "true")
    reloaded_plugin()
    note_handlers = [cb.__name__ for cb in hooks._listeners.get(Event.NOTE_CAPTURED, [])]
    assert "_on_note_captured" in note_handlers  # temporal deferral registered
    doc_handlers = [cb.__name__ for cb in hooks._listeners.get(Event.DOCUMENT_INGESTED, [])]
    assert "_on_document_ingested" in doc_handlers
    sweep_jobs = [j for j in scheduler.jobs if j["id"] == "graph_temporal_sweep"]
    assert len(sweep_jobs) == 1
    assert sweep_jobs[0]["trigger"] == "cron"


def test_temporal_pipeline_not_registered_when_flag_off(reloaded_plugin, monkeypatch):
    import hooks
    from events import Event

    scheduler = RecordingScheduler()
    monkeypatch.setattr(config, "get_scheduler", lambda: scheduler)
    monkeypatch.setenv("GRAPH_MODE", "inprocess")
    monkeypatch.delenv("LUMOGIS_FF_TEMPORAL_KG", raising=False)
    reloaded_plugin()
    note_handlers = [cb.__name__ for cb in hooks._listeners.get(Event.NOTE_CAPTURED, [])]
    assert "_on_note_captured" not in note_handlers
    assert not [j for j in scheduler.jobs if j["id"] == "graph_temporal_sweep"]


def test_temporal_pipeline_not_registered_when_graph_mode_disabled(reloaded_plugin, monkeypatch):
    import hooks
    from events import Event

    scheduler = RecordingScheduler()
    monkeypatch.setattr(config, "get_scheduler", lambda: scheduler)
    monkeypatch.setenv("GRAPH_MODE", "disabled")
    monkeypatch.setenv("LUMOGIS_FF_TEMPORAL_KG", "true")
    reloaded_plugin()
    note_handlers = [cb.__name__ for cb in hooks._listeners.get(Event.NOTE_CAPTURED, [])]
    assert "_on_note_captured" not in note_handlers
    assert scheduler.jobs == []


def test_pair_stamp_fan_out_is_capped(monkeypatch, gs, ms, provider_spy):
    """verify-plan fix: C(n,2) pair generation is capped at the config PAIR
    cap so an entity-dense episode cannot explode into thousands of
    sequential graph round-trips."""
    monkeypatch.setenv("LUMOGIS_FF_TEMPORAL_KG", "true")
    monkeypatch.setattr(config, "get_graph_max_cooccurrence_pairs", lambda: 5)
    _seed_happy_path(gs, ms)
    ms.seed_all("FROM entity_relations er", [{"source_id": f"e{i:02d}"} for i in range(10)])
    _run(monkeypatch, gs, ms)
    pair_reads = [
        c for c, p in gs.queries if "MATCH (a)-[r:RELATES_TO]->(b)" in c and "RETURN id(r)" in c
    ]
    assert 0 < len(pair_reads) <= 5  # capped, not C(10,2)=45


def test_inflight_cap_defers_to_sweep_unstamped(monkeypatch, gs, ms, provider_spy):
    """verify-plan fix: episode checks beyond the LLM-concurrency cap are
    skipped UNSTAMPED (sweep re-drives) so temporal work cannot occupy every
    hooks-executor worker during an ingest burst."""
    monkeypatch.setenv("LUMOGIS_FF_TEMPORAL_KG", "true")
    monkeypatch.setenv("LUMOGIS_TEMPORAL_LLM_CONCURRENCY", "1")
    _seed_happy_path(gs, ms)
    with tp._inflight_lock:
        tp._inflight.add("other-episode")  # one slot occupied
    try:
        result = _run(monkeypatch, gs, ms)
    finally:
        with tp._inflight_lock:
            tp._inflight.discard("other-episode")
    assert result is None  # deferred
    assert _stamp_status(gs) is None  # unstamped → sweep picks it up
