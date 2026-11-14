# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""GC mass-delete guard knobs in the graph service's config (LUM-952).

Mirrored by orchestrator/tests/test_graph_gc_config.py: Core runs the graph
reconcile in-process with its own config, so both modules must agree.
"""

import pytest

import config

_KNOBS = [
    ("get_gc_max_orphans", "GRAPH_GC_MAX_ORPHANS", 500, "40", 40),
    ("get_gc_max_orphan_ratio", "GRAPH_GC_MAX_ORPHAN_RATIO", 0.20, "0.5", 0.5),
    ("get_gc_min_orphans_for_ratio", "GRAPH_GC_MIN_ORPHANS_FOR_RATIO", 25, "3", 3),
]


@pytest.mark.parametrize("fn,env,default,raw,parsed", _KNOBS)
def test_gc_knob_default_env_and_invalid(monkeypatch, fn, env, default, raw, parsed):
    get = getattr(config, fn)
    monkeypatch.delenv(env, raising=False)
    assert get() == default
    monkeypatch.setenv(env, raw)
    assert get() == parsed
    monkeypatch.setenv(env, "not-a-number")
    assert get() == default
    monkeypatch.setenv(env, "-1")
    assert get() == default
