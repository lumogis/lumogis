# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Boot-time gate tests for the single-worker refusal (LUM-832).

``WEB_CONCURRENCY`` / ``--workers`` above 1 must **refuse** the boot rather
than warn: the five pieces of in-process state (rate-limit buckets, the ``tv``
cache, the ``sid`` revocation LRU, the batch queue, the connector-permission
cache) fail silently under a second worker, so a log line is the wrong control.

Asserts:
* Refusal above 1, from either source, with the operator-actionable canaries.
* Unchanged boot at 1, at 0, and when nothing is set.
* uvicorn's precedence: an explicit ``--workers`` beats ``WEB_CONCURRENCY``,
  in **both** directions — otherwise ``--workers 1`` on a box with a stale
  ``WEB_CONCURRENCY=8`` in the environment would be refused for a mode it is
  not actually running.
* Malformed values are left to the launcher, and a valueless trailing
  ``--workers`` does not blow up the gate.
"""

from __future__ import annotations

import main
import pytest
from fastapi import FastAPI


def _refuse(argv=(), env=None):
    with pytest.raises(RuntimeError) as excinfo:
        main._enforce_single_worker(argv=list(argv), environ=dict(env or {}))
    return str(excinfo.value)


def test_web_concurrency_above_one_refuses_boot():
    msg = _refuse(env={"WEB_CONCURRENCY": "4"})
    for canary in (
        "STARTUP FAILED",
        "WEB_CONCURRENCY=4",
        # The message must name *what* breaks, not just the variable — an
        # operator reading this at 2am needs the consequence and the remedy.
        "Rate limiting",
        "session revocation",
        "30/min",
        "LUMOGIS_PG_POOL_MAX",
        "unset WEB_CONCURRENCY in .env",
    ):
        assert canary in msg, (
            f"missing canary {canary!r} from the multi-worker refusal; "
            "did the message wording regress?"
        )


def test_workers_flag_above_one_refuses_boot():
    msg = _refuse(argv=["uvicorn", "main:app", "--workers", "4"])
    assert "--workers=4" in msg
    # The remedy must match the source: telling someone to "unset --workers
    # in .env" is the kind of not-quite-right instruction that costs an hour.
    assert "drop the `--workers` flag" in msg
    assert ".env" not in msg


def test_workers_flag_equals_form_refuses_boot():
    assert "--workers=8" in _refuse(argv=["uvicorn", "main:app", "--workers=8"])


@pytest.mark.parametrize(
    "argv,env",
    [
        ([], {}),
        ([], {"WEB_CONCURRENCY": "1"}),
        # uvicorn treats 0/unset alike; either way it is not multi-worker.
        ([], {"WEB_CONCURRENCY": "0"}),
        (["uvicorn", "main:app"], {}),
        (["uvicorn", "main:app", "--workers", "1"], {}),
        (["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"], {}),
    ],
)
def test_single_worker_boots_unchanged(argv, env):
    main._enforce_single_worker(argv=argv, environ=env)


def test_explicit_single_worker_flag_beats_stale_env():
    """``--workers 1`` wins over ``WEB_CONCURRENCY=8`` — uvicorn's own order."""
    main._enforce_single_worker(
        argv=["uvicorn", "main:app", "--workers", "1"],
        environ={"WEB_CONCURRENCY": "8"},
    )


def test_workers_flag_beats_env_in_the_refusing_direction():
    msg = _refuse(
        argv=["uvicorn", "main:app", "--workers", "4"],
        env={"WEB_CONCURRENCY": "1"},
    )
    assert "--workers=4" in msg
    assert "WEB_CONCURRENCY=" not in msg


@pytest.mark.parametrize(
    "argv,env",
    [
        ([], {"WEB_CONCURRENCY": "lots"}),
        ([], {"WEB_CONCURRENCY": "  "}),
        (["uvicorn", "main:app", "--workers", "lots"], {}),
        # Valueless trailing flag: uvicorn errors on it; we must not IndexError.
        (["uvicorn", "main:app", "--workers"], {}),
    ],
)
def test_unparseable_worker_count_is_left_to_the_launcher(argv, env):
    main._enforce_single_worker(argv=argv, environ=env)


async def test_lifespan_refuses_before_touching_any_backend(monkeypatch):
    """The gate is wired first in the lifespan, ahead of the store pings.

    Without the ordering this still raises — but only after opening a Postgres
    pool the process is about to abandon, so the exploding getter is the point
    of the test, not incidental.
    """

    def _explode():
        raise AssertionError("backend was pinged before the single-worker gate ran")

    monkeypatch.setattr("config.get_vector_store", _explode)
    monkeypatch.setattr("config.get_metadata_store", _explode)
    monkeypatch.setenv("WEB_CONCURRENCY", "4")
    monkeypatch.setattr("sys.argv", ["uvicorn", "main:app"])

    with pytest.raises(RuntimeError, match="WEB_CONCURRENCY=4"):
        async with main.lifespan(FastAPI()):
            pass
