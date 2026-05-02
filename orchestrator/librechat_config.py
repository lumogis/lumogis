# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Generate librechat.yaml dynamically from current model state.

Reads models.yaml + live Ollama state to produce modelSpecs entries with
auto-generated labels, so every enabled model appears in the LibreChat
dropdown with a human-readable name — no manual config needed.

The written path (default ``/project/config/librechat.yaml``) is gitignored.
Commit changes to the cold-start shape in ``config/librechat.coldstart.yaml`` only;
``docker-entrypoint.sh`` seeds ``librechat.yaml`` from that template when missing.
"""

import logging
import os
import re
from pathlib import Path

import yaml

_log = logging.getLogger(__name__)

_LIBRECHAT_CONFIG_PATH = Path(
    os.environ.get("LIBRECHAT_CONFIG_PATH", "/project/config/librechat.yaml")
)

# _prettify_name lives in ollama_client — imported here and used by _model_label.
from ollama_client import _prettify_name  # noqa: E402


def _model_label(alias: str, cfg: dict, param_size: str | None) -> str:
    """Build a display label like 'Qwen 2.5 7B (Local)' or 'Claude (Cloud)'."""
    base_url = (cfg.get("base_url") or "").lower()
    is_local = "ollama" in base_url or cfg.get("dynamic_ollama")
    model_field = cfg.get("model", alias)

    # For the display name, prefer the alias (user-facing) over the raw model ID.
    # Dynamic Ollama models use the base model name.
    if cfg.get("dynamic_ollama"):
        base_name = model_field.split(":")[0]
    else:
        base_name = alias
    pretty = _prettify_name(base_name)

    # For size, prefer the model tag (e.g., "7b") over the precise param_size
    # (e.g., "7.6B") since tags are what users recognise.
    size_str = ""
    if ":" in model_field:
        tag = model_field.split(":")[1]
        if re.match(r"^[\d.]+[bBmM]?$", tag):
            normalised = tag.upper() if tag[-1].lower() in ("b", "m") else tag
            size_str = f" {normalised}"
        elif tag != "latest" and param_size:
            size_str = f" {param_size}"
    if not size_str and param_size and is_local:
        size_str = f" {param_size}"

    suffix = "Local" if is_local else "Cloud"
    return f"{pretty}{size_str} ({suffix})"


def generate_librechat_yaml() -> bool:
    """Regenerate librechat.yaml with modelSpecs for all enabled models.

    Returns True if the file was written, False on error.
    """
    try:
        import config as app_config
        import ollama_client

        all_models = app_config.get_all_models_config()
        local_models = ollama_client.list_local_models()

        param_sizes: dict[str, str] = {}
        for m in local_models:
            name = m.get("name", "")
            ps = (m.get("details") or {}).get("parameter_size", "")
            if ps:
                param_sizes[name] = ps
                param_sizes[name.split(":")[0]] = ps

        catalog_by_base = {
            e["name"].split(":")[0]: e
            for e in ollama_client.get_curated_catalog()
        }
        _CLOUD_DESCRIPTIONS: dict[str, str] = {
            "anthropic": (
                "Anthropic's Claude models, known for careful reasoning, long-context "
                "understanding, and thoughtful responses."
            ),
        }

        specs_list = []
        for alias, cfg in all_models.items():
            # Plan llm_provider_keys_per_user_migration Pass 3.13: the
            # generated ``librechat.yaml`` is a **household** model list
            # (per question 3 of the plan: LibreChat keeps the household
            # view; per-user filtering happens dynamically at the
            # ``/v1/models`` endpoint). Passing ``user_id=None`` is
            # explicit so the auth-on semantics are unambiguous: under
            # ``AUTH_ENABLED=true`` cloud models are listed iff the
            # household optional toggle is on (the substrate has no
            # household-level "any user has a key" aggregate by design).
            # Users without their own key get a 424 at chat time, mapped
            # to a friendly message in the chat UI.
            if not app_config.is_model_enabled(alias, user_id=None):
                continue

            ollama_model = cfg.get("model", "")
            ps = param_sizes.get(ollama_model) or param_sizes.get(alias)
            label = _model_label(alias, cfg, ps)

            base_url = (cfg.get("base_url") or "").lower()
            is_local = "ollama" in base_url or cfg.get("dynamic_ollama")

            if is_local:
                base_name = cfg.get("model", alias).split(":")[0]
                catalog_entry = catalog_by_base.get(base_name, {})
                description = catalog_entry.get("description") or "A locally-running AI model."
            else:
                adapter = cfg.get("adapter") or ""
                description = _CLOUD_DESCRIPTIONS.get(adapter, "A cloud-hosted AI model.")

            spec: dict = {
                "name": alias,
                "label": label,
                "description": description,
                "preset": {
                    "endpoint": "Lumogis",
                    "model": alias,
                    "modelLabel": label,
                },
            }

            if is_local:
                spec["iconURL"] = "ollama"
            elif "anthropic" in adapter:
                spec["iconURL"] = "anthropic"

            specs_list.append(spec)

        # Build the Knowledge Graph footer link.  The orchestrator URL uses the
        # ORCHESTRATOR_EXTERNAL_URL env var when set (e.g. behind a reverse proxy),
        # otherwise falls back to the default localhost:8000.
        _ext_url = os.environ.get("ORCHESTRATOR_EXTERNAL_URL", "http://localhost:8000").rstrip("/")
        _dashboard_url = f"{_ext_url}/dashboard"
        _graph_url = f"{_ext_url}/graph/mgm"
        _custom_footer = f"[Dashboard]({_dashboard_url}) | [Knowledge Graph]({_graph_url})"

        librechat_cfg = {
            "version": "1.3.5",
            "cache": True,
            "interface": {
                "customFooter": _custom_footer,
            },
            "endpoints": {
                "custom": [{
                    "name": "Lumogis",
                    "apiKey": "ignored",
                    "baseURL": "http://orchestrator:8000/v1",
                    "models": {
                        "default": [s["name"] for s in specs_list],
                        "fetch": True,
                    },
                    "titleConvo": False,
                    "summarize": False,
                    "forcePrompt": False,
                    "modelDisplayLabel": "Lumogis",
                }],
            },
            "modelSpecs": {
                "enforce": True,
                "prioritize": True,
                "list": specs_list,
            },
        }

        _LIBRECHAT_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_LIBRECHAT_CONFIG_PATH, "w") as f:
            yaml.dump(librechat_cfg, f, default_flow_style=False, sort_keys=False)

        model_names = [s["label"] for s in specs_list]
        _log.info(
            "librechat.yaml regenerated with %d models: %s",
            len(specs_list),
            model_names,
        )
        return True

    except Exception:
        _log.exception("Failed to generate librechat.yaml")
        return False
