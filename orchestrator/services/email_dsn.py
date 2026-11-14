# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Delivery-status notification parsing (LUM-820).

SMTP submission succeeding means the **provider accepted** the message, not that
it was delivered. A bounce arrives minutes or hours later as a new message from
`MAILER-DAEMON`. Today Lumogis does nothing with it: the audit row still says
the send succeeded, and the bounce lands in the triage lanes as ordinary mail —
quite possibly filed good-to-know and never opened.

So the failure is not a missing convenience: **the user believes a message was
delivered when it was not**, and the evidence is somewhere they may never look.

This module is the parsing half — a pure function over MIME bytes, with no
database and no network, because that is the part where being wrong is quiet.

**Recognised by structure, never by sender.** RFC 3464 defines
``multipart/report; report-type=delivery-status``. The `MAILER-DAEMON` display
name is not standardised and differs per provider, so matching on it would both
miss real bounces and fire on ordinary mail that mentions the phrase.

**Hard vs soft is the `Status:` field, not the prose.** A 5.x.x is permanent and
is what the user needs telling about. A 4.x.x is a retry still in progress —
alarming someone about a message the server is still trying to deliver trains
them to ignore the notice that matters. The human-readable part of a DSN says
things like "failed" in both cases, which is exactly why it is not consulted.

**Correlation is the original `Message-ID`.** RFC 3464 puts the returned message
in a ``message/rfc822`` part, or its headers alone in ``text/rfc822-headers``.
Both are handled; providers differ on which they send.

Parsing never raises. A DSN that cannot be understood must degrade to "ordinary
message", because the alternative — an exception on the ingest path — turns a
malformed bounce into a stalled mailbox.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from email import message_from_bytes
from email.message import Message
from email.policy import compat32

_log = logging.getLogger(__name__)

_STATUS_RE = re.compile(r"^\s*Status:\s*([245])\.(\d+)\.(\d+)", re.IGNORECASE | re.MULTILINE)
_MESSAGE_ID_RE = re.compile(r"^\s*Message-ID:\s*(<[^>]+>)", re.IGNORECASE | re.MULTILINE)


@dataclass(frozen=True)
class DsnReport:
    """A parsed delivery-status notification.

    ``original_message_id`` may be empty: some providers return neither the
    message nor its headers. That is a correlation failure, not a parse
    failure — the report is still a DSN and must still be kept out of the
    triage lanes.
    """

    permanent: bool
    """True for 5.x.x. False for 4.x.x, which is a retry still in progress."""

    status: str
    """The raw ``Status:`` value, e.g. ``5.1.1``."""

    original_message_id: str
    """The ``Message-ID`` of the message being reported on, or ``""``."""

    failed_recipient: str
    """The ``Final-Recipient`` address, or ``""``."""


def _walk(msg: Message):
    try:
        yield from msg.walk()
    except Exception:  # noqa: BLE001 — a hostile message must not stall ingest
        yield msg


def _is_delivery_status_report(msg: Message) -> bool:
    """Structural check: ``multipart/report; report-type=delivery-status``.

    ``get_param`` is used rather than a substring match on the raw header so
    parameter order, quoting and casing are the stdlib's problem rather than a
    regex's.
    """
    if (msg.get_content_type() or "").lower() != "multipart/report":
        return False
    report_type = msg.get_param("report-type", failobj="")
    return str(report_type).lower() == "delivery-status"


def _extract_status(msg: Message) -> tuple[str, bool] | None:
    """Return ``(status, permanent)`` from the ``message/delivery-status`` part."""
    for part in _walk(msg):
        if (part.get_content_type() or "").lower() != "message/delivery-status":
            continue
        # The per-recipient fields are sub-parts; the simplest reliable read is
        # the raw text of the part, because compat32 exposes the groups as
        # nested Messages whose payloads are themselves header blocks.
        try:
            raw = part.as_string()
        except Exception:  # noqa: BLE001
            continue
        m = _STATUS_RE.search(raw)
        if m:
            status = f"{m.group(1)}.{m.group(2)}.{m.group(3)}"
            return status, m.group(1) == "5"
    return None


def _extract_original_message_id(msg: Message) -> str:
    """The reported-on ``Message-ID``, from either RFC 3464 return form."""
    for part in _walk(msg):
        ctype = (part.get_content_type() or "").lower()
        if ctype not in ("message/rfc822", "text/rfc822-headers"):
            continue
        try:
            if ctype == "message/rfc822":
                payload = part.get_payload()
                inner = payload[0] if isinstance(payload, list) and payload else None
                if inner is not None:
                    mid = str(inner.get("Message-ID") or "").strip()
                    if mid:
                        return mid
                raw = part.as_string()
            else:
                raw = part.get_payload(decode=True) or b""
                raw = raw.decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            continue
        m = _MESSAGE_ID_RE.search(raw)
        if m:
            return m.group(1).strip()
    return ""


def _extract_failed_recipient(msg: Message) -> str:
    for part in _walk(msg):
        if (part.get_content_type() or "").lower() != "message/delivery-status":
            continue
        try:
            raw = part.as_string()
        except Exception:  # noqa: BLE001
            continue
        m = re.search(r"^\s*Final-Recipient:\s*[^;]+;\s*(\S+)", raw, re.IGNORECASE | re.MULTILINE)
        if m:
            return m.group(1).strip()
    return ""


def parse_dsn(raw: bytes | None) -> DsnReport | None:
    """Return a :class:`DsnReport`, or ``None`` when ``raw`` is not a DSN.

    ``None`` means "treat as ordinary mail" — the safe direction. A message
    wrongly treated as ordinary is merely un-correlated; a message wrongly
    treated as a bounce would be hidden from the lanes.
    """
    if not raw:
        return None
    try:
        msg = message_from_bytes(raw, policy=compat32)
    except Exception:  # noqa: BLE001 — never stall ingest on a malformed message
        _log.warning("email_dsn: unparseable message; treating as ordinary mail")
        return None

    if not _is_delivery_status_report(msg):
        return None

    found = _extract_status(msg)
    if found is None:
        # Structurally a report but with no machine-readable status. Not
        # actionable, and guessing from the prose is what this module exists to
        # avoid — so it stays ordinary mail.
        return None

    status, permanent = found
    return DsnReport(
        permanent=permanent,
        status=status,
        original_message_id=_extract_original_message_id(msg),
        failed_recipient=_extract_failed_recipient(msg),
    )


def correlate(report: DsnReport, user_id: str) -> dict | None:
    """Find the send this report is about, or ``None``.

    The join key is the original ``Message-ID``, which ``smtp_send`` already
    records in ``email_sent_log.message_id``. Scoped to ``user_id``: a bounce
    arriving in one member's mailbox must not resolve against another member's
    send, which would tell them a message they never wrote had failed.

    Returning ``None`` is a **safe no-op** and the ticket requires it: an
    unmatched DSN stays an ordinary message rather than being dropped. Losing a
    bounce entirely is the one outcome worse than not correlating it.
    """
    import config

    mid = (report.original_message_id or "").strip()
    if not mid:
        return None

    ms = config.get_metadata_store()
    row = ms.fetch_one(
        # SCOPE-EXEMPT: the caller's own sent log, user_id-scoped on the same
        # predicate. A bounce must never resolve against another member's send.
        "SELECT id, subject, to_addrs, sent_at FROM email_sent_log "
        "WHERE user_id = %s AND message_id = %s LIMIT 1",
        (user_id, mid),
    )
    return dict(row) if row else None


def record_bounce(report: DsnReport, user_id: str) -> bool:
    """Record a correlated bounce. Returns True when the DSN was handled here.

    ``False`` means **not correlated** — and the caller must then let the report
    through as an ordinary message. That is the ticket's requirement and the
    safe direction: an unmatched DSN that is swallowed is a bounce the user can
    never see, which is worse than one sitting awkwardly in a lane.

    A soft bounce (4.x.x) is recorded but flagged non-permanent, so a surface
    can show "still trying" without alarming anyone about a message the server
    has not given up on.
    """
    import config

    sent = correlate(report, user_id)
    if sent is None:
        return False

    ms = config.get_metadata_store()
    try:
        ms.execute(
            "INSERT INTO email_bounce "
            "(user_id, sent_log_id, message_id, recipient, status, permanent) "
            "VALUES (%s, %s, %s, %s, %s, %s) "
            # A provider re-delivering the same report must not surface twice.
            "ON CONFLICT (user_id, message_id, recipient) DO NOTHING",
            (
                user_id,
                sent.get("id"),
                report.original_message_id,
                report.failed_recipient,
                report.status,
                report.permanent,
            ),
        )
    except Exception:
        _log.warning("email_dsn: could not record bounce; leaving it as ordinary mail")
        # Fall back to the ordinary path rather than swallowing the report.
        return False

    _log.info(
        "email_bounce_recorded user_id=%s status=%s permanent=%s",
        user_id,
        report.status,
        report.permanent,
    )
    return True


def list_unacknowledged(user_id: str, *, limit: int = 20) -> list[dict]:
    """Permanent bounces this user has not been told about yet.

    **Permanent only.** A 4.x.x is a retry still in progress, and the ticket is
    explicit that it "should not alarm anyone" — surfacing it would mean warning
    someone about a message the server has not given up on, which is how a real
    warning gets tuned out.

    Joined to `email_sent_log` so the surface can name the message rather than
    the Message-ID: "your reply to Lunch? did not reach jhon@example.com" is
    actionable, an opaque identifier is not. LEFT JOIN, because the send row may
    have been pruned and a bounce naming only the recipient still beats none.
    """
    import config

    ms = config.get_metadata_store()
    rows = ms.fetch_all(
        # SCOPE-EXEMPT: the caller's own bounces; user_id-scoped. `visible_filter`
        # is not used — a household member must not see whose mail failed.
        "SELECT b.id, b.recipient, b.status, b.seen_at, "
        "       s.subject AS subject, s.sent_at AS sent_at "
        "FROM email_bounce b "
        "LEFT JOIN email_sent_log s ON s.id = b.sent_log_id "
        "WHERE b.user_id = %s AND b.acknowledged_at IS NULL AND b.permanent "
        "ORDER BY b.seen_at DESC LIMIT %s",
        (user_id, limit),
    )
    return [dict(r) for r in (rows or [])]


def acknowledge(bounce_id: str, user_id: str) -> bool:
    """Mark one bounce as told. Returns False when it is not the caller's.

    Ownership is the WHERE clause, so acknowledging someone else's bounce is a
    no-op rather than an error — the caller learns nothing about whether it
    exists.
    """
    import config

    ms = config.get_metadata_store()
    row = ms.fetch_one(
        # SCOPE-EXEMPT: ownership check on the caller's own bounce row.
        "SELECT 1 AS ok FROM email_bounce WHERE id::text = %s AND user_id = %s",
        (str(bounce_id), user_id),
    )
    if row is None:
        return False
    ms.execute(
        "UPDATE email_bounce SET acknowledged_at = NOW() "
        "WHERE id::text = %s AND user_id = %s AND acknowledged_at IS NULL",
        (str(bounce_id), user_id),
    )
    return True
