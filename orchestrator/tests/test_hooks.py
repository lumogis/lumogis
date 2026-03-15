"""Tests for hooks.py: register, fire, fire_background, Event constants."""

import time

import hooks
from events import Event


def test_fire_calls_callbacks_in_order():
    results = []
    hooks.register("test_event", lambda: results.append("a"))
    hooks.register("test_event", lambda: results.append("b"))
    hooks.fire("test_event")
    assert results == ["a", "b"]


def test_fire_with_args():
    received = []
    hooks.register("test_args", lambda x, y: received.append((x, y)))
    hooks.fire("test_args", 1, 2)
    assert received == [(1, 2)]


def test_fire_unknown_event_is_safe():
    hooks.fire("nonexistent_event_xyz")


def test_fire_background_executes():
    results = []
    hooks.register("test_bg", lambda: results.append("done"))
    hooks.fire_background("test_bg")
    time.sleep(0.2)
    assert results == ["done"]


def test_event_constants():
    assert Event.DOCUMENT_INGESTED == "on_document_ingested"
    assert Event.ENTITY_CREATED == "on_entity_created"
    assert Event.SESSION_ENDED == "on_session_ended"
    assert Event.TOOL_REGISTERED == "on_tool_registered"
