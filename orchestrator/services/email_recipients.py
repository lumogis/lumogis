# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Recipient suggestions for the composer (LUM-818).

Composing mail in Lumogis means typing the whole address from memory. The
corpus needed to fix that already exists and is already assembled in core:
:func:`actions.handlers.smtp_send._is_first_contact` unions **received**
(``signals.from_addr``) with **sent** (``email_sent_log.to_addrs || cc_addrs``),
normalised on both sides. What did not exist was any way to *read* it — that
union was only ever a boolean membership test. This module is that read path.

Correctness, not convenience, is the argument. A send goes out **as the user**
and `_ADDR_RE` (LUM-650) only checks the shape ``local@domain.tld``, so
``jhon@example.com`` passes every gate Lumogis has.

**BCC IS NEVER A SOURCE.**
``actions/handlers/smtp_send.py`` and migration ``064-email-cc-bcc.sql`` both
say so in capitals, and give ``bcc_addrs`` no index so the mistake is hard to
make by accident: querying it would turn the first-contact gate into a side
channel over addresses the user deliberately kept private.

That reasoning binds **this** module harder than the gate it was written for.
The gate leaks at most one bit — *is this a first contact?* A suggestion list
prints the address onto a screen that may be shared or observed. Someone who
BCC'd a lawyer, a recruiter or a doctor precisely so it would not be visible
would find that address offered back to them. ``test_bcc_is_never_a_source``
is the guard; do not "unify" the two queries in a way that reaches it.

Commercial scope: **core / AGPL**. The corpus is `signals` + `email_sent_log`;
neither is the knowledge graph, so ADR-206 places this in core. Resolving a
*relationship* ("Mum") to a person is the premium layer above — LUM-654.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone

import config
from services import sql_like

_log = logging.getLogger(__name__)

MIN_PREFIX_CHARS = 2
"""Below this, refuse rather than return the corpus.

Mirrors `email_search`'s refusal to run an unusable query. A substring endpoint
over correspondents is an enumeration surface: it is the caller's OWN data, so
this is not cross-user leakage, but it does turn "reconstructable by scrolling"
into "exfiltrable in a few hundred requests" if a session is compromised.
"""

DEFAULT_LIMIT = 8
MAX_LIMIT = 25

_CANDIDATE_CAP = 200
"""Rows fetched per tier before ranking — NOT the number returned.

Ranking merges the sent and received sides in Python (sent-to outranks
received-from regardless of recency), so a `LIMIT 8` in SQL would let an
arbitrary slice of the received side crowd out a sent correspondent that should
have ranked first. The cap therefore has to sit well above the result limit for
the ranking to mean anything; it exists only so a two-character query against a
very large mailbox cannot pull every distinct correspondent into memory.

Ordered by recency, so when the cap does bite it drops the stalest candidates
rather than an arbitrary set.
"""

_NOREPLY_MARKERS = ("noreply", "no-reply", "no_reply", "donotreply", "do-not-reply")
"""Ranked last, deliberately NOT filtered out (owner decision, 2026-08-14).

Some ``no-reply`` addresses do accept mail, and this module is a memory aid
rather than a validator — silently hiding an address the user demonstrably
corresponded with would be a quiet wrong answer. Sinking them is enough: the
received corpus is dominated by newsletters, and without this the dropdown
buries real people under mailing lists.
"""


@dataclass(frozen=True)
class RecipientSuggestion:
    """One suggested recipient.

    ``display_name`` is available for **received** correspondents only —
    ``email_sent_log`` stores addr-specs without names. The asymmetry is real
    and is surfaced rather than papered over.
    """

    address: str
    display_name: str
    corresponded: bool
    """True when the user has SENT to this address, not merely received from it."""


def _escape_like(raw: str) -> str:
    """Neutralise LIKE metacharacters in caller input.

    Without this, a query of ``%`` matches the entire corpus — the exact
    enumeration :data:`MIN_PREFIX_CHARS` exists to bound.
    """
    return sql_like.escape_like(raw)


def _is_noreply(address: str) -> bool:
    local = address.split("@", 1)[0].lower()
    return any(marker in local for marker in _NOREPLY_MARKERS)


def _suppressed_addresses(ms, user_id: str) -> set[str]:
    """Addresses the user has silenced, in ANY of their mailboxes.

    Owner decision (2026-08-14): a suppressed sender does not autocomplete.
    Someone who hid a sender expressed a preference about seeing them, and
    offering them as a recipient is the opposite of honouring it.

    Suppression rules are per ``(user_id, source_id, from_addr)``, so an
    address hidden in one mailbox is excluded everywhere. That over-excludes
    slightly, and deliberately: the cost is typing an address by hand, which is
    exactly the status quo, while the alternative surfaces someone the user
    asked not to see.
    """
    try:
        rows = ms.fetch_all(
            # SCOPE-EXEMPT: the caller's own suppression rules; user_id-scoped.
            "SELECT DISTINCT lower(from_addr) AS a FROM email_sender_rule "
            "WHERE user_id = %s AND active = TRUE",
            (user_id,),
        )
    except Exception:  # noqa: BLE001 — see below
        # FAIL CLOSED, unlike `should_suppress` which fails OPEN. The postures
        # differ because the mistakes differ: there, degrading hides mail the
        # user wanted; here, degrading OFFERS a contact the user silenced.
        # Suggesting nothing is a missing convenience; suggesting a hidden
        # sender undoes a stated preference.
        _log.warning("email_recipients: suppression lookup failed; suggesting nothing")
        raise

    return {str(r["a"]) for r in (rows or []) if r.get("a")}


def _received(ms, user_id: str, like: str) -> dict[str, tuple[str, datetime | None, int]]:
    """``{address: (display_name, last_seen, count)}`` from mail received."""
    rows = ms.fetch_all(
        # SCOPE-EXEMPT: the caller's own correspondents. `visible_filter` is
        # deliberately NOT used — the household union would complete one
        # member's correspondents in another's composer.
        "SELECT lower(from_addr) AS addr, "
        "       MAX(from_display) AS display, "
        "       MAX(created_at)  AS last_at, "
        "       COUNT(*)         AS n "
        "FROM signals "
        # SCOPE-EXEMPT: repeated next to the predicate itself, not only above the
        # statement — the guard looks within 6 lines, and a marker that drifts out
        # of range as a query grows is a marker that silently stops being read.
        "WHERE user_id = %s AND from_addr <> '' "
        "  AND (lower(from_addr) LIKE %s ESCAPE '\\' "
        "       OR lower(from_display) LIKE %s ESCAPE '\\') "
        "GROUP BY 1 ORDER BY last_at DESC NULLS LAST LIMIT %s",
        (user_id, like, like, _CANDIDATE_CAP),
    )
    out: dict[str, tuple[str, datetime | None, int]] = {}
    for r in rows or []:
        addr = str(r.get("addr") or "")
        if addr:
            out[addr] = (str(r.get("display") or ""), r.get("last_at"), int(r.get("n") or 0))
    return out


def _sent(ms, user_id: str, like: str) -> dict[str, tuple[datetime | None, int]]:
    """``{address: (last_seen, count)}`` from mail sent.

    ``to_addrs || cc_addrs`` — and **never** ``bcc_addrs``. See the module
    docstring; this is the line that matters.
    """
    rows = ms.fetch_all(
        # SCOPE-EXEMPT: the caller's own sent log; user_id-scoped.
        "SELECT lower(t) AS addr, MAX(sent_at) AS last_at, COUNT(*) AS n "
        "FROM email_sent_log, unnest(to_addrs || cc_addrs) AS t "
        "WHERE user_id = %s AND lower(t) LIKE %s ESCAPE '\\' "
        "GROUP BY 1 ORDER BY last_at DESC NULLS LAST LIMIT %s",
        (user_id, like, _CANDIDATE_CAP),
    )
    out: dict[str, tuple[datetime | None, int]] = {}
    for r in rows or []:
        addr = str(r.get("addr") or "")
        if addr:
            out[addr] = (r.get("last_at"), int(r.get("n") or 0))
    return out


def _sort_key(s: RecipientSuggestion, last_at: datetime | None, count: int) -> tuple:
    """Sent-to first, then non-``no-reply``, then recency, then frequency.

    Ranking is not cosmetic here. The received corpus is mostly newsletters and
    automated senders — addresses you can never usefully write *to* — so a naive
    recency sort buries the handful of real people the user actually mails.
    """
    when = last_at or datetime.min.replace(tzinfo=timezone.utc)
    return (
        0 if s.corresponded else 1,
        1 if _is_noreply(s.address) else 0,
        -when.timestamp(),
        -count,
    )


def suggest_recipients(
    user_id: str,
    query: str,
    *,
    limit: int = DEFAULT_LIMIT,
) -> list[RecipientSuggestion]:
    """Return ranked recipient suggestions for ``query``.

    Substring match, not prefix-only: people search for a domain or a surname
    as readily as for the start of an address.

    Raises:
        ValueError: ``query`` is shorter than :data:`MIN_PREFIX_CHARS` after
            stripping. Refused rather than answered, so an empty field cannot
            page out the corpus.
    """
    cleaned = (query or "").strip().lower()
    if len(cleaned) < MIN_PREFIX_CHARS:
        raise ValueError(f"query must be at least {MIN_PREFIX_CHARS} characters")
    limit = max(1, min(int(limit), MAX_LIMIT))

    ms = config.get_metadata_store()
    like = f"%{_escape_like(cleaned)}%"

    suppressed = _suppressed_addresses(ms, user_id)
    received = _received(ms, user_id, like)
    sent = _sent(ms, user_id, like)

    ranked: list[tuple[tuple, RecipientSuggestion]] = []
    for addr in set(received) | set(sent):
        if addr in suppressed:
            continue
        display, r_last, r_n = received.get(addr, ("", None, 0))
        s_last, s_n = sent.get(addr, (None, 0))
        suggestion = RecipientSuggestion(
            address=addr,
            display_name=display,
            corresponded=addr in sent,
        )
        last_at = max((d for d in (r_last, s_last) if d is not None), default=None)
        ranked.append((_sort_key(suggestion, last_at, r_n + s_n), suggestion))

    ranked.sort(key=lambda pair: pair[0])
    return [s for _, s in ranked[:limit]]
