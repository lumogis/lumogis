# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Shared seams for the task-model-routing tests (LUM-877).

Hermetic: no Postgres, no Ollama, no DNS. ``install`` patches the model registry,
the ``tasks:`` table and the dynamic-alias listing; ``policy`` builds the privacy
policy ``privacy_mode.effective_privacy_mode`` would return.
"""

from __future__ import annotations

from models.privacy_mode import InstancePrivacyMode
from models.privacy_mode import PrivacyUserRestriction

import config
from services import privacy_mode
from services import task_models

LLAMA = {
    "adapter": "openai",
    "model": "llama3.2:3b",
    "base_url": "http://ollama:11434/v1",
    "context_budget": 8192,
    "tools": False,
}
BIG = {
    "adapter": "openai",
    "model": "big",
    "base_url": "http://ollama:11434/v1",
    "context_budget": 32768,
}
CLOUD = {
    "adapter": "anthropic",
    "model": "claude-x",
    "api_key_env": "ANTHROPIC_API_KEY",
    "context_budget": 100000,
}


def install(monkeypatch, models=None, tasks=None, dynamic=None, *, real_dynamic=False):
    """Patch the registry, the tasks table and the Ollama alias listing."""
    all_models = {"llama": dict(LLAMA)}
    all_models.update(models or {})
    monkeypatch.setattr(config, "_models_config", all_models)
    monkeypatch.setattr(config, "_tasks_config", tasks if tasks is not None else {})
    if not real_dynamic:
        monkeypatch.setattr(config, "_dynamic_ollama_models", lambda: dict(dynamic or {}))
    task_models._fallthrough_warned.clear()
    return all_models


def policy(mode: str = "allow_cloud", *, user: str = "inherit") -> privacy_mode.EffectivePrivacy:
    inst = InstancePrivacyMode(mode)
    return privacy_mode.EffectivePrivacy(
        instance_mode=inst,
        instance_locked=False,
        instance_effective=inst,
        user_restriction=PrivacyUserRestriction(user),
        effective=InstancePrivacyMode.LOCAL_ONLY
        if (inst == InstancePrivacyMode.LOCAL_ONLY or user == "local_only")
        else InstancePrivacyMode.ALLOW_CLOUD,
    )


def set_policy(monkeypatch, mode: str = "allow_cloud", *, user: str = "inherit") -> None:
    pol = policy(mode, user=user)
    monkeypatch.setattr(privacy_mode, "effective_privacy_mode", lambda uid: pol)


def grant_key(monkeypatch, present: bool = True) -> None:
    """Auth on, and every user holds (or lacks) a stored key."""
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setattr("services.llm_connector_map.has_credential", lambda uid, env: present)
