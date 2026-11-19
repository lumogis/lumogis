# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Is the configured speech model installed in the sidecar? If not, install it once (LUM-995).

Speaches 0.8 answers ``/health`` before any model exists and does not install one by
itself, so a clean Release install would have a running sidecar that rejects every
transcription. Core owns the install: one attempt at start-up, a retry job with
backoff, and an on-demand install when a transcription finds the model missing.

Design rules (the plan's "Locking rule"):

* **One process only.** State is module-level because Core refuses to boot with more
  than one worker (LUM-832); a multi-worker Core would run one install per worker.
* Every read and write of the module state takes ``_lock``; **no HTTP call runs under
  it**. A worker does its calls unlocked, then *publishes* the result and clears
  ``_worker`` in one critical section. ``kick()`` treats "busy" as
  ``_worker is not None``, so a kick right after a publish is never lost.
* A transcription that finds the model missing calls :func:`note_model_missing`. It
  never changes the state itself: the state moves only when the sidecar answers
  ``GET /v1/models/{id}``.
* Services never import adapters: the adapter is reached through
  ``config.get_speech_to_text()`` and recognised by the :class:`SpeechModelInstaller`
  protocol.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Literal

import httpx
from ports.speech_to_text import SpeechModelInstaller
from ports.speech_to_text import SttModelUnknown
from ports.speech_to_text import SttUnreachable

import config

_log = logging.getLogger(__name__)

ModelState = Literal[
    "not_applicable",
    "unknown",
    "installing",
    "ready",
    "unreachable",
    "unknown_model",
    "failed",
]

# ``owner/name`` or ``name``: letters, digits, ``.``, ``_``, ``-``; no ``..`` segment.
_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}(/[A-Za-z0-9][A-Za-z0-9._-]{0,99})?$")

_BACKOFF_BASE_SEC = 30
_BACKOFF_CAP_SEC = 900
_RETRY_INTERVAL_SEC = 30

_lock = threading.Lock()
_state: ModelState = "unknown"
_worker: threading.Thread | None = None
_failures = 0
_next_attempt_at = 0.0  # time.monotonic()
_last_logged_state: str | None = None
_miss_gen = 0  # incremented by every note_model_missing()
_miss_pending = False  # a miss arrived since a run last looked
_hint_logged = False
_resolve_error_logged = False


@dataclass
class _Outcome:
    state: ModelState
    detail: str = ""
    gen: int = 0  # _miss_gen seen right before the deciding check
    hint: bool = False  # a miss was pending, yet the model was already installed
    timed_out: bool = False  # the install call hit its read timeout


def _reset_for_tests() -> None:
    """Clear module state (tests only)."""
    global _state, _worker, _failures, _next_attempt_at, _last_logged_state
    global _miss_gen, _miss_pending, _hint_logged, _resolve_error_logged
    with _lock:
        _state = "unknown"
        _worker = None
        _failures = 0
        _next_attempt_at = 0.0
        _last_logged_state = None
        _miss_gen = 0
        _miss_pending = False
        _hint_logged = False
        _resolve_error_logged = False


def _applicable() -> bool:
    """True when the wired backend is a sidecar whose model Core can manage. May raise."""
    if config.get_stt_backend() != "whisper_sidecar":
        return False
    return isinstance(config.get_speech_to_text(), SpeechModelInstaller)


def state() -> ModelState:
    """Current model state. Never raises: a config error reads as ``failed``."""
    global _resolve_error_logged
    try:
        if not _applicable():
            return "not_applicable"
    except Exception as exc:
        with _lock:
            first = not _resolve_error_logged
            _resolve_error_logged = True
        if first:
            _log.error("event=stt_model_state state=failed detail=%s", f"cannot resolve STT: {exc}")
        return "failed"
    with _lock:
        return _state


def _log_state(new: str, model: str, detail: str) -> None:
    """One line per state change, never per tick. Caller holds ``_lock``."""
    global _last_logged_state
    if new == "installing" and _last_logged_state in ("failed", "unreachable", "unknown_model"):
        return  # a retry's "installing" would log every attempt; the result logs once
    if new == _last_logged_state:
        return
    _last_logged_state = new
    msg = "event=stt_model_state state=%s model=%s detail=%s"
    if new == "unknown_model":
        _log.error(msg, new, model, detail)
    elif new in ("failed", "unreachable"):
        _log.warning(msg, new, model, detail)
    else:
        _log.info(msg, new, model, detail)


def _delay_for(failures: int, state: str, timed_out: bool) -> float:
    if state == "unknown_model":
        return float(_BACKOFF_CAP_SEC)
    delay = float(min(_BACKOFF_BASE_SEC * 2 ** max(failures - 1, 0), _BACKOFF_CAP_SEC))
    if timed_out:
        # Speaches keeps downloading after Core gives up; a second POST 30 s later
        # would race the first.
        try:
            delay = max(delay, float(config.get_stt_model_install_timeout_sec()))
        except Exception:
            delay = max(delay, 900.0)
    return delay


def _snapshot() -> tuple[int, bool]:
    """Read the miss counter and consume the pending flag, right before a check."""
    global _miss_pending
    with _lock:
        pending, _miss_pending = _miss_pending, False
        return _miss_gen, pending


def _set_installing(model: str) -> None:
    global _state
    with _lock:
        _state = "installing"
        _log_state("installing", model, "downloading the speech model")


def _run_cycle(model: str) -> _Outcome:
    """One check-and-install pass. Makes the HTTP calls; holds no lock while doing so."""
    if not _MODEL_ID_RE.match(model) or ".." in model.split("/"):
        return _Outcome(
            "unknown_model",
            f"STT_MODEL {model!r} is not a valid model id; use owner/name "
            "(letters, digits, '.', '_', '-')",
        )
    try:
        adapter = config.get_speech_to_text()
        if not isinstance(adapter, SpeechModelInstaller):
            return _Outcome("failed", "the speech engine cannot install models")
        gen, pending = _snapshot()
        if adapter.model_installed(model):
            return _Outcome("ready", gen=gen, hint=pending)
        _set_installing(model)
        adapter.install_model(model)
        gen, _ = _snapshot()
        if adapter.model_installed(model):
            return _Outcome("ready", gen=gen)
        # Speaches #587: the install call can report success with the model absent.
        return _Outcome("failed", "the install call succeeded but the model is still absent")
    except SttUnreachable as exc:
        return _Outcome("unreachable", str(exc))
    except SttModelUnknown:
        return _Outcome(
            "unknown_model", f"STT_MODEL {model!r} is not a model the speech engine knows"
        )
    except httpx.ReadTimeout:
        return _Outcome("failed", "the model download did not finish in time", timed_out=True)
    except Exception as exc:
        # ValueError from the adapter carries the sidecar's status and body head.
        _log.exception("event=stt_model_install_error model=%s", model)
        return _Outcome("failed", str(exc)[:300])


def _publish_locked(out: _Outcome, model: str, may_rerun: bool) -> bool:
    """Apply an outcome. Returns True when the worker should run once more."""
    global _state, _failures, _next_attempt_at, _hint_logged, _miss_pending
    if out.state == "ready":
        _state = "ready"
        _failures = 0
        _next_attempt_at = 0.0
        _log_state("ready", model, "the speech model is installed")
        if out.hint and not _hint_logged:
            _hint_logged = True
            _log.warning(
                "event=stt_404_with_model_installed model=%s "
                'hint="check STT_SIDECAR_TRANSCRIBE_PATH and STT_MODEL"',
                model,
            )
        if _miss_gen != out.gen and may_rerun:
            return True  # a miss arrived after the deciding check (e.g. a volume reset)
        _miss_pending = False
        return False
    _hint_logged = False
    # The miss that may be pending is already answered by this failure and its backoff;
    # left set, the retry would read an installed model as a "wrong transcribe path".
    _miss_pending = False
    _state = out.state  # type: ignore[assignment]
    _failures += 1
    _next_attempt_at = time.monotonic() + _delay_for(_failures, out.state, out.timed_out)
    _log_state(out.state, model, out.detail)
    return False


def _ensure(*, clear_worker: bool) -> ModelState:
    global _worker
    model = config.get_stt_model()
    reran = False
    while True:
        out = _run_cycle(model)
        with _lock:
            rerun = _publish_locked(out, model, may_rerun=not reran)
            if not rerun and clear_worker:
                _worker = None  # last act: a kick after this publish is never lost
            result: ModelState = _state
        if not rerun:
            return result
        reran = True


def ensure_model_sync() -> ModelState:
    """Check the model and install it when missing; blocking (tests and the worker)."""
    try:
        if not _applicable():
            return "not_applicable"
    except Exception:
        return state()
    return _ensure(clear_worker=False)


def _worker_main() -> None:
    global _worker, _state
    try:
        _ensure(clear_worker=True)
    except Exception:
        _log.exception("event=stt_model_worker_error")
        with _lock:
            _state = "failed"
    finally:
        with _lock:
            if _worker is threading.current_thread():
                _worker = None


def kick(*, force: bool = False) -> bool:
    """Start a background check-and-install unless one is running. Non-blocking."""
    global _worker
    try:
        if not _applicable():
            return False
    except Exception:
        pass  # a config error: let the worker record it as ``failed`` with a backoff
    with _lock:
        if _worker is not None:
            return False
        if _state == "ready" and not force:
            return False
        if not force and time.monotonic() < _next_attempt_at:
            return False
        thread = threading.Thread(target=_worker_main, name="stt-model-install", daemon=True)
        _worker = thread
    try:
        thread.start()
    except Exception:
        with _lock:
            if _worker is thread:
                _worker = None
        _log.exception("event=stt_model_thread_start_failed")
        return False
    return True


def note_model_missing() -> None:
    """A transcription found the model missing. Starts an install; never waits for it.

    Changes no state: it moves only when the sidecar answers the model check.
    """
    global _miss_gen, _miss_pending
    with _lock:
        _miss_pending = True
        _miss_gen += 1
        st = _state
    # Force only the first miss after a working model (e.g. a volume reset). Any other
    # state is governed by the backoff and the retry job, so repeated clicks do not each
    # start an install POST.
    kick(force=st in ("ready", "unknown"))


def _retry_tick() -> None:
    kick()


def start(scheduler) -> None:
    """Start-up hook: one attempt now, plus a retry job for the life of the process."""
    if state() == "not_applicable":
        return
    kick(force=True)
    if scheduler:
        scheduler.add_job(
            _retry_tick,
            trigger="interval",
            seconds=_RETRY_INTERVAL_SEC,
            id="stt_model_readiness_retry",
            name="Speech model readiness retry",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
            misfire_grace_time=30,
        )
        _log.info(
            "STT model readiness retry job registered (every %d seconds)", _RETRY_INTERVAL_SEC
        )
