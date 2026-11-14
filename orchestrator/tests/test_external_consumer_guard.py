# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-729 — the predicate, the existence query, and the copy.

The predicate has **two arming clauses and either one alone looks complete**,
which is the entire reason this file exists.

The first draft of the plan gated on the *value* of `organize_folder`. Review
corrected that to the OFF→ON *transition*, because `EmailOrganizeUpdate` is a
full replace and reading the value would re-prompt a user every time they edited
their category list. That correction was right, and it opened a hole:
`apply_to_existing` sets `organize_since` to epoch — a retroactive bulk move over
the whole mailbox — and an already-sorting user who ticks it never crosses the
transition. The fix for a harmless re-prompt let through the single most
destructive request the route accepts.

So both clauses are mutation targets here. Delete either one and a test fails.
"""

from __future__ import annotations

import logging

import pytest

import config
from services import external_consumer_guard as ecg

# --- the predicate: two clauses, neither sufficient --------------------------


def test_turning_folder_sort_on_arms_it():
    assert (
        ecg.organize_arming(currently_on=False, requested_on=True, apply_to_existing=False)
        == ecg.ARM_ENABLE
    )


def test_apply_to_existing_on_an_ALREADY_sorting_source_arms_it():
    """**Mutation target for the second clause.**

    This is the case the transition-only rule missed. It is also the worst one:
    `organize_since = 'epoch'` widens the standing sweep across all history, so
    every classified message the mailbox ever received moves at once.
    """
    assert (
        ecg.organize_arming(currently_on=True, requested_on=True, apply_to_existing=True)
        == ecg.ARM_RETROACTIVE
    )


def test_apply_to_existing_wins_over_a_plain_enable():
    """Both clauses fire at once when a first-time enable also backfills. The
    retroactive copy is the one that must be shown — it names the larger action."""
    assert (
        ecg.organize_arming(currently_on=False, requested_on=True, apply_to_existing=True)
        == ecg.ARM_RETROACTIVE
    )


def test_editing_categories_while_already_sorting_is_NOT_arming():
    """**Mutation target for the first clause.**

    The PATCH body is a full replace, so `organize_folder: true` arrives on every
    save. Reading the value instead of the change would toll a user on every
    category edit — consent for arming the trap, not a toll on living with it.
    """
    assert (
        ecg.organize_arming(currently_on=True, requested_on=True, apply_to_existing=False) is None
    )


def test_turning_folder_sort_OFF_is_never_arming():
    """Reducing the harm must never be obstructed."""
    assert (
        ecg.organize_arming(currently_on=True, requested_on=False, apply_to_existing=False) is None
    )


def test_turning_it_off_WITH_apply_to_existing_is_still_not_arming():
    """`apply_to_existing` without folder-sort moves nothing — `organize_since`
    widens a sweep that has no move to perform. Gating it would refuse a request
    that cannot cause the harm."""
    assert (
        ecg.organize_arming(currently_on=True, requested_on=False, apply_to_existing=True) is None
    )


def test_leaving_it_off_is_not_arming():
    assert (
        ecg.organize_arming(currently_on=False, requested_on=False, apply_to_existing=False) is None
    )


# --- the existence query -----------------------------------------------------


class _Store:
    def __init__(self, row=None):
        self.row = row
        self.queries: list[tuple] = []

    def fetch_one(self, sql, params=None):
        self.queries.append((" ".join(sql.split()), params))
        return self.row


def test_an_active_paperless_source_is_an_external_consumer(monkeypatch):
    st = _Store({"present": 1})
    monkeypatch.setattr(config, "get_metadata_store", lambda: st)

    assert ecg.has_external_consumer("alice") is True


def test_no_paperless_source_means_no_consumer(monkeypatch):
    st = _Store(None)
    monkeypatch.setattr(config, "get_metadata_store", lambda: st)

    assert ecg.has_external_consumer("alice") is False


def test_the_query_is_scoped_to_the_caller_and_counts_PAUSED_sources(monkeypatch):
    """A household member's paperless source must not gate another member's
    mailbox, and a source they *paused* still gates them.

    Inverted by LUM-844 (amends ADR 192). The old name and docstring said "a
    source they disabled must not gate them at all" — written while
    ``sources.active`` could never be false. Paused is not gone: paperless-ngx
    runs its own mail rules whether or not Lumogis polls it.
    """
    st = _Store(None)
    monkeypatch.setattr(config, "get_metadata_store", lambda: st)

    ecg.has_external_consumer("alice")

    sql, params = st.queries[0]
    assert "user_id = %s" in sql
    assert "source_type = 'paperless'" in sql
    assert "active" not in sql
    assert params == ("alice",)


class _RowStore:
    """Holds rows and evaluates the ``active = TRUE`` predicate **only if the SQL
    asks for it** — unlike ``_Store``, which answers a fixed value and so cannot
    tell whether the predicate is in the query at all."""

    def __init__(self, rows):
        self.rows = rows

    def fetch_one(self, sql, params=None):
        q = " ".join(sql.split()).lower()
        (user_id,) = params
        for row in self.rows:
            if row["user_id"] != user_id or row["source_type"] != "paperless":
                continue
            if "active = true" in q and not row["active"]:
                continue
            return {"present": 1}
        return None


def test_a_paused_paperless_source_is_still_an_external_consumer(monkeypatch):
    """LUM-844 — paused is not gone. paperless keeps reading the mailbox on its
    own schedule, so a Lumogis-side pause must not lift the starvation warning or
    the Do-mode move force-Ask. **Mutation target:** restoring ``AND active = TRUE``
    to the query fails this."""
    st = _RowStore([{"user_id": "alice", "source_type": "paperless", "active": False}])
    monkeypatch.setattr(config, "get_metadata_store", lambda: st)

    assert ecg.has_external_consumer_strict("alice") is True
    assert ecg.has_external_consumer("alice") is True
    assert ecg.has_external_consumer("bob") is False  # household isolation


def test_the_query_is_parameterised(monkeypatch):
    st = _Store(None)
    monkeypatch.setattr(config, "get_metadata_store", lambda: st)

    ecg.has_external_consumer("'; DROP TABLE sources; --")

    sql, params = st.queries[0]
    assert "DROP TABLE" not in sql
    assert params == ("'; DROP TABLE sources; --",)


# --- fail-open, and the WARNING that stops it being silent -------------------


class _Broken:
    def fetch_one(self, sql, params=None):
        raise RuntimeError("postgres is down")


def test_a_failing_guard_fails_OPEN(monkeypatch):
    """A broken guard must not stop a user configuring their own mailbox. The
    bug it prevents is latent; the outage it would cause is immediate."""
    monkeypatch.setattr(config, "get_metadata_store", lambda: _Broken())

    assert ecg.has_external_consumer("alice") is False


def test_failing_open_logs_a_WARNING(monkeypatch, caplog):
    """**Fail-open is not free.**

    On a real database outage the caller's own UPDATE fails immediately anyway,
    so this branch only ever absorbs guard-*specific* bugs — and would absorb
    them invisibly. The log is what makes it a failure rather than a silence.
    """
    monkeypatch.setattr(config, "get_metadata_store", lambda: _Broken())

    with caplog.at_level(logging.WARNING, logger=ecg.__name__):
        ecg.has_external_consumer("alice")

    assert any(r.levelno >= logging.WARNING for r in caplog.records), "failed open silently"
    assert "UNGATED" in caplog.text


# --- the copy ----------------------------------------------------------------


@pytest.mark.parametrize("arming", [ecg.ARM_ENABLE, ecg.ARM_RETROACTIVE, ecg.ARM_BULK_MOVE])
def test_every_arming_event_names_the_consequence_not_the_error(arming):
    """The gate exists because the consequence was invisible. A message that only
    said "external_consumer_may_starve" would reproduce the bug in words."""
    msg = ecg.message_for(arming)

    assert "paperless" in msg.lower()
    assert "inbox" in msg.lower()
    assert len(msg) > 60


def test_the_retroactive_message_says_it_touches_MAIL_YOU_ALREADY_HAVE():
    """The distinction the second clause exists for. If the copy did not draw it,
    gating the case would still leave the user uninformed about the scale."""
    msg = ecg.message_for(ecg.ARM_RETROACTIVE).lower()

    assert "already" in msg
    assert "already" not in ecg.message_for(ecg.ARM_ENABLE).lower()


def test_the_alternative_is_offered_without_a_provider_condition():
    """D4, reversed. `_apply_label` and `_apply_move` are separate operations read
    from two independent booleans, so labels-only leaves mail in INBOX on every
    provider. Naming Gmail here would withhold the fix from the users with the
    fewest options."""
    alt = ecg.ALTERNATIVE.lower()

    assert "label" in alt
    assert "gmail" not in alt


def test_the_recovery_caveat_does_not_promise_paperless_gets_what_it_missed():
    """Undo puts messages back in INBOX. It does not hand paperless the documents
    it never saw. A warning that implied otherwise would be the same silent hole
    with a reassuring message on top."""
    rec = ecg.RECOVERY_CAVEAT.lower()

    assert "does not" in rec
    assert "missed" in rec


def test_the_refusal_body_carries_a_stable_code_and_the_copy():
    """`code` is what the web branches on; the prose may be reworded freely. A UI
    matching on copy would silently stop recognising the refusal."""
    body = ecg.refusal_detail(ecg.ARM_ENABLE)

    assert body["code"] == "external_consumer_may_starve"
    assert body["message"] and body["alternative"] and body["recovery"]


def test_an_unknown_arming_still_yields_a_usable_message():
    """Degrades to the general case rather than raising — a guard that 500s is
    worse than a guard that under-explains."""
    assert ecg.message_for("something-added-later") == ecg.message_for(ecg.ARM_ENABLE)


# --- the MIRROR: already folder-sorting, now connecting paperless ------------
#
# Found in code review, after the rest had shipped. The whole plan reasoned about
# arming organize WITH paperless present and never about arming paperless WITH
# organize present. Identical harm, mirrored — and Lumogis-first-then-paperless is
# at least as likely an ordering as the one that was covered.


def test_a_folder_sorting_mailbox_is_detected(monkeypatch):
    st = _Store({"present": 1})
    monkeypatch.setattr(config, "get_metadata_store", lambda: st)

    assert ecg.has_folder_sorting_mailbox("alice") is True


def test_no_folder_sorting_mailbox_means_nothing_to_warn_about(monkeypatch):
    st = _Store(None)
    monkeypatch.setattr(config, "get_metadata_store", lambda: st)

    assert ecg.has_folder_sorting_mailbox("alice") is False


def test_the_mirror_query_requires_the_OPT_IN_not_just_a_mailbox(monkeypatch):
    """Every user with email has an imap source. Only the ones with folder-sort
    ON are moving mail, and warning the rest would make the gate noise."""
    st = _Store(None)
    monkeypatch.setattr(config, "get_metadata_store", lambda: st)

    ecg.has_folder_sorting_mailbox("alice")

    sql, params = st.queries[0]
    assert "organize_folder_enabled = TRUE" in sql
    assert "source_type = 'imap'" in sql
    assert "active = TRUE" in sql
    assert "user_id = %s" in sql
    assert params == ("alice",)


def test_the_mirror_also_fails_OPEN_with_a_WARNING(monkeypatch, caplog):
    monkeypatch.setattr(config, "get_metadata_store", lambda: _Broken())

    with caplog.at_level(logging.WARNING, logger=ecg.__name__):
        assert ecg.has_folder_sorting_mailbox("alice") is False

    assert "UNGATED" in caplog.text


def test_the_connect_message_addresses_the_person_setting_up_PAPERLESS():
    """The other two messages tell an organize user about paperless. This one
    tells a paperless user about organize, and has to name the fix available to
    THEM — the mailbox setting, not the paperless setting."""
    msg = ecg.message_for(ecg.ARM_CONNECT_PAPERLESS).lower()

    assert "folder" in msg
    assert "inbox" in msg
    assert "turn folder sorting off" in msg
