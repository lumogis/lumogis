"""Plugin loader: scans plugins/ subdirectories for __init__.py, imports each."""

import importlib
import logging
from pathlib import Path

_log = logging.getLogger(__name__)


def load_plugins() -> list[str]:
    loaded = []
    plugins_dir = Path(__file__).parent
    for candidate in sorted(plugins_dir.iterdir()):
        if candidate.is_dir() and (candidate / "__init__.py").exists():
            if candidate.name.startswith("_"):
                continue
            module_name = f"plugins.{candidate.name}"
            try:
                importlib.import_module(module_name)
                loaded.append(candidate.name)
                _log.info("Plugin loaded: %s", candidate.name)
            except Exception:
                _log.exception("Failed to load plugin: %s", candidate.name)
    if not loaded:
        _log.info("No plugins found in plugins/")
    return loaded
