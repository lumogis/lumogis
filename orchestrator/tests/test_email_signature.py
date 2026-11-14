# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Per-mailbox outgoing signature (LUM-817).

Two properties carry the ticket's decisions rather than plumbing:

* **Per mailbox, not per user.** A household member with a personal mailbox and
  a shared one needs different signatures, and a per-user signature would sign
  shared mail with private details.
* **Never on `email_unsubscribe`.** That message is a machine-read `mailto:`
  command; a signature on it hands a list operator the user's name and phone
  number. It is excluded by construction — unsubscribe never touches the
  composer, and its body is a hardcoded literal — and pinned here so a future
  server-side append cannot quietly reintroduce it.
"""

from __future__ import annotations

import pytest

import config
from services import email_signature


class _Store:
    def __init__(self):
        self.rows: dict[tuple[str, str], str] = {}
        self.updates: list[tuple] = []

    def seed(self, source_id, user_id, signature=""):
        self.rows[(source_id, user_id)] = signature

    def fetch_one(self, sql, params=None):
        sid, uid = params
        if (sid, uid) not in self.rows:
            return None
        if "SELECT 1 AS ok" in sql:
            return {"ok": 1}
        return {"signature": self.rows[(sid, uid)]}

    def fetch_all(self, sql, params=None):
        return []

    def execute(self, sql, params=None):
        self.updates.append(params)
        text, sid, uid = params
        if (sid, uid) in self.rows:  # ownership is the WHERE clause
            self.rows[(sid, uid)] = text


@pytest.fixture
def store(monkeypatch):
    s = _Store()
    monkeypatch.setattr(config, "get_metadata_store", lambda: s)
    return s


# ---------------------------------------------------------------------------
# Per mailbox, not per user
# ---------------------------------------------------------------------------


def test_two_mailboxes_of_the_same_user_hold_different_signatures(store):
    """The reason this is a `sources` column rather than a `users` one."""
    store.seed("personal", "alice")
    store.seed("shared", "alice")

    email_signature.set_signature("personal", "alice", "Alice\n07700 900000")
    email_signature.set_signature("shared", "alice", "The Smiths")

    assert email_signature.get_signature("personal", "alice") == "Alice\n07700 900000"
    assert email_signature.get_signature("shared", "alice") == "The Smiths"


def test_another_users_mailbox_is_not_readable(store):
    store.seed("bobs-box", "bob", "Bob Jones")

    assert email_signature.get_signature("bobs-box", "alice") == ""


def test_another_users_mailbox_is_not_writable(store):
    """Ownership is the WHERE clause, so a foreign write touches zero rows."""
    store.seed("bobs-box", "bob", "Bob Jones")

    email_signature.set_signature("bobs-box", "alice", "Not Bob")

    assert store.rows[("bobs-box", "bob")] == "Bob Jones"


def test_an_unset_signature_is_empty_not_none(store):
    """Every read site wants 'text to append'; None would need a check at each."""
    store.seed("m1", "alice")

    assert email_signature.get_signature("m1", "alice") == ""


def test_a_missing_mailbox_reads_as_empty_rather_than_raising(store):
    assert email_signature.get_signature("nope", "alice") == ""


# ---------------------------------------------------------------------------
# Bounds and hygiene
# ---------------------------------------------------------------------------


def test_an_oversized_signature_is_refused(store):
    store.seed("m1", "alice")

    with pytest.raises(email_signature.SignatureTooLong):
        email_signature.set_signature("m1", "alice", "x" * 3000)


def test_the_cap_is_measured_in_bytes_not_characters(store):
    """A signature in a non-Latin script must not get a smaller allowance."""
    store.seed("m1", "alice")
    # 3 bytes per char in UTF-8 — under the char count, over the byte cap.
    too_long = "あ" * 800

    with pytest.raises(email_signature.SignatureTooLong):
        email_signature.set_signature("m1", "alice", too_long)


def test_surrounding_whitespace_is_trimmed(store):
    """A trailing newline would compound with the blank line the composer adds."""
    store.seed("m1", "alice")

    stored = email_signature.set_signature("m1", "alice", "\n\n  Alice  \n\n")

    assert stored == "Alice"


def test_an_empty_signature_clears_it(store):
    store.seed("m1", "alice", "Alice")

    email_signature.set_signature("m1", "alice", "")

    assert email_signature.get_signature("m1", "alice") == ""


# ---------------------------------------------------------------------------
# The exclusion that matters
# ---------------------------------------------------------------------------


def test_unsubscribe_carries_no_signature():
    """A signature on an unsubscribe hands a list operator the user's details.

    `email_unsubscribe` builds a hardcoded body and never routes through the
    composer, so it is excluded by construction. This asserts the construction
    rather than trusting it: if a future change makes the unsubscribe body
    user-supplied, or adds a server-side append, this fails.
    """
    from pathlib import Path

    src = Path(__file__).resolve().parent.parent / "actions" / "handlers" / "smtp_send.py"
    text = src.read_text(encoding="utf-8")

    assert 'body="unsubscribe"' in text, (
        "the unsubscribe body is no longer a hardcoded literal — re-check that a "
        "signature cannot reach it"
    )
    assert "signature" not in text.lower(), (
        "smtp_send.py must not append signatures: the composer already seeds one "
        "into the visible body, so a server-side append would double it"
    )
