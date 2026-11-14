# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Boot-check tests for ``config._check_background_model_defaults`` (LUM-877).

Replaces ``test_main_startup.py`` (ADR 026, ``SIGNAL_LLM_MODEL`` only). The check
now covers every background model task: T-B*.
"""

from __future__ import annotations

import logging

import pytest
from tests._task_models_support import CLOUD
from tests._task_models_support import LLAMA
from tests._task_models_support import install

import config
from services import task_models

_LOGGER = "services.task_models"


def _boom(*a, **k):
    raise AssertionError("must not be called")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in task_models.RETIRED_MODEL_ENV:
        monkeypatch.delenv(var, raising=False)


def _warnings(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]


def _boot(caplog):
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        config._check_background_model_defaults()
    return _warnings(caplog)


def test_default_config_boot_is_silent(monkeypatch, caplog):
    install(monkeypatch, tasks={}, real_dynamic=True)
    monkeypatch.setenv("AUTH_ENABLED", "true")
    import ollama_client

    monkeypatch.setattr(ollama_client, "list_local_models", _boom)
    assert _boot(caplog) == []


@pytest.mark.parametrize("var", list(task_models.RETIRED_MODEL_ENV))
def test_retired_env_var_warns_name_not_value(monkeypatch, caplog, var):
    install(monkeypatch, tasks={})
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv(var, "claude-secretish-value")
    msgs = _boot(caplog)
    assert len([m for m in msgs if "retired_model_env" in m]) == 1
    hit = next(m for m in msgs if "retired_model_env" in m)
    assert var in hit
    assert task_models.RETIRED_MODEL_ENV[var] in hit
    assert all("claude-secretish-value" not in m for m in msgs)
    assert all("claude-secretish-value" not in r.getMessage() for r in caplog.records)


def test_signal_enrichment_ignores_retired_signal_llm_model(monkeypatch):
    install(monkeypatch, models={"claude": dict(CLOUD)}, tasks={})
    monkeypatch.setenv("SIGNAL_LLM_MODEL", "claude")
    assert task_models.resolve_task_model("signal_enrichment", user_id=None) == "llama"


def test_unknown_task_key_warns(monkeypatch, caplog):
    install(monkeypatch, tasks={"nope": ["llama"]})
    assert any("task_config_invalid" in m and "'nope'" in m for m in _boot(caplog))


def test_bad_task_value_shape_warns(monkeypatch, caplog):
    install(monkeypatch, tasks={"session_summary": "llama"})
    assert any("task_config_invalid" in m and "session_summary" in m for m in _boot(caplog))
    assert task_models.resolve_task_model("session_summary", user_id="u1") == "llama"


def test_unsatisfiable_entry_warns(monkeypatch, caplog):
    install(
        monkeypatch,
        models={"claude": dict(CLOUD)},
        tasks={"session_summary": ["ghost"], "email_classify": ["claude"]},
        # A non-empty Ollama listing, so "ghost" is genuinely unknown (an empty
        # listing reads as "Ollama unavailable", T-B13).
        dynamic={"qwen": {"adapter": "openai", "model": "q", "base_url": "http://ollama:11434/v1"}},
    )
    msgs = _boot(caplog)
    assert any("task=session_summary model=ghost reason=unknown" in m for m in msgs)
    assert any("task=email_classify model=claude reason=not_on_box" in m for m in msgs)


def test_keyed_entry_on_no_user_task_warns_under_auth(monkeypatch, caplog):
    install(
        monkeypatch,
        models={"claude": dict(CLOUD)},
        tasks={"signal_enrichment": ["claude", "llama"]},
    )
    monkeypatch.setenv("AUTH_ENABLED", "true")
    assert any("task_model_needs_user" in m and "claude" in m for m in _boot(caplog))


def _keyed_llama(monkeypatch, tasks):
    install(monkeypatch, models={"llama": dict(LLAMA, api_key_env="SOME_KEY")}, tasks=tasks)


def test_no_keyless_candidate_on_no_user_task_raises_under_auth(monkeypatch):
    _keyed_llama(monkeypatch, {"signal_enrichment": []})
    monkeypatch.setenv("AUTH_ENABLED", "true")
    with pytest.raises(RuntimeError) as exc:
        config._check_background_model_defaults()
    msg = str(exc.value)
    for canary in (
        "task 'signal_enrichment'",
        "AUTH_ENABLED=true",
        "tasks:",
        "config/models.yaml",
        "This boot check guards jobs that run without a user only.",
    ):
        assert canary in msg, canary


def test_same_but_auth_off_passes(monkeypatch):
    _keyed_llama(monkeypatch, {})
    monkeypatch.setenv("AUTH_ENABLED", "false")
    config._check_background_model_defaults()


def test_boot_dns_error_warns_not_raises(monkeypatch, caplog):
    install(
        monkeypatch,
        models={"far": {"adapter": "openai", "model": "x", "base_url": "http://ollama.far:1/v1"}},
        tasks={"email_classify": ["far"]},
    )

    def raiser(name, **kw):
        raise OSError("dns down")

    monkeypatch.setattr(task_models, "is_on_box_local", raiser)
    assert any("model=far reason=error" in m for m in _boot(caplog))


def test_dns_failure_at_boot_reads_not_on_box(monkeypatch, caplog):
    # The REAL is_on_box_local swallows a DNS OSError and answers False.
    install(
        monkeypatch,
        models={"far": {"adapter": "openai", "model": "x", "base_url": "http://ollama.far:1/v1"}},
        tasks={"email_classify": ["far"]},
    )

    def no_dns(host):
        raise OSError("dns down")

    monkeypatch.setattr("services.outbound_http_url._resolved_ips", no_dns)
    msgs = _boot(caplog)
    assert any("model=far reason=not_on_box" in m for m in msgs)


def test_unknown_default_under_auth_warns_does_not_refuse(monkeypatch):
    # A models.yaml without `llama` used to warn at boot, not stop it.
    install(monkeypatch, tasks={})
    monkeypatch.setattr(config, "_models_config", {"qwen": dict(LLAMA)})
    monkeypatch.setenv("AUTH_ENABLED", "true")
    config._check_background_model_defaults()


def test_config_entry_point_delegates(monkeypatch):
    calls: list[int] = []
    monkeypatch.setattr(task_models, "check_tasks_at_boot", lambda: calls.append(1))
    config._check_background_model_defaults()
    assert calls == [1]


def test_entry_without_context_budget_warns(monkeypatch, caplog):
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
    assert any("task_model_no_context_budget" in m and "qwen" in m for m in _boot(caplog))
    caplog.clear()
    install(monkeypatch, tasks={"session_summary": ["llama"]})
    assert not any("task_model_no_context_budget" in m for m in _boot(caplog))


def test_ollama_unreachable_at_boot_warns_once(monkeypatch, caplog):
    install(
        monkeypatch,
        tasks={
            "session_summary": ["qwen"],
            "temporal_extract": ["phi"],
            "relation_extraction": ["qwen", "phi"],
        },
        real_dynamic=True,
    )
    import ollama_client

    calls: list[int] = []
    monkeypatch.setattr(ollama_client, "list_local_models", lambda *a, **k: calls.append(1) or [])
    msgs = _boot(caplog)
    assert len([m for m in msgs if "task_models_ollama_unavailable_at_boot" in m]) == 1
    assert not any("reason=unknown" in m for m in msgs)
    assert len(calls) == 1


def test_destructive_task_with_two_entries_warns(monkeypatch, caplog):
    install(monkeypatch, tasks={"temporal_judge": ["llama", "llama3.2"]})
    assert any("task_config_invalid" in m and "uses only the first" in m for m in _boot(caplog))


def test_non_string_item_warns_per_item(monkeypatch, caplog):
    install(monkeypatch, tasks={"session_summary": ["llama", 3]})
    bad = [m for m in _boot(caplog) if "task_config_invalid" in m]
    assert len(bad) == 1 and "item 1" in bad[0]


def test_unexpected_boot_error_warns_and_continues(monkeypatch, caplog):
    install(monkeypatch, tasks={})
    monkeypatch.setattr(
        task_models, "configured_list", lambda t: (_ for _ in ()).throw(KeyError(t))
    )
    msgs = _boot(caplog)
    assert len([m for m in msgs if "task_models_boot_check_failed" in m]) == 1
    # ...but the refusal still propagates through the guard.
    _keyed_llama(monkeypatch, {})
    monkeypatch.setattr(task_models, "configured_list", lambda t: ())
    monkeypatch.setenv("AUTH_ENABLED", "true")
    with pytest.raises(RuntimeError):
        task_models.check_tasks_at_boot()
