# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-729 follow-up (FP-076) — the organize undo sweep must give up.

`email_cleanup` has had `_UNDO_MAX_TICKS = 20` since LUM-646, with the comment
"bound the 'undoing' state so an irreversible message can't wedge a job". The
organize undo sweep, written in the same arc, had no equivalent — and its failure
is strictly worse than a wedged job.

`undo_tick` cleared `organize_undo_scope` only when **no** un-reversed row
remained. One message that can never be reversed — deleted upstream, moved by
another client, a folder that no longer exists — left the scope set forever. And
`feed_monitor._run_organize_tick` returns early whenever the undo reports work:

    undo = email_organize.undo_tick(...)
    if undo.get("remaining") or undo.get("reversed") or undo.get("failed"):
        return   # skip reconcile this tick

So one doomed row did not merely wedge the undo. It **permanently disabled
organize for that mailbox** — the standing reconcile sweep never ran again —
while opening a fresh IMAP session on every poll tick, forever, to retry the same
row. Nothing said so.

Giving up **silently** would have been the wrong fix: it trades one invisible
failure for another, which is the exact complaint LUM-729 exists about. So the
give-up is stamped, surfaced, and logged.
"""

from __future__ import annotations

import logging

import pytest

import config
from services import email_organize as eo


class _Store:
    """Enough of the metadata store to drive `undo_tick`'s control flow.

    `unreversible` rows are the point: they are returned by every fetch and never
    marked reversed, which is precisely what a message deleted upstream looks
    like from here.
    """

    def __init__(self, *, scope="all", ticks=0, unreversible=1):
        self.scope = scope
        self.ticks = ticks
        self.unreversible = unreversible
        self.gave_up_at = None
        self.executed: list[tuple] = []

    def fetch_one(self, sql, params=None):
        q = " ".join(sql.split()).lower()
        if "organize_undo_scope" in q and "from sources" in q:
            return {"organize_undo_scope": self.scope, "ticks": self.ticks}
        if "count(*)" in q and "email_organize_log" in q:
            return {"n": self.unreversible}
        if "select 1 from email_organize_log" in q:
            return {"present": 1} if self.unreversible else None
        return None

    def fetch_all(self, sql, params=None):
        if "from email_organize_log" in " ".join(sql.split()).lower():
            return [
                {"id": i, "signal_url": f"imap://s/1/{i}", "op": "move", "reverse_token": {}}
                for i in range(self.unreversible)
            ]
        return []

    def execute(self, sql, params=None):
        q = " ".join(sql.split())
        self.executed.append((q, params))
        if "organize_undo_scope = NULL" in q:
            self.scope = None
        if "organize_undo_gave_up_at = NOW()" in q:
            self.gave_up_at = "now"
        if "organize_undo_ticks = %s" in q:
            self.ticks = params[0]


class _Adapter:
    """Every reversal fails — the message is gone from the server's point of view."""

    def organize_session(self):
        return object()

    def close_session(self, client):
        pass

    def reverse_writeback(self, **kwargs):
        raise RuntimeError("message no longer exists")


@pytest.fixture
def store(monkeypatch):
    def _install(**kwargs):
        st = _Store(**kwargs)
        monkeypatch.setattr(config, "get_metadata_store", lambda: st)
        monkeypatch.setattr(eo, "_adapter_for", lambda *a, **k: _Adapter())
        return st

    return _install


# --- the bound ---------------------------------------------------------------


def test_a_sweep_that_leaves_rows_behind_counts_against_the_bound(store):
    st = store(ticks=0)

    out = eo.undo_tick(source_id="s1", user_id="u1")

    assert out["remaining"] == 1
    assert st.ticks == 1, "an unfinished sweep must be counted, or the bound never advances"


def test_the_sweep_GIVES_UP_at_the_bound(store):
    """**The whole point.** Before this, `organize_undo_scope` stayed set forever."""
    st = store(ticks=eo._UNDO_MAX_TICKS - 1)

    out = eo.undo_tick(source_id="s1", user_id="u1")

    assert out["gave_up"] is True
    assert st.scope is None, "the scope must be cleared or organize stays dead"
    assert st.gave_up_at is not None


def test_giving_up_RESETS_the_counter(store):
    """Otherwise the source is left at the limit and the next undo the user asks
    for abandons on its first sweep — the bound would quietly become "one
    attempt, ever"."""
    st = store(ticks=eo._UNDO_MAX_TICKS - 1)

    eo.undo_tick(source_id="s1", user_id="u1")

    give_up = [s for s, _ in st.executed if "organize_undo_gave_up_at = NOW()" in s]
    assert give_up and "organize_undo_ticks = 0" in give_up[0]


def test_giving_up_logs_a_WARNING_naming_the_count(store, caplog):
    """A give-up nobody can see is the same silent failure with a bound bolted
    on. The count is what makes the log actionable rather than decorative."""
    store(ticks=eo._UNDO_MAX_TICKS - 1, unreversible=3)

    with caplog.at_level(logging.WARNING, logger=eo.__name__):
        eo.undo_tick(source_id="s1", user_id="u1")

    assert "undo_gave_up" in caplog.text
    assert "unreversed=3" in caplog.text


def test_the_bound_is_the_SAME_number_as_email_cleanup():
    """Two undo sweeps in one product with different patience would be a
    coin-flip for the user. Asserted so a change to one is a change to both, or a
    deliberate divergence someone had to write down."""
    from services import email_cleanup

    assert eo._UNDO_MAX_TICKS == email_cleanup._UNDO_MAX_TICKS


# --- the ordinary paths still behave ----------------------------------------


def test_a_COMPLETED_undo_clears_the_scope_and_the_counter(store):
    """Nothing left to reverse. The counter must reset here too, or a long but
    successful undo leaves the next one with a shrunken budget."""
    st = store(unreversible=0, ticks=7)

    out = eo.undo_tick(source_id="s1", user_id="u1")

    assert out["gave_up"] is False
    assert st.scope is None
    finish = [s for s, _ in st.executed if "organize_undo_scope = NULL" in s]
    assert finish and "organize_undo_ticks = 0" in finish[0]


def test_a_COMPLETED_undo_clears_a_PREVIOUS_give_up_stamp(store):
    """A stale "we gave up" banner sitting over a successful undo would be worse
    than no banner — it says the product failed at something it just did."""
    st = store(unreversible=0)

    eo.undo_tick(source_id="s1", user_id="u1")

    finish = [s for s, _ in st.executed if "organize_undo_scope = NULL" in s]
    assert finish and "organize_undo_gave_up_at = NULL" in finish[0]


def test_no_pending_undo_touches_nothing(store):
    st = store(scope=None)

    out = eo.undo_tick(source_id="s1", user_id="u1")

    assert out == {"reversed": 0, "failed": 0, "remaining": 0, "gave_up": False}
    assert st.executed == []


def test_the_unreversed_count_never_breaks_the_give_up(monkeypatch, store):
    """The count is a diagnostic. If counting fails, the source must still be
    released — abandoning the give-up because the log line was unwritable would
    reinstate the exact bug."""
    st = store(ticks=eo._UNDO_MAX_TICKS - 1)

    def _boom(sql, params=None):
        if "count(*)" in " ".join(sql.split()).lower():
            raise RuntimeError("no")
        return _Store.fetch_one(st, sql, params)

    monkeypatch.setattr(st, "fetch_one", _boom)

    out = eo.undo_tick(source_id="s1", user_id="u1")

    assert out["gave_up"] is True
    assert st.scope is None


# --- the migration, and the two routes that read it -------------------------


def test_the_migration_adds_both_columns_additively():
    """Apply-only, `IF NOT EXISTS`, and `NOT NULL DEFAULT 0` on the counter so an
    existing row is 0 rather than NULL — the tick arithmetic would otherwise
    silently never advance on any source that predates this."""
    from pathlib import Path

    sql = (
        Path(__file__).resolve().parents[2]
        / "postgres"
        / "migrations"
        / "071-organize-undo-bound.sql"
    ).read_text()

    assert "ALTER TABLE sources" in sql
    assert "ADD COLUMN IF NOT EXISTS organize_undo_ticks      INTEGER NOT NULL DEFAULT 0" in sql
    assert "ADD COLUMN IF NOT EXISTS organize_undo_gave_up_at TIMESTAMPTZ" in sql
    assert "DROP COLUMN" not in sql.split("-- Rollback")[0], "apply-only"


def test_starting_a_NEW_undo_resets_the_budget_and_clears_the_stamp():
    """**Not optional.** An undo that ran to the bound leaves the counter at its
    limit; without this reset the next undo the user asks for abandons on its
    first sweep, and the bound quietly becomes "one attempt, ever"."""
    import inspect

    from routes import signals as sroutes

    src = inspect.getsource(sroutes.undo_email_organize)

    assert "organize_undo_ticks = 0" in src
    assert "organize_undo_gave_up_at = NULL" in src


def test_the_log_route_surfaces_the_give_up():
    """A bound that gives up invisibly trades one silent failure for another —
    which is the complaint LUM-729 exists about."""
    import inspect

    from routes import signals as sroutes

    src = inspect.getsource(sroutes.get_email_organize_log)

    assert "organize_undo_gave_up_at" in src
    assert "undo_unreversed" in src


def test_the_log_route_does_NOT_count_on_the_ordinary_path():
    """The count is only meaningful when there is a give-up to report. Running it
    on every request would put a COUNT(*) over the ledger on a polled surface."""
    import inspect

    from routes import signals as sroutes

    src = inspect.getsource(sroutes.get_email_organize_log)
    guard = src.index("if gave_up is not None")
    count = src.index("SELECT COUNT(*) AS n FROM email_organize_log")

    assert guard < count, "the COUNT must sit behind the give-up check"
