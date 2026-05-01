# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Plugin loader: scans plugins/ subdirectories for __init__.py, imports each.

After import, checks if the module exposes a ``router`` attribute
(FastAPI APIRouter).  Collected routers are returned so main.py can
call app.include_router() for each one.
"""

import importlib
import logging
from pathlib import Path

from fastapi import APIRouter

_log = logging.getLogger(__name__)


def load_plugins() -> list[APIRouter]:
    """Import all plugin packages and return any APIRouter objects they expose."""
    routers: list[APIRouter] = []
    plugins_dir = Path(__file__).parent
    for candidate in sorted(plugins_dir.iterdir()):
        if candidate.is_dir() and (candidate / "__init__.py").exists():
            if candidate.name.startswith("_"):
                continue
            module_name = f"plugins.{candidate.name}"
            try:
                mod = importlib.import_module(module_name)
                _log.info("Plugin loaded: %s", candidate.name)
                router = getattr(mod, "router", None)
                if isinstance(router, APIRouter):
                    routers.append(router)
                    _log.info("Plugin router registered: %s", candidate.name)
            except Exception:
                _log.exception("Failed to load plugin: %s", candidate.name)
    if not routers:
        _log.info("No plugin routers found")
    return routers
