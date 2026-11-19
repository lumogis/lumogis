# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Speech-model readiness: check, install, single flight, backoff (LUM-995).

The fake installer fails the way the real adapter does: ``model_installed`` returns
``False`` for a missing model (Speaches answers 404), and raises ``SttUnreachable`` for
a dead engine; ``install_model`` raises ``SttModelUnknown`` for an id the engine does not
know. Nothing here touches the network or starts the app lifespan.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from pathlib import Path
from typing import Callable

import httpx
import pytest
from models.api_v1 import AdminDiagnosticsSpeechToText
from ports.speech_to_text import SttModelUnknown
from ports.speech_to_text import SttUnreachable

import config
from services import stt_model_readiness as rd

_MODEL = "Systran/faster-whisper-base.en"


class FakeInstaller:
    """A SpeechModelInstaller + SpeechToText stand-in with scripted answers."""

    def __init__(self, installed: list | None = None, install: Callable | None = None) -> None:
        self._installed = list(installed or [])
        self._install = install
        self.installed_calls = 0
        self.install_calls = 0
        self.models: list[str] = []

    def ping(self) -> bool:  # pragma: no cover - not used by readiness
        return True

    def transcribe(self, *_a, **_k):  # pragma: no cover
        raise AssertionError

    def model_installed(self, model: str) -> bool:
        self.installed_calls += 1
        self.models.append(model)
        if not self._installed:
            return True
        ans = self._installed.pop(0) if len(self._installed) > 1 else self._installed[0]
        if isinstance(ans, BaseException):
            raise ans
        if callable(ans):
            return ans()
        return ans

    def install_model(self, model: str) -> None:
        self.install_calls += 1
        self.models.append(model)
        if self._install:
            self._install()


@pytest.fixture(autouse=True)
def _fresh(monkeypatch: pytest.MonkeyPatch):
    rd._reset_for_tests()
    monkeypatch.setenv("STT_BACKEND", "whisper_sidecar")
    monkeypatch.setenv("STT_MODEL", _MODEL)
    monkeypatch.delenv("STT_MODEL_INSTALL_TIMEOUT_SEC", raising=False)
    yield
    _join()
    rd._reset_for_tests()


def _use(monkeypatch: pytest.MonkeyPatch, fake) -> None:
    monkeypatch.setattr(config, "get_speech_to_text", lambda: fake)


def _join(timeout: float = 5.0) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        with rd._lock:
            if rd._worker is None:
                return
        time.sleep(0.01)
    raise AssertionError("worker did not finish")


def _wait(cond: Callable[[], bool], timeout: float = 5.0) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return
        time.sleep(0.01)
    raise AssertionError("condition not reached")


def test_installed_model_is_ready_without_post(monkeypatch):
    fake = FakeInstaller(installed=[True])
    _use(monkeypatch, fake)
    assert rd.ensure_model_sync() == "ready"
    assert fake.install_calls == 0


def test_missing_model_is_installed_then_verified(monkeypatch):
    fake = FakeInstaller(installed=[False, True])
    _use(monkeypatch, fake)
    assert rd.ensure_model_sync() == "ready"
    assert fake.install_calls == 1


def test_post_ok_but_still_missing_is_failed(monkeypatch):
    """Speaches #587: the install call can succeed with the model absent."""
    fake = FakeInstaller(installed=[False, False])
    _use(monkeypatch, fake)
    assert rd.ensure_model_sync() == "failed"
    assert rd._next_attempt_at > time.monotonic()


def test_unreachable_state_and_backoff_grows(monkeypatch):
    fake = FakeInstaller(installed=[SttUnreachable("down")])
    _use(monkeypatch, fake)
    waits = []
    for _ in range(8):
        assert rd.ensure_model_sync() == "unreachable"
        waits.append(rd._next_attempt_at - time.monotonic())
    assert 28 < waits[0] <= 30
    assert 58 < waits[1] <= 60  # doubles
    assert max(waits) <= 900  # capped
    assert waits[-1] > 800


def test_unknown_model_id_from_engine(monkeypatch):
    def boom():
        raise SttModelUnknown("nope")

    fake = FakeInstaller(installed=[False], install=boom)
    _use(monkeypatch, fake)
    assert rd.ensure_model_sync() == "unknown_model"
    assert rd._next_attempt_at - time.monotonic() > 800  # 15 minutes, not 30 s


@pytest.mark.parametrize("bad", ["../x", "a/b/c", "a?b", "a b", "a/../b", "-x", "a" * 101])
def test_bad_model_id_makes_no_http_call(monkeypatch, bad, caplog):
    fake = FakeInstaller(installed=[True])
    _use(monkeypatch, fake)
    monkeypatch.setenv("STT_MODEL", bad)
    caplog.set_level(logging.INFO)
    assert rd.ensure_model_sync() == "unknown_model"
    assert fake.installed_calls == 0 and fake.install_calls == 0


def test_bad_model_id_error_names_the_shape(monkeypatch, caplog):
    _use(monkeypatch, FakeInstaller())
    monkeypatch.setenv("STT_MODEL", "../x")
    caplog.set_level(logging.INFO)
    rd.ensure_model_sync()
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1 and "owner/name" in errors[0].getMessage()


def test_kick_is_single_flight(monkeypatch):
    gate = threading.Event()
    entered = threading.Event()

    def blocked() -> bool:
        entered.set()
        gate.wait(5)
        return True

    fake = FakeInstaller(installed=[blocked])
    _use(monkeypatch, fake)
    assert rd.kick(force=True) is True
    assert entered.wait(5)
    assert rd.kick(force=True) is False
    gate.set()
    _join()
    assert fake.installed_calls == 1 and fake.install_calls == 0


def test_kick_respects_backoff_unless_forced(monkeypatch):
    fake = FakeInstaller(installed=[SttUnreachable("down")])
    _use(monkeypatch, fake)
    rd.ensure_model_sync()
    before = fake.installed_calls
    assert rd.kick() is False  # inside the backoff window
    assert fake.installed_calls == before
    assert rd.kick(force=True) is True
    _join()
    assert fake.installed_calls == before + 1


def test_kick_right_after_publish_is_not_lost(monkeypatch):
    """A miss that lands while the worker is checking is seen by one more run."""
    gate = threading.Event()
    entered = threading.Event()
    calls = []

    def first_check() -> bool:
        calls.append(1)
        if len(calls) == 1:
            entered.set()
            gate.wait(5)
        return True

    fake = FakeInstaller(installed=[first_check])
    _use(monkeypatch, fake)
    assert rd.kick(force=True)
    assert entered.wait(5)
    rd.note_model_missing()  # worker is busy: counted, not started
    gate.set()
    _join()
    assert fake.installed_calls == 2
    assert rd.state() == "ready"
    # and a kick after the publish starts a fresh worker
    assert rd.kick(force=True) is True
    _join()
    assert fake.installed_calls == 3


def test_miss_in_failed_does_not_force_install(monkeypatch):
    fake = FakeInstaller(installed=[False, False])
    _use(monkeypatch, fake)
    rd.ensure_model_sync()  # failed, backoff set
    before = (fake.installed_calls, fake.install_calls)
    for _ in range(3):
        rd.note_model_missing()
    assert rd._worker is None
    assert (fake.installed_calls, fake.install_calls) == before


def test_note_model_missing_keeps_state_until_checked(monkeypatch):
    gate = threading.Event()
    entered = threading.Event()
    answers = [True]

    def check() -> bool:
        if answers:
            answers.pop()
            return True
        entered.set()
        gate.wait(5)
        return True

    fake = FakeInstaller(installed=[check])
    _use(monkeypatch, fake)
    assert rd.ensure_model_sync() == "ready"
    rd.note_model_missing()
    assert entered.wait(5)
    assert rd.state() == "ready"  # not flipped to "installing" by the miss itself
    gate.set()
    _join()


def test_install_read_timeout_floors_backoff(monkeypatch):
    monkeypatch.setenv("STT_MODEL_INSTALL_TIMEOUT_SEC", "600")

    def slow():
        raise httpx.ReadTimeout("slow")

    fake = FakeInstaller(installed=[False], install=slow)
    _use(monkeypatch, fake)
    assert rd.ensure_model_sync() == "failed"
    assert rd._next_attempt_at - time.monotonic() >= 598


def test_note_model_missing_after_ready_reinstalls(monkeypatch):
    present = {"v": True}

    fake = FakeInstaller(
        installed=[lambda: present["v"]],
        install=lambda: present.update(v=True),
    )
    _use(monkeypatch, fake)
    assert rd.ensure_model_sync() == "ready"
    present["v"] = False  # a volume reset
    rd.note_model_missing()
    _join()
    assert fake.install_calls == 1
    assert rd.state() == "ready"


def test_note_model_missing_does_not_force_unknown_model(monkeypatch):
    def boom():
        raise SttModelUnknown("nope")

    fake = FakeInstaller(installed=[False], install=boom)
    _use(monkeypatch, fake)
    rd.ensure_model_sync()
    assert rd.state() == "unknown_model"
    before = fake.install_calls
    rd.note_model_missing()
    assert rd._worker is None and fake.install_calls == before


def test_click_during_download_does_not_log_the_hint(monkeypatch, caplog):
    """A miss while the first install runs is the normal case, not a wrong path."""
    state = {"installed": False}
    gate = threading.Event()
    entered = threading.Event()

    def install():
        entered.set()
        gate.wait(5)
        state["installed"] = True

    fake = FakeInstaller(installed=[lambda: state["installed"]], install=install)
    _use(monkeypatch, fake)
    caplog.set_level(logging.INFO)
    assert rd.kick(force=True)
    assert entered.wait(5)
    rd.note_model_missing()
    gate.set()
    _join()
    assert rd.state() == "ready"
    assert not [r for r in caplog.records if "stt_404_with_model_installed" in r.getMessage()]
    assert fake.install_calls == 1


def test_miss_during_a_failed_install_does_not_cause_a_false_hint(monkeypatch, caplog):
    """A click during a download that then times out must not read as a wrong path later."""
    gate = threading.Event()
    entered = threading.Event()
    state = {"installed": False}

    def slow_install():
        entered.set()
        gate.wait(5)
        raise httpx.ReadTimeout("slow")

    fake = FakeInstaller(installed=[lambda: state["installed"]], install=slow_install)
    _use(monkeypatch, fake)
    assert rd.kick(force=True)
    assert entered.wait(5)
    rd.note_model_missing()
    gate.set()
    _join()
    assert rd.state() == "failed"
    state["installed"] = True  # Speaches finished downloading in the background
    caplog.set_level(logging.INFO)
    assert rd.kick(force=True)
    _join()
    assert rd.state() == "ready"
    assert not [r for r in caplog.records if "stt_404_with_model_installed" in r.getMessage()]


def test_miss_with_model_installed_logs_hint(monkeypatch, caplog):
    fake = FakeInstaller(installed=[True])
    _use(monkeypatch, fake)
    assert rd.ensure_model_sync() == "ready"
    caplog.set_level(logging.INFO)
    rd.note_model_missing()
    _join()
    assert rd.state() == "ready" and fake.install_calls == 0
    hints = [r for r in caplog.records if "stt_404_with_model_installed" in r.getMessage()]
    assert len(hints) == 1 and hints[0].levelno == logging.WARNING


def test_state_never_raises(monkeypatch):
    def broken():
        raise RuntimeError("Invalid STT_SIDECAR_URL")

    monkeypatch.setattr(config, "get_speech_to_text", broken)
    assert rd.state() == "failed"


def test_start_without_scheduler_only_kicks(monkeypatch):
    fake = FakeInstaller(installed=[True])
    _use(monkeypatch, fake)
    rd.start(None)
    _join()
    assert rd.state() == "ready"


class _Scheduler:
    def __init__(self) -> None:
        self.added: list[dict] = []
        self.removed: list[str] = []

    def add_job(self, func, **kw):
        self.added.append({"func": func, **kw})

    def remove_job(self, job_id):  # pragma: no cover - must not be called
        self.removed.append(job_id)


def test_not_applicable_for_none_and_fake(monkeypatch):
    sched = _Scheduler()
    for backend in ("none", "fake_stt"):
        monkeypatch.setenv("STT_BACKEND", backend)
        assert rd.state() == "not_applicable"
        rd.start(sched)
        assert rd._worker is None
    assert sched.added == []


def test_not_applicable_for_a_non_installer_adapter(monkeypatch):
    class Plain:
        def ping(self):
            return True

    _use(monkeypatch, Plain())
    assert rd.state() == "not_applicable"
    assert rd.kick(force=True) is False


def test_retry_job_stays_registered_after_ready(monkeypatch):
    fake = FakeInstaller(installed=[True])
    _use(monkeypatch, fake)
    sched = _Scheduler()
    rd.start(sched)
    _join()
    assert rd.state() == "ready"
    assert len(sched.added) == 1 and sched.removed == []
    job = sched.added[0]
    assert job["id"] == "stt_model_readiness_retry"
    assert job["max_instances"] == 1 and job["seconds"] == 30
    before = fake.installed_calls
    rd._retry_tick()
    assert rd._worker is None and fake.installed_calls == before  # ready: a lock and a compare
    with rd._lock:
        rd._state = "failed"
        rd._next_attempt_at = 0.0
    rd._retry_tick()
    _join()
    assert fake.installed_calls == before + 1
    assert sched.removed == []


def test_state_change_logged_once(monkeypatch, caplog):
    fake = FakeInstaller(installed=[SttUnreachable("down")])
    _use(monkeypatch, fake)
    caplog.set_level(logging.INFO)
    for _ in range(3):
        assert rd.ensure_model_sync() == "unreachable"
    warns = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "state=unreachable" in r.getMessage()
    ]
    assert len(warns) == 1
    fake._installed = [True]
    assert rd.ensure_model_sync() == "ready"
    infos = [
        r for r in caplog.records if r.levelno == logging.INFO and "state=ready" in r.getMessage()
    ]
    assert len(infos) == 1


def test_a_failed_retry_does_not_log_installing_every_attempt(monkeypatch, caplog):
    fake = FakeInstaller(installed=[False, False])
    _use(monkeypatch, fake)
    caplog.set_level(logging.INFO)
    for _ in range(3):
        assert rd.ensure_model_sync() == "failed"
    msgs = [r.getMessage() for r in caplog.records if "stt_model_state" in r.getMessage()]
    assert sum("state=failed" in m for m in msgs) == 1
    assert sum("state=installing" in m for m in msgs) == 1  # only the first attempt


def test_model_state_literals_match():
    import typing

    field = AdminDiagnosticsSpeechToText.model_fields["model_state"].annotation
    inner = [a for a in typing.get_args(field) if a is not type(None)][0]
    assert set(typing.get_args(inner)) == set(typing.get_args(rd.ModelState))


def test_readiness_module_imports_no_adapters():
    src = Path(rd.__file__).read_text()
    assert not re.search(r"^\s*(from|import)\s+adapters\b", src, re.M)
