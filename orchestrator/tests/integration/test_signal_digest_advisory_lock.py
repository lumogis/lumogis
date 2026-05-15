# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Postgres advisory lock contention for signal digest (LUM-39).

Uses two DB sessions against the same Postgres: one holds ``pg_try_advisory_lock``
so the orchestrator :class:`~adapters.postgres_store.PostgresStore` session
blocks and exits before notifying.

Requires migration **021** (or newer migrations) alongside this feature —
otherwise credential reads may drift; behaviour under test is only advisory keys.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock
from urllib.parse import urlparse

import psycopg2
import psycopg2.extras
import pytest


def test_digest_skips_notifier_when_digest_advisory_lock_held(monkeypatch):
    """Holder connection keeps advisory lock — digest exits without fan-out."""
    raw = os.environ.get("LUMOGIS_INTEGRATION_POSTGRES_DSN")
    if not raw:
        pytest.skip(
            "LUMOGIS_INTEGRATION_POSTGRES_DSN unset — Postgres integration "
            "not configured for this workstation",
        )

    from adapters.postgres_store import PostgresStore
    from signals.digest import ADVISORY_LOCK_KEY1
    from signals.digest import ADVISORY_LOCK_KEY2
    from signals.digest import _send_digest

    import config as cfg

    notifier = MagicMock()
    monkeypatch.setattr(cfg, "get_notifier", lambda: notifier)

    u = urlparse(raw)

    pg_store = PostgresStore(
        host=u.hostname or "localhost",
        port=int(u.port or 5432),
        user=u.username or "lumogis",
        password=u.password or "",
        dbname=(u.path or "/lumogis").strip("/").split("?")[0] or "lumogis",
    )

    prev = cfg._instances.pop("metadata_store", None)
    cfg._instances["metadata_store"] = pg_store

    holder = psycopg2.connect(raw)
    try:
        with holder.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT pg_try_advisory_lock(%s::integer, %s::integer) AS ok",
                (ADVISORY_LOCK_KEY1, ADVISORY_LOCK_KEY2),
            )
            row = cur.fetchone()
            assert row is not None and row["ok"] is True

        _send_digest()
        notifier.notify.assert_not_called()
    finally:
        try:
            with holder.cursor() as cur:
                cur.execute(
                    "SELECT pg_advisory_unlock(%s::integer, %s::integer)",
                    (ADVISORY_LOCK_KEY1, ADVISORY_LOCK_KEY2),
                )
        finally:
            holder.close()

        cfg._instances.pop("metadata_store", None)
        if prev is not None:
            cfg._instances["metadata_store"] = prev
        try:
            pg_store.close()
        except Exception:
            pass
