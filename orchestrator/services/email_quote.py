# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Quoted-original builder for reply and forward composition (LUM-698).

``build_quote`` returns the plain-text block a composer shows — **visible and
editable** — above/below the user's own note, and which is sent verbatim as part
of the outgoing body. It is deliberately NOT applied server-side at send time:
the user must be able to see and trim what they are forwarding before an
irreversible send.

Posture is copied exactly from :mod:`services.email_reply_draft`:

* **Owner-scoped** through the real chain (``_signal_url`` → ``_parse_imap_url``
  → ``_load_owned_source``). A non-owned signal returns ``""`` and **no body is
  read** — the ownership gate runs before any body fetch.
* **Best-effort, never raises** — ``""`` on any miss so the composer degrades to
  "no quote" and the send still works.

Attribution comes from the stored ``signals`` row, not a header re-fetch:
``fetch_thread_headers`` carries Subject/Message-ID/References/In-Reply-To but
**no Date and no From**.
"""

from __future__ import annotations

import logging
from typing import Optional

import config

_log = logging.getLogger(__name__)

# The quote is SENT, not prompted, so this sits well above the reply-draft's
# LLM input budget (_DRAFT_BODY_TOKENS ≈ 6k chars): over-trimming a real thread
# is worse than a larger payload. It still bounds a pathological thread before
# it reaches the browser or the outgoing MIME.
_MAX_QUOTE_CHARS = 20000
_TRUNCATION_MARKER = "[… quoted message truncated …]"

_FORWARD_SEPARATOR = "---------- Forwarded message ----------"


def fetch_body_text(
    source,
    uid: int,
    *,
    user_id: str,
    signal_id: str,
    uidvalidity: int = 0,
) -> str:
    """Bounded on-demand body fetch for quote/draft (LUM-698 / LUM-652).

    **Same locate contract as the read view** (:meth:`EmailImapSource.fetch_body_bounded`):
    ``Message-ID`` + organize folder hint + UIDVALIDITY gate, not a blind
    ``INBOX`` uid fetch. The old ``fetch_bodies`` path ignored moves (LUM-645/646)
    and trusted the uid after a UIDVALIDITY reset — the same failure mode
    ``fetch_original_headers`` already refuses for Reply-all.

    ``signals`` never stores ``raw_content`` (transient at ingest only), so there
    is no stored-body fast path — only this fetch.
    """
    try:
        from adapters.email_imap_source import EmailImapSource
        from services.email_message_locate import message_id_for
        from services.email_message_locate import organize_folder_for

        fetched = EmailImapSource(source).fetch_body_bounded(
            uid=int(uid),
            message_id=message_id_for(signal_id, user_id),
            folder_hint=organize_folder_for(signal_id, user_id),
            uidvalidity=int(uidvalidity),
        )
        return fetched.text if fetched.reason == "ok" else ""
    except Exception:  # noqa: BLE001 - degrade to no body, never a hard fail
        return ""


def _sender_label(from_display: str, from_addr: str) -> str:
    """``Jane Doe <jane@x.com>`` when a display name exists, else the bare addr."""
    display = (from_display or "").strip()
    addr = (from_addr or "").strip()
    if display and addr:
        return f"{display} <{addr}>"
    return display or addr or "the sender"


def _format_date(published_at) -> str:
    """``published_at`` is a NULLABLE timestamptz — degrade to ``""`` rather than
    emitting a literal "None" or inventing a date. The caller omits the date
    clause entirely when this is empty."""
    if not published_at:
        return ""
    try:
        return published_at.strftime("%a, %d %b %Y at %H:%M")
    except Exception:  # noqa: BLE001 - a str/odd type still shouldn't break the quote
        return str(published_at)


def _truncate(text: str) -> str:
    if len(text) <= _MAX_QUOTE_CHARS:
        return text
    return text[:_MAX_QUOTE_CHARS].rstrip() + f"\n{_TRUNCATION_MARKER}"


def _normalize_lines(body: str) -> list[str]:
    """Normalise CRLF / lone-CR endings before prefixing, so a quote of a
    Windows-authored message doesn't end up with stray ``\\r`` inside lines."""
    return body.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def _reply_quote(*, sender: str, date: str, body: str) -> str:
    # "On {date}, {sender} wrote:" — the de-facto convention. Date omitted when
    # published_at is NULL.
    header = f"On {date}, {sender} wrote:" if date else f"{sender} wrote:"
    # A blank line becomes a bare ">" (not "> "), matching every mainstream client.
    quoted = "\n".join(f"> {ln}" if ln else ">" for ln in _normalize_lines(body))
    return f"{header}\n{quoted}" if body else header


def _forward_quote(*, sender: str, date: str, subject: str, body: str) -> str:
    # Forwarded blocks are conventionally an unquoted header block + raw body —
    # NOT ">"-prefixed.
    lines = [_FORWARD_SEPARATOR, f"From: {sender}"]
    if date:
        lines.append(f"Date: {date}")
    if subject:
        lines.append(f"Subject: {subject}")
    lines.append("")
    if body:
        lines.extend(_normalize_lines(body))
    return "\n".join(lines)


def build_quote(signal_id: str, *, user_id: str, style: str = "reply") -> str:
    """The quoted-original block for ``signal_id``, or ``""``. Never raises.

    ``style`` is ``"reply"`` (``> ``-prefixed, "On … wrote:") or ``"forward"``
    (an unquoted forwarded-header block).
    """
    try:
        from actions.handlers.imap_writeback import _load_owned_source
        from actions.handlers.imap_writeback import _parse_imap_url
        from actions.handlers.imap_writeback import _signal_url

        url = _signal_url(signal_id)
        if url is None:
            return ""
        recovered = _parse_imap_url(url)
        if recovered is None:
            return ""
        source_id, uidvalidity, uid = recovered
        # Ownership gate BEFORE any body read — a non-owned signal raises here
        # and the except below returns "" without ever fetching a body.
        source = _load_owned_source(source_id, user_id)

        row = _load_signal_row(signal_id, user_id)
        if row is None:
            return ""

        body = fetch_body_text(
            source,
            uid,
            user_id=user_id,
            signal_id=signal_id,
            uidvalidity=int(uidvalidity),
        )
        sender = _sender_label(str(row.get("from_display") or ""), str(row.get("from_addr") or ""))
        date = _format_date(row.get("published_at"))
        subject = str(row.get("title") or "")

        if style == "forward":
            quote = _forward_quote(sender=sender, date=date, subject=subject, body=body)
        else:
            quote = _reply_quote(sender=sender, date=date, body=body)
        return _truncate(quote)
    except Exception as exc:  # noqa: BLE001 - best-effort; never raises to the route
        _log.info("build_quote: degrading to blank (%s)", type(exc).__name__)
        return ""


def _load_signal_row(signal_id: str, user_id: str) -> Optional[dict]:
    ms = config.get_metadata_store()
    # SCOPE-EXEMPT: ownership was established above; bind user_id for defence in depth.
    return ms.fetch_one(
        "SELECT title, from_addr, from_display, published_at "
        "FROM signals WHERE signal_id = %s AND user_id = %s LIMIT 1",
        (signal_id, user_id),
    )
