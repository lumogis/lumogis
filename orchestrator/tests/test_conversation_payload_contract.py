# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""One payload contract for the Qdrant ``conversations`` collection (LUM-954).

``services.memory.retrieve_context`` reads ``session_id`` and ``summary`` from
every point in ``conversations``. Notes and audio memos shared to the household
used to be written as ``{note_id, text}`` / ``{audio_id, transcript}``, so a
member's recall found them with an empty summary. Every writer now builds its
payload through ``services.memory.conversation_payload``; these tests pin that
for each writer by driving the writer and then the reader, and a source
tripwire fails when a module writes to ``conversations`` without the helper.
"""

from __future__ import annotations

import ast
import uuid
from pathlib import Path

import pytest
from auth import UserContext
from models.memory import SessionSummary

from services import memory
from services import projection

_ORCHESTRATOR = Path(__file__).resolve().parent.parent


def _points(mock_vector_store) -> list[dict]:
    return mock_vector_store._collections.get("conversations", [])


# ---------------------------------------------------------------------------
# The helper
# ---------------------------------------------------------------------------


def test_conversation_payload_has_reader_keys():
    assert memory.conversation_payload(
        item_id="n1", summary="t", user_id="u", scope="shared", note_id="n1"
    ) == {
        "session_id": "n1",
        "summary": "t",
        "user_id": "u",
        "scope": "shared",
        "note_id": "n1",
    }


def test_conversation_payload_coerces_item_id_to_str():
    uid = uuid.uuid4()
    payload = memory.conversation_payload(item_id=uid, summary="t", user_id="u", scope="personal")
    assert payload["session_id"] == str(uid)


def test_conversation_payload_none_summary_becomes_empty_string():
    payload = memory.conversation_payload(item_id="a", summary=None, user_id="u", scope="personal")
    assert payload["summary"] == ""


def test_conversation_payload_rejects_empty_item_id():
    with pytest.raises(ValueError):
        memory.conversation_payload(item_id="", summary="t", user_id="u", scope="personal")


def test_conversation_payload_rejects_session_id_in_extra():
    with pytest.raises(ValueError, match="session_id"):
        memory.conversation_payload(
            item_id="a", summary="t", user_id="u", scope="personal", session_id="b"
        )


def test_conversation_payload_rejects_unknown_scope():
    with pytest.raises(ValueError, match="scope"):
        memory.conversation_payload(item_id="a", summary="t", user_id="u", scope="household")


# ---------------------------------------------------------------------------
# Every writer satisfies the reader
# ---------------------------------------------------------------------------


def test_project_note_payload_is_recallable_by_member(mock_vector_store):
    src = str(uuid.uuid4())
    projection.project_note(
        {"note_id": src, "text": "dinner plan", "user_id": "alice", "source": "quick_capture"},
        target_scope="shared",
        actor=UserContext(user_id="alice"),
    )

    (point,) = _points(mock_vector_store)
    pk = projection.projection_pk("notes", src, "shared")
    payload = point["payload"]
    assert payload["session_id"] == pk
    assert payload["summary"] == "dinner plan"
    assert payload["note_id"] == pk
    assert payload["published_from"] == src
    assert payload["source"] == "quick_capture"
    assert "text" not in payload

    hits = memory.retrieve_context("dinner plan", user_id="bob")
    assert len(hits) == 1
    assert hits[0].session_id == pk
    assert hits[0].summary == "dinner plan"
    assert hits[0].scope == "shared"


def test_project_audio_memo_payload_is_recallable_by_member(mock_vector_store):
    src = str(uuid.uuid4())
    projection.project_audio_memo(
        {"audio_id": src, "transcript": "call the plumber", "user_id": "alice"},
        target_scope="shared",
        actor=UserContext(user_id="alice"),
    )

    (point,) = _points(mock_vector_store)
    pk = projection.projection_pk("audio_memos", src, "shared")
    payload = point["payload"]
    assert payload["session_id"] == pk
    assert payload["summary"] == "call the plumber"
    assert payload["audio_id"] == pk
    assert payload["published_from"] == src
    assert "transcript" not in payload

    hits = memory.retrieve_context("call the plumber", user_id="bob")
    assert [(h.session_id, h.summary) for h in hits] == [(pk, "call the plumber")]


def test_project_audio_memo_with_null_transcript(mock_vector_store):
    src = str(uuid.uuid4())
    projection.project_audio_memo(
        {"audio_id": src, "transcript": None, "user_id": "alice"},
        target_scope="shared",
        actor=UserContext(user_id="alice"),
    )
    # The embedder is a mock, so a point is written; the payload must not raise
    # and must carry an empty (not None) summary.
    (point,) = _points(mock_vector_store)
    assert point["payload"]["summary"] == ""


def test_project_session_payload_unchanged(mock_vector_store):
    src = str(uuid.uuid4())
    projection.project_session(
        {
            "session_id": src,
            "summary": "talked about holidays",
            "topics": ["travel"],
            "entities": ["Lisbon"],
            "user_id": "alice",
        },
        target_scope="shared",
        actor=UserContext(user_id="alice"),
    )

    (point,) = _points(mock_vector_store)
    assert set(point["payload"]) == {
        "session_id",
        "summary",
        "topics",
        "entities",
        "user_id",
        "scope",
        "published_from",
    }
    hits = memory.retrieve_context("holidays", user_id="bob")
    assert [h.summary for h in hits] == ["talked about holidays"]


def test_store_session_payload_unchanged(mock_vector_store):
    memory.store_session(
        SessionSummary(
            session_id="s-1", summary="a summary", topics=["t"], entities=["e"], entity_ids=[]
        ),
        user_id="alice",
    )
    (point,) = _points(mock_vector_store)
    assert set(point["payload"]) == {
        "session_id",
        "summary",
        "topics",
        "entities",
        "user_id",
        "scope",
    }


# ---------------------------------------------------------------------------
# Negative: visibility is still enforced
# ---------------------------------------------------------------------------


def test_personal_note_not_recalled_by_other_member(mock_vector_store):
    mock_vector_store.upsert(
        collection="conversations",
        id="p1",
        vector=[0.0] * 768,
        payload=memory.conversation_payload(
            item_id="n1", summary="private plan", user_id="alice", scope="personal", note_id="n1"
        ),
    )
    assert memory.retrieve_context("private plan", user_id="bob") == []
    assert len(memory.retrieve_context("private plan", user_id="alice")) == 1


def test_opted_out_member_does_not_recall_shared_note(mock_vector_store, monkeypatch):
    import services.users

    monkeypatch.setattr(services.users, "get_allows_shared", lambda uid: uid != "dana")
    projection.project_note(
        {"note_id": str(uuid.uuid4()), "text": "dinner plan", "user_id": "alice"},
        target_scope="shared",
        actor=UserContext(user_id="alice"),
    )

    assert len(memory.retrieve_context("dinner plan", user_id="bob")) == 1  # positive control
    assert memory.retrieve_context("dinner plan", user_id="dana") == []


# ---------------------------------------------------------------------------
# Tripwire: a module that writes to `conversations` must use the helper
# ---------------------------------------------------------------------------

# Today's writers. A new writer fails the equality below until it is listed
# here, and fails the per-module check until it calls conversation_payload.
# A write whose collection name is a variable (admin restore, user import: they
# re-upsert stored payloads verbatim) is intentionally not matched.
_EXPECTED_WRITERS = {
    "services/memory.py": 1,
    "services/captures.py": 1,
    "services/projection.py": 3,
}


def _callee_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    if isinstance(node.func, ast.Name):
        return node.func.id
    return None


def _is_conversations_const(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and node.value == "conversations"


def _collection_arg(node: ast.Call) -> ast.expr | None:
    """The collection argument of an upsert-style call, positional or keyword."""
    for kw in node.keywords:
        if kw.arg == "collection":
            return kw.value
    return node.args[0] if node.args else None


def _count_writes_and_helper_calls(tree: ast.AST) -> tuple[int, int]:
    writes = helper = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _callee_name(node)
        if name == "conversation_payload":
            helper += 1
        elif name in ("upsert", "_qdrant_upsert_safe"):
            arg = _collection_arg(node)
            if arg is not None and _is_conversations_const(arg):
                writes += 1
    return writes, helper


def test_every_conversations_writer_uses_the_helper():
    writers: dict[str, int] = {}
    problems: list[str] = []
    for path in sorted(_ORCHESTRATOR.rglob("*.py")):
        rel = path.relative_to(_ORCHESTRATOR).as_posix()
        if rel.startswith("tests/") or "/tests/" in rel:
            continue
        writes, helper = _count_writes_and_helper_calls(ast.parse(path.read_text()))
        if writes:
            writers[rel] = writes
            if helper < writes:
                problems.append(
                    f"{rel}: {writes} write(s) to 'conversations', {helper} helper call(s)"
                )

    assert not problems, (
        "Build every `conversations` payload with services.memory.conversation_payload() "
        "(LUM-954): " + "; ".join(problems)
    )
    assert writers == _EXPECTED_WRITERS, (
        "The set of modules writing to `conversations` changed. Build the new payload with "
        "services.memory.conversation_payload() and list the module in _EXPECTED_WRITERS."
    )
