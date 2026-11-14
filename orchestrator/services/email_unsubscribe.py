# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""``List-Unsubscribe`` parsing and per-sender persistence (LUM-697).

**This module must never gain an HTTP client.** A ``https:`` unsubscribe URI is
written by whoever sent the message — including whoever sent the spam — so
fetching it server-side is a server-side request to an attacker-chosen host.
Nextcloud Mail shipped exactly that and took a blind-SSRF advisory
(GHSA-4pp4-m8ph-2999 / CVE-2023-48307). The URI is stored verbatim and handed
to the *user's browser*, which is what mainstream mail clients do. See
``cursor/adrs/email-unsubscribe.md``; ``test_no_outbound_http_in_unsubscribe_modules``
enforces it by execution rather than by convention.

The header itself is already on the wire: ``List-Unsubscribe`` has been in
:data:`adapters.email_imap_source._HEADER_FIELDS` since LUM-170 and is already
read as a *boolean* by ``email_classifier._has_bulk_header``. Only the value was
being discarded.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from urllib.parse import parse_qs
from urllib.parse import unquote
from urllib.parse import urlsplit

import config

_log = logging.getLogger(__name__)

# A header field is attacker-controlled. Bound it before it reaches the database
# or the browser.
_MAX_URI_CHARS = 2000

# Mirrors actions.handlers.smtp_send._ADDR_RE — an unsubscribe recipient must
# clear the same bar as any other recipient before it can become one.
_ADDR_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# THE security boundary of this module. Everything not on this list is dropped:
# `javascript:` (Horde stored XSS, CVE-2025-68673), `data:`, `file:`, `ftp:`,
# and plain `http:` (RFC 8058 requires https, and a cleartext unsubscribe link
# is not worth rendering).
_ALLOWED_SCHEMES = ("mailto", "https")

# RFC 8058 §3.1. Whitespace-insensitive, case-insensitive.
_ONE_CLICK_RE = re.compile(r"^\s*list-unsubscribe\s*=\s*one-click\s*$", re.IGNORECASE)


@dataclass
class ParsedUnsubscribe:
    """What a ``List-Unsubscribe`` header yielded. All-empty on any miss."""

    mailto_addr: str = ""
    mailto_subject: str = ""
    https_uri: str = ""
    one_click: bool = False

    def is_empty(self) -> bool:
        return not self.mailto_addr and not self.https_uri


def _split_uris(raw: str) -> list[str]:
    """Split a ``List-Unsubscribe`` value into its ``<...>`` members.

    Commas separate members, but a comma *inside* angle brackets is part of a
    URI (query strings routinely contain them), so a naive ``split(",")`` would
    shred a legitimate one-click link.
    """
    out: list[str] = []
    depth = 0
    buf: list[str] = []
    for ch in raw or "":
        if ch == "<":
            depth += 1
            continue
        if ch == ">":
            depth = max(0, depth - 1)
            continue
        if ch == "," and depth == 0:
            out.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    out.append("".join(buf))
    return [u.strip() for u in out if u.strip()]


def _parse_mailto(uri: str) -> tuple[str, str]:
    """``(address, subject)`` for a usable ``mailto:``, else ``("", "")``.

    Only the **first** address is taken. A crafted
    ``mailto:a@x.com,b@y.com,c@z.com`` must not fan out into a send to three
    recipients the user never reviewed.
    """
    parts = urlsplit(uri)
    first = unquote(parts.path or "").split(",")[0].strip()
    if not first or not _ADDR_RE.match(first):
        return "", ""
    subject = ""
    try:
        qs = parse_qs(parts.query or "")
        subject = (qs.get("subject") or [""])[0].strip()
    except Exception:  # noqa: BLE001 - a malformed query must not lose the address
        subject = ""
    # CR/LF here would be header injection at send time. build_message rejects it
    # too, but dropping it at parse means it never reaches the database.
    if any(c in subject for c in "\r\n"):
        subject = ""
    return first, subject[:200]


def _parse_https(uri: str) -> str:
    """The URI if it is a usable ``https:`` link, else ``""``."""
    parts = urlsplit(uri)
    if not parts.hostname:
        return ""
    # A URI carrying userinfo (https://evil@real-bank.example/...) is either a
    # phishing display trick — the visible host is not the real one — or a
    # credential leak into our database and the browser's history. No legitimate
    # unsubscribe link has this shape, and we render this value as a clickable
    # link, which is exactly where a display spoof pays off.
    if parts.username or parts.password:
        return ""
    return uri


def parse_list_unsubscribe(raw: str, post_header: str = "") -> ParsedUnsubscribe:
    """Parse ``List-Unsubscribe`` (+ ``List-Unsubscribe-Post``). Never raises.

    A malformed or hostile header yields an all-empty result rather than an
    exception — this runs on the ingest path for every message.
    """
    out = ParsedUnsubscribe()
    try:
        for uri in _split_uris(raw):
            if len(uri) > _MAX_URI_CHARS:
                continue
            scheme = urlsplit(uri).scheme.lower()
            if scheme not in _ALLOWED_SCHEMES:
                # Log the scheme, never the URI — the URI is attacker-controlled
                # content and does not belong in operator logs.
                if scheme:
                    _log.debug("unsubscribe: dropped disallowed scheme %r", scheme[:20])
                continue
            if scheme == "mailto" and not out.mailto_addr:
                out.mailto_addr, out.mailto_subject = _parse_mailto(uri)
            elif scheme == "https" and not out.https_uri:
                out.https_uri = _parse_https(uri)
        out.one_click = bool(_ONE_CLICK_RE.match(post_header or ""))
    except Exception:  # noqa: BLE001 - ingest path; degrade to "no target"
        _log.info("unsubscribe: header parse degraded to empty")
        return ParsedUnsubscribe()
    return out


def record_from_headers(
    *,
    user_id: str,
    source_id: str,
    from_addr: str,
    from_display: str,
    headers: dict[str, str] | None,
) -> None:
    """Upsert the per-sender unsubscribe target. Best-effort, never raises."""
    try:
        # An unparseable `From` yields "" from _extract_sender. The unique key is
        # (user_id, source_id, from_addr), so without this guard every sender we
        # failed to parse would collapse into a single '' row with a runaway
        # count and an unsubscribe target belonging to whichever message wrote
        # last.
        if not (from_addr or "").strip():
            return
        lowered = {k.lower(): (v or "") for k, v in (headers or {}).items()}
        parsed = parse_list_unsubscribe(
            lowered.get("list-unsubscribe", ""), lowered.get("list-unsubscribe-post", "")
        )
        ms = config.get_metadata_store()
        if parsed.is_empty() and not _row_exists(ms, user_id, source_id, from_addr):
            # An ordinary personal email is not a subscription and should not
            # appear in the list.
            return
        ms.execute(
            "INSERT INTO email_unsubscribe_target "
            "(user_id, source_id, from_addr, from_display, mailto_addr, "
            " mailto_subject, https_uri, one_click, message_count) "
            "VALUES (%s, %s::uuid, %s, %s, %s, %s, %s, %s, 1) "
            "ON CONFLICT (user_id, source_id, from_addr) DO UPDATE SET "
            # Preserve-on-empty. A sender that emits both bulk mail (carrying
            # the header) and ordinary mail (not carrying it) is completely
            # normal — a retailer's newsletter and its order confirmations come
            # from one address. A plain `= EXCLUDED.x` would blank a working
            # unsubscribe target the moment a confirmation arrived, and the
            # sender would silently become un-unsubscribable.
            "  mailto_addr = COALESCE(NULLIF(EXCLUDED.mailto_addr, ''), "
            "                         email_unsubscribe_target.mailto_addr), "
            "  mailto_subject = COALESCE(NULLIF(EXCLUDED.mailto_subject, ''), "
            "                            email_unsubscribe_target.mailto_subject), "
            "  https_uri = COALESCE(NULLIF(EXCLUDED.https_uri, ''), "
            "                       email_unsubscribe_target.https_uri), "
            "  from_display = COALESCE(NULLIF(EXCLUDED.from_display, ''), "
            "                          email_unsubscribe_target.from_display), "
            "  one_click = email_unsubscribe_target.one_click OR EXCLUDED.one_click, "
            "  message_count = email_unsubscribe_target.message_count + 1, "
            "  last_seen_at = NOW()",
            (
                user_id,
                str(source_id),
                from_addr,
                from_display or "",
                parsed.mailto_addr,
                parsed.mailto_subject,
                parsed.https_uri,
                parsed.one_click,
            ),
        )
    except Exception as exc:  # noqa: BLE001 - ingest must not fail over a subscription row
        _log.warning("unsubscribe: record failed (%s)", type(exc).__name__)


def _row_exists(ms, user_id: str, source_id: str, from_addr: str) -> bool:
    row = ms.fetch_one(
        "SELECT 1 AS ok FROM email_unsubscribe_target "
        # SCOPE-EXEMPT: caller's own row, keyed on their own user_id.
        "WHERE user_id = %s AND source_id = %s::uuid AND from_addr = %s LIMIT 1",
        (user_id, str(source_id), from_addr),
    )
    return bool(row)


def list_targets(user_id: str, *, source_id: str | None = None, limit: int = 200) -> list[dict]:
    """This user's senders, busiest first. Owner-scoped. Never raises."""
    try:
        ms = config.get_metadata_store()
        # SCOPE-EXEMPT: the caller's own subscription list, not a household read.
        sql = (
            "SELECT t.source_id::text AS source_id, t.from_addr, t.from_display, "
            "       t.message_count, t.mailto_addr <> '' AS has_mailto, "
            "       t.https_uri <> '' AS has_https, t.one_click, t.https_uri, "
            "       t.last_seen_at, t.unsubscribed_at, "
            "       r.action AS suppressed_as "
            "FROM email_unsubscribe_target t "
            "LEFT JOIN email_sender_rule r "
            "  ON r.user_id = t.user_id AND r.source_id = t.source_id "
            " AND r.from_addr = t.from_addr AND r.active "
            # SCOPE-EXEMPT: caller's own subscription list, not a household read.
            "WHERE t.user_id = %s"
        )
        params: list = [user_id]
        if source_id:
            sql += " AND t.source_id = %s::uuid"
            params.append(str(source_id))
        sql += " ORDER BY t.message_count DESC, t.from_addr LIMIT %s"
        params.append(int(limit))
        return ms.fetch_all(sql, tuple(params)) or []
    except Exception:  # noqa: BLE001 - a broken list degrades to empty, never a 500
        _log.warning("unsubscribe: list_targets degraded to empty")
        return []


def resolve_targets(
    user_id: str, source_id: str, senders: list[str]
) -> tuple[list[dict], list[str]]:
    """``(resolved, skipped)`` for ``senders``.

    Owner-scoped resolution of sender → unsubscribe recipient, run **server-side
    at propose time**. The client sends sender addresses only and never supplies
    a recipient, so a compromised session cannot dress an arbitrary address up as
    an unsubscribe. Resolving here rather than at execute also means the payload
    the user approved is byte-for-byte the payload that sends — a message
    arriving between approval and execute cannot change where the mail goes.
    """
    ms = config.get_metadata_store()
    resolved: list[dict] = []
    skipped: list[str] = []
    for addr in senders:
        # SCOPE-EXEMPT: caller's own row, keyed on their own user_id.
        row = ms.fetch_one(
            "SELECT mailto_addr, mailto_subject FROM email_unsubscribe_target "
            # SCOPE-EXEMPT: caller's own row, keyed on their own user_id.
            "WHERE user_id = %s AND source_id = %s::uuid AND from_addr = %s LIMIT 1",
            (user_id, str(source_id), addr),
        )
        mailto = str((row or {}).get("mailto_addr") or "")
        if not mailto:
            skipped.append(addr)
            continue
        resolved.append(
            {
                "from_addr": addr,
                "mailto_addr": mailto,
                "subject": str((row or {}).get("mailto_subject") or "") or "unsubscribe",
            }
        )
    return resolved, skipped


def mark_unsubscribed(user_id: str, source_id: str, from_addr: str) -> None:
    """Stamp one target as done. Called per target, as its own send succeeds."""
    try:
        ms = config.get_metadata_store()
        ms.execute(
            "UPDATE email_unsubscribe_target SET unsubscribed_at = NOW() "
            # SCOPE-EXEMPT: caller's own row, keyed on their own user_id.
            "WHERE user_id = %s AND source_id = %s::uuid AND from_addr = %s",
            (user_id, str(source_id), from_addr),
        )
    except Exception:  # noqa: BLE001 - the mail already went; a missing stamp is not fatal
        _log.warning("unsubscribe: mark_unsubscribed failed for one target")


def batch_job_progress(user_id: str, job_id: int) -> dict | None:
    """Per-target progress for one off-thread unsubscribe batch (LUM-710).

    ``None`` when the job does not exist, is not an unsubscribe batch, or is not
    this user's — one indistinguishable answer, so polling job ids cannot be used
    to enumerate another member's jobs or probe which ids exist.

    Progress is read from ``unsubscribed_at`` rather than tracked separately
    because the send loop already stamps it per target, as each send succeeds.
    Two consequences worth naming:

    * a target stamped by an *earlier* batch reads done the moment this job is
      enqueued. That is accurate — the sender IS unsubscribed — and it is the
      same fact the worker uses to skip it;
    * the counts are derived, so they cannot drift from what actually sent.

    The queue's ``error`` column is deliberately **not** exposed: it holds
    ``repr()`` of whatever the SMTP stack raised, which is remote-controlled text
    on a path where the remote is the sender being unsubscribed from. ``status``
    plus the per-target list says everything a client needs.
    """
    try:
        ms = config.get_metadata_store()
        # SCOPE-EXEMPT: user_batch_jobs is operational queue state; the caller's
        # own job, keyed on their own user_id.
        job = ms.fetch_one(
            "SELECT id, status, payload, attempt, enqueued_at, started_at, finished_at "
            "FROM user_batch_jobs WHERE id = %s AND user_id = %s AND kind = %s",
            (int(job_id), user_id, "email_unsubscribe"),
        )
        if not job:
            return None
        payload = job.get("payload")
        if isinstance(payload, str):
            import json

            payload = json.loads(payload)
        payload = payload or {}
        source_id = str(payload.get("source_id") or "")
        addrs = [str(t.get("from_addr") or "") for t in (payload.get("targets") or [])]
        addrs = [a for a in addrs if a]

        done: set[str] = set()
        if addrs and source_id:
            rows = (
                ms.fetch_all(
                    "SELECT from_addr FROM email_unsubscribe_target "
                    # SCOPE-EXEMPT: caller's own rows, keyed on their own user_id.
                    "WHERE user_id = %s AND source_id = %s::uuid "
                    "AND from_addr = ANY(%s) AND unsubscribed_at IS NOT NULL",
                    (user_id, source_id, addrs),
                )
                or []
            )
            done = {str(r["from_addr"]) for r in rows}

        return {
            "job_id": int(job["id"]),
            "status": str(job["status"]),
            "attempt": int(job.get("attempt") or 0),
            "source_id": source_id,
            "enqueued_at": job.get("enqueued_at"),
            "started_at": job.get("started_at"),
            "finished_at": job.get("finished_at"),
            "total": len(addrs),
            "sent": len(done),
            "outstanding": len(addrs) - len(done),
            "targets": [{"from_addr": a, "unsubscribed": a in done} for a in addrs],
        }
    except Exception:  # noqa: BLE001 - a progress poll must never 500 a UI
        _log.warning("unsubscribe: batch_job_progress degraded")
        return None


def list_batch_jobs(user_id: str, *, limit: int = 10) -> list[dict]:
    """This user's recent unsubscribe batches, newest first (LUM-710).

    The discovery half of the polling pair. ``ActionResult`` carries only a
    human-readable string, so the client cannot learn the job id from the
    approval it just executed — it lists its own recent batches instead. Ids and
    counts only; recipients come from :func:`batch_job_progress`.
    """
    try:
        ms = config.get_metadata_store()
        # SCOPE-EXEMPT: user_batch_jobs is operational queue state; the caller's
        # own jobs, keyed on their own user_id.
        rows = (
            ms.fetch_all(
                "SELECT id, status, enqueued_at, finished_at, "
                "       jsonb_array_length(COALESCE(payload->'targets', '[]'::jsonb)) AS total "
                "FROM user_batch_jobs WHERE user_id = %s AND kind = %s "
                "ORDER BY id DESC LIMIT %s",
                (user_id, "email_unsubscribe", int(limit)),
            )
            or []
        )
        return [
            {
                "job_id": int(r["id"]),
                "status": str(r["status"]),
                "total": int(r.get("total") or 0),
                "enqueued_at": r.get("enqueued_at"),
                "finished_at": r.get("finished_at"),
            }
            for r in rows
        ]
    except Exception:  # noqa: BLE001 - degrades to empty, never a 500
        _log.warning("unsubscribe: list_batch_jobs degraded to empty")
        return []


def already_unsubscribed(user_id: str, source_id: str, from_addr: str) -> bool:
    """True when this target already sent. Makes a batch retry idempotent."""
    try:
        ms = config.get_metadata_store()
        row = ms.fetch_one(
            "SELECT unsubscribed_at FROM email_unsubscribe_target "
            # SCOPE-EXEMPT: caller's own row, keyed on their own user_id.
            "WHERE user_id = %s AND source_id = %s::uuid AND from_addr = %s LIMIT 1",
            (user_id, str(source_id), from_addr),
        )
        return bool(row and row.get("unsubscribed_at"))
    except Exception:  # noqa: BLE001 - fail OPEN: re-sending an unsubscribe is
        # harmless; skipping one the user asked for is not.
        return False
