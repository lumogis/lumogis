# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-613 / ADR-170 §0 — the in-process plugin loader refuses non-first-party
modules BEFORE the broad import try/except, so a refusal is a distinct event and
not indistinguishable from an ordinary broken import.
"""

from __future__ import annotations

import plugins as plugins_pkg


def test_load_plugins_refuses_non_first_party_before_import(monkeypatch) -> None:
    # A non-first-party module must never reach importlib.import_module.
    monkeypatch.setattr(plugins_pkg, "_plugin_module_names", lambda: ["evil_plugin", "graph"])

    imported: list[str] = []

    def _fake_import(module_name: str):
        imported.append(module_name)

        class _Mod:
            router = None

        return _Mod()

    monkeypatch.setattr(plugins_pkg.importlib, "import_module", _fake_import)

    plugins_pkg.load_plugins()

    assert "plugins.evil_plugin" not in imported  # refused before import (OOP-only)
    assert "plugins.graph" in imported  # first-party still loads


def test_first_party_plugins_contains_graph() -> None:
    assert "graph" in plugins_pkg.FIRST_PARTY_PLUGINS
