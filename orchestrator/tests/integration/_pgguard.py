# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""The real-Postgres lane's skip-or-fail guard (LUM-718).

A plain module rather than a `conftest.py` helper: conftest is loaded by pytest as
a *plugin*, not placed on `sys.path` as an importable module, so
`from conftest import ...` fails at collection. Both `tests/` and
`tests/integration/` carry an `__init__.py`, so the import is by package path —
`from tests.integration._pgguard import ...`.

Two gates, because there are two ways this lane goes quietly green:

* :func:`require_postgres` — the database is configured but *unreachable*.
* :func:`require_integration_dsn` — the database is reachable but the test was
  never *pointed at* it. Three modules gate on their own
  ``LUMOGIS_INTEGRATION_POSTGRES_DSN`` rather than the ``POSTGRES_*`` variables,
  so before LUM-718 they skipped even in a run with a live service container
  attached. An unset DSN is a CI misconfiguration, not a laptop.
"""

from __future__ import annotations

import os

import pytest

#: Set by CI. When "1", an unreachable Postgres is a hard failure.
REQUIRE_ENV = "LUMOGIS_REQUIRE_INTEGRATION_DB"

#: DSN-style pointer used by the three modules that predate the POSTGRES_* wiring.
DSN_ENV = "LUMOGIS_INTEGRATION_POSTGRES_DSN"

#: The other convention. 42 modules read these; 3 read :data:`DSN_ENV`.
_PG_VARS = ("POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB")


def integration_db_required() -> bool:
    return os.environ.get(REQUIRE_ENV, "").strip() == "1"


def _dsn_from_postgres_env() -> str | None:
    """Compose a DSN from the ``POSTGRES_*`` convention, or ``None`` (LUM-807).

    The repo has two Postgres conventions and they were not interchangeable: set
    only ``POSTGRES_*`` and the three DSN-reading modules skipped; set only the
    DSN and ~66 ``POSTGRES_*`` tests errored with ``could not translate host name
    "postgres"``. Both are correct behaviour from their own side, which is
    exactly why it cost 20 minutes every time — the operator could not tell "your
    env is wrong" from "the code is broken".

    Bridging here means either convention configures the whole lane. Nothing
    downstream changes: the DSN readers still receive a DSN.

    Requires ``POSTGRES_HOST`` explicitly — the default is ``postgres``, the
    compose *service* name, which resolves to nothing from the host, so
    defaulting it would manufacture a DSN that cannot connect and turn a clear
    "not configured" skip into a confusing failure.
    """
    host = os.environ.get("POSTGRES_HOST", "").strip()
    if not host:
        return None
    from urllib.parse import quote

    user = quote(os.environ.get("POSTGRES_USER", "lumogis").strip(), safe="")
    password = quote(os.environ.get("POSTGRES_PASSWORD", "").strip(), safe="")
    port = os.environ.get("POSTGRES_PORT", "5432").strip() or "5432"
    dbname = os.environ.get("POSTGRES_DB", "lumogis").strip() or "lumogis"
    auth = f"{user}:{password}" if password else user
    return f"postgresql://{auth}@{host}:{port}/{dbname}"


def _env_summary() -> str:
    """What each convention currently resolves to — the 'say why' half of LUM-788.

    Never renders ``POSTGRES_PASSWORD``; its presence is reported, not its value.
    """
    lines = [f"  {DSN_ENV}={os.environ.get(DSN_ENV, '') or '<unset>'}"]
    for var in _PG_VARS:
        raw = os.environ.get(var, "")
        shown = "<set>" if var == "POSTGRES_PASSWORD" and raw else (raw or "<unset>")
        lines.append(f"  {var}={shown}")
    host = os.environ.get("POSTGRES_HOST", "").strip()
    if host == "postgres":
        lines.append(
            "  NOTE: POSTGRES_HOST=postgres is the compose SERVICE name — it resolves "
            "inside the stack network but not from the host. Host-side runs want "
            "127.0.0.1 with the port published."
        )
    return "\n".join(lines)


def require_postgres(exc: Exception, *, what: str = "Postgres") -> None:
    """Skip when the DB is merely absent; **fail** when CI promised one.

    Call from a fixture's ``except`` branch instead of ``pytest.skip`` directly.
    The distinction is the whole point: on a laptop with no database, skipping is
    correct and this test tells you nothing either way. In CI, where a service
    container is configured, "cannot connect" means the lane is broken — and
    reporting that as a skip is how a suite goes green while testing nothing.
    """
    if integration_db_required():
        pytest.fail(
            f"{REQUIRE_ENV}=1 but {what} is unreachable: {exc}. "
            "This lane is required here — a skip would report green while "
            f"validating nothing (LUM-718). Environment as resolved:\n{_env_summary()}",
            pytrace=False,
        )
    pytest.skip(
        f"{what} not reachable — set {REQUIRE_ENV}=1 to make this fatal: {exc}\n{_env_summary()}"
    )


def require_integration_dsn() -> str:
    """Return the integration DSN; **fail** when CI promised one and did not set it.

    The sibling of :func:`require_postgres` for the other silent-green path. These
    modules ask for a DSN, so a run that exports only ``POSTGRES_HOST`` and friends
    skipped them while appearing to exercise the lane — which is how a live
    ``psycopg2`` "can't adapt type 'UUID'" in ``entity_write_guard`` sat unnoticed
    behind a green suite.
    """
    raw = os.environ.get(DSN_ENV, "").strip()
    if raw:
        return raw
    # LUM-807 — fall back to the OTHER convention rather than skipping. Setting
    # POSTGRES_* used to configure 42 modules and silently leave these three
    # unconfigured; the two conventions now bridge in one place.
    composed = _dsn_from_postgres_env()
    if composed:
        return composed
    if integration_db_required():
        pytest.fail(
            f"{REQUIRE_ENV}=1 but no Postgres is configured by EITHER convention. "
            f"These tests read {DSN_ENV}; most read POSTGRES_*. Set one — either "
            f"works (LUM-807). Resolved now:\n{_env_summary()}",
            pytrace=False,
        )
    pytest.skip(
        f"No Postgres configured by either convention ({DSN_ENV} or POSTGRES_HOST) "
        f"— set {REQUIRE_ENV}=1 to make this fatal (LUM-718). Resolved now:\n"
        f"{_env_summary()}"
    )


def integration_dsn_or_skip() -> str:
    """:func:`require_integration_dsn` plus a one-shot reachability probe (LUM-807).

    The three DSN modules take a DSN and then call ``psycopg2.connect`` directly,
    so a bad host surfaced as a raw

        psycopg2.OperationalError: could not translate host name "postgres"

    — the exact message LUM-807 exists to stop, naming a hostname the operator
    never typed (it is the compose *service* name, the default of
    ``POSTGRES_HOST``). Probing once here routes that failure through
    :func:`require_postgres`, so it inherits both the skip-vs-fail rule and the
    environment summary that says which convention was read and what it resolved
    to.

    Cheap: one short-timeout connect per module, not per test.
    """
    dsn = require_integration_dsn()
    try:
        import psycopg2

        psycopg2.connect(dsn, connect_timeout=3).close()
    except Exception as exc:  # noqa: BLE001 — routed through the LUM-718 guard
        require_postgres(exc)
    return dsn
