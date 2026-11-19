# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Resolver tests for ``services/task_models.py`` (LUM-877): T-R*, T-E*, T-O*."""

from __future__ import annotations

import logging
from dataclasses import replace

import pytest
from services.task_models import TASKS
from tests._task_models_support import BIG
from tests._task_models_support import CLOUD
from tests._task_models_support import LLAMA
from tests._task_models_support import grant_key
from tests._task_models_support import install
from tests._task_models_support import set_policy

import config
from services import outbound_http_url
from services import privacy_mode
from services import task_models


def _boom(*a, **k):
    raise AssertionError("must not be called")


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_EXPECTED = {
    #                      default  local  tools  threads destr  email
    "signal_enrichment": ("llama", False, False, False, False, False),
    "email_classify": ("llama", True, False, True, False, True),
    "email_reply_draft": ("llama", True, False, True, False, True),
    "email_compose_refine": ("llama", True, False, True, False, True),
    "entity_extraction": ("llama", True, False, False, False, False),
    "relation_extraction": ("llama", False, False, False, False, False),
    "session_summary": ("llama", True, False, True, False, False),
    "routine_weekly_review": ("llama", True, False, True, False, False),
    "temporal_extract": ("llama", True, False, True, False, False),
    "temporal_judge": ("llama", True, False, True, True, False),
}


def test_registry_has_exactly_the_ten_tasks():
    assert list(TASKS) == list(_EXPECTED)
    for name, spec in TASKS.items():
        got = (
            spec.default,
            spec.local_only,
            spec.needs_tools,
            spec.threads_user_id,
            spec.destructive,
            spec.email_boundary,
        )
        assert got == _EXPECTED[name], name
        assert spec.description
        if spec.email_boundary:
            assert spec.local_only, f"{name}: email_boundary implies local_only"
    assert {n for n, s in TASKS.items() if s.email_boundary} == {
        "email_classify",
        "email_reply_draft",
        "email_compose_refine",
    }


def test_judge_default_is_the_eval_reference():
    import plugins.graph  # noqa: F401  (puts services/lumogis-graph on sys.path)
    from plugins.graph import temporal_pipeline as tp

    assert TASKS["temporal_judge"].default == tp.TEMPORAL_JUDGE_REFERENCE_MODEL
    assert not hasattr(tp, "TEMPORAL_EXTRACT_MODEL_DEFAULT")


# ---------------------------------------------------------------------------
# resolve_task_model
# ---------------------------------------------------------------------------


def test_empty_table_returns_code_default_with_no_io(monkeypatch):
    install(monkeypatch, tasks={})
    monkeypatch.setattr(config, "get_model_config", _boom)
    monkeypatch.setattr(config, "get_all_models_config", _boom)
    monkeypatch.setattr(config, "model_disabled_reason", _boom)
    monkeypatch.setattr(config, "_resolve_config_file", _boom)
    monkeypatch.setattr(config.yaml, "safe_load", _boom)
    monkeypatch.setattr(privacy_mode, "effective_privacy_mode", _boom)
    monkeypatch.setattr(task_models, "is_on_box_local", _boom)
    for _ in range(2):  # the second pass proves the empty table is cached
        for name, spec in TASKS.items():
            for uid in (None, "u1"):
                assert task_models.resolve_task_model(name, user_id=uid) == spec.default


def test_first_usable_entry_wins_and_later_entries_not_evaluated(monkeypatch):
    install(
        monkeypatch,
        models={"b": dict(LLAMA), "c": dict(LLAMA)},
        tasks={"session_summary": ["a", "b", "c"]},
    )
    seen: list[str] = []
    real = task_models._skip_reason

    def spy(spec, name, **kw):
        seen.append(name)
        return real(spec, name, **kw)

    monkeypatch.setattr(task_models, "_skip_reason", spy)
    assert task_models.resolve_task_model("session_summary", user_id="u1") == "b"
    assert seen == ["a", "b"]


def test_all_entries_skipped_falls_back_to_default_unconditionally(monkeypatch):
    install(monkeypatch, tasks={"temporal_extract": ["nope"]})
    assert task_models.resolve_task_model("temporal_extract", user_id="u1") == "llama"
    # Even when the default itself is unknown.
    install(monkeypatch, tasks={"session_summary": ["nope"]})
    spec = replace(TASKS["session_summary"], default="not-in-catalogue")
    monkeypatch.setattr(task_models, "TASKS", {**TASKS, "session_summary": spec})
    assert task_models.resolve_task_model("session_summary", user_id="u1") == "not-in-catalogue"


def test_unknown_task_raises_value_error():
    with pytest.raises(ValueError):
        task_models.resolve_task_model("nope", user_id=None)


def test_configured_list_normalises(monkeypatch):
    install(
        monkeypatch,
        tasks={
            "session_summary": ["  llama ", "", "llama", "qwen", "  "],
            "relation_extraction": "llama",
            "entity_extraction": {"a": 1},
            "email_classify": ["llama", 3, None, "qwen", True],
            "temporal_judge": ["a", "b"],
        },
    )
    assert task_models.configured_list("session_summary") == ("llama", "qwen")
    assert task_models.configured_list("relation_extraction") == ()
    assert task_models.configured_list("entity_extraction") == ()
    assert task_models.configured_list("email_classify") == ("llama", "qwen")
    assert task_models.configured_list("temporal_judge") == ("a",)
    assert task_models.configured_list("temporal_extract") == ()


def test_session_summary_and_entity_extraction_share_the_on_box_rule():
    # LUM-899: both read the same session text (batch_handlers/session_end.py),
    # which can quote email (ADR 174), so neither may be loosened alone.
    assert TASKS["session_summary"].local_only
    assert TASKS["session_summary"].local_only == TASKS["entity_extraction"].local_only


def test_temporal_tasks_stay_on_box_because_session_summaries_feed_them():
    # LUM-899 (ADR 250): SESSION episodes hydrate from the stored session summary,
    # which can quote email, and temporal_judge reads the facts extracted from it.
    assert TASKS["temporal_extract"].local_only
    assert TASKS["temporal_judge"].local_only


@pytest.mark.parametrize("mode", ["allow_cloud", "local_only"])
def test_temporal_extract_off_box_entry_resolves_to_code_default(monkeypatch, mode):
    install(monkeypatch, models={"claude": dict(CLOUD)}, tasks={"temporal_extract": ["claude"]})
    grant_key(monkeypatch, present=True)
    set_policy(monkeypatch, mode)
    assert task_models.resolve_task_model("temporal_extract", user_id="u1") == "llama"
    exp = task_models.explain_task("temporal_extract", user_id="u1")
    assert [(e.model, e.skip_reason) for e in exp.entries] == [("claude", "not_on_box")]


@pytest.mark.parametrize("mode", ["allow_cloud", "local_only"])
@pytest.mark.parametrize("uid", [None, "u1"])
def test_session_summary_off_box_entry_resolves_to_code_default(monkeypatch, mode, uid):
    # LUM-899: a usable, keyed cloud model listed for session_summary must not
    # carry the conversation off the box, even when privacy mode allows cloud.
    install(monkeypatch, models={"claude": dict(CLOUD)}, tasks={"session_summary": ["claude"]})
    grant_key(monkeypatch, present=True)
    set_policy(monkeypatch, mode)
    assert task_models.resolve_task_model("session_summary", user_id=uid) == "llama"
    exp = task_models.explain_task("session_summary", user_id=uid)
    assert [(e.model, e.skip_reason) for e in exp.entries] == [("claude", "not_on_box")]
    assert exp.resolved_from == "default"
    # The same model on a task that is not local_only is chosen (control).
    install(monkeypatch, models={"claude": dict(CLOUD)}, tasks={"relation_extraction": ["claude"]})
    if mode == "allow_cloud" and uid is not None:
        assert task_models.resolve_task_model("relation_extraction", user_id=uid) == "claude"


def test_skip_reason_order(monkeypatch):
    install(monkeypatch, models={"claude": dict(CLOUD)}, tasks={})
    grant_key(monkeypatch, present=False)
    local_spec = TASKS["email_classify"]
    plain_spec = TASKS["relation_extraction"]

    def reason(spec, name):
        return task_models._skip_reason(spec, name, user_id="u1", models=task_models._ModelLookup())

    # unknown is decided before any on-box test
    monkeypatch.setattr(task_models, "is_on_box_local", _boom)
    assert reason(local_spec, "ghost") == "unknown"
    monkeypatch.undo()
    install(monkeypatch, models={"claude": dict(CLOUD)}, tasks={})
    grant_key(monkeypatch, present=False)
    # a cloud model on a local_only task reads not_on_box, not no_credentials
    assert reason(local_spec, "claude") == "not_on_box"
    # ...and on a task that is not local_only it reads no_credentials
    assert reason(plain_spec, "claude") == "no_credentials"


def test_entry_check_exception_reads_error_and_continues(monkeypatch, caplog):
    install(
        monkeypatch,
        models={"claude": dict(CLOUD)},
        tasks={"relation_extraction": ["claude", "llama"]},
    )
    real = config.model_disabled_reason

    def flaky(name, **kw):
        if name == "claude":
            raise RuntimeError("secret-api-value-123")
        return real(name, **kw)

    monkeypatch.setattr(config, "model_disabled_reason", flaky)
    with caplog.at_level(logging.WARNING, logger="services.task_models"):
        assert task_models.resolve_task_model("relation_extraction", user_id="u1") == "llama"
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "entry check failed" in text and "RuntimeError" in text
    assert "secret-api-value-123" not in text
    exp = task_models.explain_task("relation_extraction", user_id="u1")
    assert [e.skip_reason for e in exp.entries] == ["error", None]


# ---------------------------------------------------------------------------
# explain_task
# ---------------------------------------------------------------------------


def test_explain_evaluates_every_entry(monkeypatch):
    install(
        monkeypatch,
        models={"claude": dict(CLOUD)},
        tasks={"relation_extraction": ["x-unknown", "llama", "claude"]},
    )
    monkeypatch.setenv("AUTH_ENABLED", "true")  # user_id=None + keyed => no_credentials
    set_policy(monkeypatch, "allow_cloud")
    exp = task_models.explain_task("relation_extraction", user_id=None)
    assert [(e.model, e.skip_reason) for e in exp.entries] == [
        ("x-unknown", "unknown"),
        ("llama", None),
        ("claude", "no_credentials"),
    ]
    assert exp.resolved_model == "llama"
    assert exp.resolved_from == "list"


@pytest.mark.parametrize(
    "tasks",
    [
        {},
        {"session_summary": ["ghost"]},
        {"session_summary": ["ghost", "llama"]},
        {"temporal_judge": ["ghost", "llama"]},
        {"temporal_judge": ["claude"]},
        {"temporal_judge": ["claude", "llama"]},
        {"temporal_judge": ["llama"]},
        {"email_classify": ["claude", "llama"]},
        {"email_classify": ["claude"]},
    ],
)
@pytest.mark.parametrize("uid", [None, "u1"])
def test_explain_and_resolve_agree(monkeypatch, tasks, uid):
    install(monkeypatch, models={"claude": dict(CLOUD)}, tasks=tasks)
    grant_key(monkeypatch, present=True)
    set_policy(monkeypatch, "allow_cloud")
    for name in TASKS:
        exp = task_models.explain_task(name, user_id=uid)
        assert exp.resolved_model == task_models.resolve_task_model(name, user_id=uid), name


# ---------------------------------------------------------------------------
# is_on_box_local (moved from email_classifier)
# ---------------------------------------------------------------------------


def _ollama(url):
    return {"adapter": "openai", "model": "x", "base_url": url}


def test_is_on_box_local_moved_behaviour(monkeypatch):
    install(
        monkeypatch,
        models={
            "loop": _ollama("http://localhost:11434/ollama/v1"),
            "lan": _ollama("http://gpu.lan:11434/ollama/v1"),
            "pub": _ollama("http://ollama.example.com:11434/v1"),
            "cgnat": _ollama("http://ts.example:11434/ollama/v1"),
            "dnsfail": _ollama("http://nodns.example:11434/ollama/v1"),
            "anth": {"adapter": "anthropic", "model": "c"},
            "plain": {"adapter": "openai", "model": "p", "base_url": "http://api.example.com/v1"},
        },
    )
    ips = {
        "ollama.example.com": ["93.184.216.34"],
        "ts.example": ["100.64.0.1"],
        "gpu.lan": ["192.168.1.9"],
    }

    def fake_ips(host):
        if host == "nodns.example":
            raise OSError("no dns")
        return ips[host]

    monkeypatch.setattr(outbound_http_url, "_resolved_ips", fake_ips)
    assert task_models.is_on_box_local("llama")  # ollama host
    assert task_models.is_on_box_local("loop")
    assert task_models.is_on_box_local("lan")  # private IP
    monkeypatch.setenv("EMAIL_LLM_ALLOWLIST", "ts.example")
    assert task_models.is_on_box_local("cgnat")  # allowlisted
    monkeypatch.delenv("EMAIL_LLM_ALLOWLIST")
    assert not task_models.is_on_box_local("pub")
    assert not task_models.is_on_box_local("cgnat")  # CGNAT is neither private nor global
    assert not task_models.is_on_box_local("dnsfail")
    assert not task_models.is_on_box_local("plain")  # not an "ollama" URL
    assert not task_models.is_on_box_local("anth")  # no base_url at all
    assert not task_models.is_on_box_local("ghost")


def test_email_classifier_alias_is_the_same_function():
    from services import email_classifier

    assert email_classifier._model_is_on_box_local is task_models.is_on_box_local
    assert email_classifier._email_llm_allowlist is task_models._email_llm_allowlist
    assert email_classifier._DEFAULT_LOCAL_HOSTS is task_models._DEFAULT_LOCAL_HOSTS


def test_is_on_box_local_with_cfg_makes_no_lookup(monkeypatch):
    install(monkeypatch)
    monkeypatch.setattr(config, "get_model_config", _boom)
    monkeypatch.setattr(config, "is_local_model", _boom)
    monkeypatch.setattr(outbound_http_url, "_resolved_ips", lambda h: ["93.184.216.34"])
    assert task_models.is_on_box_local("llama", cfg=dict(LLAMA))
    assert not task_models.is_on_box_local("x", cfg=_ollama("http://ollama.example.com/v1"))
    assert not task_models.is_on_box_local("x", cfg={"adapter": "anthropic", "model": "c"})


# ---------------------------------------------------------------------------
# needs_tools, Ollama, privacy hint, lookup equivalence
# ---------------------------------------------------------------------------


def test_needs_tools_branch(monkeypatch, caplog):
    install(
        monkeypatch,
        models={"notools": dict(LLAMA, tools=False), "tools": dict(LLAMA, tools=True)},
        tasks={"session_summary": ["notools", "tools"]},
    )
    spec = replace(TASKS["session_summary"], needs_tools=True)
    monkeypatch.setattr(task_models, "TASKS", {**TASKS, "session_summary": spec})
    exp = task_models.explain_task("session_summary", user_id="u1")
    assert [e.skip_reason for e in exp.entries] == ["no_tools", None]
    assert task_models.resolve_task_model("session_summary", user_id="u1") == "tools"
    with caplog.at_level(logging.WARNING, logger="services.task_models"):
        task_models.check_tasks_at_boot()
    assert any("reason=no_tools" in r.getMessage() for r in caplog.records)


def test_ollama_down_lists_once_and_falls_to_default(monkeypatch, caplog):
    install(monkeypatch, tasks={"session_summary": ["qwen", "phi"]}, real_dynamic=True)
    import ollama_client

    calls: list[int] = []
    monkeypatch.setattr(ollama_client, "list_local_models", lambda *a, **k: calls.append(1) or [])
    with caplog.at_level(logging.WARNING, logger="services.task_models"):
        assert task_models.resolve_task_model("session_summary", user_id="u1") == "llama"
        first = [r for r in caplog.records if "no listed model usable" in r.getMessage()]
        assert len(first) == 1
        assert task_models.resolve_task_model("session_summary", user_id="u1") == "llama"
    assert len(calls) == 2  # exactly one listing per resolution
    assert len([r for r in caplog.records if "no listed model usable" in r.getMessage()]) == 1


def test_yaml_entries_make_no_ollama_call(monkeypatch):
    install(monkeypatch, tasks={"session_summary": ["llama"]}, real_dynamic=True)
    import ollama_client

    monkeypatch.setattr(ollama_client, "list_local_models", _boom)
    assert task_models.resolve_task_model("session_summary", user_id="u1") == "llama"


def test_local_entry_reads_no_privacy_setting(monkeypatch):
    install(monkeypatch, tasks={"session_summary": ["llama"]})
    monkeypatch.setattr(privacy_mode, "effective_privacy_mode", _boom)
    monkeypatch.setattr(config, "get_metadata_store", _boom)
    assert task_models.resolve_task_model("session_summary", user_id="u1") == "llama"
    exp = task_models.explain_task("session_summary", user_id="u1")
    assert exp.entries[0].skip_reason is None


def test_destructive_task_malformed_first_item_is_not_promoted(monkeypatch):
    install(monkeypatch, tasks={"temporal_judge": [3, "llama"]})
    assert task_models.configured_list("temporal_judge") == ()
    assert task_models.resolve_task_model("temporal_judge", user_id="u1") == "llama"


def _tp():
    import plugins.graph  # noqa: F401  (puts services/lumogis-graph on sys.path)
    from plugins.graph import temporal_pipeline as tp

    return tp


def _messages(caplog, level=logging.WARNING):
    return [r.getMessage() for r in caplog.records if r.levelno >= level]


def test_destructive_task_returns_on_box_first_entry_unchecked(monkeypatch, caplog):
    """An on-box first entry is used with no key, privacy or switched-off read (ADR 241 §8)."""
    install(
        monkeypatch,
        models={"judge-local": dict(BIG)},
        tasks={"temporal_judge": ["judge-local", "llama"]},
    )
    monkeypatch.setattr(config, "model_disabled_reason", _boom)
    monkeypatch.setattr(privacy_mode, "effective_privacy_mode", _boom)
    assert task_models.resolve_task_model("temporal_judge", user_id="u1") == "judge-local"
    # An unknown entry is not on-box proven: the code default is used (LUM-983).
    install(monkeypatch, tasks={"temporal_judge": ["qwen-judge"]})
    with caplog.at_level(logging.WARNING, logger="services.task_models"):
        assert task_models.resolve_task_model("temporal_judge", user_id="u1") == "llama"
    assert any("reason=unknown" in m for m in _messages(caplog))


def test_temporal_judge_on_box_keyless_entry_is_still_the_judge(monkeypatch):
    """The other half of ADR 241 §8: keyless, blocked or switched off is kept, not swapped.

    It fails at the call site into judge_failed.
    """
    install(
        monkeypatch, models={"judge-local": dict(BIG)}, tasks={"temporal_judge": ["judge-local"]}
    )
    monkeypatch.setattr(config, "model_disabled_reason", lambda *a, **k: "no_credentials")
    assert task_models.resolve_task_model("temporal_judge", user_id="u1") == "judge-local"
    exp = task_models.explain_task("temporal_judge", user_id="u1")
    assert exp.resolved_model == "judge-local"
    assert exp.resolved_from == "list"
    assert [(e.model, e.skip_reason) for e in exp.entries] == [("judge-local", "no_credentials")]


@pytest.mark.parametrize("mode", ["allow_cloud", "local_only"])
@pytest.mark.parametrize("uid", [None, "u1"])
def test_temporal_judge_off_box_entry_resolves_to_code_default(monkeypatch, mode, uid):
    # LUM-983: the judge reads episode text (notes, voice memos, session summaries).
    install(monkeypatch, models={"claude": dict(CLOUD)}, tasks={"temporal_judge": ["claude"]})
    grant_key(monkeypatch, present=True)
    set_policy(monkeypatch, mode)
    assert task_models.resolve_task_model("temporal_judge", user_id=uid) == "llama"
    exp = task_models.explain_task("temporal_judge", user_id=uid)
    assert exp.resolved_model == "llama"
    assert exp.resolved_from == "default"
    assert [(e.model, e.skip_reason) for e in exp.entries] == [("claude", "not_on_box")]


def test_temporal_judge_never_takes_a_later_entry(monkeypatch):
    install(
        monkeypatch,
        models={"claude": dict(CLOUD), "big": dict(BIG)},
        tasks={"temporal_judge": ["claude", "big"]},
    )
    grant_key(monkeypatch, present=True)
    set_policy(monkeypatch, "allow_cloud")
    assert task_models.resolve_task_model("temporal_judge", user_id="u1") == "llama"
    assert task_models.explain_task("temporal_judge", user_id="u1").resolved_model == "llama"


@pytest.mark.parametrize("host_kind", ["unresolved", "public"])
def test_temporal_judge_first_entry_on_unresolved_or_public_host_resolves_to_default(
    monkeypatch, host_kind
):
    url = (
        "http://nodns.example:11434/ollama/v1"
        if host_kind == "unresolved"
        else "http://ollama.example.com:11434/v1"
    )

    def fake_ips(host):
        if host_kind == "unresolved":
            raise OSError("no dns")
        return ["93.184.216.34"]

    install(
        monkeypatch,
        models={"far": {"adapter": "openai", "model": "x", "base_url": url}},
        tasks={"temporal_judge": ["far"]},
    )
    monkeypatch.setattr(outbound_http_url, "_resolved_ips", fake_ips)
    assert task_models.resolve_task_model("temporal_judge", user_id="u1") == "llama"
    exp = task_models.explain_task("temporal_judge", user_id="u1")
    assert exp.resolved_model == "llama"
    assert exp.resolved_from == "default"
    assert [(e.model, e.skip_reason) for e in exp.entries] == [("far", "not_on_box")]


@pytest.mark.parametrize("where", ["lookup", "on_box", "on_box_preset"])
def test_temporal_judge_lookup_error_resolves_to_default_without_leaking(
    monkeypatch, caplog, where
):
    install(
        monkeypatch, models={"judge-local": dict(BIG)}, tasks={"temporal_judge": ["judge-local"]}
    )

    def raiser_runtime(*a, **k):
        raise RuntimeError("secret-api-value-123")

    def raiser_value(*a, **k):
        raise ValueError("secret-api-value-123")

    if where == "lookup":
        monkeypatch.setattr(task_models._ModelLookup, "get", raiser_runtime)
    else:
        monkeypatch.setattr(task_models, "is_on_box_local", raiser_value)
    with caplog.at_level(logging.WARNING, logger="services.task_models"):
        if where == "on_box_preset":
            # The preset-map branch the admin route uses (admin_diagnostics.py).
            exp = task_models.explain_task(
                "temporal_judge",
                user_id=None,
                _models={"llama": dict(LLAMA), "judge-local": dict(BIG)},
            )
        else:
            assert task_models.resolve_task_model("temporal_judge", user_id="u1") == "llama"
            exp = task_models.explain_task("temporal_judge", user_id="u1")
    assert exp.resolved_model == "llama"
    assert exp.resolved_from == "default"
    if where != "on_box_preset":
        assert [(e.model, e.skip_reason) for e in exp.entries] == [("judge-local", "error")]
    text = "\n".join(_messages(caplog))
    assert "secret-api-value-123" not in text
    if where != "on_box_preset":  # explain_task never logs the warn-once line
        assert "reason=error" in text
    assert "reason=unknown" not in text
    assert ("RuntimeError" if where == "lookup" else "ValueError") in text


_DYN_REMOTE = {
    "adapter": "openai",
    "model": "qwen2.5:7b",
    "base_url": "http://ollama.example.com:11434/v1",
}
_DYN_LOCAL = dict(_DYN_REMOTE, base_url="http://ollama:11434/v1")


@pytest.mark.parametrize(
    "dynamic,public,want",
    [
        ({}, False, "llama"),  # (a) unknown while Ollama is down
        ({"qwen2.5": _DYN_REMOTE}, True, "llama"),  # (b) alias on a public host
        ({"qwen2.5": _DYN_LOCAL}, False, "qwen2.5"),  # (c) positive control
    ],
    ids=["unknown", "remote", "on_box"],
)
def test_temporal_judge_dynamic_alias_must_be_on_box(monkeypatch, caplog, dynamic, public, want):
    install(monkeypatch, tasks={"temporal_judge": ["qwen2.5"]}, dynamic=dynamic)
    if public:
        monkeypatch.setattr(outbound_http_url, "_resolved_ips", lambda h: ["93.184.216.34"])
    with caplog.at_level(logging.WARNING, logger="services.task_models"):
        assert task_models.resolve_task_model("temporal_judge", user_id="u1") == want
    msgs = _messages(caplog)
    if not dynamic:
        assert any(
            "reason=unknown (Ollama listed no models: unreachable or empty)" in m for m in msgs
        )
    elif public:
        assert any("reason=not_on_box" in m for m in msgs)
    else:
        assert not any("destructive task" in m for m in msgs)


def test_temporal_judge_default_warning_logged_once_and_not_by_explain(monkeypatch, caplog):
    install(monkeypatch, models={"claude": dict(CLOUD)}, tasks={"temporal_judge": ["claude"]})
    grant_key(monkeypatch, present=True)
    set_policy(monkeypatch, "allow_cloud")

    def hits():
        return [
            m
            for m in _messages(caplog)
            if "destructive task=temporal_judge" in m
            and "reason=not_on_box" in m
            and "using code default llama" in m
        ]

    with caplog.at_level(logging.WARNING, logger="services.task_models"):
        task_models.explain_task("temporal_judge", user_id="u1")
        assert hits() == []  # diagnostics must not spend the one warning
        task_models.resolve_task_model("temporal_judge", user_id="u1")
        assert len(hits()) == 1
        task_models.resolve_task_model("temporal_judge", user_id="u1")
    assert len(hits()) == 1


def test_temporal_judge_warning_after_boot_call_is_per_reason(monkeypatch, caplog):
    """Production sequence: the plugin's boot call, then episodes (LUM-983)."""
    tp = _tp()
    alias = dict(_DYN_REMOTE, base_url="http://gpu.lan:11434/ollama/v1")
    install(monkeypatch, tasks={"temporal_judge": ["qwen2.5"]}, dynamic={"qwen2.5": alias})
    set_policy(monkeypatch, "allow_cloud")

    def no_dns(host):
        raise OSError("no dns")

    monkeypatch.setattr(outbound_http_url, "_resolved_ips", no_dns)
    with caplog.at_level(logging.DEBUG, logger="services.task_models"):
        assert tp._judge_model(None) == "llama"
        assert tp._judge_model("u1") == "llama"
        assert tp._judge_model("u1") == "llama"
        warns = [r for r in caplog.records if r.levelno == logging.WARNING]
        debugs = [
            r.getMessage()
            for r in caplog.records
            if r.levelno == logging.DEBUG and "substituted code default llama" in r.getMessage()
        ]
        assert len(warns) == 1 and "reason=not_on_box" in warns[0].getMessage()
        assert len(debugs) == 2
        # Ollama goes away without clearing the warn-once set: a new reason warns again.
        monkeypatch.setattr(config, "_dynamic_ollama_models", lambda: {})
        assert tp._judge_model("u1") == "llama"
    warns = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warns) == 2 and "reason=unknown" in warns[1]


def test_temporal_judge_follows_a_flapping_first_entry(monkeypatch):
    """A first entry that is only sometimes usable is decided per resolution.

    This is the accepted behaviour (plan LUM-983, "What this builds"): the operator's
    model when it is proven on-box, else the code default. Today the same flap ends
    judge_failed, which is never retried.
    """
    install(monkeypatch, tasks={"temporal_judge": ["qwen2.5"]})
    calls = {"n": 0}

    def flapping():
        calls["n"] += 1
        return {} if calls["n"] == 2 else {"qwen2.5": dict(_DYN_LOCAL)}

    monkeypatch.setattr(config, "_dynamic_ollama_models", flapping)
    got = [task_models.resolve_task_model("temporal_judge", user_id="u1") for _ in range(3)]
    assert got == ["qwen2.5", "llama", "qwen2.5"]


def test_destructive_task_that_is_not_local_only_returns_first_entry_with_no_lookup(monkeypatch):
    install(monkeypatch, models={"claude": dict(CLOUD)}, tasks={"temporal_judge": ["claude"]})
    spec = replace(TASKS["temporal_judge"], local_only=False)
    monkeypatch.setattr(task_models, "TASKS", {**TASKS, "temporal_judge": spec})
    monkeypatch.setattr(task_models._ModelLookup, "get", _boom)
    assert task_models.resolve_task_model("temporal_judge", user_id=None) == "claude"


def test_on_box_resolution(monkeypatch, caplog):
    install(monkeypatch, tasks={})
    with pytest.raises(ValueError):
        task_models.resolve_on_box_model("nope", user_id="u1")
    monkeypatch.setattr(task_models, "is_on_box_local", _boom)
    with caplog.at_level(logging.ERROR, logger="services.task_models"):
        assert task_models.resolve_on_box_model("session_summary", user_id="u1") is None
        assert task_models.resolve_on_box_model("routine_weekly_review", user_id="u1") is None
    assert len([r for r in caplog.records if r.levelno == logging.ERROR]) == 2
    monkeypatch.setattr(task_models, "is_on_box_local", lambda name, **kw: False)
    assert task_models.resolve_on_box_model("email_classify", user_id="u1") is None
    monkeypatch.setattr(task_models, "is_on_box_local", lambda name, **kw: True)
    assert task_models.resolve_on_box_model("email_classify", user_id="u1") == "llama"


@pytest.mark.parametrize("ollama_url", [True, False])
@pytest.mark.parametrize("mode", ["allow_cloud", "local_only"])
@pytest.mark.parametrize("user", ["inherit", "local_only"])
@pytest.mark.parametrize("keyed", [True, False])
def test_privacy_hint_equals_unhinted_answer(monkeypatch, ollama_url, mode, user, keyed):
    cfg = {
        "adapter": "openai",
        "model": "m",
        "base_url": "http://ollama:11434/v1" if ollama_url else "https://api.example.com/v1",
    }
    if keyed:
        cfg["api_key_env"] = "SOME_KEY"
    install(monkeypatch, models={"m": cfg})
    grant_key(monkeypatch, present=True)
    set_policy(monkeypatch, mode, user=user)
    expected = config.model_disabled_reason("m", user_id="u1")
    got = task_models._skip_reason(
        TASKS["relation_extraction"], "m", user_id="u1", models=task_models._ModelLookup()
    )
    assert got == expected


def test_model_lookup_matches_get_model_config(monkeypatch):
    dyn = {
        "qwen": {
            "adapter": "openai",
            "model": "qwen:7b",
            "base_url": "http://ollama:11434/v1",
            "tools": False,
            "dynamic_ollama": True,
        }
    }
    install(monkeypatch, models={"claude": dict(CLOUD)}, dynamic=dyn)
    for name in ("llama", "claude", "qwen", "ghost"):
        got = task_models._ModelLookup().get(name)
        try:
            want = config.get_model_config(name)
        except config.UnknownModelError:
            want = None
        assert got == want, name


def test_big_model_fixture_is_local():
    # Guards the shared fixture other test files rely on.
    assert "ollama" in BIG["base_url"]
