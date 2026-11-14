# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for the symbol/edge mapper (LUM-301). Pure, no graph."""

from codegraph.mapper import map_module
from codegraph.models import lumogis_id
from codegraph.parser import CodeParser


def _map(src: bytes, file_rel: str, bank: str = "coding"):
    pm = CodeParser().parse_file(src, file_rel)
    return pm, map_module(pm, bank=bank)


def test_symbol_to_component():
    _pm, res = _map(b"def a():\n    return 1\n", "m.py")
    comp = next(c for c in res.components if c.props.get("name") == "a")
    assert comp.label == "Component"
    assert comp.lumogis_id == lumogis_id("coding", "m.a", "function")


def test_calls_edge_rel_type():
    _pm, res = _map(b"def a():\n    return b()\n\n\ndef b():\n    return 1\n", "m.py")
    call_edges = [e for e in res.edges if e.rel_type == "CALLS"]
    assert len(call_edges) == 1
    e = call_edges[0]
    assert e.from_id == lumogis_id("coding", "m.a", "function")
    assert e.to_id == lumogis_id("coding", "m.b", "function")


def test_external_import_to_library():
    _pm, res = _map(b"import requests\n", "pkg/m.py")
    libs = [c for c in res.components if c.label == "Library"]
    assert any(c.props["name"] == "requests" for c in libs)
    dep = [e for e in res.edges if e.rel_type == "DEPENDS_ON"]
    assert len(dep) == 1
    assert dep[0].to_id == lumogis_id("coding", "requests", "library")


def test_internal_import_deferred_v1():
    # First-party imports (relative or same-top-package) emit NO edge in v1 —
    # cross-file resolution is deferred. Only external imports -> Library.
    src = b"from pkg.other import thing\nfrom . import sibling\n"
    _pm, res = _map(src, "pkg/m.py")
    assert not [e for e in res.edges if e.rel_type == "DEPENDS_ON"]
    assert not [c for c in res.components if c.label == "Library"]


def test_implements_edge_from_base_class():
    src = b"class A:\n    pass\n\n\nclass B(A):\n    pass\n"
    _pm, res = _map(src, "m.py")
    impl = [e for e in res.edges if e.rel_type == "IMPLEMENTS"]
    assert len(impl) == 1
    assert impl[0].from_id == lumogis_id("coding", "m.B", "class")
    assert impl[0].to_id == lumogis_id("coding", "m.A", "class")


def test_lumogis_id_no_collision():
    # Two methods both named `run` in different classes → distinct ids.
    src = (
        b"class A:\n    def run(self):\n        return 1\n\n\n"
        b"class B:\n    def run(self):\n        return 2\n"
    )
    _pm, res = _map(src, "m.py")
    run_ids = {c.lumogis_id for c in res.components if c.props.get("name") == "run"}
    assert len(run_ids) == 2


def test_unresolved_call_dropped_and_counted():
    # `nope` is defined nowhere in the module → no CALLS edge, counted.
    _pm, res = _map(b"def a():\n    return nope()\n", "m.py")
    assert not [e for e in res.edges if e.rel_type == "CALLS"]
    assert res.unresolved_calls == 1


def test_no_raw_source_in_props():
    src = b"def secret():\n    password = 'hunter2'\n    return password\n"
    _pm, res = _map(src, "m.py")
    comp = next(c for c in res.components if c.props.get("name") == "secret")
    blob = repr(comp.props)
    assert "hunter2" not in blob  # function body text never enters props
