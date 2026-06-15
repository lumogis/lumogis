# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-128: SessionState transition invariants for the unified tool loop."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError

import hooks
import pytest
from events import Event
from models.llm import LLMEvent
from models.llm import LLMResponse
from models.llm import LLMToolCall
from models.session_state import SessionLoopEvent
from models.session_state import SessionParams
from models.session_state import SessionState
from models.session_state import SessionTerminal
from models.session_state import ToolChainBudget
from models.session_state import TransitionReason
from models.session_state import initial_session_state


def _base_params(**overrides) -> SessionParams:
    defaults = dict(
        user_id="u-test",
        system="sys",
        tools=[],
        use_tools=True,
        model="claude",
        auto_rag_point_ids=None,
    )
    defaults.update(overrides)
    return SessionParams(**defaults)


def _loop_collector() -> tuple[list[tuple[SessionLoopEvent, SessionState]], object]:
    events: list[tuple[SessionLoopEvent, SessionState]] = []

    def on_loop_event(event: SessionLoopEvent, state: SessionState) -> None:
        events.append((event, state))

    return events, on_loop_event


def session_loop_stream_fixture(
    monkeypatch: pytest.MonkeyPatch,
    *,
    provider,
    cap: int = 2,
    user_id: str = "u3",
) -> tuple[SessionTerminal, SessionState]:
    """Port of test_injection_sanitiser streaming cap test via _run_session_loop."""
    import loop as loop_mod

    params = _base_params(user_id=user_id)
    state = initial_session_state(
        messages=[{"role": "user", "content": "hi"}],
        chain_budget=ToolChainBudget(cap=cap),
    )
    return loop_mod._finish_session_loop(
        loop_mod._run_session_loop(
            state,
            params,
            provider=provider,
            stream=True,
        )
    )


def test_tool_round_atomic_replace_no_assistant_only_state(monkeypatch: pytest.MonkeyPatch) -> None:
    import loop as loop_mod

    monkeypatch.setattr(
        loop_mod,
        "run_tool",
        lambda name, args, *, user_id, auto_rag_point_ids=None: json.dumps({"t": name}),
    )

    collector, on_loop_event = _loop_collector()
    n_start = 1

    class StubProvider:
        def chat(self, messages, tools=None, system=None, max_tokens=None):
            return LLMResponse(
                text="",
                tool_calls=[LLMToolCall("1", "search_files", {"q": 1})],
                stop_reason="tool_calls",
            )

    params = _base_params()
    state = initial_session_state(
        messages=[{"role": "user", "content": "hi"}],
        chain_budget=None,
    )
    loop_mod._finish_session_loop(
        loop_mod._run_session_loop(
            state,
            params,
            provider=StubProvider(),
            stream=False,
            on_loop_event=on_loop_event,
        )
    )

    for event, snap in collector:
        if event == SessionLoopEvent.SITE_TOOL_DISPATCH:
            assert len(snap.messages) >= n_start + 2
        elif event == SessionLoopEvent.SITE_MODEL_CALL and snap.transition == TransitionReason.MODEL_CALL:
            if snap.terminal is None and len(snap.messages) == n_start + 1:
                assert any(m.get("role") == "tool" for m in snap.messages)


def test_tool_round_single_replace_message_count(monkeypatch: pytest.MonkeyPatch) -> None:
    import loop as loop_mod

    monkeypatch.setattr(
        loop_mod,
        "run_tool",
        lambda name, args, *, user_id, auto_rag_point_ids=None: "{}",
    )

    class StubProvider:
        _calls = 0

        def chat(self, messages, tools=None, system=None, max_tokens=None):
            self._calls += 1
            if self._calls == 1:
                return LLMResponse(
                    text="",
                    tool_calls=[LLMToolCall("1", "search_files", {"q": 1})],
                    stop_reason="tool_calls",
                )
            return LLMResponse(text="done", tool_calls=[], stop_reason="stop")

    params = _base_params()
    state = initial_session_state(
        messages=[{"role": "user", "content": "hi"}],
        chain_budget=None,
    )
    n = len(state.messages)
    collector, on_loop_event = _loop_collector()
    loop_mod._finish_session_loop(
        loop_mod._run_session_loop(
            state,
            params,
            provider=StubProvider(),
            stream=False,
            on_loop_event=on_loop_event,
        )
    )
    dispatch_states = [s for ev, s in collector if ev == SessionLoopEvent.SITE_TOOL_DISPATCH]
    assert len(dispatch_states) == 1
    assert len(dispatch_states[0].messages) == n + 2


def test_multi_tool_round_single_replace(monkeypatch: pytest.MonkeyPatch) -> None:
    import loop as loop_mod

    monkeypatch.setattr(
        loop_mod,
        "run_tool",
        lambda name, args, *, user_id, auto_rag_point_ids=None: "{}",
    )

    class StubProvider:
        _calls = 0

        def chat(self, messages, tools=None, system=None, max_tokens=None):
            self._calls += 1
            if self._calls == 1:
                return LLMResponse(
                    text="",
                    tool_calls=[
                        LLMToolCall("1", "search_files", {"q": 1}),
                        LLMToolCall("2", "read_file", {"path": "a"}),
                        LLMToolCall("3", "search_files", {"q": 2}),
                    ],
                    stop_reason="tool_calls",
                )
            return LLMResponse(text="done", tool_calls=[], stop_reason="stop")

    params = _base_params()
    state = initial_session_state(
        messages=[{"role": "user", "content": "hi"}],
        chain_budget=None,
    )
    n = len(state.messages)
    collector, on_loop_event = _loop_collector()
    loop_mod._finish_session_loop(
        loop_mod._run_session_loop(
            state,
            params,
            provider=StubProvider(),
            stream=False,
            on_loop_event=on_loop_event,
        )
    )
    dispatch_states = [s for ev, s in collector if ev == SessionLoopEvent.SITE_TOOL_DISPATCH]
    assert len(dispatch_states) == 1
    assert len(dispatch_states[0].messages) == n + 4


def test_mid_tool_exception_no_assistant_only_published_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import loop as loop_mod

    def run_tool_side_effect(name, args, *, user_id, auto_rag_point_ids=None):
        if name == "read_file":
            raise RuntimeError("tool boom")
        return "{}"

    monkeypatch.setattr(loop_mod, "run_tool", run_tool_side_effect)

    class StubProvider:
        def chat(self, messages, tools=None, system=None, max_tokens=None):
            return LLMResponse(
                text="",
                tool_calls=[
                    LLMToolCall("1", "search_files", {"q": 1}),
                    LLMToolCall("2", "read_file", {"path": "x"}),
                ],
                stop_reason="tool_calls",
            )

    params = _base_params()
    state = initial_session_state(
        messages=[{"role": "user", "content": "hi"}],
        chain_budget=None,
    )
    n = len(state.messages)

    with pytest.raises(RuntimeError, match="tool boom"):
        loop_mod._finish_session_loop(
            loop_mod._run_session_loop(
                state, params, provider=StubProvider(), stream=False
            )
        )


def test_transition_reason_model_call_then_tool_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import loop as loop_mod

    monkeypatch.setattr(
        loop_mod,
        "run_tool",
        lambda name, args, *, user_id, auto_rag_point_ids=None: "{}",
    )
    collector, on_loop_event = _loop_collector()

    class StubProvider:
        _calls = 0

        def chat(self, messages, tools=None, system=None, max_tokens=None):
            self._calls += 1
            if self._calls == 1:
                return LLMResponse(
                    text="",
                    tool_calls=[LLMToolCall("1", "search_files", {"q": 1})],
                    stop_reason="tool_calls",
                )
            return LLMResponse(text="done", tool_calls=[], stop_reason="stop")

    params = _base_params()
    state = initial_session_state(
        messages=[{"role": "user", "content": "hi"}],
        chain_budget=None,
    )
    loop_mod._finish_session_loop(
        loop_mod._run_session_loop(
            state,
            params,
            provider=StubProvider(),
            stream=False,
            on_loop_event=on_loop_event,
        )
    )

    transitions = [snap.transition for _, snap in collector if snap.transition is not None]
    assert TransitionReason.MODEL_CALL in transitions
    assert TransitionReason.TOOL_DISPATCH in transitions


def test_sync_stop_reason_stops_without_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    import loop as loop_mod

    calls: list[str] = []

    def capture(name, args, *, user_id, auto_rag_point_ids=None):
        calls.append(name)
        return "{}"

    monkeypatch.setattr(loop_mod, "run_tool", capture)

    class StubProvider:
        def chat(self, messages, tools=None, system=None, max_tokens=None):
            return LLMResponse(
                text="answer",
                tool_calls=[LLMToolCall("1", "search_files", {"q": 1})],
                stop_reason="stop",
            )

    params = _base_params()
    state = initial_session_state(
        messages=[{"role": "user", "content": "hi"}],
        chain_budget=None,
    )
    n = len(state.messages)
    terminal, final = loop_mod._finish_session_loop(
        loop_mod._run_session_loop(
            state, params, provider=StubProvider(), stream=False
        )
    )
    assert terminal == SessionTerminal.COMPLETED
    assert len(final.messages) == n + 1
    assert final.messages[-1]["content"] == "answer"
    assert calls == []


def test_terminal_completed_no_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    import loop as loop_mod

    class StubProvider:
        def chat(self, messages, tools=None, system=None, max_tokens=None):
            return LLMResponse(text="plain", tool_calls=[], stop_reason="stop")

    params = _base_params()
    state = initial_session_state(
        messages=[{"role": "user", "content": "hi"}],
        chain_budget=None,
    )
    terminal, final = loop_mod._finish_session_loop(
        loop_mod._run_session_loop(
            state, params, provider=StubProvider(), stream=False
        )
    )
    assert terminal == SessionTerminal.COMPLETED
    assert final.messages[-1]["role"] == "assistant"
    assert final.messages[-1]["content"] == "plain"


def test_terminal_max_tool_rounds(monkeypatch: pytest.MonkeyPatch) -> None:
    import loop as loop_mod

    monkeypatch.setattr(
        loop_mod,
        "run_tool",
        lambda name, args, *, user_id, auto_rag_point_ids=None: "{}",
    )

    class StubProvider:
        _calls = 0

        def chat(self, messages, tools=None, system=None, max_tokens=None):
            self._calls += 1
            if tools is not None:
                return LLMResponse(
                    text="",
                    tool_calls=[LLMToolCall("1", "search_files", {"q": self._calls})],
                    stop_reason="tool_calls",
                )
            return LLMResponse(text="forced final", tool_calls=[], stop_reason="stop")

    params = _base_params()
    state = initial_session_state(
        messages=[{"role": "user", "content": "hi"}],
        chain_budget=None,
    )
    terminal, final = loop_mod._finish_session_loop(
        loop_mod._run_session_loop(
            state, params, provider=StubProvider(), stream=False
        )
    )
    assert terminal == SessionTerminal.MAX_TOOL_ROUNDS
    assert final.messages[-1]["content"] == "forced final"
    assert final.messages[-1]["role"] == "assistant"


def test_ask_and_ask_stream_same_message_outcome(monkeypatch: pytest.MonkeyPatch) -> None:
    import loop as loop_mod

    monkeypatch.setattr(
        loop_mod,
        "run_tool",
        lambda name, args, *, user_id, auto_rag_point_ids=None: json.dumps({"n": name}),
    )

    class SharedStub:
        _sync_calls = 0
        _stream_calls = 0

        def chat(self, messages, tools=None, system=None, max_tokens=None):
            self._sync_calls += 1
            if self._sync_calls == 1:
                return LLMResponse(
                    text="",
                    tool_calls=[LLMToolCall("1", "search_files", {"q": 1})],
                    stop_reason="tool_calls",
                )
            return LLMResponse(text="done", tool_calls=[], stop_reason="stop")

        def chat_stream(self, messages, tools=None, system=None, max_tokens=None):
            self._stream_calls += 1
            if self._stream_calls == 1:
                yield LLMEvent(
                    type="tool_call",
                    tool_call=LLMToolCall("1", "search_files", {"q": 1}),
                )
            else:
                yield LLMEvent(type="text", content="done")
            yield LLMEvent(type="end")

    params = _base_params()
    base_messages = [{"role": "user", "content": "hi"}]
    state_sync = initial_session_state(messages=base_messages, chain_budget=None)
    state_stream = initial_session_state(messages=base_messages, chain_budget=None)

    sync_stub = SharedStub()
    stream_stub = SharedStub()

    _, final_sync = loop_mod._finish_session_loop(
        loop_mod._run_session_loop(
            state_sync, params, provider=sync_stub, stream=False
        )
    )
    _, final_stream = loop_mod._finish_session_loop(
        loop_mod._run_session_loop(
            state_stream, params, provider=stream_stub, stream=True
        )
    )

    assert final_sync.messages == final_stream.messages


def test_max_tool_rounds_model_call_count(monkeypatch: pytest.MonkeyPatch) -> None:
    import loop as loop_mod

    monkeypatch.setattr(
        loop_mod,
        "run_tool",
        lambda name, args, *, user_id, auto_rag_point_ids=None: "{}",
    )
    call_count = {"n": 0}

    class StubProvider:
        def chat(self, messages, tools=None, system=None, max_tokens=None):
            call_count["n"] += 1
            if tools is not None:
                return LLMResponse(
                    text="",
                    tool_calls=[LLMToolCall("1", "search_files", {"q": call_count["n"]})],
                    stop_reason="tool_calls",
                )
            return LLMResponse(text="final", tool_calls=[], stop_reason="stop")

    params = _base_params()
    state = initial_session_state(
        messages=[{"role": "user", "content": "hi"}],
        chain_budget=None,
    )
    loop_mod._finish_session_loop(
        loop_mod._run_session_loop(
            state, params, provider=StubProvider(), stream=False
        )
    )
    assert call_count["n"] == 4


def test_frozen_session_state_rejects_assignment() -> None:
    state = initial_session_state(
        messages=[{"role": "user", "content": "x"}],
        chain_budget=None,
    )
    with pytest.raises(FrozenInstanceError):
        state.turn_count = 1  # type: ignore[misc]


def test_tool_chain_cap_still_trips(monkeypatch: pytest.MonkeyPatch) -> None:
    import loop as loop_mod

    calls: list[str] = []

    def capture(name, args, *, user_id, auto_rag_point_ids=None):
        calls.append(name)
        return json.dumps({"t": name})

    monkeypatch.setattr(loop_mod, "run_tool", capture)
    latch: list[str] = []
    monkeypatch.setattr(
        hooks,
        "fire_background",
        lambda ev, **_kw: latch.append(ev),
    )

    class StreamStub:
        def chat_stream(self, messages, tools=None, system=None, max_tokens=None):
            yield LLMEvent(type="tool_call", tool_call=LLMToolCall("1", "search_files", {"q": 1}))
            yield LLMEvent(type="tool_call", tool_call=LLMToolCall("2", "search_files", {"q": 2}))
            yield LLMEvent(type="tool_call", tool_call=LLMToolCall("3", "search_files", {"q": 3}))
            yield LLMEvent(type="end")

    session_loop_stream_fixture(monkeypatch, provider=StreamStub(), cap=2)
    assert len(calls) == 2
    assert Event.TOOL_CHAIN_CAP_TRIPPED in latch


def test_loop_event_ordering_two_tool_rounds(monkeypatch: pytest.MonkeyPatch) -> None:
    import loop as loop_mod

    monkeypatch.setattr(
        loop_mod,
        "run_tool",
        lambda name, args, *, user_id, auto_rag_point_ids=None: "{}",
    )
    collector, on_loop_event = _loop_collector()

    class StubProvider:
        _calls = 0

        def chat(self, messages, tools=None, system=None, max_tokens=None):
            self._calls += 1
            if self._calls == 1:
                return LLMResponse(
                    text="",
                    tool_calls=[LLMToolCall("1", "search_files", {"q": 1})],
                    stop_reason="tool_calls",
                )
            return LLMResponse(text="done", tool_calls=[], stop_reason="stop")

    params = _base_params()
    state = initial_session_state(
        messages=[{"role": "user", "content": "hi"}],
        chain_budget=None,
    )
    loop_mod._finish_session_loop(
        loop_mod._run_session_loop(
            state,
            params,
            provider=StubProvider(),
            stream=False,
            on_loop_event=on_loop_event,
        )
    )

    event_names = [ev for ev, _ in collector]
    assert event_names == [
        SessionLoopEvent.SITE_MODEL_CALL,
        SessionLoopEvent.SITE_TOOL_DISPATCH,
        SessionLoopEvent.SITE_TURN_ADVANCE,
        SessionLoopEvent.SITE_MODEL_CALL,
        SessionLoopEvent.SITE_TERMINATE,
    ]


def test_loop_event_ordering_max_tool_rounds(monkeypatch: pytest.MonkeyPatch) -> None:
    import loop as loop_mod

    monkeypatch.setattr(
        loop_mod,
        "run_tool",
        lambda name, args, *, user_id, auto_rag_point_ids=None: "{}",
    )
    collector, on_loop_event = _loop_collector()

    class StubProvider:
        _calls = 0

        def chat(self, messages, tools=None, system=None, max_tokens=None):
            self._calls += 1
            if tools is not None:
                return LLMResponse(
                    text="",
                    tool_calls=[LLMToolCall("1", "search_files", {"q": self._calls})],
                    stop_reason="tool_calls",
                )
            return LLMResponse(text="forced", tool_calls=[], stop_reason="stop")

    params = _base_params()
    state = initial_session_state(
        messages=[{"role": "user", "content": "hi"}],
        chain_budget=None,
    )
    loop_mod._finish_session_loop(
        loop_mod._run_session_loop(
            state,
            params,
            provider=StubProvider(),
            stream=False,
            on_loop_event=on_loop_event,
        )
    )

    event_names = [ev for ev, _ in collector]
    assert event_names[-2:] == [
        SessionLoopEvent.SITE_MODEL_CALL,
        SessionLoopEvent.SITE_TERMINATE,
    ]
    assert event_names.count(SessionLoopEvent.SITE_MODEL_CALL) == 4
    assert event_names.count(SessionLoopEvent.SITE_TOOL_DISPATCH) == 3
    assert event_names.count(SessionLoopEvent.SITE_TURN_ADVANCE) == 3
