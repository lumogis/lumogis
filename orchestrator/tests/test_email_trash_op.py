# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-646 — adapter-level coverage of the cleanup write primitives on EmailImapSource:

* ``apply_organize(op="trash")`` — resolves the provider's ``\\Trash`` SPECIAL-USE folder,
  moves there, and returns an ``op:"move"`` reverse token pointing back at the source
  mailbox (so undo restores it). ``no_trash_folder`` when the server advertises none.
* ``enumerate_role_folder`` — the spam-walk analogue of ``backfill_slice`` that targets an
  arbitrary role folder (``\\Junk``), NOT INBOX.

A fake ``imapclient`` (no real server) records the STORE/MOVE/LIST calls.
"""

from __future__ import annotations

import pytest
from actions.errors import PermanentActionError
from models.signals import SourceConfig


class FakeClient:
    def __init__(
        self,
        *,
        caps=("MOVE",),
        uidvalidity=100,
        message_id="<mid@x>",
        folders=None,
        uids_by_folder=None,
    ):
        self._caps = [c.encode() for c in caps]
        self.uidvalidity = uidvalidity
        self.message_id = message_id
        # list_folders rows: (flags_tuple, delimiter, name)
        self._folders = folders or [((), b"/", "INBOX")]
        self._uids_by_folder = uids_by_folder or {}
        self.calls: list = []
        self.selected = None

    def list_folders(self):
        return list(self._folders)

    def select_folder(self, name, readonly=False):
        self.selected = name
        self.calls.append(("select", name, readonly))
        return {b"UIDVALIDITY": self.uidvalidity, b"UIDNEXT": 999, b"MESSAGES": 3}

    def capabilities(self):
        return tuple(self._caps)

    def fetch(self, uids, keys):
        uid = uids[0]
        if self.message_id is None:
            return {uid: {}}
        hdr = ("Message-ID: %s\r\n" % self.message_id).encode()
        return {uid: {b"BODY[HEADER.FIELDS (MESSAGE-ID)]": hdr}}

    def search(self, criteria):
        if criteria and criteria[0] == "UID":
            return list(self._uids_by_folder.get(self.selected, []))
        return []

    def move(self, uids, folder):
        self.calls.append(("move", list(uids), folder))

    def copy(self, uids, folder):
        self.calls.append(("copy", list(uids), folder))

    def add_flags(self, uids, flags):
        self.calls.append(("add_flags", list(uids), list(flags)))

    def expunge(self, uids=None):
        self.calls.append(("expunge", list(uids) if uids else None))

    def logout(self):
        self.calls.append(("logout",))

    def names(self):
        return [c[0] for c in self.calls]


_GMAIL_FOLDERS = [
    ((), b"/", "INBOX"),
    ((b"\\Junk",), b"/", "[Gmail]/Spam"),
    ((b"\\Trash",), b"/", "[Gmail]/Trash"),
]


def _source(user_id="A"):
    return SourceConfig(
        id="11111111-1111-1111-1111-111111111111",
        name="mbox",
        source_type="imap",
        url="imap://imap.gmail.com:993/personal",
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

    return lambda fake: EmailImapSource(_source(), client_factory=lambda conn: fake)


def test_trash_moves_to_trash_role_folder_and_reverse_token(adapter_factory):
    fc = FakeClient(caps=("MOVE",), folders=_GMAIL_FOLDERS)
    rev = adapter_factory(fc).apply_organize(
        uidvalidity=100, uid=5, op="trash", label="", mailbox="INBOX"
    )
    # Moved to the \Trash-role folder (server-reported name), not a hard-coded string.
    assert ("move", [5], "[Gmail]/Trash") in fc.calls
    # Reverse token is op:"move" back to the source mailbox — undo restores it.
    assert rev == {
        "op": "move",
        "mailbox": "INBOX",
        "message_id": "<mid@x>",
        "target": "[Gmail]/Trash",
    }


def test_trash_from_junk_mailbox_restores_to_junk(adapter_factory):
    # The spam-empty pass passes mailbox=<junk folder>; the reverse token must carry it.
    fc = FakeClient(caps=("MOVE",), folders=_GMAIL_FOLDERS)
    rev = adapter_factory(fc).apply_organize(
        uidvalidity=100, uid=7, op="trash", label="", mailbox="[Gmail]/Spam"
    )
    assert rev["mailbox"] == "[Gmail]/Spam" and rev["target"] == "[Gmail]/Trash"


def test_trash_no_trash_folder_raises(adapter_factory):
    fc = FakeClient(caps=("MOVE",), folders=[((), b"/", "INBOX")])  # no \Trash special-use
    with pytest.raises(PermanentActionError) as exc:
        adapter_factory(fc).apply_organize(uidvalidity=100, uid=5, op="trash", label="")
    assert exc.value.code == "no_trash_folder"


def test_enumerate_role_folder_targets_junk_not_inbox(adapter_factory):
    fc = FakeClient(
        caps=("MOVE",), folders=_GMAIL_FOLDERS, uids_by_folder={"[Gmail]/Spam": [1, 2, 3]}
    )
    adapter = adapter_factory(fc)
    folder, uidvalidity, uids, advance, empty = adapter.enumerate_role_folder(
        fc, role="junk", after_uid=0, max_uid=100
    )
    assert folder == "[Gmail]/Spam"  # the \Junk-role folder, NOT INBOX
    assert fc.selected == "[Gmail]/Spam"
    assert uids == [1, 2, 3] and advance == 3 and empty is False


def test_enumerate_role_folder_absent_role_is_empty(adapter_factory):
    fc = FakeClient(folders=[((), b"/", "INBOX")])
    folder, _uv, uids, _adv, empty = adapter_factory(fc).enumerate_role_folder(
        fc, role="junk", after_uid=0, max_uid=100
    )
    assert folder is None and uids == [] and empty is True
