# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Shared Ollama admin operations for legacy `/settings/ollama-*` and v1 routes (LUM-451)."""

from __future__ import annotations

import os
import re
from typing import Any

import ollama_client
from fastapi import HTTPException
from ollama_client import _prettify_name
from services.ollama_pull_jobs import finalize_ollama_pull
from settings_store import get_setting

import config


def _safe_get_setting(key: str, store) -> str | None:
    try:
        return get_setting(key, store)
    except Exception:
        return None


def _safe_is_enabled(name: str) -> bool:
    try:
        return config.is_model_enabled(name, user_id=None)
    except Exception:
        return False


def validate_model_name(name: str) -> str:
    cleaned = name.strip()
    if not cleaned or not re.match(r"^[a-zA-Z0-9_\-.:]+$", cleaned):
        raise HTTPException(status_code=400, detail="Invalid model name.")
    return cleaned


def build_ollama_discovery() -> dict[str, Any]:
    """Return local Ollama models and the public catalog for admin UIs."""
    local = ollama_client.list_local_models()
    catalog = ollama_client.fetch_catalog()
    local_names = {m.get("name", "").split(":")[0] for m in local}
    for entry in catalog:
        entry["installed"] = entry["name"].split(":")[0] in local_names
        entry["display_name"] = _prettify_name(entry["name"])

    for m in local:
        base = (m.get("name") or "").split(":")[0]
        m["display_name"] = _prettify_name(base) if base else "Unknown model"

    all_models = config.get_all_models_config()
    alias_map: dict[str, str] = {}
    for alias, cfg in all_models.items():
        ollama_model = cfg.get("model", "")
        base_url = (cfg.get("base_url") or "").lower()
        if "ollama" in base_url or cfg.get("dynamic_ollama"):
            alias_map[ollama_model] = alias

    store = config.get_metadata_store()
    all_model_names = list(all_models.keys())
    stored_default = _safe_get_setting("default_model", store)
    if stored_default and _safe_is_enabled(stored_default):
        default_model = stored_default
    else:
        enabled_names = [n for n in all_model_names if _safe_is_enabled(n)]
        default_model = (
            enabled_names[0] if enabled_names else (all_model_names[0] if all_model_names else None)
        )
    embedding_model = os.environ.get("EMBEDDING_MODEL", "nomic-embed-text")

    return {
        "local": local,
        "catalog": catalog,
        "alias_map": alias_map,
        "embedding_model": embedding_model,
        "default_model": default_model,
    }


def sync_pull_model(name: str, app_state: Any) -> dict[str, Any]:
    """Blocking pull for legacy HTML dashboard (`POST /settings/ollama-pull`)."""
    validated = validate_model_name(name)
    try:
        ollama_client.pull_model(validated)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Ollama pull failed: {exc}") from exc

    qdrant_init_warning = finalize_ollama_pull(validated, app_state)
    return {
        "status": "pulled",
        "name": validated,
        "qdrant_init_warning": qdrant_init_warning,
    }


def delete_model(name: str) -> dict[str, str]:
    """Remove a locally pulled Ollama model and refresh LibreChat config."""
    validated = validate_model_name(name)
    try:
        ollama_client.delete_model(validated)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Ollama delete failed: {exc}") from exc

    # Lazy import avoids routes.admin ↔ services.admin_ollama cycle at import time.
    from routes.admin import _sync_librechat_config

    _sync_librechat_config()
    return {"status": "deleted", "name": validated}
