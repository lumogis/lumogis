# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Migration 075 indexes the message, not the thread (LUM-826).

`test_email_quote_strip.py` proves the stripper's behaviour in Python. This
proves the half that only Postgres can answer: that the generated column
actually reads the stripped copy, and that the fallback works.

The headline assertion is the ticket's first "done looks like" — *a phrase
written once in a thread returns one hit, not one per reply* — checked against
a real `tsvector` GIN index rather than inferred from the stripper's return
value. The two can disagree: a correct stripper wired to a column that still
reads `text` would pass every unit test and change nothing a user sees.

Skips when no Postgres is reachable, like the other migration regressions here.
Runs against a throwaway database because it replaces a generated column.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

psycopg2 = pytest.importorskip("psycopg2")

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_MIGRATIONS = _REPO_ROOT / "postgres" / "migrations"

_SIGNALS_STUB = """
CREATE TABLE signals (
    signal_id UUID PRIMARY KEY,
    user_id TEXT,
    title TEXT,
    from_addr TEXT,
    from_display TEXT,
    content_summary TEXT
)
"""


def _admin_kwargs(dbname: str | None = None) -> dict:
    return {
        "host": os.environ.get("POSTGRES_HOST", "postgres"),
        "port": int(os.environ.get("POSTGRES_PORT", "5432")),
        "user": os.environ.get("POSTGRES_USER", "lumogis"),
        "password": os.environ.get("POSTGRES_PASSWORD", "lumogis-dev"),
        "dbname": dbname or os.environ.get("POSTGRES_DB", "lumogis"),
        "connect_timeout": 3,
    }


@pytest.fixture(scope="module")
def migrated_db():
    """A throwaway database with 074 + 075 applied. Yields a connection."""
    try:
        admin = psycopg2.connect(**_admin_kwargs())
    except Exception as exc:  # noqa: BLE001 — diagnostic skip
        pytest.skip(f"Postgres not reachable for migration 075 test: {exc}")
    admin.autocommit = True
    name = f"t075_{uuid.uuid4().hex[:12]}"
    try:
        with admin.cursor() as cur:
            cur.execute(f'CREATE DATABASE "{name}"')
    except Exception as exc:  # noqa: BLE001
        admin.close()
        pytest.skip(f"cannot create a test database (needs CREATE): {exc}")

    conn = psycopg2.connect(**_admin_kwargs(name))
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(_SIGNALS_STUB)
            for f in ("074-email-body-text-search.sql", "075-email-body-text-stripped.sql"):
                cur.execute((_MIGRATIONS / f).read_text(encoding="utf-8"))
        yield conn
    finally:
        conn.close()
        with admin.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        admin.close()


def _insert_thread(conn, user_id: str, messages: list[tuple[str, str | None]]) -> None:
    """Insert one row per message: ``(full_text, stripped_or_None)``."""
    with conn.cursor() as cur:
        for full, stripped in messages:
            sid = str(uuid.uuid4())
            cur.execute("INSERT INTO signals (signal_id, user_id) VALUES (%s, %s)", (sid, user_id))
            cur.execute(
                "INSERT INTO email_body_text (signal_id, user_id, text, text_stripped) "
                "VALUES (%s, %s, %s, %s)",
                (sid, user_id, full, stripped),
            )


def _hits(conn, user_id: str, term: str) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM email_body_text "
            "WHERE user_id = %s AND text_tsv @@ to_tsquery('english', %s)",
            (user_id, term),
        )
        return cur.fetchone()[0]


def test_a_phrase_written_once_returns_one_hit_not_one_per_reply(migrated_db):
    """The headline. Four messages, one original phrase, one hit."""
    from services.email_quote_strip import strip_quotes

    user = f"u_{uuid.uuid4().hex[:8]}"
    original = "Can we move the radiator service to Tuesday?"
    thread = original
    rows = [(original, strip_quotes(original))]
    for reply in ("Tuesday works.", "Confirmed.", "See you then."):
        message = reply + "\n\n" + "".join(f"> {ln}\n" for ln in thread.splitlines())
        thread = message
        rows.append((message, strip_quotes(message)))

    _insert_thread(migrated_db, user, rows)

    assert _hits(migrated_db, user, "radiator:*") == 1, (
        "the quoted copies in the three replies must not be indexed"
    )


def test_without_stripping_the_same_thread_returns_a_hit_per_reply(migrated_db):
    """The before-picture, so the assertion above cannot pass vacuously.

    Same four messages, ``text_stripped`` left NULL — which is exactly what
    rows written before migration 075 look like.
    """
    user = f"u_{uuid.uuid4().hex[:8]}"
    original = "Can we move the radiator service to Tuesday?"
    thread = original
    rows = [(original, None)]
    for reply in ("Tuesday works.", "Confirmed.", "See you then."):
        message = reply + "\n\n" + "".join(f"> {ln}\n" for ln in thread.splitlines())
        thread = message
        rows.append((message, None))

    _insert_thread(migrated_db, user, rows)

    assert _hits(migrated_db, user, "radiator:*") == 4, (
        "un-stripped rows should show the duplication this migration removes"
    )


def test_a_null_stripped_column_falls_back_to_the_full_text(migrated_db):
    """COALESCE is the contract: NULL means 'index the original', not 'index nothing'.

    This is what keeps a wholly-quoted forward findable, and what makes rows
    written before 075 behave exactly as they did before.
    """
    user = f"u_{uuid.uuid4().hex[:8]}"
    _insert_thread(migrated_db, user, [("boiler inspection booked", None)])

    assert _hits(migrated_db, user, "boiler:*") == 1


def test_the_stripped_copy_wins_over_the_full_text(migrated_db):
    """If the column were still generated from `text`, this would return 1."""
    user = f"u_{uuid.uuid4().hex[:8]}"
    _insert_thread(migrated_db, user, [("new content\n> quoted radiator line", "new content")])

    assert _hits(migrated_db, user, "radiator:*") == 0
    assert _hits(migrated_db, user, "content:*") == 1
