# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Turn the knowledge graph on and off from Admin (LUM-989).

Core **decides and records**; it never runs ``docker``. :func:`enable` and
:func:`disable` check that the action is possible (install kind, the graph state,
memory, consent), write the install folder's ``.env`` through
:mod:`services.host_env`, record the operation, and return. :func:`run` then asks
the stack-control sidecar to start or stop the containers, after the HTTP answer
has gone out, because the sequence ends by recreating Core itself.

Whether an action is available is computed once, server-side, by
:func:`services.graph_status.build_graph_status` (the ADR 267 state machine stays
the single source); this module asks it rather than re-deriving.

The toggle record is four ``app_settings`` keys owned by this module:
``graph_toggle_op`` (``start`` | ``stop``), ``graph_toggle_started_at``,
``graph_toggle_failed_at`` and ``graph_toggle_failed_code``. ``.env`` keys written
by Turn on/off are listed in the LUM-989 plan; nothing else may write them.
"""

from __future__ import annotations

import json
import logging
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from pathlib import Path
from typing import Callable
from typing import Literal

import httpx
import settings_store
import yaml

import config
from services import compose_freshness
from services import host_env
from services import stack_control

_log = logging.getLogger(__name__)

InstallKind = Literal["release", "checkout", "unknown"]
ToggleOp = Literal["start", "stop"]

COMPOSE_PATH = Path("/project/docker-compose.yml")
GRAPH_PROFILE = "graph"
GRAPH_SERVICE = "lumogis-graph"
GRAPH_URL = "http://lumogis-graph:8001"
FALKORDB_URL = "redis://falkordb:6379"

# A start waits for the image pull (stack-control allows 25 minutes), the
# recreate, FalkorDB's warm-up and Core's registry discovery (up to 5 minutes); a
# stop is a few container removals. Both windows must outlast the sidecar's own
# budget, or the page would report "off" while the sequence is still running.
START_WINDOW = timedelta(minutes=35)
STOP_WINDOW = timedelta(minutes=10)

# When this Core process started. A stop is over once Core has been recreated
# after it began (the last step of the sequence), see graph_status.derive_state.
CORE_STARTED_AT = datetime.now(timezone.utc)

# Core's background call to stack-control: the pull step (1500 s) plus the rest.
RUN_TIMEOUT_SEC = 1800.0
HEALTH_TIMEOUT_SEC = 1.0
HEALTH_TTL_SEC = 30.0

KEY_OP = "graph_toggle_op"
KEY_STARTED = "graph_toggle_started_at"
KEY_FAILED_AT = "graph_toggle_failed_at"
KEY_FAILED_CODE = "graph_toggle_failed_code"
_RECORD_KEYS = (KEY_OP, KEY_STARTED, KEY_FAILED_AT, KEY_FAILED_CODE)

_FAILED_CODES = ("compose_failed", "timeout", "unreachable")

_lock = threading.Lock()


# --------------------------------------------------------------------- outcomes


class ToggleError(Exception):
    """Base for the refusals :func:`enable` and :func:`disable` raise."""


class Blocked(ToggleError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class NeedsConfirmation(ToggleError):
    pass


class Busy(ToggleError):
    pass


# ----------------------------------------------------------------------- record


@dataclass(frozen=True)
class ToggleRecord:
    """The last Turn on / Turn off and, if it failed, when and how."""

    op: ToggleOp | None = None
    started_at: datetime | None = None
    failed_at: datetime | None = None
    failed_code: str | None = None

    def window(self) -> timedelta:
        return STOP_WINDOW if self.op == "stop" else START_WINDOW

    def failed(self) -> bool:
        """A failure newer than the operation's start."""
        return bool(
            self.op
            and self.failed_at is not None
            and self.started_at is not None
            and self.failed_at >= self.started_at
        )

    def in_window(self, now: datetime) -> bool:
        """The operation started inside its window and has not failed."""
        return bool(
            self.op
            and self.started_at is not None
            and not self.failed()
            and timedelta(0) <= now - self.started_at < self.window()
        )


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


_RECORD_SQL = "SELECT key, value FROM app_settings WHERE key = ANY(%s)"


def _fetch_record_values() -> dict[str, str]:
    """The stored record values by key (absent keys missing). Raises on a store error."""
    rows = config.get_metadata_store().fetch_all(_RECORD_SQL, (list(_RECORD_KEYS),))
    return {r.get("key"): r.get("value") or "" for r in rows or []}


def read_record() -> ToggleRecord:
    """The saved record; an unreadable store reads as "nothing happened"."""
    try:
        values = _fetch_record_values()
    except Exception:  # noqa: BLE001 - status must render without the store
        _log.debug("graph_toggle: could not read the toggle record", exc_info=True)
        return ToggleRecord()
    op = values.get(KEY_OP)
    code = values.get(KEY_FAILED_CODE)
    return ToggleRecord(
        op=op if op in ("start", "stop") else None,
        started_at=_parse_dt(values.get(KEY_STARTED)),
        failed_at=_parse_dt(values.get(KEY_FAILED_AT)),
        failed_code=code if code in _FAILED_CODES else None,
    )


def _save(updates: dict[str, str]) -> None:
    settings_store.put_settings(config.get_metadata_store(), updates)


def _raw_record() -> dict[str, str]:
    """The four record values as stored (``""`` when absent), to restore on failure."""
    try:
        values = _fetch_record_values()
    except Exception:  # noqa: BLE001
        return {k: "" for k in _RECORD_KEYS}
    return {k: values.get(k, "") for k in _RECORD_KEYS}


def _begin_then(op: ToggleOp, now: datetime, write_env: Callable[[], None]) -> None:
    """Record the operation, then write ``.env``; undo the record if the write fails.

    Record first: a failure to record then changes nothing, instead of leaving
    ``.env`` switched with no record and no sequence running.
    """
    previous = _raw_record()
    _begin(op, now)
    try:
        write_env()
    except BaseException:
        try:
            _save(previous)
        except Exception:  # noqa: BLE001
            _log.exception("graph_toggle: could not restore the toggle record")
        raise


def _begin(op: ToggleOp, now: datetime) -> None:
    # A new operation clears the old failure, so a Retry never reads as failed.
    _save(
        {
            KEY_OP: op,
            KEY_STARTED: now.isoformat(),
            KEY_FAILED_AT: "",
            KEY_FAILED_CODE: "",
        }
    )


def _record_failure(code: str, now: datetime) -> None:
    _save({KEY_FAILED_AT: now.isoformat(), KEY_FAILED_CODE: code})


# ----------------------------------------------------------------- install kind

_compose_cache: tuple[tuple[str, int, int], bool] | None = None


def install_kind(path: Path | None = None) -> InstallKind:
    """``release`` when the compose file carries the generated header, ``checkout``
    when it is a plain file, ``unknown`` when it cannot be read."""
    path = path if path is not None else COMPOSE_PATH
    try:
        fingerprint = compose_freshness.release_fingerprint(path)
    except OSError:
        return "unknown"
    return "release" if fingerprint else "checkout"


def release_has_graph(path: Path | None = None) -> bool:
    """The compose file defines ``lumogis-graph`` under the ``graph`` profile.

    This, not the freshness fingerprint, is the gate: a stale Release file can
    still have the profile, and one without it cannot turn the graph on whatever
    its fingerprint says. Cached on (path, mtime, size); any error reads False.
    """
    global _compose_cache
    path = path if path is not None else COMPOSE_PATH
    try:
        st = path.stat()
        key = (str(path), st.st_mtime_ns, st.st_size)
        if _compose_cache is not None and _compose_cache[0] == key:
            return _compose_cache[1]
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        svc = (doc.get("services") or {}).get(GRAPH_SERVICE) or {}
        result = GRAPH_PROFILE in (svc.get("profiles") or [])
    except Exception:  # noqa: BLE001 - an unreadable file simply has no graph
        return False
    _compose_cache = (key, result)
    return result


# ------------------------------------------------------------ stack-control probe

_health_cache: tuple[float, bool] | None = None


def stack_control_reachable(*, _mono: Callable[[], float] = time.monotonic) -> bool:
    """``GET /health`` on the sidecar, cached for 30 s with a 1 s timeout."""
    global _health_cache
    now = _mono()
    if _health_cache is not None and now - _health_cache[0] < HEALTH_TTL_SEC:
        return _health_cache[1]
    try:
        ok = stack_control.get("/health", timeout=HEALTH_TIMEOUT_SEC).status_code == 200
    except Exception:  # noqa: BLE001 - unreachable is an answer, not an error
        ok = False
    _health_cache = (now, ok)
    return ok


def _reset_caches_for_tests() -> None:
    global _compose_cache, _health_cache
    _compose_cache = None
    _health_cache = None


# ---------------------------------------------------------------------- audit


def _audit(action: str, user_id: str, summary: dict) -> None:
    """One ``audit_log`` row; failures are logged, never raised. Never a secret."""
    try:
        from actions.audit import AuditEntry
        from actions.audit import write_audit

        write_audit(
            AuditEntry(
                action_name=action,
                connector="admin",
                mode="system",
                input_summary=json.dumps(summary, default=str),
                result_summary=json.dumps({}, default=str),
                executed_at=_now(),
                user_id=user_id,
            )
        )
    except Exception:
        _log.exception("graph_toggle: audit write failed for %s", action)


# ------------------------------------------------------------------ enable / off


def _status(now: datetime):
    from services import graph_status  # late: graph_status imports this module

    return graph_status.build_graph_status(now=now)


def enable(*, confirm_low_memory: bool, actor: str, now: datetime | None = None) -> None:
    """Record and write the settings for Turn on. Raises a :class:`ToggleError`.

    Nothing is written when the action is refused.
    """
    now = now or _now()
    with _lock:
        status = _status(now)
        turn_on = status.turn_on
        if not turn_on.available:
            if turn_on.block == "in_progress":
                raise Busy()
            raise Blocked(turn_on.block or "blocked")
        if turn_on.needs_confirmation and not confirm_low_memory:
            raise NeedsConfirmation()
        _begin_then("start", now, _write_enable_keys)
    _audit(
        "knowledge_graph.turned_on",
        actor,
        {"confirm_low_memory": bool(confirm_low_memory), "install_kind": status.install_kind},
    )


def _write_enable_keys() -> None:
    def apply(content: str) -> str:
        profiles = host_env.list_with(
            host_env.get_value(content, "COMPOSE_PROFILES"), GRAPH_PROFILE
        )
        urls = host_env.list_with(host_env.get_value(content, "CAPABILITY_SERVICE_URLS"), GRAPH_URL)
        content = host_env.set_key(content, "COMPOSE_PROFILES", profiles)
        content = host_env.set_key(content, "GRAPH_MODE", "service")
        content = host_env.set_key(content, "KG_SERVICE_URL", GRAPH_URL)
        content = host_env.set_key(content, "CAPABILITY_SERVICE_URLS", urls)
        content = host_env.set_key(content, "FALKORDB_URL", FALKORDB_URL)
        if host_env.get_value(content, "GRAPH_WEBHOOK_SECRET") in host_env.PLACEHOLDERS | {None}:
            content = host_env.set_key(content, "GRAPH_WEBHOOK_SECRET", secrets.token_hex(32))
        return content

    try:
        host_env.update(apply)
    except host_env.EnvUnwritable as exc:
        raise Blocked("env_unwritable") from exc


def disable(*, actor: str, now: datetime | None = None) -> None:
    """Record and write the settings for Turn off. Raises a :class:`ToggleError`."""
    now = now or _now()
    with _lock:
        status = _status(now)
        turn_off = status.turn_off
        if not turn_off.available:
            if turn_off.block == "in_progress":
                raise Busy()
            raise Blocked(turn_off.block or "blocked")
        _begin_then("stop", now, _write_disable_keys)
    _audit("knowledge_graph.turned_off", actor, {"install_kind": status.install_kind})


def _write_disable_keys() -> None:
    def apply(content: str) -> str:
        profiles = host_env.list_without(
            host_env.get_value(content, "COMPOSE_PROFILES"), GRAPH_PROFILE
        )
        urls = host_env.list_without(
            host_env.get_value(content, "CAPABILITY_SERVICE_URLS"), GRAPH_URL
        )
        content = host_env.set_key(content, "GRAPH_MODE", "disabled")
        # Only touch the lists when they exist: Turn off must not invent a key.
        if host_env.get_value(content, "COMPOSE_PROFILES") is not None:
            content = host_env.set_key(content, "COMPOSE_PROFILES", profiles)
        if host_env.get_value(content, "CAPABILITY_SERVICE_URLS") is not None:
            content = host_env.set_key(content, "CAPABILITY_SERVICE_URLS", urls)
        return content

    try:
        host_env.update(apply)
    except host_env.EnvUnwritable as exc:
        raise Blocked("env_unwritable") from exc


# -------------------------------------------------------------------- background


# A sidecar that was just recreated (an update, a restart) is up but not yet
# listening for a few seconds. A refused or timed-out connect means it never got
# the request, so retrying cannot run a sequence twice (LUM-1001).
UNREACHABLE_RETRY_SEC = 15.0
UNREACHABLE_RETRY_INTERVAL_SEC = 1.5


def run(
    op: ToggleOp,
    *,
    now: Callable[[], datetime] = _now,
    _sleep: Callable[[float], None] = time.sleep,
    _mono: Callable[[], float] = time.monotonic,
) -> None:
    """Ask stack-control to carry out ``op`` and record a failure that Core survives.

    Runs after the 202 has been sent. ``ConnectError`` / ``ConnectTimeout`` mean
    nothing ran; they are retried for ``UNREACHABLE_RETRY_SEC`` and then recorded
    as ``unreachable``. A read or protocol error after the request was sent means
    Core itself was recreated mid-call: the sequence reached its last step, so
    nothing is recorded. ``{ok: false, code}`` means step 1 or 2 failed while Core
    was still alive. ``busy`` is another sequence in progress, never a failure of
    this one.
    """
    path = "/graph/start" if op == "start" else "/graph/stop"
    deadline = _mono() + UNREACHABLE_RETRY_SEC
    while True:
        try:
            # A short connect timeout: a sidecar that drops packets is "unreachable"
            # in seconds, not after the 30 minutes the sequence itself may take.
            response = stack_control.post(path, timeout=httpx.Timeout(RUN_TIMEOUT_SEC, connect=5.0))
            break
        except (httpx.ConnectError, httpx.ConnectTimeout):
            if _mono() + UNREACHABLE_RETRY_INTERVAL_SEC > deadline:
                _safe_failure("unreachable", now())
                return
            _log.info("graph_toggle: %s: stack-control not reachable yet, retrying", op)
            _sleep(UNREACHABLE_RETRY_INTERVAL_SEC)
        except httpx.HTTPError:
            _log.info("graph_toggle: %s: connection ended mid-call (Core recreated)", op)
            return
        except Exception:  # noqa: BLE001
            _log.exception("graph_toggle: %s: unexpected error calling stack-control", op)
            return
    if response.status_code != 200:
        _safe_failure("compose_failed", now())
        return
    try:
        body = response.json()
    except ValueError:
        _safe_failure("compose_failed", now())
        return
    if not isinstance(body, dict):
        _safe_failure("compose_failed", now())
        return
    if body.get("ok") is True:
        return
    code = body.get("code")
    if code == "busy":
        _log.info("graph_toggle: %s: stack-control is busy with another sequence", op)
        return
    _safe_failure(code if code in _FAILED_CODES else "compose_failed", now())


def _safe_failure(code: str, now: datetime) -> None:
    try:
        _record_failure(code, now)
    except Exception:  # noqa: BLE001
        _log.exception("graph_toggle: could not record the failure")
