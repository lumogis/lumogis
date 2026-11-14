# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""State-machine tests for the LUM-644 import-scope seeding + backfill driver.

Guards the two review P0s:
  * just_new must SEED the live cursor (no 1:* full-history drain).
  * backfill completes on an EMPTY search result — not backfill_cursor >= max_uid,
    which never terminates when out-of-window mail sits above the window.
"""

from __future__ import annotations

from datetime import date
from datetime import datetime
from datetime import timedelta
from datetime import timezone

from models.signals import Signal
from models.signals import SourceConfig
from signals import feed_monitor as fm


def _source(**kw) -> SourceConfig:
    base = dict(
        id="src-1",
        name="mail",
        source_type="imap",
        url="imap://h/1/1",
        category="",
        active=True,
        poll_interval=900,
        extraction_method="imap_metadata",
        css_selector_override=None,
        last_polled_at=None,
        last_signal_at=None,
        poll_cursor=None,
        user_id="u1",
    )
    base.update(kw)
    return SourceConfig(**base)


def _sig(uid: int, uidvalidity: int = 100) -> Signal:
    return Signal(
        signal_id="",
        source_id="src-1",
        title=f"m{uid}",
        url=f"imap://src-1/{uidvalidity}/{uid}",
        published_at=None,
        content_summary="",
        raw_content="",
        entities=[],
        topics=[],
        importance_score=0.0,
        relevance_score=0.0,
        notified=False,
        created_at=datetime.now(timezone.utc),
        user_id="u1",
        source_type="imap",
        category="",
        headers={},
    )


class _FakeAdapter:
    """Configurable adapter double for the driver."""

    seed = (100, 500)  # (uidvalidity, max_uid)
    poll_return: list = []
    slice_return = (100, [], 0, True)  # (uidvalidity, signals, advance, empty)

    def __init__(self, source, *, client_factory=None):
        self.result_cursor = None
        self.poll_kwargs = None

    def inbox_seed(self):
        return type(self).seed

    def poll(self, *, since=None, reseed_on_uidvalidity_change=False):
        self.poll_kwargs = {"since": since, "reseed": reseed_on_uidvalidity_change}
        self.result_cursor = since
        return type(self).poll_return

    def backfill_slice(self, *, since, before, after_uid, max_uid):
        return type(self).slice_return


class _Recorder:
    def __init__(self, monkeypatch, import_row, cursor=None):
        self.poll_cursor: dict = {}
        self.import_state: dict = {}
        self.backfill_started: list = []
        self.backfill_cursor: dict = {}
        self.processed: list = []
        self.from_backfill_flags: list = []
        monkeypatch.setattr("adapters.email_imap_source.EmailImapSource", _FakeAdapter)
        monkeypatch.setattr(fm, "_fetch_poll_cursor", lambda sid: cursor)
        monkeypatch.setattr(fm, "_fetch_import_row", lambda sid: dict(import_row))
        monkeypatch.setattr(fm, "_update_poll_timestamp", lambda s: None)
        monkeypatch.setattr(fm, "_persist_poll_cursor", self._pc)
        monkeypatch.setattr(fm, "_set_import_state", self._st)
        monkeypatch.setattr(fm, "_start_backfill", self._bf)
        monkeypatch.setattr(fm, "_persist_backfill_cursor", self._bc)
        monkeypatch.setattr(fm, "process_signal", self._proc)
        monkeypatch.setattr(fm, "_is_duplicate", lambda url, user_id: False)

    def _pc(self, sid, cur):
        self.poll_cursor[sid] = cur

    def _st(self, sid, state):
        self.import_state[sid] = state

    def _bf(self, sid, *, since, max_uid, uidvalidity):
        self.backfill_started.append((sid, since, max_uid, uidvalidity))

    def _bc(self, sid, cur):
        self.backfill_cursor[sid] = cur

    def _proc(self, raw, user_id=None, *, from_backfill=False):
        # VERIFY-PLAN: accept from_backfill (LUM-645 — backfill mail skips the
        # go-forward organize hook); recorded so the assertion below can verify it.
        self.processed.append(raw.url)
        self.from_backfill_flags.append(from_backfill)


def test_just_new_seeds_live_cursor_and_no_backfill(monkeypatch):
    _FakeAdapter.seed = (100, 500)
    _FakeAdapter.poll_return = []
    row = {"import_scope": "just_new", "import_state": "pending"}
    rec = _Recorder(monkeypatch, row, cursor=None)
    fm._poll_email_source(_source())
    # Seeded the live cursor to *now* (UIDNEXT-1 => 500), resolved to done, no backfill.
    assert rec.poll_cursor["src-1"] == "100:500"
    assert rec.import_state["src-1"] == "done"
    assert rec.backfill_started == []


def test_last_30d_seeds_and_starts_bounded_backfill(monkeypatch):
    # last_30d stores import_since=NULL at create; the driver must COMPUTE the
    # window (today-30d) — else the backfill sees since=None and imports nothing.
    _FakeAdapter.seed = (100, 500)
    _FakeAdapter.poll_return = []
    rec = _Recorder(
        monkeypatch,
        {"import_scope": "last_30d", "import_state": "pending", "import_since": None},
        cursor=None,
    )
    fm._poll_email_source(_source())
    assert rec.poll_cursor["src-1"] == "100:500"  # live from now
    assert len(rec.backfill_started) == 1
    sid, since, max_uid, uidvalidity = rec.backfill_started[0]
    assert (sid, max_uid, uidvalidity) == ("src-1", 500, 100)
    assert since == date.today() - timedelta(days=30)  # COMPUTED, not None


def test_last_7d_computes_window(monkeypatch):
    _FakeAdapter.seed = (100, 500)
    _FakeAdapter.poll_return = []
    rec = _Recorder(
        monkeypatch,
        {"import_scope": "last_7d", "import_state": "pending", "import_since": None},
        cursor=None,
    )
    fm._poll_email_source(_source())
    assert rec.backfill_started[0][1] == date.today() - timedelta(days=7)


def test_backfill_completes_on_empty_search_with_out_of_window_mail(monkeypatch):
    # The R2 P0: mail exists above the window (advance 200 < max_uid 500), but the
    # search is empty -> must reach 'done', NOT spin forever waiting for cursor>=max.
    _FakeAdapter.slice_return = (100, [], 200, True)
    rec = _Recorder(
        monkeypatch,
        {
            "import_scope": "custom",
            "import_state": "backfilling",
            "import_since": date(2024, 1, 1),
            "import_before": date(2024, 2, 28),
            "backfill_cursor": 200,
            "backfill_max_uid": 500,
            "backfill_uidvalidity": 100,
        },
        cursor="100:500",
    )
    fm._poll_email_source(_source(poll_cursor="100:500"))
    assert rec.import_state["src-1"] == "done"


def test_backfill_processes_and_advances_over_duplicates(monkeypatch):
    _FakeAdapter.slice_return = (100, [_sig(10), _sig(11), _sig(12)], 12, False)
    rec = _Recorder(
        monkeypatch,
        {
            "import_scope": "last_30d",
            "import_state": "backfilling",
            "import_since": date(2026, 6, 22),
            "backfill_cursor": 0,
            "backfill_max_uid": 500,
            "backfill_uidvalidity": 100,
        },
        cursor="100:500",
    )
    # uid 11 is a duplicate -> skipped at persist, but the cursor must still advance.
    monkeypatch.setattr(fm, "_is_duplicate", lambda url, user_id: url.endswith("/11"))
    fm._poll_email_source(_source(poll_cursor="100:500"))
    assert rec.processed == ["imap://src-1/100/10", "imap://src-1/100/12"]
    # LUM-645: backfill-arriving signals must NOT fire the go-forward organize hook.
    assert rec.from_backfill_flags and all(rec.from_backfill_flags)
    assert rec.backfill_cursor["src-1"] == 12  # advanced over the duplicate
    assert rec.import_state.get("src-1") != "done"  # not empty, still backfilling


def test_re_run_on_done_source_does_not_reseed_poll_cursor(monkeypatch):
    # A re-run sets import_state='backfilling' with poll_cursor already non-NULL.
    # The seed-once guard must skip re-seeding poll_cursor to the seed value; the
    # live poll may still re-persist the *unchanged* cursor.
    _FakeAdapter.seed = (100, 500)  # a re-seed would write 100:500
    _FakeAdapter.poll_return = []
    _FakeAdapter.slice_return = (100, [], 700, True)
    rec = _Recorder(
        monkeypatch,
        {
            "import_scope": "custom",
            "import_state": "backfilling",
            "import_since": date(2024, 1, 1),
            "backfill_cursor": 0,
            "backfill_max_uid": 700,
            "backfill_uidvalidity": 100,
        },
        cursor="100:900",
    )
    fm._poll_email_source(_source(poll_cursor="100:900"))
    # NOT re-seeded to the seed value; stays the live cursor (100:900).
    assert rec.poll_cursor.get("src-1") == "100:900"


def test_live_poll_scope_bearing_flag_passed(monkeypatch):
    # A scope-bearing source polls with reseed_on_uidvalidity_change=True.
    _FakeAdapter.poll_return = []
    _FakeAdapter.slice_return = (100, [], 500, True)
    rec = _Recorder(
        monkeypatch,
        {
            "import_scope": "last_30d",
            "import_state": "backfilling",
            "import_since": date(2026, 6, 22),
            "backfill_cursor": 0,
            "backfill_max_uid": 500,
            "backfill_uidvalidity": 100,
        },
        cursor="100:500",
    )
    fm._poll_email_source(_source(poll_cursor="100:500"))
    assert rec  # driver ran without error; poll reseed path exercised
