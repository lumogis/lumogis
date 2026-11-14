# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""The live-paperless lane's skip-or-fail guard (LUM-309, sibling of :mod:`_pgguard`).

A plain module rather than a ``conftest.py`` helper, for the same reason
``_pgguard`` is: conftest is loaded by pytest as a *plugin*, not placed on
``sys.path`` as an importable module, so ``from conftest import ...`` fails at
collection. The import is by package path —
``from tests.integration._paperless_guard import ...``.

The distinction this enforces is the entire point of the module. On a laptop
with no fixture running, skipping is correct and these tests tell you nothing
either way. Under ``make compose-test-paperless`` or in CI, where a container
was just brought up and waited on, "cannot reach paperless" means the lane is
broken — and reporting that as a skip is how a suite goes green while
validating nothing. That is LUM-718's lesson, and this lane is a prime
candidate to relearn it: every test here is skip-guarded, so a
misconfiguration that skips all of them looks exactly like a pass.
"""

from __future__ import annotations

import os

import pytest

#: Set by `make compose-test-paperless` and by CI. When "1", an unreachable
#: paperless fixture is a hard failure rather than a skip.
PAPERLESS_REQUIRE_ENV = "LUMOGIS_REQUIRE_PAPERLESS_FIXTURE"

#: Where the fixture publishes its API. Matches the compose file's
#: 127.0.0.1:8010 -> 8000 mapping.
PAPERLESS_BASE_URL_ENV = "PAPERLESS_BASE_URL"

#: Minted at runtime by `_paperless_seed.mint_token`; never committed, never
#: exported by the Makefile (it cannot exist until mid-session).
PAPERLESS_TOKEN_ENV = "PAPERLESS_TOKEN"

_DEFAULT_BASE_URL = "http://127.0.0.1:8010"


def paperless_fixture_required() -> bool:
    return os.environ.get(PAPERLESS_REQUIRE_ENV, "").strip() == "1"


def require_paperless(exc: Exception, *, what: str = "paperless fixture") -> None:
    """Skip when the fixture is merely absent; **fail** when the run promised one.

    Call from a fixture's ``except`` branch instead of ``pytest.skip`` directly.
    """
    if paperless_fixture_required():
        pytest.fail(
            f"{PAPERLESS_REQUIRE_ENV}=1 but {what} is unreachable: {exc}. "
            "This lane is required here — a skip would report green while "
            "validating nothing (LUM-718). Check that "
            "`docker compose -f docker-compose.paperless-test.yml` came up "
            "healthy and that port 8010 is published.",
            pytrace=False,
        )
    pytest.skip(
        f"{what} not reachable — run `make compose-test-paperless`, or set "
        f"{PAPERLESS_REQUIRE_ENV}=1 to make this fatal: {exc}"
    )


def paperless_base_url() -> str:
    """The fixture's base URL, defaulting to the compose file's published port."""
    return (os.environ.get(PAPERLESS_BASE_URL_ENV, "").strip() or _DEFAULT_BASE_URL).rstrip("/")


def paperless_token() -> str:
    """The runtime-minted API token.

    Unlike the base URL this has no sensible default: it is minted per run by
    :func:`_paperless_seed.mint_token` from a per-run generated admin password.
    An empty value means the seeding step did not run, which is a bug in the
    fixture wiring rather than a missing service — so it fails loudly rather
    than skipping.
    """
    token = os.environ.get(PAPERLESS_TOKEN_ENV, "").strip()
    if not token:
        pytest.fail(
            f"{PAPERLESS_TOKEN_ENV} is unset. It is minted at runtime by "
            "`mint_token()`, not exported by the Makefile — this means the "
            "session-scoped seeding fixture did not run, not that paperless "
            "is down.",
            pytrace=False,
        )
    return token
