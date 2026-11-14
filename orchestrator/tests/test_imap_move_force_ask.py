# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-729 follow-up (FP-077) — a Do-mode `imap_move` must be Asked.

LUM-729 gated the two standing policies that take mail out of `INBOX`
(folder-sort and the cleanup window), then the mirror (connecting paperless while
one is already sorting). `imap_move` is the path that was left: LUM-623's
per-message write-back, which runs under whatever permission mode the `imap`
connector is set to. Set that to Do and an agent can move a message out of
`INBOX` before paperless consumes it, with nobody looking.

It is a different **shape** from the others — one message, chosen by the agent,
not a standing policy — so a consent dialog is the wrong instrument. The right
one already exists: LUM-650's create-time annotation, which forces an explicit
Ask even in Do mode.

Two things here are decisions rather than plumbing.

**The resolver reads a new key.** `_resolve_force_ask` read only `first_contact`.
LUM-697 could honestly set that for unsubscribe — an unsubscribe address *is* one
you have never emailed. A folder move is not first contact under any reading, so
the resolver learned `force_ask` instead of the field name being bent.

**The hook raises where the route swallows.** `has_external_consumer` fails OPEN,
because a broken guard must not stop someone configuring their own mailbox. This
hook calls the **strict** variant, because `_compute_annotation` fails CLOSED and
the failure it is protecting against here is an unreviewed mailbox mutation, not
a blocked settings page. Same question, opposite direction, on purpose.
"""

from __future__ import annotations

import pytest
from actions import executor
from actions.handlers import imap_writeback as iw

import config


class _Store:
    def __init__(self, paperless: bool):
        self.paperless = paperless

    def fetch_one(self, sql, params=None):
        if "source_type = 'paperless'" in " ".join(sql.split()):
            return {"present": 1} if self.paperless else None
        return None


class _Broken:
    def fetch_one(self, sql, params=None):
        raise RuntimeError("postgres is down")


# --- the hook ----------------------------------------------------------------


def test_a_move_is_force_asked_when_paperless_is_configured(monkeypatch):
    monkeypatch.setattr(config, "get_metadata_store", lambda: _Store(True))

    assert iw._annotate_move({}, "alice") == {"force_ask": True, "external_consumer": True}


class _PredicateStore:
    """One paused paperless row; honours ``active = TRUE`` only if the SQL has it."""

    def fetch_one(self, sql, params=None):
        q = " ".join(sql.split())
        if "source_type = 'paperless'" in q and "active = TRUE" not in q:
            return {"present": 1}
        return None


def test_a_move_is_force_asked_when_the_only_paperless_source_is_PAUSED(monkeypatch):
    """paperless keeps consuming mail while Lumogis's poll is paused (LUM-844,
    ADR 192 amendment), so pausing the source must not turn the Do-mode move
    force-Ask off. The store evaluates the predicate, unlike ``_Store``."""
    monkeypatch.setattr(config, "get_metadata_store", lambda: _PredicateStore())

    assert iw._annotate_move({}, "alice") == {"force_ask": True, "external_consumer": True}


def test_a_move_is_NOT_force_asked_without_paperless(monkeypatch):
    """The gate must be invisible to everyone who does not run paperless.
    Force-asking every move would make Do mode useless for the ordinary case."""
    monkeypatch.setattr(config, "get_metadata_store", lambda: _Store(False))

    assert iw._annotate_move({}, "alice") == {"force_ask": False, "external_consumer": False}


def test_the_hook_RAISES_rather_than_failing_open(monkeypatch):
    """**The load-bearing difference.**

    `has_external_consumer` swallows and returns False. Calling it here would
    have meant a database blip silently permits an unreviewed move.
    `_compute_annotation` fails closed on a raised exception, so raising is what
    wires this into that posture — asserted, because the tempting one-word change
    to the fail-open helper would be invisible in review.
    """
    monkeypatch.setattr(config, "get_metadata_store", lambda: _Broken())

    with pytest.raises(Exception):
        iw._annotate_move({}, "alice")


def test_a_raising_hook_becomes_force_ask_at_the_proposal_layer(monkeypatch):
    """The other half of the same statement: proving the raise is actually
    converted into a gate rather than a 500."""
    from services import action_proposals

    monkeypatch.setattr(config, "get_metadata_store", lambda: _Broken())
    spec = type("S", (), {"annotate": staticmethod(iw._annotate_move), "name": "imap_move"})()

    assert action_proposals._compute_annotation(spec, {}, "alice") == {"first_contact": True}


# --- which ops carry it ------------------------------------------------------


def test_ONLY_move_carries_the_annotation():
    """`mark_read`, `flag` and `label` leave the message in INBOX, so paperless
    still consumes it. Annotating them would train people to click through the
    one op that matters."""
    from actions.registry import get_action

    assert get_action("imap_move").annotate is iw._annotate_move
    for name in ("imap_mark_read", "imap_flag", "imap_label"):
        assert get_action(name).annotate is None, f"{name} does not move mail"


# --- the resolver ------------------------------------------------------------


class _Spec:
    is_write = True

    def __init__(self, ann):
        self._ann = ann

    def annotate(self, input, user_id):
        return self._ann


def test_the_resolver_honours_the_new_force_ask_key():
    spec = _Spec({"force_ask": True})

    assert executor._resolve_force_ask(spec, {}, "u", approved=False, annotation=None) is True


def test_the_resolver_still_honours_first_contact():
    """LUM-650's key must keep working — it is what the Approvals banner reads."""
    spec = _Spec({"first_contact": True})

    assert executor._resolve_force_ask(spec, {}, "u", approved=False, annotation=None) is True


def test_neither_key_means_no_force_ask():
    spec = _Spec({"force_ask": False, "external_consumer": False})

    assert executor._resolve_force_ask(spec, {}, "u", approved=False, annotation=None) is False


def test_the_STORED_annotation_wins_over_a_recompute():
    """LUM-650's rule, unchanged and re-asserted for the new key: on the claimed
    proposal path the gate must match what the human was shown, not what the
    world looks like now."""
    spec = _Spec({"force_ask": False})

    assert (
        executor._resolve_force_ask(spec, {}, "u", approved=False, annotation={"force_ask": True})
        is True
    )


def test_an_action_with_no_hook_is_never_force_asked():
    """The resolver's first rule. Widening the key set must not accidentally
    start gating every write in the system."""
    spec = type("S", (), {"is_write": True, "annotate": None})()

    assert executor._resolve_force_ask(spec, {}, "u", approved=False, annotation=None) is False
