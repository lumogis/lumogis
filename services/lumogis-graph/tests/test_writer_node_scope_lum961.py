# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Every graph node a writer creates carries ``scope`` (LUM-961, ADR 015).

``visible_cypher_fragment`` admits ``scope = 'personal' AND user_id = me`` and
``scope IN ['shared','system']``. Before LUM-961 the personal writers never set
``scope``, so every personal node was invisible to every filtered graph read.

Hermetic: ``_ScopeAssertingGraph`` is a ``GraphStore`` stand-in that *raises*
on a ``create_node`` without a valid ``scope`` (the way the visibility contract
fails, not a mock that merely records). The live writer → reader round trip is
``orchestrator/tests/integration/test_graph_node_scope_falkordb_live.py``.

The AST ratchet at the bottom is the guard against "a future writer forgets
``scope``": a new ``create_node`` call site, or a second call inside a covered
function, fails until it is added to ``COVERED`` (the author must then also drive it above;
the ratchet counts call sites, it does not check that a test exists).
"""

from __future__ import annotations

import ast
import inspect
import os
import re
from collections import Counter
from pathlib import Path

import pytest
from graph import reconcile
from graph import temporal
from graph import writer
from graph.schema import EVIDENCE_TYPE_LABEL_MAP
from graph.schema import PERSONAL_SCOPE
from graph.schema import node_scope
from graph.temporal import TEMPORAL_CHECK_STATUSES

import config

_VALID = {"personal", "shared", "system"}


class _ScopeAssertingGraph:
    """GraphStore fake: ``create_node`` raises unless ``scope`` is a valid scope."""

    def __init__(self):
        self.calls: list[tuple[str, list[str], dict]] = []

    def create_node(self, labels, properties):
        if "scope" not in properties:
            raise AssertionError(f"create_node without scope: {labels} {properties}")
        if properties["scope"] not in _VALID:
            raise AssertionError(f"create_node with invalid scope: {properties}")
        self.calls.append(("create_node", list(labels), dict(properties)))
        return f"n:{properties['lumogis_id']}"

    def create_edge(self, **kwargs):
        self.calls.append(("create_edge", [], dict(kwargs)))

    def query(self, cypher, params=None):
        self.calls.append(("query", [], {"cypher": cypher, "params": params}))
        return []

    @property
    def nodes(self) -> list[tuple[list[str], dict]]:
        return [(c[1], c[2]) for c in self.calls if c[0] == "create_node"]


class _MS:
    """Metadata store fake: no rows, records writes, optional canned rows by SQL fragment."""

    def __init__(self, rows: dict[str, list[dict]] | None = None):
        self.rows = rows or {}
        self.executed: list[tuple[str, tuple | None]] = []
        self.fetched: list[str] = []

    def fetch_one(self, sql, params=None):
        return None

    def fetch_all(self, sql, params=None):
        self.fetched.append(sql)
        for frag, rows in self.rows.items():
            if frag in sql:
                return list(rows)
        return []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))


@pytest.fixture
def gs() -> _ScopeAssertingGraph:
    return _ScopeAssertingGraph()


@pytest.fixture
def ms(monkeypatch) -> _MS:
    store = _MS()
    monkeypatch.setattr(config, "get_metadata_store", lambda: store)
    return store


# ---------------------------------------------------------------------------
# Writers stamp scope
# ---------------------------------------------------------------------------


def test_project_document_stamps_personal_scope(gs, ms):
    writer.project_document(gs, file_path="/a.md", file_type="md", user_id="u1", ms=ms)
    [(labels, props)] = gs.nodes
    assert labels == ["Document"]
    assert props["scope"] == "personal"
    assert "published_from" not in props


def test_project_document_has_no_scope_parameter():
    # Finding 6: the Document node is keyed (file_path, user_id), which the shared
    # file_index projection row also has. It must never take a shared scope.
    assert "scope" not in inspect.signature(writer.project_document).parameters


def test_project_entity_stamps_personal_on_entity_and_source_node(gs, ms):
    writer.project_entity(
        gs,
        entity_id="e1",
        entity_type="PERSON",
        name="Ada",
        evidence_id="/a.md",
        evidence_type="DOCUMENT",
        user_id="u1",
        ms=ms,
    )
    labels = [lbl for lbl, _ in gs.nodes]
    assert labels == [["Person"], ["Document"]]
    assert [p["scope"] for _, p in gs.nodes] == ["personal", "personal"]


@pytest.mark.parametrize("evidence_type", sorted(EVIDENCE_TYPE_LABEL_MAP))
def test_ensure_source_node_stamps_personal_for_every_evidence_label(gs, evidence_type):
    node_id = writer._ensure_source_node(gs, "ev-1", evidence_type, "u1")
    assert node_id is not None
    [(labels, props)] = gs.nodes
    assert labels == [EVIDENCE_TYPE_LABEL_MAP[evidence_type]]
    assert props["scope"] == "personal"


def test_ensure_source_node_unknown_type_creates_nothing(gs):
    assert writer._ensure_source_node(gs, "ev-1", "NOPE", "u1") is None
    assert gs.nodes == []


def test_project_session_personal_default(gs, ms):
    writer.project_session(
        gs, session_id="s1", summary="x", topics=[], entities=[], user_id="u1", ms=ms
    )
    [(labels, props)] = gs.nodes
    assert labels == ["Session"]
    assert props["scope"] == "personal"
    assert "published_from" not in props


@pytest.mark.parametrize("scope", ["shared", "system"])
def test_project_session_projection_takes_row_scope(gs, ms, scope):
    writer.project_session(
        gs,
        session_id="proj-1",
        summary="x",
        topics=[],
        entities=[],
        user_id="u1",
        scope=scope,
        published_from="src-1",
        ms=ms,
    )
    [(_, props)] = gs.nodes
    assert props["scope"] == scope
    assert props["published_from"] == "src-1"


@pytest.mark.parametrize("scope", ["shared", "system"])
def test_project_note_and_audio_projection_take_row_scope_and_published_from(gs, ms, scope):
    writer.project_note(gs, note_id="pn", user_id="u1", scope=scope, published_from="src-n", ms=ms)
    writer.project_audio(
        gs,
        audio_id="pa",
        file_path="/a.wav",
        user_id="u1",
        scope=scope,
        published_from="src-a",
        ms=ms,
    )
    (note_labels, note), (audio_labels, audio) = gs.nodes
    assert note_labels == ["Note"] and audio_labels == ["AudioMemo"]
    assert (note["scope"], note["published_from"]) == (scope, "src-n")
    assert (audio["scope"], audio["published_from"]) == (scope, "src-a")


def test_project_note_and_audio_personal_default(gs, ms):
    writer.project_note(gs, note_id="n1", user_id="u1", ms=ms)
    writer.project_audio(gs, audio_id="a1", file_path="/a.wav", user_id="u1", ms=ms)
    for _, props in gs.nodes:
        assert props["scope"] == "personal"
        assert "published_from" not in props


def test_temporal_evidence_nodes_carry_personal_scope(gs):
    temporal._merge_evidence_node(gs, "e1", "SESSION", "u1")
    temporal.stamp_temporal_checked(
        gs,
        evidence_id="e1",
        evidence_type="SESSION",
        user_id="u1",
        status=TEMPORAL_CHECK_STATUSES[0],
    )
    assert len(gs.nodes) == 3  # merge, merge (inside the stamp), stamp
    assert {p["scope"] for _, p in gs.nodes} == {"personal"}


def test_code_structure_nodes_carry_personal_scope_and_cannot_be_overridden(gs):
    from codegraph.models import ComponentNode

    comp = ComponentNode(
        lumogis_id="c1",
        label="Component",
        props={"name": "f", "scope": "shared"},  # no parser emits this; pins the ordering
        user_id="u1",
    )
    writer.project_code_structure(
        gs, components=[comp], edges=[], bank="b", user_id="u1", ingest_run_id="r1"
    )
    [(_, props)] = gs.nodes
    assert props["scope"] == "personal"


# ---------------------------------------------------------------------------
# Negatives: invalid input fails BEFORE any graph write
# ---------------------------------------------------------------------------


def test_node_scope_rejects_unknown_value(gs, ms):
    with pytest.raises(ValueError):
        writer.project_note(
            gs, note_id="n", user_id="u", scope="household", published_from="s", ms=ms
        )
    assert gs.calls == []


@pytest.mark.parametrize("bad", [None, "", 3])
def test_node_scope_rejects_non_string_or_empty_scope(bad):
    with pytest.raises(ValueError):
        node_scope(bad, None)


def test_node_scope_rejects_personal_with_published_from(gs, ms):
    with pytest.raises(ValueError):
        writer.project_session(
            gs,
            session_id="s",
            summary="x",
            topics=[],
            entities=[],
            user_id="u",
            scope="personal",
            published_from="s",
            ms=ms,
        )
    assert gs.calls == []  # no create_node and no query


@pytest.mark.parametrize("published_from", [None, ""])
def test_node_scope_rejects_projection_without_published_from(gs, ms, published_from):
    with pytest.raises(ValueError):
        writer.project_audio(
            gs,
            audio_id="a",
            file_path="/a.wav",
            user_id="u",
            scope="shared",
            published_from=published_from,
            ms=ms,
        )
    assert gs.calls == []


# ---------------------------------------------------------------------------
# Reconcile arms
# ---------------------------------------------------------------------------


def _wire(monkeypatch, store, graph) -> None:
    monkeypatch.setattr(config, "get_metadata_store", lambda: store)
    monkeypatch.setattr(config, "get_graph_store", lambda bank=None: graph)


def test_reconcile_documents_selects_personal_rows_only(monkeypatch, gs):
    store = _MS()
    _wire(monkeypatch, store, gs)
    reconcile.reconcile_documents()
    [sql] = [s for s in store.fetched if "FROM file_index" in s]
    cond = reconcile._STALE_CONDITION_FILE_INDEX
    assert f"({cond}) AND (scope = 'personal')" in sql
    assert "IS NULL) AND" not in sql  # file_index.scope is NOT NULL: no NULL branch


def test_reconcile_sessions_notes_audio_pass_row_scope(monkeypatch, gs):
    store = _MS(
        {
            "FROM sessions": [
                {
                    "session_id": "s-p",
                    "summary": "x",
                    "topics": [],
                    "entities": [],
                    "entity_ids": [],
                    "user_id": "u1",
                    "scope": "personal",
                    "published_from": None,
                },
                {
                    "session_id": "s-s",
                    "summary": "x",
                    "topics": [],
                    "entities": [],
                    "entity_ids": [],
                    "user_id": "u1",
                    "scope": "shared",
                    "published_from": "s-p",
                },
            ],
            "FROM notes": [
                {"note_id": "n-p", "user_id": "u1", "scope": "personal", "published_from": None},
                {"note_id": "n-s", "user_id": "u1", "scope": "shared", "published_from": "n-p"},
            ],
            "FROM audio_memos": [
                {
                    "audio_id": "a-p",
                    "file_path": "/a",
                    "duration_seconds": 1,
                    "user_id": "u1",
                    "scope": "personal",
                    "published_from": None,
                },
                {
                    "audio_id": "a-s",
                    "file_path": "/a",
                    "duration_seconds": 1,
                    "user_id": "u1",
                    "scope": "system",
                    "published_from": "a-p",
                },
            ],
        }
    )
    _wire(monkeypatch, store, gs)
    for arm in (reconcile.reconcile_sessions, reconcile.reconcile_notes, reconcile.reconcile_audio):
        assert arm()["projected_failed"] == 0

    by_id = {p["lumogis_id"]: p for _, p in gs.nodes}
    for pid in ("s-p", "n-p", "a-p"):
        assert by_id[pid]["scope"] == "personal"
        assert "published_from" not in by_id[pid]
    assert (by_id["s-s"]["scope"], by_id["s-s"]["published_from"]) == ("shared", "s-p")
    assert (by_id["n-s"]["scope"], by_id["n-s"]["published_from"]) == ("shared", "n-p")
    assert (by_id["a-s"]["scope"], by_id["a-s"]["published_from"]) == ("system", "a-p")


def test_reconcile_notes_selects_scope_and_published_from(monkeypatch, gs):
    store = _MS()
    _wire(monkeypatch, store, gs)
    reconcile.reconcile_notes()
    reconcile.reconcile_audio()
    reconcile.reconcile_sessions()
    for table in ("notes", "audio_memos", "sessions"):
        [sql] = [s for s in store.fetched if f"FROM {table}" in s]
        assert "scope" in sql and "published_from" in sql


def test_reconcile_note_with_invalid_scope_is_failed_and_unstamped(monkeypatch, gs):
    store = _MS(
        {
            "FROM notes": [
                {"note_id": "bad", "user_id": "u1", "scope": "bogus", "published_from": None},
                {"note_id": "good", "user_id": "u1", "scope": "personal", "published_from": None},
            ]
        }
    )
    _wire(monkeypatch, store, gs)
    counter = reconcile.reconcile_notes()
    assert counter["projected_failed"] == 1
    # only the good row is stamped, and the bad row is never updated
    assert counter["stamped"] == 1
    stamped_ids = [p[0] for s, p in store.executed if "UPDATE notes" in s]
    assert stamped_ids == ["good"]
    assert [p["lumogis_id"] for _, p in gs.nodes] == ["good"]


# ---------------------------------------------------------------------------
# AST ratchet: every create_node call site is accounted for
# ---------------------------------------------------------------------------

_REPO = Path(__file__).resolve().parents[3]
_KG_ROOT = Path(__file__).resolve().parents[1]
_ORCH_ROOT = _REPO / "orchestrator"
_SKIP_DIRS = {"tests", "adapters", "ports", "__pycache__"}

# (module path relative to the tree root, enclosing function) -> number of
# ``.create_node(`` calls. Each of these is driven by a test above.
COVERED: Counter = Counter(
    {
        ("graph/writer.py", "project_document"): 1,
        ("graph/writer.py", "project_entity"): 1,
        ("graph/writer.py", "project_session"): 1,
        ("graph/writer.py", "project_note"): 1,
        ("graph/writer.py", "project_audio"): 1,
        ("graph/writer.py", "_ensure_source_node"): 1,
        ("graph/writer.py", "project_code_structure"): 1,
        ("graph/temporal.py", "_merge_evidence_node"): 1,
        ("graph/temporal.py", "stamp_temporal_checked"): 1,
        ("projection.py", "project_entity_into_graph"): 1,
    }
)

# Orchestrator callers that are deliberately not stamped: the user import
# restores archive node properties verbatim (archives from the fixed writer
# already carry ``scope``; inventing one for older archives would be guessing).
EXEMPT: Counter = Counter({("services/user_export.py", "_import_user_impl"): 2})

# A labelled node pattern after MERGE/CREATE: ``MERGE (n:Person``, ``MERGE (s:{label}`` (an
# f-string placeholder) and a path ending in one, ``MERGE (a)-[:R]->(n:Person``. Edge-only
# merges (``MERGE (e)-[r:MENTIONS]->(d)``) have no ``(x:`` and do not match.
_RAW_NODE_PATTERN = re.compile(r"\b(?:MERGE|CREATE)\b[^;]*?\(\w*:[\w{]")


def _collect_create_node_calls(source: str, module: str = "<module>") -> Counter:
    """Count ``<expr>.create_node(...)`` calls per ``(module, enclosing function)``."""
    counts: Counter = Counter()

    def visit(node: ast.AST, fn: str) -> None:
        for child in ast.iter_child_nodes(node):
            child_fn = fn
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                child_fn = child.name
            if (
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and child.func.attr == "create_node"
            ):
                counts[(module, fn)] += 1
            visit(child, child_fn)

    visit(ast.parse(source), "<module-level>")
    return counts


def _collect_raw_node_merges(source: str) -> list[str]:
    """String constants that contain a labelled-node MERGE/CREATE pattern."""
    hits: list[str] = []
    for node in ast.walk(ast.parse(source)):
        text = None
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
        elif isinstance(node, ast.JoinedStr):
            # f-string: keep the literal parts, stand in `{` for each placeholder
            text = "".join(p.value if isinstance(p, ast.Constant) else "{" for p in node.values)
        if text is not None and _RAW_NODE_PATTERN.search(text):
            hits.append(text)
    return hits


def _python_files(root: Path):
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root)
        if _SKIP_DIRS & set(rel.parts):
            continue
        yield rel.as_posix(), path


def _count_tree(root: Path) -> Counter:
    counts: Counter = Counter()
    for rel, path in _python_files(root):
        counts += _collect_create_node_calls(path.read_text(encoding="utf-8"), rel)
    return counts


def _orchestrator_or_skip():
    if _ORCH_ROOT.is_dir():
        return _ORCH_ROOT
    if os.environ.get("CI"):
        pytest.fail("orchestrator/ is absent in CI; the ratchet's orchestrator half must run")
    pytest.skip("orchestrator/ not present (graph-service image)")


def test_create_node_call_sites_are_all_covered():
    found = _count_tree(_KG_ROOT)
    assert found == COVERED, (
        "A create_node call site changed. Every graph node must carry `scope` (LUM-961): "
        "stamp it, drive it in this file, then update COVERED. "
        f"unexpected={dict(found - COVERED)} missing={dict(COVERED - found)}"
    )


def test_orchestrator_create_node_callers_are_exempt_or_covered():
    found = _count_tree(_orchestrator_or_skip())
    assert found == EXEMPT, (
        "A new orchestrator create_node caller. Stamp `scope` (LUM-961) or add it to EXEMPT "
        f"with a reason. unexpected={dict(found - EXEMPT)} missing={dict(EXEMPT - found)}"
    )


def test_no_raw_labelled_node_merge_outside_create_node():
    roots = [_KG_ROOT]
    if _ORCH_ROOT.is_dir():
        roots.append(_ORCH_ROOT)
    elif os.environ.get("CI"):
        pytest.fail("orchestrator/ is absent in CI; the raw-MERGE scan must cover it")
    offenders = [
        (rel, hit)
        for root in roots
        for rel, path in _python_files(root)
        for hit in _collect_raw_node_merges(path.read_text(encoding="utf-8"))
    ]
    assert offenders == [], (
        f"Raw Cypher node creation bypasses create_node and its `scope` ratchet: {offenders}"
    )


def test_ratchet_detects_an_uncovered_create_node_site():
    src = "def f(gs):\n    gs.create_node(labels=[], properties={})\n"
    assert _collect_create_node_calls(src) == Counter({("<module>", "f"): 1})


def test_ratchet_counts_a_second_call_in_a_covered_function():
    src = (
        "def f(gs):\n"
        "    gs.create_node(labels=[], properties={})\n"
        "    gs.create_node(labels=[], properties={})\n"
    )
    assert _collect_create_node_calls(src) == Counter({("<module>", "f"): 2})


def test_raw_node_merge_scanner_sees_a_labelled_merge():
    assert len(_collect_raw_node_merges('Q = "MERGE (n:Person {lumogis_id: $x})"')) == 1
    assert _collect_raw_node_merges('Q = "MERGE (e)-[r:MENTIONS]->(d)"') == []
    # the idioms writer.py actually uses: an f-string label and a path ending in a node
    assert len(_collect_raw_node_merges('Q = f"MERGE (s:{LABEL} {{lumogis_id: $x}})"')) == 1
    assert len(_collect_raw_node_merges('Q = "MATCH (a) MERGE (a)-[:R]->(n:Person {x: 1})"')) == 1


# ---------------------------------------------------------------------------
# The two visibility.py copies cannot drift
# ---------------------------------------------------------------------------


def _function_dumps(path: Path, names: set[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                body = body[1:]  # strip the docstring
            node.body = body
            out[node.name] = ast.dump(node)
    return out


def test_visible_cypher_fragment_copies_match():
    # `_effective_allows_shared` differs by design (its DB lookup), so it is excluded.
    orch = _orchestrator_or_skip() / "visibility.py"
    names = {"visible_cypher_fragment", "_validate_scope_filter"}
    service = _function_dumps(_KG_ROOT / "visibility.py", names)
    core = _function_dumps(orch, names)
    assert set(service) == names and set(core) == names
    assert service == core, "visible_cypher_fragment drifted between orchestrator and KG service"


def test_personal_scope_constant_is_the_visibility_literal():
    assert PERSONAL_SCOPE == "personal"
