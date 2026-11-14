# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Handler + adapter unit tests for IMAP mailbox write-back (LUM-623).

Two layers:
* **Adapter** (`EmailImapSource.apply_writeback` / `reverse_writeback`) — driven
  by a fake `imapclient` connection that records the STORE/MOVE/COPY/label calls
  and the Message-ID FETCH. Covers capability resolution, the MOVE/UIDPLUS gate
  (never a bare EXPUNGE), Message-ID reverse, stale UIDVALIDITY, idempotent move.
* **Handler** (`actions.handlers.imap_writeback`) — ownership-not-visibility gate
  (no connection opened for a non-owned source), url parsing, target validation.

No real IMAP server or `imapclient` is needed — real-server behaviour (Dovecot +
Gmail) is a documented manual verification step.
"""

from __future__ import annotations

import pytest
from actions.errors import PermanentActionError
from models.signals import SourceConfig

import config


class FakeClient:
    def __init__(
        self, *, caps=(), uidvalidity=100, permflags=(), message_id="<mid@x>", folders=None
    ):
        self._caps = [c.encode() for c in caps]
        self.uidvalidity = uidvalidity
        self.permflags = permflags
        self.message_id = message_id
        self.calls: list = []
        self.selected = None
        # folder-name -> set of (uid, message_id) available to search
        self._folders = folders or {}

    def select_folder(self, name, readonly=False):
        self.selected = name
        self.calls.append(("select", name, readonly))
        return {b"UIDVALIDITY": self.uidvalidity, b"PERMANENTFLAGS": tuple(self.permflags)}

    def capabilities(self):
        return tuple(self._caps)

    def fetch(self, uids, keys):
        uid = uids[0]
        if self.message_id is None:
            return {uid: {}}
        hdr = ("Message-ID: %s\r\n" % self.message_id).encode()
        return {uid: {b"BODY[HEADER.FIELDS (MESSAGE-ID)]": hdr}}

    def add_flags(self, uids, flags):
        self.calls.append(("add_flags", list(uids), list(flags)))

    def remove_flags(self, uids, flags):
        self.calls.append(("remove_flags", list(uids), list(flags)))

    def move(self, uids, folder):
        self.calls.append(("move", list(uids), folder))

    def copy(self, uids, folder):
        self.calls.append(("copy", list(uids), folder))

    def expunge(self, uids=None):
        self.calls.append(("expunge", list(uids) if uids else None))

    def add_gmail_labels(self, uids, labels):
        self.calls.append(("add_gmail_labels", list(uids), list(labels)))

    def remove_gmail_labels(self, uids, labels):
        self.calls.append(("remove_gmail_labels", list(uids), list(labels)))

    def search(self, criteria):
        mid = criteria[-1]
        return [u for (u, m) in self._folders.get(self.selected, set()) if m == mid]

    def logout(self):
        self.calls.append(("logout",))

    def names(self):
        return [c[0] for c in self.calls]


def _source(user_id="A"):
    return SourceConfig(
        id="11111111-1111-1111-1111-111111111111",
        name="mbox",
        source_type="imap",
        url="imap://x",
        category="",
        active=True,
        poll_interval=3600,
        extraction_method="imap_metadata",
        css_selector_override=None,
        last_polled_at=None,
        last_signal_at=None,
        user_id=user_id,
    )


@pytest.fixture
def adapter_factory(monkeypatch):
    from services import email_credentials

    monkeypatch.setattr(email_credentials, "load_connection", lambda uid, *, instance: object())

    from adapters.email_imap_source import EmailImapSource

    def make(fake):
        return EmailImapSource(_source(), client_factory=lambda conn: fake)

    return make


# --------------------------------------------------------------------------- #
# Adapter — capability-resolved writes
# --------------------------------------------------------------------------- #
def test_mark_read_sets_seen(adapter_factory):
    fc = FakeClient(caps=["IMAP4REV1"])
    rev = adapter_factory(fc).apply_writeback(uidvalidity=100, uid=5, op="mark_read")
    assert ("add_flags", [5], ["\\Seen"]) in fc.calls
    assert ("select", "INBOX", False) in fc.calls  # read-write, not EXAMINE
    assert rev["op"] == "mark_read" and rev["flag"] == "\\Seen"


def test_flag_sets_flagged(adapter_factory):
    fc = FakeClient(caps=["IMAP4REV1"])
    adapter_factory(fc).apply_writeback(uidvalidity=100, uid=5, op="flag")
    assert ("add_flags", [5], ["\\Flagged"]) in fc.calls


def test_move_uses_MOVE_when_available(adapter_factory):
    fc = FakeClient(caps=["MOVE"])
    rev = adapter_factory(fc).apply_writeback(uidvalidity=100, uid=5, op="move", target="Archive")
    assert ("move", [5], "Archive") in fc.calls
    assert "copy" not in fc.names() and "expunge" not in fc.names()
    assert rev["target"] == "Archive" and rev["message_id"] == "<mid@x>"


def test_move_falls_back_to_copy_expunge_under_uidplus(adapter_factory):
    fc = FakeClient(caps=["UIDPLUS"])
    adapter_factory(fc).apply_writeback(uidvalidity=100, uid=5, op="move", target="Archive")
    assert ("copy", [5], "Archive") in fc.calls
    assert ("add_flags", [5], ["\\Deleted"]) in fc.calls
    assert ("expunge", [5]) in fc.calls  # targeted UID expunge, never bare


def test_move_rejects_when_no_move_and_no_uidplus(adapter_factory):
    fc = FakeClient(caps=["IMAP4REV1"])
    with pytest.raises(PermanentActionError) as exc:
        adapter_factory(fc).apply_writeback(uidvalidity=100, uid=5, op="move", target="Archive")
    assert exc.value.code == "unsupported_move"
    assert "expunge" not in fc.names()  # never a bare expunge


def test_move_rejects_when_no_message_id(adapter_factory):
    fc = FakeClient(caps=["MOVE"], message_id=None)
    with pytest.raises(PermanentActionError) as exc:
        adapter_factory(fc).apply_writeback(uidvalidity=100, uid=5, op="move", target="Archive")
    assert exc.value.code == "no_message_id"
    assert "move" not in fc.names()


def test_mark_read_rejects_when_no_message_id(adapter_factory):
    # Every op is reversible-by-Message-ID; a message with no Message-ID would be
    # silently irreversible, so mark_read/flag/label fail closed too (not just
    # move). The write must NOT be applied (code-review #3).
    fc = FakeClient(caps=["IMAP4REV1"], message_id=None)
    with pytest.raises(PermanentActionError) as exc:
        adapter_factory(fc).apply_writeback(uidvalidity=100, uid=5, op="mark_read")
    assert exc.value.code == "no_message_id"
    assert "add_flags" not in fc.names()  # no write applied


def test_label_gmail_uses_gm_labels(adapter_factory):
    fc = FakeClient(caps=["X-GM-EXT-1"])
    rev = adapter_factory(fc).apply_writeback(uidvalidity=100, uid=5, op="label", target="School")
    assert ("add_gmail_labels", [5], ["School"]) in fc.calls
    assert rev["label_kind"] == "gmail"


def test_label_keyword_when_server_allows_arbitrary_keywords(adapter_factory):
    fc = FakeClient(caps=["IMAP4REV1"], permflags=(b"\\*",))
    rev = adapter_factory(fc).apply_writeback(uidvalidity=100, uid=5, op="label", target="School")
    assert ("add_flags", [5], ["School"]) in fc.calls
    assert rev["label_kind"] == "keyword"


def test_label_needs_folder_move_when_no_mechanism(adapter_factory):
    fc = FakeClient(caps=["IMAP4REV1"], permflags=(b"\\Seen",))
    with pytest.raises(PermanentActionError) as exc:
        adapter_factory(fc).apply_writeback(uidvalidity=100, uid=5, op="label", target="School")
    assert exc.value.code == "needs_folder_move"


def test_stale_uidvalidity_is_permanent(adapter_factory):
    fc = FakeClient(caps=["MOVE"], uidvalidity=999)
    with pytest.raises(PermanentActionError) as exc:
        adapter_factory(fc).apply_writeback(uidvalidity=100, uid=5, op="flag")
    assert exc.value.code == "stale"


def test_move_idempotent_skips_copy_when_already_in_target(adapter_factory):
    # A prior transient-failed attempt already COPYed the message to Archive.
    fc = FakeClient(caps=["UIDPLUS"], folders={"Archive": {(9, "<mid@x>")}})
    adapter_factory(fc).apply_writeback(uidvalidity=100, uid=5, op="move", target="Archive")
    assert "copy" not in fc.names()  # not duplicated
    assert ("expunge", [5]) in fc.calls  # source still expunged


def test_reverse_move_relocates_by_message_id(adapter_factory):
    # Message now lives in Archive with uid 9; reverse moves it back to INBOX.
    fc = FakeClient(caps=["MOVE"], folders={"Archive": {(9, "<mid@x>")}})
    adapter_factory(fc).reverse_writeback(
        op="move", mailbox="INBOX", message_id="<mid@x>", target="Archive"
    )
    assert ("move", [9], "INBOX") in fc.calls


def test_reverse_flag_removes_flag(adapter_factory):
    fc = FakeClient(caps=["IMAP4REV1"], folders={"INBOX": {(5, "<mid@x>")}})
    adapter_factory(fc).reverse_writeback(
        op="flag", mailbox="INBOX", message_id="<mid@x>", flag="\\Flagged"
    )
    assert ("remove_flags", [5], ["\\Flagged"]) in fc.calls


def test_reverse_move_not_found_is_permanent(adapter_factory):
    fc = FakeClient(caps=["MOVE"], folders={"Archive": set()})
    with pytest.raises(PermanentActionError) as exc:
        adapter_factory(fc).reverse_writeback(
            op="move", mailbox="INBOX", message_id="<mid@x>", target="Archive"
        )
    assert exc.value.code == "reverse_not_found"
    assert "move" not in fc.names()


def test_reverse_refuses_ambiguous_message_id(adapter_factory):
    # Two messages share the Message-ID in the target — the reverse must refuse
    # to guess which to move back rather than touch an arbitrary one (#6).
    fc = FakeClient(caps=["MOVE"], folders={"Archive": {(9, "<mid@x>"), (10, "<mid@x>")}})
    with pytest.raises(PermanentActionError) as exc:
        adapter_factory(fc).reverse_writeback(
            op="move", mailbox="INBOX", message_id="<mid@x>", target="Archive"
        )
    assert exc.value.code == "reverse_ambiguous"
    assert "move" not in fc.names()


# --------------------------------------------------------------------------- #
# Handler — ownership-not-visibility gate
# --------------------------------------------------------------------------- #
class _HandlerStore:
    """Serves the handler's signals-url + sources-ownership queries."""

    def __init__(self, *, signal_url, source_owner):
        self._signal_url = signal_url
        self._source_owner = source_owner  # user_id that owns the source, or None

    def fetch_one(self, sql, params=None):
        q = " ".join(sql.split()).lower()
        p = params or ()
        if "from signals where signal_id" in q:
            return {"url": self._signal_url} if self._signal_url else None
        if "from sources where id::text" in q:
            _sid, uid = p[0], p[1]
            if self._source_owner is not None and uid == self._source_owner:
                return {
                    "id": "11111111-1111-1111-1111-111111111111",
                    "name": "m",
                    "source_type": "imap",
                    "url": "imap://x",
                    "category": "",
                    "active": True,
                    "user_id": self._source_owner,
                }
            return None
        return None


@pytest.fixture
def no_adapter(monkeypatch):
    """Fail loudly if the handler ever constructs the IMAP adapter — used to
    prove no connection is opened on a denied (non-owned) write."""
    import adapters.email_imap_source as mod

    def _boom(*a, **k):
        raise AssertionError("EmailImapSource must not be constructed on denial")

    monkeypatch.setattr(mod, "EmailImapSource", _boom)
    return mod


def test_handler_denies_write_to_non_owned_source(no_adapter):
    # A shared signal owned by A; caller B does not own the source → denied,
    # and the adapter is never constructed (no load_connection for A).
    from actions.handlers.imap_writeback import _apply

    store = _HandlerStore(
        signal_url="imap://11111111-1111-1111-1111-111111111111/100/5",
        source_owner="A",  # source owned by A
    )
    config._instances["metadata_store"] = store
    try:
        with pytest.raises(PermanentActionError) as exc:
            _apply("flag", {"signal_id": "s1"}, user_id="B")  # B != owner
        assert exc.value.code == "not_found"
    finally:
        config._instances.pop("metadata_store", None)


def test_handler_rejects_non_imap_signal(no_adapter):
    from actions.handlers.imap_writeback import _apply

    store = _HandlerStore(signal_url="https://feed.example/x", source_owner="A")
    config._instances["metadata_store"] = store
    try:
        with pytest.raises(PermanentActionError) as exc:
            _apply("flag", {"signal_id": "s1"}, user_id="A")
        assert exc.value.code == "not_imap_signal"
    finally:
        config._instances.pop("metadata_store", None)


def test_handler_rejects_missing_signal(no_adapter):
    from actions.handlers.imap_writeback import _apply

    store = _HandlerStore(signal_url=None, source_owner="A")
    config._instances["metadata_store"] = store
    try:
        with pytest.raises(PermanentActionError) as exc:
            _apply("flag", {"signal_id": "nope"}, user_id="A")
        assert exc.value.code == "not_found"
    finally:
        config._instances.pop("metadata_store", None)
