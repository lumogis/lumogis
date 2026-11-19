# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Call-site tests for task-based model routing (LUM-877).

The default-config tests (T-C1) pin the hard promise: with no ``tasks:`` entries,
every call site asks ``config.get_llm_provider`` for exactly the model it asked for
before, sized with the same budget, through the same privacy path. They use the
REAL ``privacy_mode.resolve_job_model`` (policy patched, no DB) and a recording
``get_llm_provider``. The rest pin routing, budgets and the temporal rules.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import re
from datetime import datetime
from datetime import timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from tests._task_models_support import BIG
from tests._task_models_support import CLOUD
from tests._task_models_support import LLAMA
from tests._task_models_support import grant_key
from tests._task_models_support import install
from tests._task_models_support import set_policy

import config
from services import context_budget
from services import entities
from services import mcp_write
from services import memory
from services import outbound_http_url
from services import privacy_mode
from services import routines
from services import signal_processor as sp
from services import task_models

_REPO = Path(__file__).resolve().parents[2]


def _tp():
    import plugins.graph  # noqa: F401  (puts services/lumogis-graph on sys.path)
    from plugins.graph import temporal_pipeline as tp

    return tp


class _Provider:
    def __init__(self, text: str):
        self.text = text

    def chat(self, messages, system=None, max_tokens=None, **kw):
        return SimpleNamespace(text=self.text)


@pytest.fixture
def llm(monkeypatch):
    """Record every model name asked of ``config.get_llm_provider``."""
    rec = SimpleNamespace(models=[], text="[]")

    def fake(name, *, user_id=None):
        rec.models.append(name)
        return _Provider(rec.text)

    monkeypatch.setattr(config, "get_llm_provider", fake)
    return rec


@pytest.fixture
def budgets(monkeypatch):
    """Pass-through spies: aliases given to get_budget, max_tokens given to truncation."""
    rec = SimpleNamespace(get=[], trunc=[])
    real_get = context_budget.get_budget
    real_text = context_budget.truncate_text
    real_msgs = context_budget.truncate_messages

    def get_budget(alias):
        rec.get.append(alias)
        return real_get(alias)

    def truncate_text(text, max_tokens):
        rec.trunc.append(max_tokens)
        return real_text(text, max_tokens)

    def truncate_messages(messages, max_tokens):
        rec.trunc.append(max_tokens)
        return real_msgs(messages, max_tokens)

    for mod, attr, fn in (
        (context_budget, "get_budget", get_budget),
        (context_budget, "truncate_text", truncate_text),
        (context_budget, "truncate_messages", truncate_messages),
        (sp, "get_budget", get_budget),
        (sp, "truncate_text", truncate_text),
        (memory, "truncate_text", truncate_text),
        (memory, "truncate_messages", truncate_messages),
    ):
        monkeypatch.setattr(mod, attr, fn)
    return rec


def _signal(**kw):
    from models.signals import Signal

    base = dict(
        signal_id="sig1",
        source_id="src-abc",
        title="Insurance renewal due March 15",
        url="imap://src-abc/100/5",
        published_at=None,
        content_summary="",
        raw_content="body text",
        entities=[],
        topics=[],
        importance_score=0.0,
        relevance_score=0.0,
        notified=False,
        created_at=datetime.now(timezone.utc),
        user_id="u1",
        source_type="",
        category="",
        headers={},
    )
    base.update(kw)
    return Signal(**base)


@pytest.fixture
def pipeline(monkeypatch):
    monkeypatch.setattr(sp, "_persist", lambda s: None)
    monkeypatch.setattr(sp, "_load_profile", lambda uid: None)
    monkeypatch.setattr(sp, "_notify", lambda s: False)
    monkeypatch.setattr(sp.hooks, "fire_background", lambda *a, **k: None)
    sp._score_cache.clear()


def _two_entities():
    from models.entities import ExtractedEntity

    return [
        ExtractedEntity(name="Ann", entity_type="PERSON"),
        ExtractedEntity(name="Bob", entity_type="PERSON"),
    ]


def _run_weekly(monkeypatch, tmp_path, user_id="u1"):
    monkeypatch.setattr(routines, "_OUTBOX", tmp_path)
    store = SimpleNamespace(fetch_all=lambda *a, **k: [], fetch_one=lambda *a, **k: None)
    monkeypatch.setattr(config, "get_metadata_store", lambda: store)
    monkeypatch.setattr(config, "get_vector_store", lambda: SimpleNamespace())
    out = routines._run_weekly_review(user_id=user_id)
    return json.loads(Path(out).read_text())


# ---------------------------------------------------------------------------
# T-C1: with an empty tasks table every call site asks for today's model
# ---------------------------------------------------------------------------


def test_default_config_call_sites_request_todays_model(
    monkeypatch, tmp_path, llm, budgets, pipeline
):
    install(monkeypatch, tasks={})
    set_policy(monkeypatch, "allow_cloud")
    monkeypatch.delenv("LUMOGIS_RC_SESSION_END_STUB", raising=False)

    def last() -> str:
        return llm.models[-1]

    def reset():
        llm.models.clear()
        budgets.get.clear()
        budgets.trunc.clear()

    llm.text = "[]"
    entities.extract_entities("Ann met Bob in Paris.", user_id="u1")
    assert llm.models == ["llama"]

    reset()
    mcp_write.extract_relations("Ann met Bob.", _two_entities(), user_id="u1")
    assert llm.models == ["llama"]

    reset()
    llm.text = '{"summary": "s", "topics": [], "entities": []}'
    memory.summarize_session([{"role": "user", "content": "hello"}], "s1", user_id="u1")
    assert llm.models == ["llama"]
    assert budgets.get == ["llama"]
    assert 8192 - 500 in budgets.trunc and 8192 - 300 in budgets.trunc

    reset()
    _run_weekly(monkeypatch, tmp_path)
    assert llm.models == ["llama"]
    assert budgets.get[-1] == "llama"
    assert budgets.trunc == [8192 - 600]

    reset()
    llm.text = '{"importance_score": 0.5, "content_summary": "x"}'
    sp.process_signal(_signal(), user_id="u1")
    assert llm.models == ["llama"]
    assert budgets.get == ["llama"] and budgets.trunc == [8192 - 500]

    reset()
    sp.score_importance(_signal(url="https://example.com/a"))
    assert llm.models == ["llama"]
    assert budgets.get == ["llama"] and budgets.trunc == [8192 - 500]

    reset()
    monkeypatch.setattr(task_models, "is_on_box_local", lambda name, **kw: True)
    sp.process_signal(
        _signal(
            source_type="imap",
            headers={"From": "a@b.com", "Subject": "Insurance renewal due"},
        ),
        user_id="u1",
    )
    assert llm.models == ["llama"]
    assert budgets.get == ["llama"] and budgets.trunc == [8192 - 500]

    tp = _tp()
    reset()
    llm.text = '{"valid_at": null}'
    tp.extract_valid_at("text", datetime.now(timezone.utc), "u1")
    assert llm.models == ["llama"]
    assert budgets.get == ["llama"]

    # LUM-927: the judge default is the static `llama` entry, the ADR 151 reference.
    reset()
    llm.text = "[]"
    cand = [{"edge_uid": "e1", "fact_texts": ["x"]}]
    assert tp.judge_contradictions("text", cand, "u1", datetime.now(timezone.utc)) == []
    assert llm.models == ["llama"]
    assert budgets.get == ["llama"]


# ---------------------------------------------------------------------------
# Budgets follow the resolved model
# ---------------------------------------------------------------------------


def test_budget_uses_resolved_model_memory(monkeypatch, llm, budgets):
    install(monkeypatch, models={"big": dict(BIG)}, tasks={"session_summary": ["big"]})
    llm.text = '{"summary": "s"}'
    memory.summarize_session([{"role": "user", "content": "hi"}], "s1", user_id="u1")
    assert llm.models == ["big"]
    assert 32768 - 500 in budgets.trunc and 32768 - 300 in budgets.trunc
    assert 8192 - 500 not in budgets.trunc


def test_budget_uses_resolved_model_routines(monkeypatch, tmp_path, llm, budgets):
    install(monkeypatch, models={"big": dict(BIG)}, tasks={"routine_weekly_review": ["big"]})
    monkeypatch.setattr(task_models, "is_on_box_local", lambda name, **kw: True)
    _run_weekly(monkeypatch, tmp_path)
    assert llm.models == ["big"]
    assert budgets.trunc == [32768 - 600]


def test_signal_budget_and_call_use_same_model(monkeypatch, budgets, pipeline):
    install(monkeypatch, models={"big": dict(BIG)}, tasks={"signal_enrichment": ["big"]})
    seen = {}

    def fake(title, content, *, user_id=None, model_override=None, categories=None):
        seen["override"] = model_override
        return {}

    monkeypatch.setattr(sp, "_call_llm", fake)
    sp.process_signal(_signal(), user_id="u1")
    assert budgets.get == ["big"] and seen["override"] == "big"
    assert budgets.trunc == [32768 - 500]


def test_dynamic_alias_gets_the_fallback_budget(monkeypatch, llm, budgets, caplog):
    dyn = {
        "qwen": {
            "adapter": "openai",
            "model": "qwen:7b",
            "base_url": "http://ollama:11434/v1",
            "tools": False,
            "dynamic_ollama": True,
        }
    }
    install(monkeypatch, tasks={"session_summary": ["qwen"]}, dynamic=dyn)
    llm.text = '{"summary": "s"}'
    with caplog.at_level(logging.WARNING):
        memory.summarize_session([{"role": "user", "content": "hi"}], "s1", user_id="u1")
    assert llm.models == ["qwen"]
    assert 2048 - 500 in budgets.trunc
    assert any("No context_budget" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


def test_each_email_task_uses_its_own_list(monkeypatch):
    from services import email_classifier

    install(
        monkeypatch,
        models={"a": dict(LLAMA), "b": dict(LLAMA), "c": dict(LLAMA)},
        tasks={"email_classify": ["a"], "email_reply_draft": ["b"], "email_compose_refine": ["c"]},
    )
    got = [
        email_classifier.require_local_model("u1", task=t)
        for t in ("email_classify", "email_reply_draft", "email_compose_refine")
    ]
    assert got == ["a", "b", "c"]


def test_require_local_model_refuses_non_email_task(monkeypatch, caplog):
    from services import email_classifier

    install(monkeypatch)
    with caplog.at_level(logging.ERROR, logger="services.task_models"):
        assert email_classifier.require_local_model("u1", task="session_summary") is None
    assert len([r for r in caplog.records if r.levelno == logging.ERROR]) == 1
    with pytest.raises(ValueError):
        email_classifier.require_local_model("u1", task="nope")


def test_signal_enrichment_cloud_entry_without_user_falls_to_next(monkeypatch):
    install(
        monkeypatch,
        models={"claude": dict(CLOUD)},
        tasks={"signal_enrichment": ["claude", "llama"]},
    )
    grant_key(monkeypatch, present=True)
    set_policy(monkeypatch, "allow_cloud")
    assert task_models.resolve_task_model("signal_enrichment", user_id=None) == "llama"
    assert task_models.resolve_task_model("signal_enrichment", user_id="u1") == "claude"


def test_per_user_local_only_restriction_skips_cloud_entry(monkeypatch):
    install(
        monkeypatch,
        models={"claude": dict(CLOUD)},
        tasks={"signal_enrichment": ["claude", "llama"]},
    )
    grant_key(monkeypatch, present=True)
    monkeypatch.setattr(
        privacy_mode,
        "_load_user_restriction",
        lambda uid: privacy_mode.PrivacyUserRestriction.LOCAL_ONLY,
    )
    monkeypatch.setattr(
        privacy_mode,
        "_load_instance_mode",
        lambda store: privacy_mode.InstancePrivacyMode.ALLOW_CLOUD,
    )
    monkeypatch.setattr(privacy_mode, "_load_instance_locked", lambda store: False)
    monkeypatch.setattr(config, "get_metadata_store", lambda: SimpleNamespace())
    assert task_models.resolve_task_model("signal_enrichment", user_id="u1") == "llama"
    exp = task_models.explain_task("signal_enrichment", user_id="u1")
    assert [e.skip_reason for e in exp.entries] == ["privacy_mode", None]


def test_weekly_review_lists_on_box_only(monkeypatch, tmp_path, llm):
    install(
        monkeypatch, models={"claude": dict(CLOUD)}, tasks={"routine_weekly_review": ["claude"]}
    )
    grant_key(monkeypatch, present=True)
    set_policy(monkeypatch, "allow_cloud")
    exp = task_models.explain_task("routine_weekly_review", user_id="u1")
    assert exp.entries[0].skip_reason == "not_on_box"
    _run_weekly(monkeypatch, tmp_path)
    assert llm.models == ["llama"]


def test_entity_extraction_lists_on_box_only(monkeypatch, llm):
    lan = {"adapter": "openai", "model": "q", "base_url": "http://gpu.lan:11434/ollama/v1"}
    install(
        monkeypatch,
        models={"claude": dict(CLOUD), "lan-qwen": lan},
        tasks={"entity_extraction": ["claude", "lan-qwen"]},
    )
    grant_key(monkeypatch, present=True)
    set_policy(monkeypatch, "allow_cloud")
    monkeypatch.setattr(outbound_http_url, "_resolved_ips", lambda h: ["192.168.1.9"])
    entities.extract_entities("Ann met Bob.", user_id="u1")
    assert llm.models == ["lan-qwen"]
    llm.models.clear()
    install(monkeypatch, models={"claude": dict(CLOUD)}, tasks={"entity_extraction": ["claude"]})
    entities.extract_entities("Ann met Bob.", user_id="u1")
    assert llm.models == ["llama"]


def test_off_box_default_keeps_todays_behaviour(monkeypatch, tmp_path, llm, caplog):
    from services import email_classifier

    cgnat = dict(LLAMA, base_url="http://ollama.ts.example:11434/v1")
    install(monkeypatch, models={"llama": cgnat}, tasks={})
    monkeypatch.setattr(outbound_http_url, "_resolved_ips", lambda h: ["100.64.0.1"])
    set_policy(monkeypatch, "allow_cloud")
    with caplog.at_level(logging.WARNING):
        entities.extract_entities("Ann met Bob.", user_id="u1")
        _run_weekly(monkeypatch, tmp_path)
    assert llm.models == ["llama", "llama"]
    assert not any("no listed model usable" in r.getMessage() for r in caplog.records)
    # The email boundary still refuses the same default (today's rule).
    assert email_classifier.require_local_model("u1", task="email_classify") is None


def test_weekly_review_privacy_step_exception_is_swallowed(monkeypatch, tmp_path, caplog):
    install(monkeypatch, tasks={})

    def boom(model, uid):
        raise RuntimeError("settings read failed")

    monkeypatch.setattr(privacy_mode, "resolve_job_model", boom)
    with caplog.at_level(logging.WARNING):
        review = _run_weekly(monkeypatch, tmp_path)
    assert "prose_summary" not in review
    assert any(
        "weekly_review: LLM prose generation failed" in r.getMessage() for r in caplog.records
    )


# ---------------------------------------------------------------------------
# Temporal pipeline
# ---------------------------------------------------------------------------

_CAND = [{"edge_uid": "e1", "fact_texts": ["x"]}]


def _now():
    return datetime.now(timezone.utc)


def _standard_install(monkeypatch, *, ollama_tags, ollama_down=False):
    """The shipped config/models.yaml, no tasks, and the REAL dynamic-alias logic
    over an Ollama holding ``ollama_tags``."""
    import ollama_client

    monkeypatch.setenv("MODELS_CONFIG", str(_REPO / "config/models.yaml"))
    monkeypatch.setattr(config, "_models_config", None)
    monkeypatch.setattr(config, "_tasks_config", None)
    monkeypatch.delenv("EMBEDDING_MODEL", raising=False)

    def listing(*a, **k):
        if ollama_down:
            raise ConnectionError("ollama unreachable")
        return [{"name": t} for t in ollama_tags]

    monkeypatch.setattr(ollama_client, "list_local_models", listing)


def _default_pull() -> list[str]:
    """OLLAMA_EXTRA_MODELS as shipped in .env.example and the entrypoint fallback."""
    env = (_REPO / ".env.example").read_text()
    shipped = re.search(r"^OLLAMA_EXTRA_MODELS=(.*)$", env, re.M).group(1)
    entry = (_REPO / "orchestrator/docker-entrypoint.sh").read_text()
    fallback = re.search(r'OLLAMA_EXTRA_MODELS="\$\{OLLAMA_EXTRA_MODELS-([^}]*)\}"', entry)
    assert fallback, "entrypoint OLLAMA_EXTRA_MODELS fallback not found"
    assert shipped.strip() == fallback.group(1).strip(), "the two defaults drifted"
    return [t.strip() for t in shipped.replace(",", " ").split() if t.strip()]


@pytest.mark.parametrize("ollama_down", [False, True])
def test_temporal_judge_default_resolves_on_a_standard_install(monkeypatch, ollama_down):
    """LUM-927: the judge default resolves through the real config and alias logic.

    The old default, the Ollama tag 'llama3.1:8b', never did: dynamic aliases never
    contain ':' and a standard install never pulled it, so every judgment ended
    judge_failed.
    """
    tp = _tp()
    _standard_install(
        monkeypatch,
        ollama_tags=["nomic-embed-text:latest", *_default_pull()],
        ollama_down=ollama_down,
    )
    judge = tp._judge_model(None)
    assert judge == task_models.TASKS["temporal_judge"].default
    assert ":" not in judge, "an Ollama tag is never a model alias"
    cfg = config.get_model_config(judge)  # raises UnknownModelError if it does not resolve
    # It names a model a standard install actually pulls, and that is the reference.
    assert cfg["model"] == tp.TEMPORAL_JUDGE_REFERENCE_TAG
    assert tp.TEMPORAL_JUDGE_REFERENCE_TAG in _default_pull()
    assert tp.is_reference_judge(judge) is True
    # The static entry hides the same tag from the dynamic listing: no second alias.
    assert all(c["model"] != cfg["model"] for c in config._dynamic_ollama_models().values())


def test_old_judge_default_tag_never_resolves(monkeypatch):
    """Regression pin for the bug LUM-927 fixed: a tag is not an alias."""
    _standard_install(monkeypatch, ollama_tags=["llama3.1:8b", "llama3.2:3b"])
    assert "llama3.1" in config._dynamic_ollama_models()
    with pytest.raises(config.UnknownModelError):
        config.get_model_config("llama3.1:8b")


def test_temporal_judge_cloud_entry_is_replaced_and_explicit_cloud_is_still_blocked(monkeypatch):
    """LUM-983: the resolver never returns a cloud judge; get_llm_provider still blocks one."""
    tp = _tp()
    install(monkeypatch, models={"claude": dict(CLOUD)}, tasks={"temporal_judge": ["claude"]})
    set_policy(monkeypatch, "local_only")
    blocks = []
    monkeypatch.setattr(privacy_mode, "record_privacy_block", lambda **kw: blocks.append(kw))
    assert task_models.resolve_task_model("temporal_judge", user_id="u1") == "llama"
    # Defence in depth: an explicit cloud model is still hard-blocked and audited.
    assert tp.judge_contradictions("t", _CAND, "u1", _now(), model="claude") is None
    assert len(blocks) == 1 and blocks[0]["requested_model"] == "claude"


def test_temporal_judge_cloud_entry_never_reaches_the_cloud_under_allow_cloud(monkeypatch):
    """The leak from LUM-983: a keyed cloud judge listed first received episode text."""
    tp = _tp()
    install(monkeypatch, models={"claude": dict(CLOUD)}, tasks={"temporal_judge": ["claude"]})
    grant_key(monkeypatch, present=True)
    set_policy(monkeypatch, "allow_cloud")
    asked: list[str] = []
    monkeypatch.setattr(
        config,
        "get_llm_provider",
        lambda name, *, user_id=None: asked.append(name) or _Provider("[]"),
    )
    assert tp.judge_contradictions("t", _CAND, "u1", _now()) == []
    assert asked == ["llama"]


def test_temporal_pipeline_never_calls_resolve_job_model(monkeypatch, llm):
    tp = _tp()
    install(monkeypatch, tasks={"temporal_judge": ["llama"]})

    def boom(*a, **k):
        raise AssertionError("temporal must not call the privacy swap")

    monkeypatch.setattr(privacy_mode, "resolve_job_model", boom)
    monkeypatch.setattr(privacy_mode, "resolve_local_fallback_model", boom)
    set_policy(monkeypatch, "local_only")
    llm.text = '{"valid_at": null}'
    assert tp.extract_valid_at("t", _now(), "u1")[1] == "ok"
    llm.text = "[]"
    assert tp.judge_contradictions("t", _CAND, "u1", _now()) == []
    assert llm.models == ["llama", "llama"]


def test_temporal_judge_default_under_default_privacy_reaches_the_local_judge(monkeypatch):
    """LUM-927: the default judge is the local `llama` entry, so the shipped
    `local_only` privacy mode lets it run (it used to block the unresolvable tag)."""
    tp = _tp()
    install(monkeypatch, tasks={})
    set_policy(monkeypatch, "local_only")  # the shipped default
    blocks = []
    monkeypatch.setattr(privacy_mode, "record_privacy_block", lambda **kw: blocks.append(kw))
    monkeypatch.setattr(config, "_instances", {})
    # The REAL get_llm_provider builds the local adapter for the default.
    built = []

    def fake_adapter(**kw):
        built.append(kw)
        return _Provider("[]")

    monkeypatch.setattr("adapters.openai_llm.OpenAILLM", fake_adapter)
    # Breaker and egress wrappers keep process-wide state other tests leave behind;
    # this test is about the privacy path and the adapter, so pass them through.
    monkeypatch.setattr("services.circuit_breaker.wrap_llm_provider", lambda a, key: a)
    monkeypatch.setattr("services.egress_guard.wrap_llm_provider_egress", lambda a, **kw: a)
    assert tp.judge_contradictions("t", _CAND, "u1", _now()) == []
    assert [b["model"] for b in built] == ["llama3.2:3b"]
    assert blocks == []
    # A listed local judge IS called with its name.
    install(monkeypatch, tasks={"temporal_judge": ["llama"]})
    seen = []
    monkeypatch.setattr(
        config, "get_llm_provider", lambda n, *, user_id=None: seen.append(n) or _Provider("[]")
    )
    assert tp.judge_contradictions("t", _CAND, "u1", _now()) == []
    assert seen == ["llama"]


def test_temporal_judge_never_falls_through_its_list(monkeypatch):
    tp = _tp()
    install(
        monkeypatch,
        models={"big": dict(BIG)},
        tasks={"temporal_judge": ["qwen-judge", "big"]},
        real_dynamic=True,
    )
    import ollama_client

    monkeypatch.setattr(ollama_client, "list_local_models", lambda *a, **k: [])
    set_policy(monkeypatch, "allow_cloud")
    asked: list[str] = []
    monkeypatch.setattr(
        config,
        "get_llm_provider",
        lambda name, *, user_id=None: asked.append(name) or _Provider("[]"),
    )
    # An unknown first entry gives the code default, never the second entry (LUM-983).
    assert tp._judge_model("u1") == "llama"
    assert tp.judge_contradictions("t", _CAND, "u1", _now()) == []
    assert asked == ["llama"]


@pytest.mark.parametrize(
    "auto_apply,tasks,ff,expect_not_ref,expect_unresolvable",
    [
        # LUM-927: the default and `llama` are the reference; no warning.
        ("true", {}, "true", False, False),
        ("true", {"temporal_judge": ["llama"]}, "true", False, False),
        # The pin compares Ollama tags: another alias on the same tag is the reference.
        ("true", {"temporal_judge": ["llama-judge"]}, "true", False, False),
        ("true", {"temporal_judge": ["big"]}, "true", True, False),
        (None, {"temporal_judge": ["big"]}, "true", False, False),
        ("true", {"temporal_judge": ["big"]}, None, False, False),
        # A raw tag is not an alias: the judge uses the default reference `llama`
        # instead (LUM-983). With Ollama listing nothing (this test's `dynamic={}`),
        # boot check §3 logs only task_models_ollama_unavailable_at_boot; the plugin's
        # boot call to _judge_model logs the warn-once line naming the entry.
        ("true", {"temporal_judge": ["llama3.2:3b"]}, "true", False, False),
    ],
)
def test_auto_apply_with_non_reference_judge_warns_at_boot(
    monkeypatch, caplog, auto_apply, tasks, ff, expect_not_ref, expect_unresolvable
):
    tp = _tp()
    import plugins.graph as pg

    install(monkeypatch, models={"big": dict(BIG), "llama-judge": dict(LLAMA)}, tasks=tasks)
    for var, val in (("LUMOGIS_TEMPORAL_AUTO_APPLY", auto_apply), ("LUMOGIS_FF_TEMPORAL_KG", ff)):
        if val is None:
            monkeypatch.delenv(var, raising=False)
        else:
            monkeypatch.setenv(var, val)
    monkeypatch.setattr(tp, "register_temporal_hooks", lambda: None)
    monkeypatch.setattr(tp, "register_temporal_jobs", lambda sched: None)
    monkeypatch.setattr(config, "get_scheduler", lambda: None)
    with caplog.at_level(logging.INFO):
        pg._register_temporal_pipeline()
    msgs = [r.getMessage() for r in caplog.records]
    assert any("temporal_judge_not_reference" in m for m in msgs) is expect_not_ref
    assert any("is not resolvable" in m for m in msgs) is expect_unresolvable
    raw_tag = any("first entry llama3.2:3b for destructive task=temporal_judge" in m for m in msgs)
    assert raw_tag is (tasks == {"temporal_judge": ["llama3.2:3b"]} and ff == "true")


def test_judge_boot_warning_fires_when_the_default_is_unresolvable(monkeypatch, caplog):
    """LUM-983: the boot warning is now only for a default that does not resolve."""
    tp = _tp()
    import plugins.graph as pg

    install(monkeypatch, tasks={})
    spec = dataclasses.replace(task_models.TASKS["temporal_judge"], default="not-in-catalogue")
    monkeypatch.setattr(task_models, "TASKS", {**task_models.TASKS, "temporal_judge": spec})
    monkeypatch.setenv("LUMOGIS_FF_TEMPORAL_KG", "true")
    monkeypatch.delenv("LUMOGIS_TEMPORAL_AUTO_APPLY", raising=False)
    monkeypatch.setattr(tp, "register_temporal_hooks", lambda: None)
    monkeypatch.setattr(tp, "register_temporal_jobs", lambda sched: None)
    monkeypatch.setattr(config, "get_scheduler", lambda: None)
    with caplog.at_level(logging.INFO):
        pg._register_temporal_pipeline()
    msgs = [r.getMessage() for r in caplog.records]
    assert any("is not resolvable" in m and "task_model_unsatisfiable" in m for m in msgs)
    assert not any("list a configured alias under tasks.temporal_judge" in m for m in msgs)


def test_substituted_judge_is_the_reference_and_may_auto_apply(monkeypatch):
    """A substituted judge is the ADR 151 reference, so its verdicts may auto-apply.

    Deliberate (LUM-983): the same result as a household with no judge entry. The pin
    in apply_or_propose checks the model that judged; `_run_episode_check` passes it
    the name it resolved once. apply_or_propose with a reference judge is pinned by
    tests/premium/test_temporal_pipeline.py.
    """
    tp = _tp()
    install(monkeypatch, models={"claude": dict(CLOUD)}, tasks={"temporal_judge": ["claude"]})
    set_policy(monkeypatch, "allow_cloud")
    monkeypatch.setenv("LUMOGIS_TEMPORAL_AUTO_APPLY", "true")
    assert tp._judge_model("u1") == "llama"
    assert tp.is_reference_judge(tp._judge_model("u1")) is True


def test_temporal_tasks_open_only_while_documents_are_not_hydrated():
    tp = _tp()
    assert "DOCUMENT" not in tp._HYDRATION_QUERIES or (
        task_models.TASKS["temporal_extract"].local_only
        and task_models.TASKS["temporal_judge"].local_only
    ), (
        "DOCUMENT episodes (email attachments) are now hydrated: make temporal_extract and "
        "temporal_judge local_only in the same change (supplement ADR section 8, ADR 174)"
    )


# ---------------------------------------------------------------------------
# Eval fixture, shipped models.yaml, retired names
# ---------------------------------------------------------------------------


def test_temporal_eval_fixture_llama_matches_repo():
    fixture = yaml.safe_load(
        (_REPO / "orchestrator/tests/fixtures/models.temporal-eval.yaml").read_text()
    )
    repo = yaml.safe_load((_REPO / "config/models.yaml").read_text())
    assert fixture["models"]["llama"] == repo["models"]["llama"]
    assert fixture["tasks"] == {"temporal_extract": ["llama"], "temporal_judge": ["llama"]}


def test_temporal_eval_fixture_resolves_without_a_database(monkeypatch):
    tp = _tp()
    path = _REPO / "orchestrator/tests/fixtures/models.temporal-eval.yaml"
    monkeypatch.setenv("MODELS_CONFIG", str(path))
    monkeypatch.setattr(config, "_models_config", None)
    monkeypatch.setattr(config, "_tasks_config", None)
    monkeypatch.setattr(
        config, "get_metadata_store", lambda: (_ for _ in ()).throw(AssertionError("db"))
    )
    import ollama_client

    monkeypatch.setattr(
        ollama_client,
        "list_local_models",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("ollama")),
    )
    assert tp._extract_model("eval") == "llama"
    assert tp._judge_model("eval") == "llama"


def test_shipped_models_yaml_has_no_live_tasks():
    text = (_REPO / "config/models.yaml").read_text()
    assert yaml.safe_load(text).get("tasks") is None
    for name in task_models.TASKS:
        assert re.search(rf"^#\s+{name}:\s*\[.*\]\s+# code default: ", text, re.M), name


def test_retired_env_names_not_read_in_code():
    root = _REPO / "orchestrator"
    offenders = []
    for path in root.rglob("*.py"):
        rel = path.relative_to(root).as_posix()
        if rel.startswith("tests/") or rel == "services/task_models.py":
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for name in task_models.RETIRED_MODEL_ENV:
            if name in text:
                offenders.append((rel, name))
    assert offenders == []
