# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Plugin loader: scans plugins/ subdirectories for __init__.py, imports each.

After import, checks if the module exposes a ``router`` attribute
(FastAPI APIRouter).  Collected routers are returned so main.py can
call app.include_router() for each one.
"""

import importlib
import logging
import pkgutil
from pathlib import Path

from fastapi import APIRouter

_log = logging.getLogger(__name__)

# LUM-613 / ADR-170 §0: untrusted code runs OUT-OF-PROCESS only. The in-process
# loader accepts first-party plugins exclusively; any other module is refused
# (distinct from an ordinary broken import). This is a guardrail against
# accidental/legitimate third-party in-process loading and future-loader
# regression — NOT a defence against an attacker who can already write into
# plugins/ (that is host code execution, out of scope).
FIRST_PARTY_PLUGINS: frozenset[str] = frozenset({"graph"})


def _plugin_module_names() -> list[str]:
    """Discover plugin subpackages (filesystem dev tree or frozen PyInstaller)."""
    plugins_dir = Path(__file__).parent
    if plugins_dir.is_dir():
        names: list[str] = []
        for candidate in sorted(plugins_dir.iterdir()):
            if candidate.is_dir() and (candidate / "__init__.py").exists():
                if not candidate.name.startswith("_"):
                    names.append(candidate.name)
        return names
    prefix = __name__ + "."
    return sorted(
        mod.name[len(prefix) :]
        for mod in pkgutil.iter_modules(__path__, prefix)
        if not mod.name[len(prefix) :].startswith("_")
    )


def load_plugins() -> list[APIRouter]:
    """Import all plugin packages and return any APIRouter objects they expose."""
    from services.capability_egress import UntrustedInProcessPluginError
    from services.capability_egress import assert_first_party_plugin

    routers: list[APIRouter] = []
    for plugin_name in _plugin_module_names():
        module_name = f"plugins.{plugin_name}"
        # LUM-613: refuse a non-first-party in-process plugin BEFORE the broad
        # import try/except below, so a refusal is a distinct, loud event and not
        # indistinguishable from an ordinary broken import.
        try:
            assert_first_party_plugin(plugin_name, first_party=FIRST_PARTY_PLUGINS)
        except UntrustedInProcessPluginError:
            _log.warning(
                "Refusing to load non-first-party in-process plugin %r — untrusted "
                "capabilities must run out-of-process (OOP-only, ADR-170 §0)",
                plugin_name,
            )
            continue
        try:
            mod = importlib.import_module(module_name)
            _log.info("Plugin loaded: %s", plugin_name)
            router = getattr(mod, "router", None)
            if isinstance(router, APIRouter):
                routers.append(router)
                _log.info("Plugin router registered: %s", plugin_name)
        except Exception:
            _log.exception("Failed to load plugin: %s", plugin_name)
    if not routers:
        _log.info("No plugin routers found")
    return routers
