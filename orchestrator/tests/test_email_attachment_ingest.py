# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""The attachment auto-ingest sweep (LUM-702).

The fake store below models the ledger's real semantics rather than returning
canned rows, because three of this feature's contracts *are* the ledger:

* a part with **any** verdict row — filing, skip, or reversed filing — is never
  reconsidered (which is what makes undo stick), and
* a ``duplicate`` skip row must **not** occupy the active-content slot, and
* a transient failure must leave **no** row at all.

A store that always returned ``[]`` for the selection query would let all three
pass while being wrong.

What the fake **cannot** do is enforce a unique index — so the dedupe cases here
prove the application's intent, not that Postgres refuses the second insert. That
proof lives in ``tests/integration/test_attachment_ingest_indexes_live.py``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import pytest

from services import email_attachment_ingest as sweep

USER = "alice"
SOURCE = "11111111-1111-1111-1111-111111111111"
SIG_A = "email:src-a:101"
SIG_B = "email:src-a:102"


@dataclass
class _Row:
    signal_id: str
    part_index: int
    filename: str
    size_bytes: int = 1024
    mime_type: str = "application/pdf"
    created_at: int = 100
    from_addr: str = "sender@example.com"
    subject: str = "a message"
    category: str = "bills_finance"


class _FakePart:
    def __init__(self, index: int, data: bytes, filename: str = "f.pdf"):
        self.part_index = index
        self.data = data
        self.filename = filename
        self.mime_type = "application/pdf"


class _Store:
    """Models `email_attachment` + the ledger with their real selection rules."""

    def __init__(self, attachments: list[_Row], *, since: int = 0, enabled: bool = True):
        self.attachments = attachments
        self.since = since
        self.enabled = enabled
        self.extraction_method = "imap_full_ingest"
        self.ledger: list[dict] = []
        self.rules: list[dict] = []
        self._next_id = 1
        self.queries: list[str] = []

    def add_rule(self, match_type: str, match_value: str, rule_id: str = "rule-1"):
        """Declare an "always file this" rule (LUM-727).

        Most cases below are about the FILING MACHINERY — dedupe, session reuse,
        terminal-vs-transient — which LUM-727 did not change. It changed the
        TRIGGER: the sweep no longer files what it finds. Driving those cases
        through a rule keeps them testing what they are named for.
        """
        self.rules.append(
            {
                "id": rule_id,
                "source_id": SOURCE,
                "match_type": match_type,
                "match_value": match_value,
                "action": "file",
                "created_at": 0,
            }
        )
        return self

    # -- reads ---------------------------------------------------------------
    def fetch_all(self, sql, params=None):
        self.queries.append(sql)
        if "FROM sources" in sql and "attachment_ingest_enabled" in sql:
            if not self.enabled:
                return []
            # LUM-742/763: model `AND user_id = %s` — the predicate is OPTIONAL in
            # `_opted_in_sources` (the sweep wants every user's mailboxes, so it
            # passes nothing), which is why the arg is checked rather than assumed.
            #
            # This must be modelled, not waved through. User isolation on the
            # accept path used to be enforced twice — once in SQL and once by a
            # Python filter in `candidates_for_user` — and this fake could only
            # ever see the Python one. When LUM-742 moved the path onto
            # `_eligible_sources`, which relies on the predicate alone, the
            # isolation test went red not because production broke (both
            # `AND user_id = %s` here and `AND ea.user_id = %s` in
            # `_CANDIDATES_SQL` still hold against real Postgres) but because the
            # only layer this fake could observe had gone. A fake that ignores the
            # predicate it is being asked about turns every isolation test built
            # on it into a test of nothing.
            if params and str(params[0]) != USER:
                return []
            return [
                {
                    "id": SOURCE,
                    "user_id": USER,
                    "attachment_ingest_since": self.since,
                    "extraction_method": self.extraction_method,
                }
            ]
        if "FROM email_attachment ea" in sql:
            # LUM-727 candidate query. Param order:
            # (source_id, user_id, since, max_bytes, bare_extensions, limit)
            _src, _uid, since, max_bytes, extensions, limit = params
            # `AND ea.user_id = %s` is in the real query (the second, independent
            # isolation predicate). Every `_Row` here belongs to USER, so a query
            # on anyone else's behalf must come back empty.
            if str(_uid) != USER:
                return []
            if not self.enabled:
                # `src.attachment_ingest_enabled = TRUE` is IN the query. A mailbox
                # enabled once and then disabled keeps a valid watermark, so the
                # fake must model the enabled flag or the enable→disable case
                # silently passes.
                return []
            judged = {(r["signal_id"], r["part_index"]) for r in self.ledger}
            out = []
            for a in sorted(
                self.attachments,
                key=lambda r: (-r.created_at, r.signal_id, r.part_index),
            ):
                if a.created_at < since:
                    continue
                if (a.signal_id, a.part_index) in judged:
                    continue
                if a.size_bytes > max_bytes:
                    continue
                ext = a.filename.rsplit(".", 1)[-1].lower() if "." in a.filename else ""
                if ext not in extensions:
                    continue
                out.append(
                    {
                        "signal_id": a.signal_id,
                        "part_index": a.part_index,
                        "filename": a.filename,
                        "mime_type": a.mime_type,
                        "size_bytes": a.size_bytes,
                        "signal_url": f"imap://{SOURCE}/7/{a.signal_id.rsplit(':', 1)[1]}",
                        "from_addr": a.from_addr,
                        "from_display": a.from_addr,
                        "title": a.subject,
                        "category": a.category,
                        "received_at": a.created_at,
                    }
                )
            return out[:limit]
        if "FROM email_attachment_rule" in sql:
            return list(self.rules)
        if "FROM email_attachment_ingest_log" in sql:
            rows = [r for r in self.ledger if r["user_id"] == params[0]]
            if "reversed_at IS NULL" in sql and "skipped_reason IS NULL" in sql:
                rows = [r for r in rows if r["reversed_at"] is None and r["skipped_reason"] is None]
            return rows
        return []

    def fetch_one(self, sql, params=None):
        self.queries.append(sql)
        if "FROM signals" in sql and "source_id" in sql:
            return {"source_id": SOURCE}
        if sql.startswith("DELETE FROM email_attachment_ingest_log"):
            # undo_decline (LUM-727): DELETES the verdict row rather than
            # flipping a flag, because a declined row never created a document.
            user_id, signal_id, part_index, reason = params
            for i, r in enumerate(self.ledger):
                if (
                    r["user_id"] == user_id
                    and r["signal_id"] == signal_id
                    and r["part_index"] == part_index
                    and r["skipped_reason"] == reason
                ):
                    self.ledger.pop(i)
                    return {"id": r["id"]}
            return None
        if sql.startswith("INSERT INTO email_attachment_ingest_log"):
            # `ON CONFLICT DO NOTHING ... RETURNING id` returns NO row when the
            # part already has a verdict. The production code depends on that to
            # notice a lost race and take its now-unreferenced vectors back out.
            #
            # Keyed on the CONFLICT TARGET, not on "did the ledger grow": under a
            # simulated race the row that appears is the other tick's, so a
            # size-delta proxy reports our insert as landed and the test can never
            # see the behaviour it names.
            signal_id, part_index = params[2], params[3]
            conflicted = any(
                r["signal_id"] == signal_id and r["part_index"] == part_index for r in self.ledger
            )
            self.execute(sql, params)
            if conflicted:
                return None
            return {"id": self.ledger[-1]["id"]} if self.ledger else None
        if "content_sha256 = %s" in sql:
            # source_id is part of the real WHERE clause and of the partial unique
            # index. Ignoring it here would make the per-source dedupe scope
            # untestable — the fake would report a duplicate across mailboxes that
            # the database happily allows.
            for r in self.ledger:
                if (
                    r["user_id"] == params[0]
                    and str(r["source_id"]) == str(params[1])
                    and r["content_sha256"] == params[2]
                    and r["reversed_at"] is None
                    and r["skipped_reason"] is None
                ):
                    return r
            return None
        if "FROM email_attachment_ingest_log" in sql and "id = %s" in sql:
            # Undo looks for an ACTIVE row; re-file (LUM-721) looks for a REVERSED
            # one. Same shape, opposite predicate — branching on the SQL keeps the
            # fake honest about which of the two it is answering.
            wants_reversed = "reversed_at IS NOT NULL" in sql
            for r in self.ledger:
                if r["id"] != params[0] or r["user_id"] != params[1]:
                    continue
                if r["skipped_reason"] is not None:
                    continue
                is_reversed = r["reversed_at"] is not None
                if is_reversed == wants_reversed:
                    return r
            return None
        return None

    # -- writes --------------------------------------------------------------
    def execute(self, sql, params=None):
        self.queries.append(sql)
        if sql.startswith("INSERT INTO email_attachment_ingest_log"):
            signal_id, part_index = params[2], params[3]
            # ON CONFLICT (user_id, signal_id, part_index) DO NOTHING
            if any(
                r["signal_id"] == signal_id and r["part_index"] == part_index for r in self.ledger
            ):
                return
            # BOTH inserts bind ten placeholders — the skip form writes a literal
            # 0 for chunk_count and a bound skipped_reason, the filing form binds
            # chunk_count and omits the column. Branching on the param COUNT
            # silently files every skip as a filing with the reason string parked
            # in chunk_count, which is exactly the sort of test-double lie that
            # makes a suite green and wrong. Branch on the column list instead.
            is_skip = "skipped_reason)" in sql
            row = {
                "id": self._next_id,
                "user_id": params[0],
                "source_id": params[1],
                "signal_id": signal_id,
                "part_index": part_index,
                "filename": params[4],
                "mime_type": params[5],
                "size_bytes": params[6],
                "content_sha256": params[7],
                "logical_path": params[8],
                "chunk_count": 0 if is_skip else params[9],
                "reversed_at": None,
                "skipped_reason": params[9] if is_skip else None,
                # LUM-727: which rule filed this, or None when picked by hand.
                "filed_by_rule_id": None if is_skip else (params[10] if len(params) > 10 else None),
                "ingested_at": self._next_id,
            }
            self._next_id += 1
            self.ledger.append(row)
            return
        if sql.startswith("UPDATE email_attachment_ingest_log"):
            if "reversed_at = NULL" in sql:
                # Re-file (LUM-721): clears the flag on the EXISTING row and
                # refreshes the content hash. Params are
                # (digest, chunk_count, logical_path, id, user_id).
                for r in self.ledger:
                    if r["id"] == params[3] and r["reversed_at"] is not None:
                        r["content_sha256"] = params[0]
                        r["chunk_count"] = params[1]
                        r["logical_path"] = params[2]
                        r["reversed_at"] = None
                return
            # Undo: params are (id, user_id).
            for r in self.ledger:
                if r["id"] == params[0] and r["reversed_at"] is None:
                    r["reversed_at"] = "now"
            return

    class _Txn:
        def __enter__(self):
            return None

        def __exit__(self, *a):
            return False

    def transaction(self):
        return self._Txn()

    # -- helpers -------------------------------------------------------------
    def active(self):
        return [r for r in self.ledger if r["reversed_at"] is None and r["skipped_reason"] is None]

    def skips(self):
        return [r for r in self.ledger if r["skipped_reason"] is not None]


class _Imap:
    """Records what was asked of IMAP so a test can assert it was NOT asked.

    ``logins`` counts sessions separately from ``calls`` (fetches), because
    LUM-722's whole point is that N messages in one tick cost ONE login. A fake
    that conflated them could not express the difference.
    """

    def __init__(self, payloads: dict[int, bytes] | None = None, raises=None):
        self.calls: list[dict] = []
        self.payloads = payloads or {}
        self.raises = raises
        self.logins = 0
        self.logouts = 0
        self.open_clients: list[object] = []

    def read_session(self):
        self.logins += 1
        client = object()
        self.open_clients.append(client)
        return client

    def close_session(self, client) -> None:
        self.logouts += 1
        if client in self.open_clients:
            self.open_clients.remove(client)

    def fetch_attachment_parts(
        self, *, uid, part_indexes, message_id, folder_hint, uidvalidity, client=None
    ):
        assert client is not None, "the sweep must reuse the tick's session (LUM-722)"
        self.calls.append({"uid": uid, "indexes": list(part_indexes), "client": client})
        if self.raises is not None:
            raise self.raises
        # Default bytes vary by (message, part), which is what real mail looks
        # like. A constant default would make every attachment in a multi-message
        # test a content DUPLICATE of the first, so any test about selection or
        # bounding would measure dedupe instead of what it names. Tests that want
        # identical bytes ask for them explicitly via ``payloads``.
        return [
            _FakePart(i, self.payloads.get(i, f"PDF-bytes-{uid}-{i}".encode()))
            for i in part_indexes
        ]


@pytest.fixture
def wire(monkeypatch):
    """Wire the sweep against a fake store, fake IMAP and a stub extractor."""

    def _wire(store: _Store, imap: _Imap, *, extractors=None, ingest=None):
        import config

        monkeypatch.setitem(config._instances, "metadata_store", store)
        monkeypatch.setattr(sweep, "_adapter_for", lambda s, u: imap)
        monkeypatch.setattr(sweep, "_message_id_for", lambda s, u: "<m@x>")
        monkeypatch.setattr(sweep, "_organize_folder_for", lambda s, u: None)
        monkeypatch.setattr(
            config,
            "get_extractors",
            lambda: extractors if extractors is not None else {".pdf": lambda p: "text"},
        )

        filed: list[dict] = []

        def _fake_ingest(**kw):
            filed.append(kw)
            if ingest is not None:
                return ingest(**kw)
            from models.ingest import IngestResult

            return IngestResult(
                file_path=f"email-attachment://{kw['source_id']}/{kw['signal_id']}/{kw['part_index']}",
                chunk_count=3,
            )

        monkeypatch.setattr("services.ingest.ingest_email_attachment", _fake_ingest)
        monkeypatch.setattr(sweep, "_discard_vectors", lambda **kw: None)
        # The rules cache is module-level and TTL'd; a test that declares rules
        # after a previous test read an empty set would otherwise see stale data.
        from services import email_attachment_rules

        email_attachment_rules.invalidate_cache()
        monkeypatch.setattr(
            email_attachment_rules, "_known_slugs", lambda uid: frozenset({"bills_finance"})
        )
        return filed

    return _wire


# --- 5 ----------------------------------------------------------------------


def test_only_opted_in_sources_are_swept(wire):
    store = _Store([_Row(SIG_A, 0, "a.pdf")], enabled=False).add_rule("category", "bills_finance")
    imap = _Imap()
    wire(store, imap)

    result = sweep.ingest_tick()

    assert result.scanned == 0
    assert imap.calls == [], "an opted-out mailbox must not open an IMAP session"


# --- 6 ----------------------------------------------------------------------


def test_only_attachments_after_the_watermark_are_selected(wire):
    store = _Store(
        [_Row(SIG_A, 0, "old.pdf", created_at=10), _Row(SIG_B, 0, "new.pdf", created_at=200)],
        since=100,
    ).add_rule("category", "bills_finance")
    filed = wire(store, _Imap())

    sweep.ingest_tick()

    assert [f["signal_id"] for f in filed] == [SIG_B]


# --- 7 ----------------------------------------------------------------------


def test_apply_to_existing_widens_the_sweep_over_history(wire):
    """The watermark at epoch is what 'apply to existing' means downstream."""
    store = _Store(
        [_Row(SIG_A, 0, "old.pdf", created_at=10), _Row(SIG_B, 0, "new.pdf", created_at=200)],
        since=0,
    ).add_rule("category", "bills_finance")
    filed = wire(store, _Imap())

    sweep.ingest_tick()

    assert {f["signal_id"] for f in filed} == {SIG_A, SIG_B}


# --- 8 ----------------------------------------------------------------------


def test_a_metadata_mode_mailbox_yields_nothing(wire):
    store = _Store([_Row(SIG_A, 0, "a.pdf")]).add_rule("category", "bills_finance")
    store.extraction_method = "imap_metadata"
    imap = _Imap()
    wire(store, imap)

    result = sweep.ingest_tick()

    assert result.scanned == 0
    assert imap.calls == []


# --- 10 / 11 / 31 — decided from metadata, and now not even offered ----------
#
# LUM-727 CHANGED THIS CONTRACT. Under LUM-702 an unreadable, blocked or oversize
# attachment got a `skipped_reason` row so the "Filed from email" list could
# explain the absence. Now it is filtered out of the CANDIDATE query instead and
# gets no row at all — because offering the user a decision on a file that can
# never be filed is a decision they cannot act on, and a queue full of those is
# the thing this change exists to prevent.
#
# The explanation did not disappear; it moved. `AttachmentList` in the email view
# still shows the attachment and simply offers no "File to library" action.


def test_an_unreadable_attachment_is_never_offered_as_a_candidate(wire):
    store = _Store([_Row(SIG_A, 0, "photo.heic")]).add_rule("category", "bills_finance")
    imap = _Imap()
    wire(store, imap)

    assert sweep.candidates_for_user(user_id=USER) == []

    sweep.ingest_tick()
    assert imap.calls == [], "the bytes must not move for a file we cannot read"
    assert store.ledger == [], "and no verdict is recorded — it was never a decision"


def test_a_blocked_extension_is_never_offered_as_a_candidate(wire):
    """An executable has no extractor either, so the candidate filter already
    excludes it. `_pre_fetch_verdict` still runs on the ACCEPT path, where the
    blocklist is enforced properly — that is an observation about the current
    lists, not an invariant."""
    store = _Store([_Row(SIG_A, 0, "invoice.pdf.exe")]).add_rule("category", "bills_finance")
    imap = _Imap()
    wire(store, imap)

    assert sweep.candidates_for_user(user_id=USER) == []
    assert imap.calls == []


def test_an_oversize_attachment_is_never_offered_as_a_candidate(wire, monkeypatch):
    monkeypatch.setenv("EMAIL_ATTACHMENT_INGEST_MAX_MB", "1")
    store = _Store([_Row(SIG_A, 0, "huge.pdf", size_bytes=5 * 1024 * 1024)]).add_rule(
        "category", "bills_finance"
    )
    imap = _Imap()
    wire(store, imap)

    assert sweep.candidates_for_user(user_id=USER) == []
    assert imap.calls == []


def test_a_tick_with_nothing_filable_opens_no_session(wire):
    store = _Store([_Row(SIG_A, 0, "photo.heic"), _Row(SIG_B, 0, "run.exe")]).add_rule(
        "category", "bills_finance"
    )
    imap = _Imap()
    wire(store, imap)

    sweep.ingest_tick()

    assert imap.logins == 0
    assert store.ledger == []


# --- 12 — the feed_monitor lesson -------------------------------------------


def test_one_failing_attachment_does_not_stop_the_tick(wire):
    """`feed_monitor`'s poll loop breaks on a handler error; this one continues."""
    store = _Store(
        [_Row(SIG_A, 0, "a.pdf"), _Row(SIG_A, 1, "b.pdf"), _Row(SIG_A, 2, "c.pdf")]
    ).add_rule("category", "bills_finance")

    def _boom(**kw):
        if kw["part_index"] == 1:
            raise RuntimeError("embedder exploded")
        from models.ingest import IngestResult

        return IngestResult(file_path="p", chunk_count=2)

    filed = wire(store, _Imap(), ingest=_boom)

    result = sweep.ingest_tick()

    assert [f["part_index"] for f in filed] == [0, 1, 2], "the third must still be attempted"
    assert result.filed == 2
    assert result.failed == 1


def test_a_failed_attachment_leaves_no_ledger_row_so_the_next_tick_retries(wire):
    store = _Store([_Row(SIG_A, 0, "a.pdf")]).add_rule("category", "bills_finance")

    def _boom(**kw):
        raise RuntimeError("vector store down")

    wire(store, _Imap(), ingest=_boom)

    sweep.ingest_tick()

    assert store.ledger == [], "a dead vector store is transient — it must not be recorded"


# --- 13 ---------------------------------------------------------------------


def test_the_tick_is_bounded_by_max_per_tick(wire):
    store = (
        _Store([_Row(f"email:src-a:{i}", 0, "a.pdf") for i in range(20)])
        .add_rule("category", "bills_finance")
        .add_rule("category", "bills_finance")
    )
    filed = wire(store, _Imap())

    result = sweep.ingest_tick(limit=5)

    assert len(filed) == 5
    assert result.scanned == 5


# --- 14 ---------------------------------------------------------------------


def test_one_walk_per_message_for_all_of_its_parts(wire):
    """Two attachments on one message is ONE session and ONE parse, not two."""
    store = _Store([_Row(SIG_A, 0, "a.pdf"), _Row(SIG_A, 1, "b.pdf")]).add_rule(
        "category", "bills_finance"
    )
    imap = _Imap()
    wire(store, imap)

    sweep.ingest_tick()

    assert len(imap.calls) == 1
    assert imap.calls[0]["indexes"] == [0, 1]


def test_many_messages_share_one_session(wire):
    """LUM-722. Was one login per MESSAGE; now one per tick.

    Connect+login+logout per message is a real provider rate-limit and lockout
    risk — `email_organize` carries the same note, which is why `apply_organize`
    grew `client=` first. The fetch count still tracks messages; only the login
    count collapses.
    """
    store = _Store([_Row(f"email:src-a:{i}", 0, "a.pdf") for i in range(5)]).add_rule(
        "category", "bills_finance"
    )
    imap = _Imap()
    wire(store, imap)

    sweep.ingest_tick()

    assert len(imap.calls) == 5, "still one fetch (and one parse) per message"
    assert imap.logins == 1, "but only one login for the whole tick"


def test_the_session_is_closed_when_the_tick_ends(wire):
    store = _Store([_Row(SIG_A, 0, "a.pdf")]).add_rule("category", "bills_finance")
    imap = _Imap()
    wire(store, imap)

    sweep.ingest_tick()

    assert imap.logouts == 1
    assert imap.open_clients == [], "a tick must not leak its IMAP session"


def test_a_transient_failure_drops_the_session_so_the_next_message_reconnects(wire):
    """The resilience that reuse would otherwise trade away.

    With one shared connection, a mid-tick drop would fail every remaining
    message. Dropping the dead session keeps the cost at one message, as it was
    when each message opened its own.
    """
    from adapters.email_imap_source import AttachmentFetchError

    store = _Store([_Row(SIG_A, 0, "a.pdf"), _Row(SIG_B, 0, "b.pdf")]).add_rule(
        "category", "bills_finance"
    )
    imap = _Imap()
    filed = wire(store, imap)

    real_fetch = imap.fetch_attachment_parts
    state = {"first": True}

    def _drops_once(**kw):
        if state["first"]:
            state["first"] = False
            imap.calls.append({"uid": kw["uid"], "indexes": list(kw["part_indexes"])})
            raise AttachmentFetchError("imap_unreachable", "dropped")
        return real_fetch(**kw)

    imap.fetch_attachment_parts = _drops_once

    sweep.ingest_tick()

    assert imap.logins == 2, "the dead session is replaced, not reused"
    assert len(filed) == 1, "the second message still files"


def test_a_terminal_verdict_keeps_the_session(wire):
    """A gone message says nothing about the connection — reconnecting for every
    deleted message would undo the batching this exists for."""
    from adapters.email_imap_source import AttachmentFetchError

    store = _Store([_Row(SIG_A, 0, "a.pdf"), _Row(SIG_B, 0, "b.pdf")]).add_rule(
        "category", "bills_finance"
    )
    imap = _Imap(raises=AttachmentFetchError("message_gone", "gone"))
    wire(store, imap)

    sweep.ingest_tick()

    assert imap.logins == 1


# --- 15 / 16 — transient vs terminal ----------------------------------------


def test_imap_unreachable_writes_no_log_row_so_the_next_tick_retries(wire):
    from adapters.email_imap_source import AttachmentFetchError

    store = _Store([_Row(SIG_A, 0, "a.pdf")]).add_rule("category", "bills_finance")
    wire(store, _Imap(raises=AttachmentFetchError("imap_unreachable", "down")))

    result = sweep.ingest_tick()

    assert store.ledger == []
    assert result.failed == 1


@pytest.mark.parametrize(
    "code,expected",
    [
        ("message_gone", "message_gone"),
        ("part_gone", "message_gone"),
        ("message_too_large", "too_large"),
        ("message_ambiguous", "ambiguous"),
    ],
)
def test_a_terminal_fetch_failure_writes_a_skip_so_it_is_never_retried(wire, code, expected):
    """The distinction that matters: transient leaves no row, terminal leaves one.

    ``message_ambiguous`` is terminal on purpose — duplicate Message-IDs mean we
    cannot know WHICH message, and guessing would file a stranger's document
    under a plausible filename.
    """
    from adapters.email_imap_source import AttachmentFetchError

    store = _Store([_Row(SIG_A, 0, "a.pdf")]).add_rule("category", "bills_finance")
    wire(store, _Imap(raises=AttachmentFetchError(code, "terminal")))

    sweep.ingest_tick()

    assert [r["skipped_reason"] for r in store.skips()] == [expected]


def test_a_terminal_skip_is_not_reconsidered_by_the_next_tick(wire):
    from adapters.email_imap_source import AttachmentFetchError

    store = _Store([_Row(SIG_A, 0, "a.pdf")]).add_rule("category", "bills_finance")
    imap = _Imap(raises=AttachmentFetchError("message_gone", "gone"))
    wire(store, imap)

    sweep.ingest_tick()
    sweep.ingest_tick()

    assert len(imap.calls) == 1, "a gone message must not be fetched again every 15 minutes"


# --- Extraction verdicts ----------------------------------------------------


def test_an_extractor_returning_nothing_is_a_no_text_skip(wire):
    store = _Store([_Row(SIG_A, 0, "a.pdf")]).add_rule("category", "bills_finance")
    wire(store, _Imap(), extractors={".pdf": lambda p: "   "})

    sweep.ingest_tick()

    assert [r["skipped_reason"] for r in store.skips()] == ["no_text"]


def test_an_extractor_that_raises_is_a_no_text_skip_not_a_tick_failure(wire):
    def _boom(path):
        raise ValueError("corrupt pdf")

    store = _Store([_Row(SIG_A, 0, "a.pdf")]).add_rule("category", "bills_finance")
    wire(store, _Imap(), extractors={".pdf": _boom})

    result = sweep.ingest_tick()

    assert [r["skipped_reason"] for r in store.skips()] == ["no_text"]
    assert result.failed == 0


# --- 17-20 — dedupe ---------------------------------------------------------


def test_the_same_bytes_arriving_twice_file_once(wire):
    same = b"%PDF identical bytes"
    store = _Store([_Row(SIG_A, 0, "a.pdf"), _Row(SIG_B, 0, "forwarded-a.pdf")]).add_rule(
        "category", "bills_finance"
    )
    filed = wire(store, _Imap(payloads={0: same}))

    sweep.ingest_tick()

    assert len(filed) == 1, "the same PDF forwarded twice is one document"
    assert [r["skipped_reason"] for r in store.skips()] == ["duplicate"]


def test_different_bytes_with_the_same_filename_file_twice(wire):
    """Two scans of the same invoice are two documents — the hash is of BYTES.

    The default fake payload already varies by message, so this is the honest
    pairing for ``test_the_same_bytes_arriving_twice_file_once``: identical names,
    different content, and no dedupe.
    """
    store = _Store([_Row(SIG_A, 0, "scan.pdf"), _Row(SIG_B, 0, "scan.pdf")]).add_rule(
        "category", "bills_finance"
    )
    filed = wire(store, _Imap())

    sweep.ingest_tick()

    assert len(filed) == 2
    assert store.skips() == []


def test_dedupe_is_scoped_per_source(wire):
    """The documented trade-off, pinned so a later change is deliberate.

    The same invoice in two connected mailboxes files twice — the key is
    ``(user_id, source_id, content_sha256)``, which keeps ``ON DELETE CASCADE``
    honest when one mailbox is disconnected.
    """
    same = b"%PDF identical bytes"
    digest = hashlib.sha256(same).hexdigest()
    store = _Store([_Row(SIG_A, 0, "a.pdf")]).add_rule("category", "bills_finance")
    store.ledger.append(
        {
            "id": 99,
            "user_id": USER,
            "source_id": "22222222-2222-2222-2222-222222222222",  # a DIFFERENT mailbox
            "signal_id": "email:src-b:1",
            "part_index": 0,
            "content_sha256": digest,
            "logical_path": "email-attachment://other/1/0",
            "chunk_count": 1,
            "reversed_at": None,
            "skipped_reason": None,
            "filename": "a.pdf",
            "mime_type": "application/pdf",
            "size_bytes": 10,
            "ingested_at": 0,
        }
    )
    filed = wire(store, _Imap(payloads={0: same}))

    sweep.ingest_tick()

    assert len(filed) == 1, "a different mailbox files its own copy"


# --- 18 ---------------------------------------------------------------------


def test_a_duplicate_skip_row_does_not_occupy_the_active_content_slot(wire):
    """The partial index's ``skipped_reason IS NULL`` predicate, asserted through
    behaviour rather than through DDL.

    A duplicate must still get a row so the sweep stops reconsidering it — but if
    that row claimed the content slot, undoing the ORIGINAL would never free the
    hash for a later message carrying the same bytes.
    """
    same = b"%PDF identical bytes"
    store = _Store([_Row(SIG_A, 0, "a.pdf"), _Row(SIG_B, 0, "fwd.pdf")]).add_rule(
        "category", "bills_finance"
    )
    filed = wire(store, _Imap(payloads={0: same}))

    sweep.ingest_tick()
    assert len(filed) == 1
    assert [r["skipped_reason"] for r in store.skips()] == ["duplicate"]

    # Undo the original; the content slot must now be free.
    original = store.active()[0]
    assert sweep.undo_row(USER, original["id"]) is True

    # A THIRD message carrying the same bytes can be filed — the skip row never
    # held the slot, and the reversed row released it.
    store.attachments.append(_Row("email:src-a:103", 0, "again.pdf", created_at=300))
    sweep.ingest_tick()

    assert len(filed) == 2, "the freed content slot must accept a different message"


# --- 29 / 30 — the temp-file bridge ------------------------------------------


def test_the_temp_file_never_takes_its_name_from_the_attachment(monkeypatch, tmp_path):
    """The attacker-supplied filename contributes an extension and nothing else.

    Extractors take a PATH, so bytes must reach disk. The on-disk stem is
    ``mkstemp``'s own random one — same conclusion LUM-695 reached for staging.
    """
    import config

    seen: dict = {}

    def _extractor(path):
        seen["path"] = path
        return "text"

    monkeypatch.setattr(config, "get_extractors", lambda: {".pdf": _extractor})
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))

    out = sweep._extract_text(data=b"bytes", filename="../../etc/cron.d/x.pdf")

    assert out == "text"
    path = seen["path"]
    assert path.startswith(str(tmp_path)), "the temp file escaped the temp directory"
    assert "cron.d" not in path
    assert ".." not in path
    assert path.endswith(".pdf"), "only the extension is carried over"


@pytest.mark.parametrize(
    "filename,expected",
    [
        ("report.PDF", ".pdf"),  # lowercased for the registry key
        ("../../etc/passwd.pdf", ".pdf"),
        ("x.p/df", ".pdf"),  # separator stripped, not kept as a path part
        ("noextension", ""),
        ("trailing.", ""),
        ("a." + "x" * 40, "." + "x" * 10),  # bounded
    ],
)
def test_the_suffix_is_reduced_to_a_dotted_alphanumeric_extension(filename, expected):
    """Dotted on BOTH sides: ``mkstemp(suffix=…)`` and the ``get_extractors()``
    key are both dotted, so one value serves both. Stripping the dot for one and
    not the other is a silent ``no_extractor`` skip on every file."""
    assert sweep._suffix_for(filename) == expected


def test_the_temp_file_is_deleted_even_when_the_extractor_raises(monkeypatch, tmp_path):
    import config

    seen: dict = {}

    def _boom(path):
        seen["path"] = path
        raise ValueError("corrupt")

    monkeypatch.setattr(config, "get_extractors", lambda: {".pdf": _boom})
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))

    assert sweep._extract_text(data=b"bytes", filename="a.pdf") is None

    import os

    assert not os.path.exists(seen["path"]), "the temp file outlived the exception"


def test_the_temp_file_is_deleted_on_the_happy_path_too(monkeypatch, tmp_path):
    import os

    import config

    seen: dict = {}
    monkeypatch.setattr(
        config, "get_extractors", lambda: {".pdf": lambda p: seen.setdefault("path", p) and "t"}
    )
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))

    sweep._extract_text(data=b"bytes", filename="a.pdf")

    assert not os.path.exists(seen["path"])


def test_the_temp_file_is_written_owner_only(monkeypatch, tmp_path):
    """Another local account must not be able to read a stranger's mail
    attachment out of the temp directory while it is being parsed."""
    import os
    import stat

    import config

    modes: dict = {}

    def _extractor(path):
        modes["mode"] = stat.S_IMODE(os.stat(path).st_mode)
        return "text"

    monkeypatch.setattr(config, "get_extractors", lambda: {".pdf": _extractor})
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))

    sweep._extract_text(data=b"bytes", filename="a.pdf")

    assert modes["mode"] == 0o600


def test_no_extractor_for_the_extension_reads_nothing_from_disk(monkeypatch):
    import config

    monkeypatch.setattr(config, "get_extractors", lambda: {".pdf": lambda p: "text"})

    assert sweep._extract_text(data=b"bytes", filename="photo.heic") is None


def test_a_lost_part_race_takes_its_vectors_back_out(wire, monkeypatch):
    """The orphan hole: `ON CONFLICT DO NOTHING` writes no row, but the vectors
    are already in the index by then.

    If the losing tick reported "filed", the document would stay searchable with
    no ledger row pointing at it — and undo works off the ledger, so nothing
    could ever remove it. Reachable only across processes (`max_instances=1`
    rules out the in-process race), which is why it needs a test rather than a
    reader noticing.
    """
    store = _Store([_Row(SIG_A, 0, "a.pdf")]).add_rule("category", "bills_finance")
    filed = wire(store, _Imap())

    discarded: list[str] = []
    monkeypatch.setattr(
        sweep, "_discard_vectors", lambda **kw: discarded.append(kw["logical_path"])
    )

    real_fetch_one = store.fetch_one

    def _judged_by_someone_else(sql, params=None):
        # The competing verdict has to be in place BEFORE our INSERT is
        # evaluated — that is what makes ON CONFLICT DO NOTHING swallow it.
        if sql.startswith("INSERT INTO email_attachment_ingest_log") and not store.ledger:
            store.ledger.append(
                {
                    "id": 99,
                    "user_id": USER,
                    "source_id": SOURCE,
                    "signal_id": SIG_A,
                    "part_index": 0,
                    "filename": "a.pdf",
                    "mime_type": "application/pdf",
                    "size_bytes": 10,
                    "content_sha256": "other",
                    "logical_path": "p",
                    "chunk_count": 0,
                    "reversed_at": None,
                    "skipped_reason": "no_text",
                    "ingested_at": 0,
                }
            )
        return real_fetch_one(sql, params)

    store.fetch_one = _judged_by_someone_else

    result = sweep.ingest_tick()

    assert len(filed) == 1, "we did write vectors before losing the race"
    assert discarded, "the unreferenced vectors must be removed"
    assert result.filed == 0, "a lost race is not a filing"


# =========================================================================== #
# LUM-727 — the per-attachment decision
# =========================================================================== #


def test_the_candidate_list_opens_no_imap_session(wire):
    """The central performance claim of the redesign.

    The fake fails the test if the adapter is touched, so this cannot pass by
    accident. Everything the list needs is already stored: `email_attachment`
    (066) has filename, type, size and part; `signals` has sender and category.
    """
    store = _Store([_Row(SIG_A, 0, "a.pdf"), _Row(SIG_B, 0, "b.pdf")])
    imap = _Imap()
    wire(store, imap)

    rows = sweep.candidates_for_user(user_id=USER)

    assert len(rows) == 2
    assert imap.logins == 0 and imap.calls == []


def test_a_candidate_carries_who_it_came_from(wire):
    store = _Store([_Row(SIG_A, 0, "a.pdf", from_addr="school@x.edu", subject="Trip")])
    wire(store, _Imap())

    c = sweep.candidates_for_user(user_id=USER)[0]

    assert c.from_addr == "school@x.edu"
    assert c.subject == "Trip"
    assert c.category == "bills_finance"


def test_nothing_is_filed_without_a_decision_or_a_rule(wire):
    """The whole point. A tick with no rules files nothing at all."""
    store = _Store([_Row(SIG_A, 0, "a.pdf"), _Row(SIG_B, 0, "b.pdf")])
    imap = _Imap()
    filed = wire(store, imap)

    result = sweep.ingest_tick()

    assert filed == []
    assert store.active() == []
    assert imap.logins == 0, "an undecided candidate must not cost a login"
    assert result.filed == 0


def test_accepting_files_it_and_is_the_only_path_that_spends_imap(wire):
    store = _Store([_Row(SIG_A, 0, "a.pdf")])
    imap = _Imap()
    filed = wire(store, imap)

    out = sweep.accept_candidates(USER, [(SIG_A, 0)])

    assert out["accepted"] == 1
    assert len(filed) == 1
    assert imap.logins == 1


def test_declining_costs_nothing_and_is_terminal(wire):
    store = _Store([_Row(SIG_A, 0, "a.pdf")])
    imap = _Imap()
    filed = wire(store, imap)

    assert sweep.decline_candidates(USER, [(SIG_A, 0)]) == 1

    assert imap.logins == 0, "declining must not move any bytes"
    assert filed == []
    assert sweep.candidates_for_user(user_id=USER) == [], "never offered again"
    assert [r["skipped_reason"] for r in store.skips()] == ["declined"]


def test_undoing_a_decline_returns_the_part_to_pending(wire):
    store = _Store([_Row(SIG_A, 0, "a.pdf")])
    wire(store, _Imap())
    sweep.decline_candidates(USER, [(SIG_A, 0)])

    assert sweep.undo_decline(USER, SIG_A, 0) is True

    assert store.ledger == [], "the verdict row is DELETED, not flag-flipped"
    assert len(sweep.candidates_for_user(user_id=USER)) == 1


def test_another_users_candidate_cannot_be_accepted_or_declined(wire):
    store = _Store([_Row(SIG_A, 0, "a.pdf")])
    imap = _Imap()
    filed = wire(store, imap)

    out = sweep.accept_candidates("mallory", [(SIG_A, 0)])

    assert out["accepted"] == 0
    assert sweep.decline_candidates("mallory", [(SIG_A, 0)]) == 0
    assert filed == [] and imap.logins == 0
    assert store.ledger == []


# --- the enable → disable sequence the critique caught ----------------------


def test_disabling_a_mailbox_stops_candidates_even_though_the_watermark_survives(wire):
    """ARBITRATE-R1 #1, and the reason it is written as a SEQUENCE.

    `set_attachment_ingest` deliberately PRESERVES `attachment_ingest_since` on
    disable so re-enabling does not replay history. So `since` alone cannot
    express "off": a mailbox enabled once and then disabled has a perfectly good
    watermark. A test written the obvious way — never-opted-in versus opted-in —
    passes while this is broken.
    """
    store = _Store([_Row(SIG_A, 0, "a.pdf")])
    wire(store, _Imap())
    assert len(sweep.candidates_for_user(user_id=USER)) == 1, "precondition"

    store.enabled = False  # the watermark is untouched, exactly as the route leaves it

    assert sweep.candidates_for_user(user_id=USER) == []


def test_disabling_does_not_decline_pending_candidates(wire):
    """Writing a terminal verdict for everything outstanding would be a decision
    nobody made, and re-enabling would then show an empty list forever."""
    store = _Store([_Row(SIG_A, 0, "a.pdf")])
    wire(store, _Imap())

    store.enabled = False
    sweep.ingest_tick()

    assert store.ledger == []
    store.enabled = True
    assert len(sweep.candidates_for_user(user_id=USER)) == 1


# --- rules ------------------------------------------------------------------


def test_a_sender_rule_files_without_asking(wire):
    store = _Store([_Row(SIG_A, 0, "a.pdf", from_addr="accounts@vendor.example")])
    store.rules = []
    store.add_rule("sender", "accounts@vendor.example")
    filed = wire(store, _Imap())

    sweep.ingest_tick()

    assert len(filed) == 1
    assert store.active()[0]["filed_by_rule_id"] == "rule-1"


def test_a_rule_never_overrides_an_explicit_decline(wire):
    """The guarantee is STRUCTURAL, not conditional.

    A decline writes a ledger row; the candidate query excludes anything with a
    ledger row; rules are only ever evaluated over candidates. There is no code
    path on which a rule sees a declined part. Anyone "optimising" rule
    evaluation to run over `email_attachment` directly removes this silently.
    """
    store = _Store([_Row(SIG_A, 0, "a.pdf", from_addr="accounts@vendor.example")])
    filed = wire(store, _Imap())
    sweep.decline_candidates(USER, [(SIG_A, 0)])

    store.rules = []
    store.add_rule("sender", "accounts@vendor.example")
    from services import email_attachment_rules

    email_attachment_rules.invalidate_cache()
    sweep.ingest_tick()
    sweep.ingest_tick()

    assert filed == [], "a later rule must not reverse a decision the user made"
    assert store.active() == []


def test_a_sender_rule_beats_a_category_rule(wire):
    from services import email_attachment_rules

    store = _Store([])
    store.add_rule("category", "bills_finance", rule_id="cat")
    store.add_rule("sender", "accounts@vendor.example", rule_id="snd")
    wire(store, _Imap())
    email_attachment_rules.invalidate_cache()

    hit = email_attachment_rules.matches(
        USER, SOURCE, from_addr="accounts@vendor.example", category="bills_finance"
    )

    assert hit["id"] == "snd", "naming a sender is the more specific statement"


def test_a_rule_for_another_mailbox_does_not_fire(wire):
    from services import email_attachment_rules

    store = _Store([])
    store.rules.append(
        {
            "id": "other",
            "source_id": "22222222-2222-2222-2222-222222222222",
            "match_type": "category",
            "match_value": "bills_finance",
            "action": "file",
            "created_at": 0,
        }
    )
    wire(store, _Imap())
    email_attachment_rules.invalidate_cache()

    assert (
        email_attachment_rules.matches(USER, SOURCE, from_addr="x@y", category="bills_finance")
        is None
    )
