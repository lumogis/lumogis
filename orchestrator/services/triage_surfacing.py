# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Server-side "needs you" email selection + human copy (LUM-648).

The single source of truth the proactive-surfacing jobs (``signals/triage_reminders``)
use to (a) count how many emails currently *need* a user and (b) turn a signal /
count into the meaningful sentences a push or in-app event carries.

The count mirrors the frontend ``effectiveLane`` needs-you set exactly, email-scoped:

* household-union visibility (``visible_filter``) so shared email counts, via a
  **non-privileged** :class:`~auth.UserContext` reconstructed from the user_id
  (``role="user"``, ``is_authenticated=False``) — never admin (LUM-577);
* ``url LIKE 'imap://%'`` so only email rows count;
* the LUM-643 triage LATERAL: ``triage_status='needs_you'`` OR
  (untriaged AND (``notified`` OR ``importance_score >= 0.6``)), excluding
  ``done`` and unexpired ``snoozed``;
* **owner/projection dedup** (LUM-661 parity): a self-shared email produces both a
  ``scope='personal'`` original and a ``scope='shared'`` projection (distinct
  ``signal_id``s, both carrying the copied ``notified``/``importance_score``). The
  count collapses them via ``COUNT(DISTINCT COALESCE(published_from, signal_id))``
  — the exact key the frontend ``collapseProjections`` uses — so the brief reports
  the same number the ``/email`` lanes show.

Reminder *delivery* wiring lives in ``signals/triage_reminders``; this module owns
the selection SQL + copy only.
"""

from __future__ import annotations

import logging

from auth import UserContext
from visibility import visible_filter

import config

_log = logging.getLogger(__name__)

NEEDS_YOU_IMPORTANCE = 0.6

# Deep-link target for every proactive email notification.
EMAIL_TARGET_PATH = "/email"


def _needs_you_email_where(user_id: str) -> tuple[str, list]:
    """Build the (where, params) for the needs-you email selection for ``user_id``.

    The triage LATERAL's ``st.user_id = %s`` is the FIRST placeholder in the
    assembled query (FROM emits before WHERE), so the caller's id is prepended to
    ``params`` — mirroring ``routes.signals.list_signals``.
    """
    # Non-privileged reconstructed context: is_authenticated=False blocks the
    # admin-shared bypass, and role="user" is a real Role literal
    # (auth.Role = Literal["admin","user"]). allows_shared stays None so
    # _effective_allows_shared defers to users.get_allows_shared — a personal-only
    # member (LUM-577) correctly excludes shared rows.
    user = UserContext(user_id=user_id, role="user", is_authenticated=False)

    params: list = [user_id]  # the LATERAL's st.user_id
    where_clause, where_params = visible_filter(user, None)
    conditions = [where_clause]
    params.extend(where_params)

    # Email-scoped (parameterised; the % is a literal in the bound value).
    conditions.append("url LIKE %s")
    params.append("imap://%")

    # effectiveLane needs-you mirror: explicit needs_you, or untriaged-and-derived
    # needs-you, excluding done and unexpired snoozed. An expired snooze
    # (snoozed_until <= now) counts (query-time re-surface parity).
    conditions.append(
        "("
        "t.triage_status = 'needs_you' "
        "OR (t.triage_status IS NULL AND (notified OR importance_score >= %s)) "
        "OR (t.triage_status = 'snoozed' AND t.snoozed_until <= now())"
        ")"
    )
    params.append(NEEDS_YOU_IMPORTANCE)

    return " AND ".join(conditions), params


def count_needs_you_email(user_id: str) -> int:
    """Return how many distinct emails currently *need* ``user_id`` (0 on error).

    Deduplicates owner/projection pairs via
    ``COUNT(DISTINCT COALESCE(published_from, signal_id))`` so a self-shared email
    counts once — matching the ``/email`` lanes' ``collapseProjections``.
    """
    from services.signal_triage import _TRIAGE_LATERAL

    try:
        # _needs_you_email_where resolves allows_shared via a DB read (LUM-577), so
        # it must be inside the guard too — the whole count is best-effort (0 on any
        # error) so a transient DB hiccup never crashes the brief sweep.
        where, params = _needs_you_email_where(user_id)
        sql = (
            "SELECT COUNT(DISTINCT COALESCE(published_from, signal_id)) AS n "
            f"FROM signals {_TRIAGE_LATERAL} WHERE {where}"
        )
        ms = config.get_metadata_store()
        row = ms.fetch_one(sql, tuple(params))
        return int(row["n"]) if row and row.get("n") is not None else 0
    except Exception as exc:
        _log.warning("count_needs_you_email failed user_id=%s: %s", user_id, exc)
        return 0


def _clean(text: str | None) -> str:
    return " ".join((text or "").split())


def needs_you_copy(signal_like: dict, *, count: int = 1) -> tuple[str, str]:
    """Return (title, body) for a needs-you push.

    ``count == 1`` → sender + subject of the single email; ``count > 1`` → a grouped
    summary ("N emails are back"). ``signal_like`` may be a dict-like row with
    ``from_display`` / ``from_addr`` / ``title``; only used when ``count == 1``.
    """
    if count > 1:
        return ("Emails need you", f"{count} snoozed emails are back")
    sender = _clean(signal_like.get("from_display")) or _clean(signal_like.get("from_addr"))
    subject = _clean(signal_like.get("title")) or "(no subject)"
    title = f"{sender} needs you" if sender else "An email needs you"
    return (title, subject)


def brief_copy(count: int) -> tuple[str, str]:
    """Return (title, body) for the morning brief (caller guarantees count > 0)."""
    noun = "email needs" if count == 1 else "emails need"
    return ("Morning brief", f"{count} {noun} you today")
