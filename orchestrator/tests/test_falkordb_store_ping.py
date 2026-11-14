# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""FalkorDBStore.ping() checks the connection, not a graph key (LUM-720, LUM-894).

On a fresh FalkorDB the bank's graph key does not exist, and any graph query
fails with ``Invalid graph operation on empty key``. The old ping ran such a
query, so it reported a healthy server as down until the first write. These
tests pin the fix without a server: a fake ``falkordb`` module whose graph
queries always fail the way an empty key does.
"""

from __future__ import annotations

import sys
import types

import pytest


class _EmptyKeyGraph:
    def ro_query(self, *_a, **_k):
        raise RuntimeError("Invalid graph operation on empty key")

    query = ro_query


def _fake_falkordb(monkeypatch, *, ping_result=True, ping_exc: Exception | None = None):
    calls: list[tuple[str, int]] = []

    class _Conn:
        def ping(self):
            if ping_exc is not None:
                raise ping_exc
            return ping_result

    class FalkorDB:  # noqa: D401 - mimics falkordb.FalkorDB
        def __init__(self, host="localhost", port=6379, **_k):
            calls.append((host, port))
            self.connection = _Conn()

        def select_graph(self, _name):
            return _EmptyKeyGraph()

    mod = types.ModuleType("falkordb")
    mod.FalkorDB = FalkorDB
    monkeypatch.setitem(sys.modules, "falkordb", mod)
    return calls


def _store():
    from adapters.falkordb_store import FalkorDBStore

    return FalkorDBStore("redis://falkordb.example:6390", graph_name="never_written")


def test_ping_true_on_fresh_graph_key(monkeypatch):
    calls = _fake_falkordb(monkeypatch)
    assert _store().ping() is True
    assert calls == [("falkordb.example", 6390)]


def test_ping_false_when_connection_fails(monkeypatch):
    _fake_falkordb(monkeypatch, ping_exc=ConnectionError("connection refused"))
    assert _store().ping() is False


def test_ping_false_when_server_answers_false(monkeypatch):
    _fake_falkordb(monkeypatch, ping_result=False)
    assert _store().ping() is False


def test_ping_false_when_client_library_missing(monkeypatch):
    # sys.modules[name] = None makes `import name` raise ImportError.
    monkeypatch.setitem(sys.modules, "falkordb", None)
    assert _store().ping() is False


@pytest.mark.parametrize("method", ["ro_query", "query"])
def test_fake_graph_really_rejects_queries(method):
    """Guard the fake: if it stopped failing, the first test would prove nothing."""
    with pytest.raises(RuntimeError, match="empty key"):
        getattr(_EmptyKeyGraph(), method)("RETURN 1")
