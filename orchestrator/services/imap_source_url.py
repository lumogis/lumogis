# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Plaintext IMAP source-url read + parse (LUM-634, LUM-773).

Neutral module shared by egress enumeration, paperless overlap, credential
validation (LUM-773), and ``_smtp_host_required`` (host-only). No decrypt, no DNS.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

import idna

import config


def normalize_imap_host(host: str) -> str | None:
    """Canonical comparison/storage form of an IMAP host, or ``None`` if invalid (LUM-782).

    The one helper behind every host comparison (``imap_host_port_from_source_url``
    and the LUM-773 credential compare) so the two sites cannot drift. Canonical
    form is the ASCII A-label (punycode), lower-case, no trailing dot — what an IMAP
    client actually connects with.

    * Non-ASCII hosts go through IDNA2008 (``idna`` package, UTS46 mapping), not the
      stdlib ``idna`` codec, which is IDNA2003 and maps e.g. ``faß`` to ``fass``.
    * ASCII hosts are only lower-cased and dot-stripped, so existing stored values
      (including non-LDH ones such as ``my_host.lan``) keep working; ``xn--`` labels
      are still validated so a malformed A-label fails closed.
    * IP literals are lower-cased only.
    * Invalid input (empty label, over-long label, bad punycode) returns ``None``;
      callers must treat that as a failure, never fall back to the raw string.
    """
    if not isinstance(host, str):
        return None
    h = host.strip().rstrip(".")
    if not h:
        return None
    try:
        if ":" in h:
            return str(ipaddress.ip_address(h)).lower()
        if h.isascii():
            h = h.lower()
            if any(label.startswith("xn--") for label in h.split(".")):
                idna.decode(h)
            if "" in h.split(".") or any(len(label) > 63 for label in h.split(".")):
                return None
            return h
        return idna.encode(h, uts46=True).decode("ascii")
    except (idna.IDNAError, ValueError, UnicodeError):
        return None


def imap_hostname_from_source_url(url: str) -> str | None:
    """Host-only extraction for provider classification — does not validate port.

    A valid hostname with a corrupt port must still classify via
    ``provider_for_host`` (``_smtp_host_required`` regression guard).
    """
    try:
        parts = urlsplit(url)
        if parts.scheme != "imap" or not parts.hostname:
            return None
        return parts.hostname
    except ValueError:
        return None


def imap_host_port_from_source_url(url: str) -> tuple[str, int] | None:
    """Parse ``imap://{host}:{port}/{label}`` → ``(hostname, port)``.

    Returns ``None`` on bad scheme, missing hostname, ``urlsplit`` ``ValueError``
    (invalid IPv6), or non-numeric/out-of-range port. Default port **993** when
    omitted. Uses ``.hostname`` not ``.netloc`` (``.netloc`` includes the port).
    The host is returned in :func:`normalize_imap_host` form (LUM-782), so rows
    stored as unicode or with a trailing dot still compare equal; an invalid host
    returns ``None`` (same as a bad port).
    """
    try:
        parts = urlsplit(url)
        if parts.scheme != "imap" or not parts.hostname:
            return None
        port = int(parts.port or 993)
        if isinstance(port, bool) or not (1 <= port <= 65535):
            return None
        host = normalize_imap_host(parts.hostname)
        if host is None:
            return None
        return host, port
    except ValueError:
        return None


def source_url_for_imap_instance(user_id: str, source_id: str) -> str | None:
    """Return the plaintext ``sources.url`` for an IMAP mailbox instance.

    ``fetch_one`` failures propagate — callers catch per mailbox and skip.
    """
    ms = config.get_metadata_store()
    # SCOPE-EXEMPT: `sources` has no `scope` column, so `visible_filter` cannot
    # apply. The user_id predicate is the isolation here — a household member's
    # mailbox must never be read through another member's request.
    row = ms.fetch_one(
        "SELECT url FROM sources WHERE id = %s AND user_id = %s AND source_type = 'imap' LIMIT 1",
        (source_id, user_id),
    )
    if not row:
        return None
    url = row.get("url")
    if not isinstance(url, str) or not url.strip():
        return None
    return url


def source_urls_for_imap_instances(user_id: str, source_ids: list[str]) -> dict[str, str]:
    """Bulk sibling of :func:`source_url_for_imap_instance` (LUM-744).

    **Exists so a batch does not loop the single-row read.** ``fetch_one`` per
    mailbox turns an "N HTTP calls → 1" ticket into one that ships N database
    round-trips instead, which is the same cost simply moved.

    ``sources.id`` is ``UUID``, so the ids — which arrive as ``str`` everywhere
    above this layer — need ``::uuid[]``. Without the cast Postgres cannot
    compare them and the query raises rather than returning nothing, so the
    failure is at least loud; ``email_attachment_rules.py:212`` casts for the
    same reason.

    Mailboxes with no row, a NULL url, or a blank url are simply absent from the
    result — callers skip them, exactly as they skip a ``None`` from the
    single-row version.
    """
    ids = [s for s in dict.fromkeys(source_ids) if s]
    if not ids:
        return {}
    ms = config.get_metadata_store()
    # SCOPE-EXEMPT: `sources` has no `scope` column, so `visible_filter` cannot
    # apply. The user_id predicate is the isolation here — a household member's
    # mailbox must never be read through another member's request.
    rows = (
        ms.fetch_all(
            "SELECT id::text AS id, url FROM sources "
            "WHERE user_id = %s AND source_type = 'imap' AND id = ANY(%s::uuid[])",
            (user_id, ids),
        )
        or []
    )
    out: dict[str, str] = {}
    for row in rows:
        sid = row.get("id")
        url = row.get("url")
        if isinstance(sid, str) and isinstance(url, str) and url.strip():
            out[sid] = url
    return out
