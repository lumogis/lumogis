# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""IMAP email source adapter (LUM-170).

Connections are opened via :mod:`services.imap_transport` (the sole importer of
``imapclient``, lazily — so importing this module never requires the dependency),
which chooses a direct connection or the LUM-625 Pass-3 CONNECT-proxy tunnel.

A **pure fetcher**: ``poll(since=...)`` takes the current cursor as an argument
and returns the new metadata ``Signal`` objects; it never touches Postgres.
Cursor persistence lives in ``feed_monitor._poll_email_source`` (the paperless
precedent). After a poll, :attr:`result_cursor` holds the advanced
``"{uidvalidity}:{last_uid}"`` value for the caller to persist.

Read-only: the mailbox is opened with ``EXAMINE`` (readonly) and bodies (for
full-ingest) are fetched with ``BODY.PEEK[…]`` so ``\\Seen`` is never set.
"""

from __future__ import annotations

import email
import logging
import time
import uuid
from dataclasses import dataclass
from dataclasses import field as dc_field
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from email.message import Message
from typing import TYPE_CHECKING

from actions.errors import PermanentActionError
from models.signals import Signal
from models.signals import SourceConfig
from services.egress_guard import EgressBlockedError
from services.egress_guard import imap_egress_scope

from services import email_credentials

if TYPE_CHECKING:  # pragma: no cover - typing only
    from services import email_attachments as email_attachments_mod

_log = logging.getLogger(__name__)


class AttachmentFetchError(RuntimeError):
    """An on-demand attachment fetch failed in a way the route must distinguish.

    ``code`` is one of ``imap_unreachable`` (→ 503, retryable),
    ``message_gone`` (→ 410, never retryable), ``message_too_large`` (→ 413),
    ``part_gone`` (→ 404) or ``message_ambiguous`` (→ 409). The 503/410 split is
    deliberate: fetch-on-demand's stated cost is that a deleted message takes its
    attachment with it, and collapsing that into a generic error would make the
    trade read as a bug. ``message_ambiguous`` is the third of that family — the
    message is neither gone nor unreachable, but several claim the same
    ``Message-ID`` and we refuse to guess which one the metadata describes.
    """

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


#: Class names treated as authentication failures at connect time. Matched by
#: NAME so this module need not import imapclient — `services.imap_transport` is
#: by convention its sole importer, and a lazy import here to type-check one
#: exception would erode that for no gain.
#:
#: Deliberately just ``LoginError``, the type `imap_transport`'s
#: ``client.login()`` / ``client.oauth2_login()`` calls actually raise. It is
#: tempting to add ``imaplib.IMAP4.error`` (class name ``error``), but that
#: covers protocol failures generally, not authentication — and misreading one
#: as auth tells a user to re-enter a password that is perfectly fine, which is
#: the exact harm LUM-633 exists to remove. Anything unrecognised falls to
#: ``network`` ("couldn't reach the server, usually transient"), which is the
#: honest answer when we do not know.
_AUTH_ERROR_NAMES = frozenset({"LoginError"})


class ImapConnectError(RuntimeError):
    """A failure raised while *opening* the IMAP connection (LUM-633).

    Exists so a caller can tell "this mailbox could not connect" from "this
    mailbox connected and then something went wrong mid-fetch" — a distinction
    poll-health depends on and cannot otherwise make, because
    :meth:`EmailImapSource.poll` connects and fetches inside one call.

    ``kind`` is ``"auth"`` or ``"network"`` and is decided **here**, at the wrap
    site, rather than by the caller. Auth failures arrive as
    ``imapclient.exceptions.LoginError``, and ``services.imap_transport`` is by
    convention the sole importer of ``imapclient``; making ``feed_monitor``
    inspect ``__cause__`` would either break that convention or push it into
    matching exception messages, which is fragile and provider-dependent.
    """

    def __init__(self, kind: str, message: str) -> None:
        self.kind = kind
        super().__init__(message)


@dataclass(frozen=True)
class BodyAndAttachments:
    """One full-ingest message's text plus its attachment metadata (LUM-695).

    ``message_size_bytes`` is recorded at ingest so the on-demand fetch's
    whole-message precheck usually needs no extra IMAP round trip at all.
    """

    text: str
    attachments: list["email_attachments_mod.AttachmentMeta"] = dc_field(default_factory=list)
    message_size_bytes: int = 0
    dsn: object | None = None
    """A parsed delivery-status notification (LUM-820), or None for ordinary mail.

    Extracted HERE for the reason this class exists at all: the raw MIME is
    discarded right after `_body_to_text`, so nothing downstream can parse it.
    Typed `object` rather than `DsnReport` to keep the adapter free of a service
    import; `feed_monitor` is the only consumer and knows the real type."""


@dataclass(frozen=True)
class FetchedBody:
    """One message's readable text, or why there is none (LUM-810).

    ``reason`` carries the six values the read route's response `Literal`
    declares. `truncated` is deliberately NOT here: it is a property of what
    the route ships after annotation, not of what the adapter fetched, and
    holding it in both places is how a contract drifts.
    """

    text: str
    reason: str = "ok"
    # LUM-713: filled in the session this fetch already holds. A standalone
    # `fetch_inline_images(...)` would open a SECOND IMAP session per /body
    # call — the exact cost that argued against a second endpoint. Review
    # round 1 caught that contradiction; the field is how it stays closed.
    inline_images: tuple = ()
    # Set when a CAP dropped an image, never when one was refused — unlike
    # `truncated` above, this IS the adapter's to know: the caps live where the
    # bytes are, and the route can no longer see what was skipped.
    inline_images_truncated: bool = False


# The shipped `AttachmentFetchError` codes, mapped to this path's reasons.
# `part_gone` is absent on purpose: a body fetch requests no part indexes, so
# it is unreachable here. Anything unmapped degrades rather than 500s — a code
# added to the adapter later must not become a stack trace on the read view.
_BODY_REASON_FOR_FETCH_ERROR: dict[str, str] = {
    "imap_unreachable": "mailbox_unreachable",
    "message_gone": "not_found",
    "message_too_large": "too_large",
    "message_ambiguous": "ambiguous",
}

_MAX_EMAIL_MSGS_PER_TICK = 200
_MAX_EMAIL_BODIES_PER_TICK = 50
# Bounded socket timeout for the synchronous write path so a slow/hung IMAP
# server cannot block the request thread indefinitely (LUM-623).
_WRITE_TIMEOUT_SECONDS = 30
# LUM-705: the same bound for SYNCHRONOUS, user-triggered read paths
# (`fetch_thread_headers`). `_connect`'s timeout defaults to None, which is
# fine for the background poller but not for a route the user is waiting on:
# a REFUSED connection fails fast, but a HUNG one — a host that accepts TCP
# and never answers — would block the request thread indefinitely and the
# caller's degrade-to-plain-Reply path would never run.
_READ_TIMEOUT_SECONDS = 30

# LUM-810: the read view's own ceiling, pre-parse. Deliberately NOT
# `email_attachments.max_message_fetch_bytes()` (50 MiB, env-tunable): that
# bounds pulling a message to extract a file from it, which is a different
# object with a different cost. This one bounds what `html.parser` is asked to
# chew on a request thread — 1 MiB of adversarial markup is a very different
# proposition from 50 MiB under CVE-2025-6069's quadratic behaviour.
_MAX_BODY_BYTES = 1_048_576

_HEADER_FIELDS = ("From", "Subject", "Date", "Message-ID", "List-Unsubscribe")
_HEADER_FETCH_KEY = "BODY.PEEK[HEADER.FIELDS (%s)]" % " ".join(h.upper() for h in _HEADER_FIELDS)
_HEADER_RESP_KEY = b"BODY[HEADER.FIELDS (%s)]" % b" ".join(
    h.upper().encode() for h in _HEADER_FIELDS
)

# LUM-651: a reply-only thread-header re-fetch, kept SEPARATE from the lean ingest
# _HEADER_FIELDS above (widening the ingest set would add unused bytes to every
# poll tick + the LUM-657 backfill). Read only at reply send time.
# LUM-651 threading + LUM-705 reply-all. ONE tuple builds the FETCH key, the
# response key AND the parser's field list, so the selective field-copy posture
# holds by construction: `_parse_header_fields` reads exactly these names, and a
# crafted original's injected header is never read. Widening is cheap; REMOVING
# a field silently breaks whichever consumer needed it — absent fields are
# omitted, not raised — so check both consumers before narrowing.
_THREAD_HEADER_FIELDS = ("Subject", "Message-ID", "References", "In-Reply-To", "To", "Cc")
_THREAD_FETCH_KEY = "BODY.PEEK[HEADER.FIELDS (%s)]" % " ".join(
    h.upper() for h in _THREAD_HEADER_FIELDS
)
_THREAD_RESP_KEY = b"BODY[HEADER.FIELDS (%s)]" % b" ".join(
    h.upper().encode() for h in _THREAD_HEADER_FIELDS
)


def _parse_cursor(since: str | None) -> tuple[int | None, int]:
    """Return ``(uidvalidity, last_uid)`` from a ``"{uidvalidity}:{last_uid}"`` cursor."""
    if not since:
        return (None, 0)
    try:
        uv_s, uid_s = since.split(":", 1)
        return (int(uv_s), int(uid_s))
    except (ValueError, AttributeError):
        return (None, 0)


class EmailImapSource:
    """Poll one IMAP mailbox for new-mail metadata signals."""

    def __init__(self, source: SourceConfig, *, client_factory=None) -> None:
        self.source = source
        # ``instance = sources.id`` — per-mailbox credential lookup (LUM-170).
        self.conn = email_credentials.load_connection(source.user_id, instance=source.id)
        self._client_factory = client_factory
        self.result_cursor: str | None = None

    # ---- connection ---------------------------------------------------

    def _connect(self, *, timeout: float | None = None):
        # LUM-625 Layer 1: the single ``IMAPClient`` construction site, reached by
        # every connection path (poll, fetch_bodies, apply_writeback,
        # reverse_writeback). Scoping here contains all four for free. Connect-time
        # scoping is sufficient — the TCP destination is fixed at connect and the
        # socket is reused for the rest of the session (no further getaddrinfo).
        try:
            with imap_egress_scope(self.conn, user_id=self.source.user_id):
                if self._client_factory is not None:
                    return self._client_factory(self.conn)
                # imap_transport chooses direct (with the SSRF re-check) vs. the
                # Pass-3 CONNECT-proxy tunnel per LUMOGIS_IMAP_EGRESS_PROXY, and logs
                # in. It is the sole imapclient importer (lazy).
                from services import imap_transport

                return imap_transport.connect(self.conn, timeout=timeout)
        # ORDER IS LOAD-BEARING (LUM-633). These three escape UNWRAPPED because
        # other layers catch them BY TYPE — `_connect_writeback` below maps two
        # of them to PermanentActionError so the proposal queue dead-letters a
        # permanent block instead of retrying forever (LUM-625), and poll-health
        # classifies all three distinctly.
        #
        # ImapHostBlockedError and ImapHostUnresolvedError are ValueError
        # SUBCLASSES. Move the broad clause below above them and they get
        # wrapped away silently: `_connect_writeback`'s except becomes dead code
        # and a permanent policy block is retried forever. Nothing about that
        # failure is visible at the call site — which is why
        # tests/test_imap_connect_errors.py drives this method directly.
        except (
            EgressBlockedError,
            email_credentials.ImapHostBlockedError,
            email_credentials.ImapHostUnresolvedError,
        ):
            raise
        except Exception as exc:
            # `imaplib`/`imapclient` name their auth failure "LoginError"; match
            # on the class name so this module need not import imapclient and
            # break imap_transport's sole-importer convention.
            kind = "auth" if type(exc).__name__ in _AUTH_ERROR_NAMES else "network"
            raise ImapConnectError(kind, f"IMAP connect failed ({kind}): {exc}") from exc

    def _connect_writeback(self):
        """``_connect`` for the queue-backed write-back paths, mapping an egress
        block to a permanent failure. A blocked host is a config condition a
        retry cannot fix, so the proposal queue must dead-letter (it keys strictly
        on :class:`PermanentActionError`) rather than retry forever (LUM-625)."""
        try:
            return self._connect(timeout=_WRITE_TIMEOUT_SECONDS)
        except EgressBlockedError as exc:
            raise PermanentActionError(
                "egress_blocked", f"IMAP egress blocked for host {exc.host!r}"
            ) from exc
        except email_credentials.ImapHostBlockedError as exc:
            # SSRF/policy block at connect-time re-validation is a permanent
            # config/attack condition — dead-letter, don't burn retries. A plain
            # ValueError ("did not resolve", transient DNS) is left to propagate
            # so the queue can still retry it (LUM-625 review).
            raise PermanentActionError("egress_blocked", str(exc)) from exc

    # ---- metadata poll ------------------------------------------------

    def poll(
        self, *, since: str | None = None, reseed_on_uidvalidity_change: bool = False
    ) -> list[Signal]:
        """Fetch new-mail metadata since ``since``; return Signal objects.

        Does not persist anything. Sets :attr:`result_cursor` to the advanced
        cursor (``"{uidvalidity}:{max_uid}"``) for the caller to store.

        ``reseed_on_uidvalidity_change`` — the driver passes ``True`` for
        import-scope-bearing sources (``import_state IS NOT NULL``): on a server
        UIDVALIDITY change, re-seed the watermark to the current max UID
        (``UIDNEXT - 1``) instead of re-draining from 0, so a "just_new"/"last_30d"
        mailbox does not silently re-import its whole history (LUM-644).
        """
        prev_uidvalidity, last_uid = _parse_cursor(since)
        client = self._connect()
        try:
            folder = client.select_folder("INBOX", readonly=True)
            uidvalidity = int(folder.get(b"UIDVALIDITY") or folder.get("UIDVALIDITY") or 0)

            # UIDVALIDITY changed → the server reassigned UIDs. Reset the
            # watermark and re-seed rather than trusting stale UIDs (which would
            # otherwise collide in the dedup url and silently drop new mail).
            if prev_uidvalidity is not None and uidvalidity != prev_uidvalidity:
                _log.warning(
                    "imap_uidvalidity_reset source_id=%s old=%s new=%s reseed=%s",
                    self.source.id,
                    prev_uidvalidity,
                    uidvalidity,
                    reseed_on_uidvalidity_change,
                )
                if reseed_on_uidvalidity_change:
                    uidnext = int(folder.get(b"UIDNEXT") or folder.get("UIDNEXT") or 1)
                    last_uid = max(uidnext - 1, 0)
                else:
                    last_uid = 0

            raw_uids = client.search(["UID", f"{last_uid + 1}:*"])
            # ``{cursor+1}:*`` collapses to the highest UID when nothing is new
            # (RFC 3501) — filter client-side.
            new_uids = sorted(u for u in raw_uids if int(u) > last_uid)
            if not new_uids:
                self.result_cursor = f"{uidvalidity}:{last_uid}"
                return []

            # Oldest-first cap: process the lowest new UIDs and advance the
            # cursor to the max processed, so a large first sync drains in UID
            # order over subsequent ticks (taking newest-first would advance the
            # cursor past older unprocessed mail and drop it permanently).
            capped = new_uids[:_MAX_EMAIL_MSGS_PER_TICK]  # new_uids is already sorted ascending
            fetched = client.fetch(capped, [_HEADER_FETCH_KEY])

            signals: list[Signal] = []
            max_uid = last_uid
            for uid in capped:
                data = fetched.get(uid) or {}
                headers = self._parse_headers(data)
                signals.append(self._build_signal(uid, uidvalidity, headers))
                max_uid = max(max_uid, int(uid))

            self.result_cursor = f"{uidvalidity}:{max_uid}"
            return signals
        finally:
            try:
                client.logout()
            except Exception:
                pass

    # ---- import-scope seeding + backfill (LUM-644) --------------------

    def inbox_seed(self) -> tuple[int, int]:
        """Return ``(uidvalidity, max_uid)`` for INBOX, where ``max_uid = UIDNEXT-1``.

        Read-only. Used by the driver to seed the live ``poll_cursor`` at *now* and
        to fix the backfill's upper bound. Uses ``UIDNEXT-1`` (not the ``MESSAGES``
        count, which undercounts after any expunge — UIDs are monotonic).
        """
        client = self._connect()
        try:
            folder = client.select_folder("INBOX", readonly=True)
            uidvalidity = int(folder.get(b"UIDVALIDITY") or folder.get("UIDVALIDITY") or 0)
            uidnext = int(folder.get(b"UIDNEXT") or folder.get("UIDNEXT") or 1)
            return uidvalidity, max(uidnext - 1, 0)
        finally:
            try:
                client.logout()
            except Exception:
                pass

    def backfill_slice(
        self,
        *,
        since,
        before,
        after_uid: int,
        max_uid: int,
        limit: int = _MAX_EMAIL_MSGS_PER_TICK,
    ) -> tuple[int, list[Signal], int, bool]:
        """One bounded historical slice: ``(uidvalidity, signals, advance_uid, empty)``.

        Read-only. Searches ``UID (after_uid, max_uid] SINCE since [BEFORE before+1d]``
        (``BEFORE`` is exclusive on INTERNALDATE, so ``before+1`` makes the end
        inclusive), takes the **lowest** ``limit`` matching UIDs, and builds a Signal
        for each (deduped by the caller via the URL). ``advance_uid`` is the max UID
        in the processed batch — advanced regardless of dedup, so a duplicate-only
        slice still progresses. ``empty`` is True when the search matched nothing in
        the range → the caller marks the backfill ``done`` (empty-search completion;
        NOT ``advance_uid >= max_uid``, which never terminates when out-of-window mail
        sits above the window).
        """
        client = self._connect()
        try:
            folder = client.select_folder("INBOX", readonly=True)
            uidvalidity = int(folder.get(b"UIDVALIDITY") or folder.get("UIDVALIDITY") or 0)
            if after_uid >= max_uid:
                return uidvalidity, [], after_uid, True
            criteria: list = ["UID", f"{after_uid + 1}:{max_uid}", "SINCE", since]
            if before is not None:
                criteria += ["BEFORE", before + timedelta(days=1)]
            matched = sorted(int(u) for u in client.search(criteria))
            if not matched:
                return uidvalidity, [], after_uid, True
            taken = matched[:limit]
            fetched = client.fetch(taken, [_HEADER_FETCH_KEY])
            signals: list[Signal] = []
            advance_uid = after_uid
            for uid in taken:
                data = fetched.get(uid) or {}
                headers = self._parse_headers(data)
                signals.append(self._build_signal(uid, uidvalidity, headers))
                advance_uid = max(advance_uid, int(uid))
            return uidvalidity, signals, advance_uid, False
        finally:
            try:
                client.logout()
            except Exception:
                pass

    # ---- full-ingest body fetch (opt-in) ------------------------------

    def fetch_bodies(self, uids: list[int]) -> dict[int, str]:
        """Fetch + MIME-parse the plain-text bodies for ``uids`` (read-only).

        Uses ``BODY.PEEK[]`` so ``\\Seen`` is never set. Returns ``{uid: text}``;
        a message that fails to parse is skipped (absent from the result).
        Capped at ``_MAX_EMAIL_BODIES_PER_TICK``.
        """
        if not uids:
            return {}
        capped = sorted(uids)[:_MAX_EMAIL_BODIES_PER_TICK]
        client = self._connect()
        out: dict[int, str] = {}
        try:
            client.select_folder("INBOX", readonly=True)
            fetched = client.fetch(capped, ["BODY.PEEK[]"])
            for uid in capped:
                data = fetched.get(uid) or {}
                raw = data.get(b"BODY[]") or data.get("BODY[]")
                if raw is None:
                    continue
                try:
                    out[uid] = self._body_to_text(raw)
                except Exception as exc:
                    _log.warning(
                        "email_parse_skip source_id=%s uid=%s msg=%s",
                        self.source.id,
                        uid,
                        str(exc)[:120],
                    )
        finally:
            try:
                client.logout()
            except Exception:
                pass
        return out

    # ---- full-ingest body + attachment metadata (LUM-695) -------------

    def fetch_bodies_and_attachments(self, uids: list[int]) -> dict[int, BodyAndAttachments]:
        """``fetch_bodies``' sibling: text **and** attachment metadata from ONE parse.

        This exists as a sibling rather than a widened ``fetch_bodies`` because
        the metadata must be extracted **here**, where the raw MIME bytes are.
        ``fetch_bodies`` calls ``_body_to_text(raw)`` and discards ``raw``, and
        ``feed_monitor`` then assigns a *string* to ``raw_content`` — so
        ``process_signal`` never holds MIME bytes and could not have parsed
        anything. Keeping ``fetch_bodies`` untouched also leaves its existing
        callers (``email_quote.fetch_body_text``, ``email_reply_draft``) on their
        unchanged ``dict[int, str]`` contract.

        Same ``BODY.PEEK[]`` fetch as ``fetch_bodies`` — no extra IMAP round trip
        and no second traversal. Extraction never raises (see
        ``services.email_attachments.extract_metadata``), so a hostile message
        costs its own attachment list and nothing else.
        """
        if not uids:
            return {}
        from services import email_attachments

        capped = sorted(uids)[:_MAX_EMAIL_BODIES_PER_TICK]
        client = self._connect()
        out: dict[int, BodyAndAttachments] = {}
        try:
            client.select_folder("INBOX", readonly=True)
            fetched = client.fetch(capped, ["BODY.PEEK[]"])
            for uid in capped:
                data = fetched.get(uid) or {}
                raw = data.get(b"BODY[]") or data.get("BODY[]")
                if raw is None:
                    continue
                try:
                    text = self._body_to_text(raw)
                except Exception as exc:
                    _log.warning(
                        "email_parse_skip source_id=%s uid=%s msg=%s",
                        self.source.id,
                        uid,
                        str(exc)[:120],
                    )
                    continue
                raw_bytes = raw if isinstance(raw, bytes) else str(raw).encode("utf-8", "replace")
                # LUM-820: parse the DSN from the same bytes, in the same pass.
                # Never raises (see services.email_dsn), so a hostile report
                # cannot stall the poll.
                from services.email_dsn import parse_dsn

                out[uid] = BodyAndAttachments(
                    text=text,
                    attachments=email_attachments.extract_metadata(raw_bytes),
                    message_size_bytes=len(raw_bytes),
                    dsn=parse_dsn(raw_bytes),
                )
        finally:
            try:
                client.logout()
            except Exception:
                pass
        return out

    def fetch_attachment_part(
        self,
        *,
        uid: int,
        part_index: int,
        message_id: str | None,
        folder_hint: str | None = None,
        uidvalidity: int = 0,
    ) -> "email_attachments_mod.ResolvedPart":  # noqa: F821 - see TYPE_CHECKING import
        """Fetch ONE attachment's bytes on demand — the single-part case of
        :meth:`fetch_attachment_parts`, which carries the full contract."""
        parts = self.fetch_attachment_parts(
            uid=uid,
            part_indexes=[part_index],
            message_id=message_id,
            folder_hint=folder_hint,
            uidvalidity=uidvalidity,
        )
        return parts[0]

    def fetch_body_bounded(
        self,
        *,
        uid: int,
        message_id: str | None,
        folder_hint: str | None = None,
        uidvalidity: int = 0,
    ) -> FetchedBody:
        """The body sibling of :meth:`fetch_attachment_parts` (LUM-810).

        Same locate → size-check → fetch-once shape, reusing ``_locate_message``
        unchanged, and **returns a reason instead of raising** for every
        expected failure. The route answers 200 with that reason; a read view
        that 500s on mail someone else authored is not a read view.

        Three deliberate differences from the attachment sibling:

        * **Its own, much tighter cap.** ``_MAX_BODY_BYTES`` is 1 MiB, not the
          50 MiB ``email_attachments.max_message_fetch_bytes()`` allows. That
          knob bounds pulling a message to extract a file from it; this one
          bounds what ``html.parser`` chews on the request thread.
        * **A post-fetch length check.** ``_message_size`` returns ``None`` when
          the server withholds ``RFC822.SIZE``, and the precheck then degrades
          OPEN. The attachment path tolerates that because the per-attachment
          cap from recorded metadata still applies; here there is no such
          backstop, so the bytes are measured again after the fetch and dropped
          unparsed if they are over.
        * **A connect timeout.** This is a synchronous, user-triggered read, so
          it takes ``_READ_TIMEOUT_SECONDS`` like ``fetch_thread_headers`` —
          not the attachment path's bare ``_connect()``, whose ``None`` default
          suits the background sweep that also drives it. A host that accepts
          TCP and never answers would otherwise hold the request thread open.
        """
        try:
            client = self._connect(timeout=_READ_TIMEOUT_SECONDS)
        except Exception as exc:
            _log.warning(
                "email_body_imap_unreachable source_id=%s type=%s",
                self.source.id,
                type(exc).__name__,
            )
            return FetchedBody("", "mailbox_unreachable")

        try:
            try:
                located, transport_failed = self._locate_message(
                    client,
                    uid,
                    message_id,
                    folder_hint,
                    uidvalidity,
                    deadline=time.monotonic() + _READ_TIMEOUT_SECONDS,
                )
            except AttachmentFetchError as exc:
                reason = _BODY_REASON_FOR_FETCH_ERROR.get(exc.code, "mailbox_unreachable")
                return FetchedBody("", reason)

            if located is None:
                # Never finished looking is not evidence of absence. Reporting
                # "no longer in your mailbox" for a dropped connection is the
                # exact lie the retryable/terminal split exists to avoid.
                return FetchedBody("", "mailbox_unreachable" if transport_failed else "not_found")

            size = self._message_size(client, located)
            if size is not None and size > _MAX_BODY_BYTES:
                # Oversized as a WHOLE message — but that is usually an
                # attachment, not the text. Try fetching just the text parts
                # before telling the user their one-line email is too big
                # (LUM-814). `None` means we could not, so refuse as before.
                # ONE BODYSTRUCTURE, shared. Fetching it in each helper cost a
                # third round trip on this path and contradicted the docstring
                # below that promised the walk was reused.
                struct = self._bodystructure(client, located)
                text = self._fetch_text_only(client, located, struct)
                if text is not None:
                    images, cut = self._fetch_inline_only(client, located, struct)
                    return FetchedBody(text, "ok" if text else "no_body", images, cut)
                _log.info(
                    "email_body_too_large source_id=%s size=%s limit=%s",
                    self.source.id,
                    size,
                    _MAX_BODY_BYTES,
                )
                return FetchedBody("", "too_large")

            try:
                fetched = client.fetch([located], ["BODY.PEEK[]"]) or {}
            except Exception as exc:
                _log.warning(
                    "email_body_fetch_failed source_id=%s type=%s",
                    self.source.id,
                    type(exc).__name__,
                )
                return FetchedBody("", "mailbox_unreachable")

            data = fetched.get(located) or {}
            raw = data.get(b"BODY[]") or data.get("BODY[]")
            if raw is None:
                return FetchedBody("", "not_found")
            raw_bytes = raw if isinstance(raw, bytes) else str(raw).encode("utf-8", "replace")

            if len(raw_bytes) > _MAX_BODY_BYTES:
                # Only reachable when the server withheld RFC822.SIZE above.
                _log.info(
                    "email_body_too_large_post_fetch source_id=%s size=%s limit=%s",
                    self.source.id,
                    len(raw_bytes),
                    _MAX_BODY_BYTES,
                )
                return FetchedBody("", "too_large")

            from services.email_body import extract_text

            text = extract_text(raw_bytes)
            images, cut = self._inline_from_raw(raw_bytes)
            return FetchedBody(text, "ok" if text else "no_body", images, cut)
        finally:
            self._safe_logout(client)

    # ---- text-only fetch (LUM-814) ------------------------------------

    @staticmethod
    def _text_part_specs(struct, prefix: str = "") -> list[tuple[str, int]]:
        """``[(part_number, size_bytes)]`` for the TEXT parts, per RFC 3501.

        Numbering is the part of this that bites: a top-level non-multipart
        message's body is part ``1``; a multipart's children are ``1``, ``2``…;
        a nested multipart's are ``1.1``, ``1.2``… Getting it wrong fetches the
        wrong bytes rather than failing loudly, which is why the caller treats
        an empty or surprising result as "fall back", never as "no text".
        """
        out: list[tuple[str, int]] = []
        if getattr(struct, "is_multipart", False):
            for index, sub in enumerate(struct[0], start=1):
                child = f"{prefix}{index}." if prefix else f"{index}."
                out.extend(EmailImapSource._text_part_specs(sub, child))
            return out
        maintype = (struct[0] or b"").decode("ascii", "replace").upper()
        if maintype != "TEXT":
            return out
        size = struct[6] if len(struct) > 6 and isinstance(struct[6], int) else 0
        # `prefix` carries a trailing dot for children; a lone message is "1".
        out.append((prefix.rstrip(".") or "1", int(size)))
        return out

    @staticmethod
    def _bodystructure(client, uid: int):
        """The message's ``BODYSTRUCTURE``, or ``None``. Never raises.

        Fetched once per read and passed to both walkers: the text walk and the
        inline-image walk describe the same tree, and asking twice cost a round
        trip for an answer already in hand.
        """
        try:
            fetched = client.fetch([uid], ["BODYSTRUCTURE"]) or {}
            return (fetched.get(uid) or {}).get(b"BODYSTRUCTURE")
        except Exception as exc:  # noqa: BLE001 - the caller falls back
            _log.warning("email_bodystructure_failed type=%s", type(exc).__name__)
            return None

    def _fetch_text_only(self, client, uid: int, struct=None) -> str | None:
        """The message's text, fetched WITHOUT its attachments (LUM-814).

        ``BODY.PEEK[]`` pulls the whole message, so a one-line email behind a
        900 KB photo was refused by the whole-message cap — the readability of
        the *text* decided by the size of things that are not the text.

        Returns ``None`` for "could not do this", never for "no text": every
        surprise here (a server that will not give a BODYSTRUCTURE, a shape this
        walker does not recognise, a part that comes back empty) falls the
        caller back to the whole-message path. That is deliberate — this runs
        against real IMAP servers whose BODYSTRUCTURE quirks no fake covers, so
        the failure mode has to be "as before", not "wrong".

        **Not the byte-range partial fetch the LUM-810 spike rejected.** That
        was ``BODY.PEEK[]<0.N>``, which answers on a different response key and
        truncates raw MIME mid-part. Fetching a *named* part is well-defined.
        """
        try:
            if struct is None:
                struct = self._bodystructure(client, uid)
            if struct is None:
                return None
            specs = self._text_part_specs(struct)
            if not specs:
                return None
            total = sum(size for _, size in specs)
            if total > _MAX_BODY_BYTES:
                # The TEXT itself is oversized — a real refusal, not an
                # attachment's fault. Let the caller report `too_large`.
                _log.info(
                    "email_body_text_parts_too_large source_id=%s size=%s", self.source.id, total
                )
                return None

            keys: list[str] = []
            for number, _ in specs:
                keys.append(f"BODY.PEEK[{number}.MIME]")
                keys.append(f"BODY.PEEK[{number}]")
            data = (client.fetch([uid], keys) or {}).get(uid) or {}

            parts = []
            fetched_bytes = 0
            for number, _ in specs:
                head = data.get(f"BODY[{number}.MIME]".encode()) or b""
                body = data.get(f"BODY[{number}]".encode()) or b""
                if not body:
                    continue
                fetched_bytes += len(body)
                if fetched_bytes > _MAX_BODY_BYTES:
                    # BODYSTRUCTURE lied about part sizes — refuse rather than
                    # chewing multi-MiB on the request thread (LUM-814).
                    _log.info(
                        "email_body_text_parts_too_large_post_fetch source_id=%s size=%s",
                        self.source.id,
                        fetched_bytes,
                    )
                    return None
                # Headers + body is a complete MIME entity, so the stdlib does
                # the charset and transfer-encoding work rather than this file.
                parts.append(email.message_from_bytes(head.rstrip() + b"\r\n\r\n" + body))
            if not parts:
                return None

            from services.email_body import text_from_parts

            return text_from_parts(parts)
        except Exception as exc:  # noqa: BLE001 - any surprise means fall back
            _log.warning(
                "email_body_text_only_unavailable source_id=%s type=%s",
                self.source.id,
                type(exc).__name__,
            )
            return None

    @staticmethod
    def _inline_from_raw(raw_bytes: bytes) -> tuple[tuple, bool]:
        """Inline images from a message already in hand. **Never raises.**

        The normal path has the full bytes from ``BODY.PEEK[]``, so this costs
        one parse and no IMAP work at all.

        Returns ``(images, truncated)``.
        """
        try:
            from services.email_body import _walk_body_parts
            from services.email_inline_images import inline_images_from_parts

            msg = email.message_from_bytes(raw_bytes)
            parts = _walk_body_parts(msg) if msg.is_multipart() else [msg]
            images, truncated = inline_images_from_parts(parts)
            return tuple(images), truncated
        except Exception as exc:  # noqa: BLE001 - a strip never breaks a read
            _log.warning("email_inline_from_raw_failed type=%s", type(exc).__name__)
            return (), False

    def _fetch_inline_only(self, client, uid: int, struct=None) -> tuple[tuple, bool]:
        """Inline images for a message too large to fetch whole (LUM-713).

        Runs on the session ``_fetch_text_only`` just used, on the
        ``BODYSTRUCTURE`` already fetched for it — so an oversized message costs
        one extra FETCH *item*, never an extra round trip and never a session.

        **The caps here are a transfer bound, not the decision.** A
        ``BODYSTRUCTURE`` size is the *encoded* octet count (RFC 3501), and the
        collector's caps are about decoded bytes — so applying
        ``MAX_ONE_INLINE_BYTES`` to it directly dropped any image between about
        384 KiB and 512 KiB on this path while the normal path showed it. This
        path therefore refuses only what could not *possibly* fit, and lets the
        one collector make every real decision. That is what keeps the two paths
        agreeing, which is the property the strip is specified on.

        Returns ``(images, truncated)``, and ``((), False)`` for any surprise. A
        strip is never the reason a message fails to open.
        """
        try:
            from services.email_inline_images import MAX_INLINE_IMAGES
            from services.email_inline_images import MAX_INLINE_TRANSFER_BYTES
            from services.email_inline_images import MAX_ONE_INLINE_TRANSFER_BYTES
            from services.email_inline_images import inline_images_from_parts

            if struct is None:
                struct = self._bodystructure(client, uid)
            if struct is None:
                return (), False
            specs = self._inline_part_specs(struct)
            if not specs:
                return (), False

            # Anything past the count cap is dropped here rather than fetched.
            # Unlike the byte caps this one needs no decoded size, so it means
            # the same thing on both paths.
            cut = len(specs) > MAX_INLINE_IMAGES
            keys: list[str] = []
            wanted: list[str] = []
            budget = 0
            for number, size in specs[:MAX_INLINE_IMAGES]:
                if size > MAX_ONE_INLINE_TRANSFER_BYTES:
                    # Too big under ANY transfer encoding, so no decoded size
                    # could bring it back under the cap. Genuinely truncated.
                    cut = True
                    continue
                if budget + size > MAX_INLINE_TRANSFER_BYTES:
                    # The aggregate bound, applied BEFORE the transfer — the
                    # whole reason this path exists is not moving bytes the
                    # reader will never see.
                    cut = True
                    break
                budget += size
                wanted.append(number)
                keys.append(f"BODY.PEEK[{number}.MIME]")
                keys.append(f"BODY.PEEK[{number}]")
            if not keys:
                return (), cut
            data = (client.fetch([uid], keys) or {}).get(uid) or {}

            parts = []
            for number in wanted:
                head = data.get(f"BODY[{number}.MIME]".encode()) or b""
                body = data.get(f"BODY[{number}]".encode()) or b""
                if not body:
                    continue
                parts.append(email.message_from_bytes(head.rstrip() + b"\r\n\r\n" + body))
            images, truncated = inline_images_from_parts(parts)
            return tuple(images), (truncated or cut)
        except Exception as exc:  # noqa: BLE001
            _log.warning("email_inline_fetch_failed type=%s", type(exc).__name__)
            return (), False

    @staticmethod
    def _inline_part_specs(struct, prefix: str = "") -> list[tuple[str, int]]:
        """``[(part_number, size_bytes)]`` for IMAGE parts carrying a Content-ID.

        Sibling of :meth:`_text_part_specs`, same RFC 3501 numbering. The
        Content-ID lives at index 3 of a ``body-type-1part`` — the ``id`` field —
        so no per-part MIME header fetch is needed just to find candidates.

        **``size`` is ENCODED octets**, per RFC 3501. It is only ever compared
        against the transfer bounds; the decoded caps belong to the collector.

        This must select the same parts :func:`is_inline_image` would, or the
        two read paths disagree — so it applies that predicate's disposition
        rule too, not just its type-and-Content-ID rule.
        """
        out: list[tuple[str, int]] = []
        if getattr(struct, "is_multipart", False):
            for index, sub in enumerate(struct[0], start=1):
                child = f"{prefix}{index}." if prefix else f"{index}."
                out.extend(EmailImapSource._inline_part_specs(sub, child))
            return out
        maintype = (struct[0] or b"").decode("ascii", "replace").upper()
        if maintype != "IMAGE":
            return out
        # Field 4 (index 3) is the Content-ID. Absent → the body cannot address
        # it, so it is not an inline image (same rule as `is_inline_image`).
        if len(struct) < 4 or not struct[3]:
            return out
        if EmailImapSource._is_attachment_disposition(struct):
            # A photo the sender marked `attachment` AND gave a Content-ID is in
            # the ATTACHMENT list. Fetching it here would spend the strip's
            # budget on a part the collector then rejects, so this path would
            # show fewer images than the normal one — and claim truncation for
            # the difference.
            return out
        size = struct[6] if len(struct) > 6 and isinstance(struct[6], int) else 0
        out.append((prefix.rstrip(".") or "1", int(size)))
        return out

    @staticmethod
    def _is_attachment_disposition(struct) -> bool:
        """True only when a non-text ``body-type-1part`` positively says
        ``attachment``.

        The disposition is *extension data*: a server may omit it entirely, and
        its index differs by type (a text part carries an extra ``lines`` field
        first). Only IMAGE parts reach here, so index 8 is the one — but the
        check stays defensive and answers **False when unsure**, because
        guessing "attachment" here would silently hide a real inline image.
        """
        try:
            if len(struct) < 9:
                return False
            disposition = struct[8]
            if not disposition:
                return False
            kind = disposition[0] if isinstance(disposition, (list, tuple)) else disposition
            if isinstance(kind, (bytes, bytearray)):
                return kind.decode("ascii", "replace").upper() == "ATTACHMENT"
            return str(kind).upper() == "ATTACHMENT"
        except Exception:  # noqa: BLE001 - server-controlled shape
            return False

    def fetch_attachment_parts(
        self,
        *,
        uid: int,
        part_indexes: list[int],
        message_id: str | None,
        folder_hint: str | None = None,
        uidvalidity: int = 0,
        client=None,
    ) -> list["email_attachments_mod.ResolvedPart"]:  # noqa: F821 - see TYPE_CHECKING import
        """Fetch SEVERAL attachments' bytes in ONE session. Read-only (``BODY.PEEK[]``).

        The message is located, size-checked and fetched **once**, then every
        requested index is resolved from that single parse — so forwarding three
        attachments costs one round trip, not three. Results come back in the
        order requested.

        **All-or-nothing:** an index that does not resolve raises ``part_gone``
        rather than returning a short list. A forward silently missing one file
        is worse than one that refuses, because the user already approved a
        message they believed carried it.

        **Does not assume INBOX.** LUM-645's organize moves classified mail into
        ``Lumogis/<label>`` and LUM-646's cleanup moves it further, so an
        INBOX-only fetch would report "no longer in the mailbox" for exactly the
        mail an organized user has most of. The message is located by
        ``Message-ID`` — ``folder_hint`` (from ``email_organize_log``) first,
        then INBOX, then a sweep of the folder list — and only once that search
        is empty is the message declared gone.

        **The stored UID is only trusted while UIDVALIDITY still matches.** A
        UIDVALIDITY reset reassigns UIDs, so the recorded uid can come to point
        at a DIFFERENT message — and serving its bytes under the recorded
        filename would hand the user someone else's mail with nothing to notice.
        On a mismatch the uid shortcut is skipped and only the ``Message-ID``
        search (which identifies the message, not its slot) is used. The write
        paths already assert this via ``_assert_uidvalidity``; so does this one.

        **Bounded by the WHOLE-MESSAGE size, not the attachment size.** The
        fetch pulls the entire message and ``client.fetch`` buffers, so an
        ``RFC822.SIZE`` precheck runs before any ``BODY.PEEK[]``: a 400 MB
        message carrying a 10 KB attachment is refused without being read.

        Raises :class:`AttachmentFetchError` with a ``code`` the route maps to a
        status: ``imap_unreachable`` (503, retryable), ``message_gone`` (410,
        never retryable), ``message_too_large`` / ``part_gone``. The 503/410
        split is only meaningful if transport failures stay OUT of the 410 path:
        a dropped connection mid-search must not report the message deleted.
        """
        from services import email_attachments

        wanted = [int(i) for i in (part_indexes or [])]
        if not wanted:
            # Nothing to fetch — do not open a session for an empty request.
            return []

        # A caller-supplied session (the LUM-702 sweep's batch path) is REUSED and
        # left open; we only own — and therefore only close — a session we opened.
        # Same shape as `apply_organize`, and for the same reason: connect + login
        # + logout per message is a real provider rate-limit and lockout risk.
        owns_client = client is None
        if owns_client:
            try:
                client = self._connect()
            except Exception as exc:
                # Includes EgressBlockedError and any transport failure. The mailbox
                # being unreachable is a DIFFERENT condition from the message being
                # gone: one is worth retrying, the other never will be, and
                # collapsing them makes fetch-on-demand feel like a bug.
                _log.warning(
                    "email_attachment_imap_unreachable source_id=%s type=%s",
                    self.source.id,
                    type(exc).__name__,
                )
                raise AttachmentFetchError(
                    "imap_unreachable", "mailbox is not reachable right now"
                ) from exc
        try:
            located, transport_failed = self._locate_message(
                client, uid, message_id, folder_hint, uidvalidity
            )
            if located is None:
                if transport_failed:
                    # We never finished looking. Reporting 410 here would tell the
                    # user their message was deleted when the connection merely
                    # dropped — the exact lie the 503/410 split exists to avoid.
                    raise AttachmentFetchError(
                        "imap_unreachable", "mailbox is not reachable right now"
                    )
                raise AttachmentFetchError("message_gone", "message is no longer in the mailbox")
            found_uid = located

            size = self._message_size(client, found_uid)
            limit = email_attachments.max_message_fetch_bytes()
            if size is not None and size > limit:
                _log.warning(
                    "email_attachment_message_too_large source_id=%s size=%s limit=%s",
                    self.source.id,
                    size,
                    limit,
                )
                raise AttachmentFetchError(
                    "message_too_large",
                    "message is too large to open attachments from",
                )

            try:
                fetched = client.fetch([found_uid], ["BODY.PEEK[]"]) or {}
            except Exception as exc:
                # A drop mid-fetch is transport, not absence — 503, not a raw 500.
                _log.warning(
                    "email_attachment_fetch_failed source_id=%s type=%s",
                    self.source.id,
                    type(exc).__name__,
                )
                raise AttachmentFetchError(
                    "imap_unreachable", "mailbox is not reachable right now"
                ) from exc
            data = fetched.get(found_uid) or {}
            raw = data.get(b"BODY[]") or data.get("BODY[]")
            if raw is None:
                raise AttachmentFetchError("message_gone", "message is no longer in the mailbox")
            raw_bytes = raw if isinstance(raw, bytes) else str(raw).encode("utf-8", "replace")
            # One parse, every requested index — resolved through the SAME
            # `_walk_parts` that wrote them, so the batch inherits the
            # consistency-by-construction the single-part path relies on.
            resolved = email_attachments.resolve_parts(raw_bytes, wanted)
            missing = [i for i in wanted if i not in resolved]
            if missing:
                raise AttachmentFetchError("part_gone", f"attachment part(s) not found: {missing}")
            return [resolved[i] for i in wanted]
        finally:
            if owns_client:
                self._safe_logout(client)

    def _locate_message(
        self,
        client,
        uid: int,
        message_id: str | None,
        folder_hint: str | None,
        uidvalidity: int = 0,
        deadline: float | None = None,
    ) -> tuple[int | None, bool]:
        """Locate the message; return ``(uid_or_None, transport_failed)``.

        Tries, in order: the ``folder_hint`` organize recorded, INBOX, then
        every other folder by ``Message-ID``.

        Two things this returns rather than decides:

        * **The stored uid is only used when UIDVALIDITY still matches.** After a
          reset the server reassigns UIDs, so that uid may now be a different
          message entirely — and the caller would serve its bytes under the
          recorded filename. ``Message-ID`` identifies the message itself, so
          the search path stays valid across a reset and is all we use then.
        * **``transport_failed``** tells the caller we never finished looking. A
          SELECT/LIST that raised is not evidence of absence, and reporting 410
          ("gone for good") for a dropped connection is worse than useless.
        """
        transport_failed = False
        for folder in self._candidate_folders(client, folder_hint):
            # `deadline` bounds the WHOLE sweep; the socket timeout bounds only
            # one operation. A mailbox with forty folders can spend forty times
            # the per-op budget here, which on a synchronous user-facing read
            # means a threadpool worker held for minutes. Opt-in, so the
            # background batch callers keep their existing unbounded behaviour.
            if deadline is not None and time.monotonic() > deadline:
                _log.warning(
                    "email_locate_deadline_exceeded source_id=%s folder=%s",
                    self.source.id,
                    folder,
                )
                # Not "gone" — we never finished looking, which is precisely
                # what `transport_failed` means to the caller.
                return None, True
            try:
                selected = client.select_folder(folder, readonly=True)
            except Exception:
                # Could be "folder vanished between LIST and SELECT" (benign) or
                # a dead connection (not). We cannot tell them apart here, so
                # record it and let the caller fail safe if nothing is found.
                transport_failed = True
                continue
            if (
                folder == "INBOX"
                and self._uidvalidity_matches(selected, uidvalidity)
                and self._uid_present(client, uid)
            ):
                return uid, transport_failed
            try:
                hits = self._search_message_ids(client, message_id)
            except Exception:
                transport_failed = True
                continue
            if len(hits) > 1:
                # `Message-ID` is authored by the sender and nothing enforces its
                # uniqueness, so a second message can legitimately-looking carry
                # an existing one. Picking a hit would serve THOSE bytes under
                # the filename recorded for the original — a plausible file with
                # the wrong contents, which is precisely the failure mode this
                # read path is designed around. `_resolve_unique_uid` already
                # refuses to guess on the write side; refuse here too.
                _log.warning(
                    "email_attachment_ambiguous_message_id source_id=%s folder=%s hits=%s",
                    self.source.id,
                    folder,
                    len(hits),
                )
                raise AttachmentFetchError(
                    "message_ambiguous",
                    "several messages share this Message-ID; refusing to guess",
                )
            if hits:
                return hits[0], transport_failed
        return None, transport_failed

    @staticmethod
    def _search_message_ids(client, message_id: str | None) -> list[int]:
        """ALL UIDs in the selected folder matching ``message_id``.

        Deliberately not ``_search_message_id``, whose contract is "first hit,
        only safe where *any* match is the question". Serving bytes is a
        which-exact-message question, so the caller needs the count to refuse an
        ambiguous answer.
        """
        if not message_id:
            return []
        hits = client.search(["HEADER", "Message-ID", message_id]) or []
        return [int(h) for h in hits]

    @staticmethod
    def _uidvalidity_matches(selected: dict | None, expected: int) -> bool:
        """True when the stored UIDVALIDITY still identifies this mailbox's UIDs.

        ``expected == 0`` means the caller has no stored value to compare (a
        non-IMAP-shaped signal); fall back to the ``Message-ID`` search rather
        than trusting a uid we cannot validate.
        """
        if not expected:
            return False
        raw = (selected or {}).get(b"UIDVALIDITY")
        if raw is None:
            raw = (selected or {}).get("UIDVALIDITY")
        try:
            current = int(raw) if raw is not None else 0
        except (TypeError, ValueError):
            return False
        # A server that reports nothing cannot confirm UID identity — fail closed,
        # exactly as _assert_uidvalidity does on the write paths.
        return bool(current) and current == expected

    def _candidate_folders(self, client, folder_hint: str | None):
        """``folder_hint`` → INBOX → every other folder. Yields each name once."""
        seen: set[str] = set()
        for name in (folder_hint, "INBOX"):
            if name and name not in seen:
                seen.add(name)
                yield name
        try:
            listed = client.list_folders() or ()
        except Exception:
            return
        for entry in listed:
            name = entry[2] if isinstance(entry, (tuple, list)) and len(entry) >= 3 else entry
            if isinstance(name, (bytes, bytearray)):
                name = name.decode("utf-8", "replace")
            name = str(name)
            if name and name not in seen:
                seen.add(name)
                yield name

    @staticmethod
    def _uid_present(client, uid: int) -> bool:
        try:
            fetched = client.fetch([uid], ["RFC822.SIZE"]) or {}
        except Exception:
            return False
        return uid in fetched or str(uid) in fetched

    @staticmethod
    def _message_size(client, uid: int) -> int | None:
        """``RFC822.SIZE`` for ``uid``, or ``None`` when the server withholds it.

        A server that does not answer leaves the precheck unable to refuse; that
        is a degrade, not a bypass — the per-attachment cap still applies from
        the recorded metadata before this method is ever reached.
        """
        try:
            fetched = client.fetch([uid], ["RFC822.SIZE"]) or {}
        except Exception:
            return None
        data = fetched.get(uid) or fetched.get(str(uid)) or {}
        raw = data.get(b"RFC822.SIZE")
        if raw is None:
            raw = data.get("RFC822.SIZE")
        try:
            return int(raw) if raw is not None else None
        except (TypeError, ValueError):
            return None

    # ---- header re-fetch (LUM-657 sender backfill) --------------------

    def fetch_headers(self, uids: list[int]) -> tuple[int, dict[int, dict[str, str]]]:
        """Re-fetch the header subset for ``uids`` (read-only, ``BODY.PEEK``).

        Returns ``(current_uidvalidity, {uid: parsed_headers})``. A uid whose
        message is gone is simply absent from the map. The caller must compare
        each uid's *stored* uidvalidity against the returned current value and
        skip mismatches (the server may have reassigned UIDs). Used by the
        sender backfill to recover ``From`` for signals ingested before the
        sender was persisted (LUM-642).
        """
        if not uids:
            return (0, {})
        capped = sorted(set(uids))[:_MAX_EMAIL_MSGS_PER_TICK]
        client = self._connect()
        try:
            folder = client.select_folder("INBOX", readonly=True)
            uidvalidity = int(folder.get(b"UIDVALIDITY") or folder.get("UIDVALIDITY") or 0)
            fetched = client.fetch(capped, [_HEADER_FETCH_KEY])
            out: dict[int, dict[str, str]] = {}
            for uid in capped:
                data = fetched.get(uid) or {}
                if not data:
                    continue  # message gone
                out[int(uid)] = self._parse_headers(data)
            return (uidvalidity, out)
        finally:
            try:
                client.logout()
            except Exception:
                pass

    def fetch_thread_headers(self, uid: int) -> tuple[int, dict[str, str] | None]:
        """Re-fetch one message's threading headers for a reply (LUM-651).

        Returns ``(current_uidvalidity, {Subject, Message-ID, References,
        In-Reply-To})`` — or ``(current_uidvalidity, None)`` when the uid is gone
        (moved/trashed by LUM-645/646, so absent from INBOX). Read-only
        (``BODY.PEEK``, ``readonly=True``); one connection, one FETCH; no writes.
        The caller compares the returned uidvalidity against the stored one and
        treats a mismatch as a miss. Distinct from the lean ingest ``fetch_headers``
        so the poll/backfill hot path is not widened.
        """
        client = self._connect(timeout=_READ_TIMEOUT_SECONDS)
        try:
            folder = client.select_folder("INBOX", readonly=True)
            uidvalidity = int(folder.get(b"UIDVALIDITY") or folder.get("UIDVALIDITY") or 0)
            fetched = client.fetch([uid], [_THREAD_FETCH_KEY])
            data = fetched.get(uid) or {}
            if not data:
                return (uidvalidity, None)  # message gone
            return (
                uidvalidity,
                self._parse_header_fields(data, _THREAD_HEADER_FIELDS, resp_key=_THREAD_RESP_KEY),
            )
        finally:
            try:
                client.logout()
            except Exception:
                pass

    # ---- act-tier write-back (LUM-623) --------------------------------

    def apply_writeback(
        self,
        *,
        uidvalidity: int,
        uid: int,
        op: str,
        target: str | None = None,
        mailbox: str = "INBOX",
    ) -> dict:
        """Apply a classification-driven mutation to one message; return reverse_input.

        Opens the mailbox read-write (bounded timeout), re-validates UIDVALIDITY
        (stale → permanent failure so the queue does not retry a vanished UID),
        captures the message's ``Message-ID`` as the reverse locator, then
        performs the op. Raises :class:`PermanentActionError` for conditions a
        retry cannot fix (stale UID, unsupported server capability, missing
        Message-ID, no label mechanism).
        """
        client = self._connect_writeback()
        try:
            folder = client.select_folder(mailbox, readonly=False)
            self._assert_uidvalidity(folder, uidvalidity)
            caps = self._capabilities(client)
            message_id = self._fetch_message_id(client, uid)

            # Every write-back op here is reversible, and the reverse relocates
            # the message by Message-ID. A message with no Message-ID cannot be
            # located again, so the write would be silently irreversible — fail
            # closed for ALL ops, not just move (LUM-623 code-review #3).
            if not message_id:
                raise PermanentActionError(
                    "no_message_id", "message has no Message-ID; write would be irreversible"
                )

            if op == "mark_read":
                client.add_flags([uid], ["\\Seen"])
                return {"op": op, "mailbox": mailbox, "message_id": message_id, "flag": "\\Seen"}
            if op == "flag":
                client.add_flags([uid], ["\\Flagged"])
                return {"op": op, "mailbox": mailbox, "message_id": message_id, "flag": "\\Flagged"}
            if op == "label":
                return self._apply_label(client, folder, uid, target, mailbox, message_id, caps)
            if op == "move":
                return self._apply_move(client, uid, target, mailbox, message_id, caps)
            raise PermanentActionError("unknown_op", f"unknown writeback op: {op!r}")
        finally:
            self._safe_logout(client)

    def _apply_label(self, client, folder, uid, target, mailbox, message_id, caps) -> dict:
        if not target:
            raise PermanentActionError("target_required", "label needs a target")
        if "X-GM-EXT-1" in caps:
            client.add_gmail_labels([uid], [target])
            kind = "gmail"
        elif self._supports_keywords(folder):
            client.add_flags([uid], [target])
            kind = "keyword"
        else:
            raise PermanentActionError(
                "needs_folder_move", "server has no label mechanism; move to a folder instead"
            )
        return {
            "op": "label",
            "mailbox": mailbox,
            "message_id": message_id,
            "target": target,
            "label_kind": kind,
        }

    def _apply_move(self, client, uid, target, mailbox, message_id, caps) -> dict:
        if not target:
            raise PermanentActionError("target_required", "move needs a target folder")
        if "MOVE" in caps:
            client.move([uid], target)
        elif "UIDPLUS" in caps:
            # Idempotency: a prior transient-failed attempt may have COPYed
            # already — don't duplicate. Check target, then re-select the source
            # so the delete+expunge act on the origin folder.
            already = self._message_in_folder(client, target, message_id)
            client.select_folder(mailbox, readonly=False)
            if not already:
                client.copy([uid], target)
            client.add_flags([uid], ["\\Deleted"])
            client.expunge([uid])
        else:
            raise PermanentActionError("unsupported_move", "server lacks MOVE and UIDPLUS")
        return {"op": "move", "mailbox": mailbox, "message_id": message_id, "target": target}

    # ---- LUM-645 systematic organization ------------------------------
    # A dedicated path (separate from LUM-623's user-approved apply_writeback) that
    # applies Lumogis's category classification as mailbox organization:
    #  * label  -> Gmail nested label `Lumogis/<label>` (X-GM-EXT-1), else IMAP
    #              keyword ATOM `Lumogis_<slug>` (RFC 3501 flags are atoms — no
    #              spaces/parens; imapclient does not quote the flags path), else
    #              needs_folder_move.
    #  * move   -> ensure the `Lumogis<delim><label>` folder exists (server's own
    #              hierarchy delimiter, not a literal '/'), then MOVE/UIDPLUS.
    # Returns a reverse token compatible with reverse_writeback (op/mailbox/
    # message_id/target[/flag]); the exact `target` written is recorded so the
    # ledger reversal removes the identical artifact.

    _ORGANIZE_PARENT = "Lumogis"

    @staticmethod
    def _to_keyword_atom(slug: str, label: str) -> str:
        """A valid IMAP keyword atom for a non-Gmail label (no spaces/parens)."""
        base = slug or "".join(ch for ch in (label or "").lower() if ch.isalnum() or ch == "_")
        base = base or "cat"
        return f"{EmailImapSource._ORGANIZE_PARENT}_{base}"

    @staticmethod
    def _hierarchy_delimiter(client) -> str:
        """The server's folder hierarchy delimiter (from LIST); default '/'."""
        try:
            for flags, delimiter, _name in client.list_folders() or ():
                if delimiter:
                    return delimiter.decode() if isinstance(delimiter, bytes) else str(delimiter)
        except Exception:  # noqa: BLE001 - fall back to the common default
            pass
        return "/"

    @staticmethod
    def _role_folder(client, role: str) -> str | None:
        """The mailbox folder whose RFC 6154 SPECIAL-USE flag maps to ``role`` (e.g.
        ``"trash"`` / ``"junk"``), or ``None`` when the server advertises none.

        Reuses ``imap_transport._role_for`` so the role→flag mapping stays in one place
        (LUM-644). Used by the LUM-646 cleanup paths to locate the provider's own Trash
        and spam folders without hard-coding names."""
        from services import imap_transport

        try:
            for flags, _delimiter, name in client.list_folders() or ():
                resolved, _hidden = imap_transport._role_for(flags)
                if resolved == role:
                    return name.decode() if isinstance(name, bytes) else str(name)
        except Exception:  # noqa: BLE001 - absent SPECIAL-USE => caller degrades
            pass
        return None

    def append_to_drafts(self, mime_bytes: bytes, message_id: str) -> dict:
        """APPEND a built message to the mailbox's SPECIAL-USE ``\\Drafts`` folder
        with the ``\\Draft`` flag (LUM-652 Save-to-Drafts).

        Targets the RFC 6154 SPECIAL-USE Drafts folder (via ``_role_folder``, not a
        hand-named "Drafts") so Gmail's label model doesn't produce a duplicate
        draft. A server that advertises no ``\\Drafts`` special-use raises
        ``PermanentActionError("no_drafts_folder")`` so the caller degrades to
        Send-only. Read-only w.r.t. every other folder; egress-contained via
        ``_connect_writeback``. ``message_id`` is passed by the handler (from the
        built message) — this method never re-parses ``mime_bytes``. Returns the
        target folder + Message-ID (a locator for a future reverse-delete)."""
        client = self._connect_writeback()
        try:
            drafts = self._role_folder(client, "drafts")
            if not drafts:
                raise PermanentActionError(
                    "no_drafts_folder",
                    "server advertises no \\Drafts special-use folder",
                )
            # Explicit msg_time — a bare APPEND with no internal date is rejected by
            # some servers; string "\\Draft" flag matches the codebase convention.
            client.append(
                drafts,
                mime_bytes,
                flags=["\\Draft"],
                msg_time=datetime.now(timezone.utc),
            )
            return {"folder": drafts, "message_id": message_id}
        finally:
            self._safe_logout(client)

    def delete_from_drafts(self, folder: str, message_id: str) -> dict:
        """Reverse of :meth:`append_to_drafts` (LUM-681): locate the appended draft in
        ``folder`` by its Message-ID and remove it.

        **Idempotent** — a draft already gone (the user deleted it in their own client)
        is a success no-op (the desired end state, "no such draft", already holds).
        Requires UIDPLUS for a **targeted** UID EXPUNGE, so no unrelated ``\\Deleted``
        message in the folder is ever removed (mirrors ``_apply_move``). Egress-contained
        via ``_connect_writeback``; re-uses ``_resolve_unique_uid`` so it refuses to guess
        between two same-Message-ID drafts."""
        client = self._connect_writeback()
        try:
            client.select_folder(folder, readonly=False)
            try:
                uid = self._resolve_unique_uid(client, message_id)
            except PermanentActionError as exc:
                if exc.code == "reverse_not_found":
                    return {"deleted": False, "reason": "already_gone"}
                raise
            caps = self._capabilities(client)
            if "UIDPLUS" not in caps:
                raise PermanentActionError(
                    "unsupported_expunge",
                    "server lacks UIDPLUS for a targeted draft delete",
                )
            client.add_flags([uid], ["\\Deleted"])
            client.expunge([uid])
            return {"deleted": True, "folder": folder, "message_id": message_id}
        finally:
            self._safe_logout(client)

    def enumerate_role_folder(
        self,
        client,
        *,
        role: str,
        after_uid: int,
        max_uid: int,
        limit: int = _MAX_EMAIL_MSGS_PER_TICK,
    ) -> tuple[str | None, int, list[int], int, bool]:
        """One bounded UID slice of a SPECIAL-USE role folder (the spam-walk analogue of
        ``backfill_slice``, which is INBOX-only). Selects the ``role`` folder read-only on
        the caller-supplied session, returns ``(folder, uidvalidity, uids, advance, empty)``.

        ``folder`` is ``None`` when the role folder is absent (caller degrades). ``empty``
        is True when the ``(after_uid, max_uid]`` range matched nothing → walk complete."""
        folder = self._role_folder(client, role)
        if folder is None:
            return None, 0, [], after_uid, True
        info = client.select_folder(folder, readonly=True)
        uidvalidity = int(info.get(b"UIDVALIDITY") or info.get("UIDVALIDITY") or 0)
        if after_uid >= max_uid:
            return folder, uidvalidity, [], after_uid, True
        matched = sorted(int(u) for u in client.search(["UID", f"{after_uid + 1}:{max_uid}"]))
        if not matched:
            return folder, uidvalidity, [], after_uid, True
        taken = matched[:limit]
        advance = max([after_uid, *taken])
        return folder, uidvalidity, taken, advance, False

    def folder_uidnext(self, client, folder: str) -> tuple[int, int]:
        """``(uidvalidity, uidnext)`` for ``folder`` (read-only STATUS), for seeding a walk."""
        info = client.select_folder(folder, readonly=True)
        uidvalidity = int(info.get(b"UIDVALIDITY") or info.get("UIDVALIDITY") or 0)
        uidnext = int(info.get(b"UIDNEXT") or info.get("UIDNEXT") or 0)
        return uidvalidity, uidnext

    def ensure_folder(self, client, path: str) -> None:
        """Idempotently ensure ``path`` exists. Uses a per-instance cache to avoid
        a network round-trip once known-present this process, and a portable
        ``folder_exists`` LIST pre-check rather than tolerating create errors by
        substring (which would mask a permissions/quota NO)."""
        cache = self.__dict__.setdefault("_ensured_folders", set())
        if path in cache:
            return
        if client.folder_exists(path):
            cache.add(path)
            return
        client.create_folder(path)  # a genuine failure (perms/quota) propagates
        cache.add(path)

    def organize_session(self):
        """Open one write-back IMAP session for a batch of organize/undo ops (so
        reconcile/undo don't connect+login+logout per message — a real provider
        rate-limit/lockout risk). Caller must close it via ``close_session``."""
        return self._connect_writeback()

    def read_session(self):
        """Open one READ-ONLY IMAP session for a batch of attachment fetches.

        The read-side sibling of :meth:`organize_session`. Uses ``_connect``
        rather than ``_connect_writeback`` because a blocked host on a *read*
        should surface as a retryable ``imap_unreachable``, not as the permanent
        failure the proposal queue needs in order to dead-letter a write.

        Caller must close it via :meth:`close_session`.
        """
        return self._connect()

    def close_session(self, client) -> None:
        self._safe_logout(client)

    def apply_organize(
        self,
        *,
        uidvalidity: int,
        uid: int,
        op: str,
        label: str,
        slug: str = "",
        mailbox: str = "INBOX",
        client=None,
    ) -> dict:
        # A caller-supplied session (batch path) is reused; otherwise open+close one.
        if client is not None:
            return self._apply_organize_on(
                client,
                uidvalidity=uidvalidity,
                uid=uid,
                op=op,
                label=label,
                slug=slug,
                mailbox=mailbox,
            )
        conn = self._connect_writeback()
        try:
            return self._apply_organize_on(
                conn,
                uidvalidity=uidvalidity,
                uid=uid,
                op=op,
                label=label,
                slug=slug,
                mailbox=mailbox,
            )
        finally:
            self._safe_logout(conn)

    def _apply_organize_on(
        self,
        client,
        *,
        uidvalidity: int,
        uid: int,
        op: str,
        label: str,
        slug: str,
        mailbox: str,
    ) -> dict:
        if True:
            folder = client.select_folder(mailbox, readonly=False)
            self._assert_uidvalidity(folder, uidvalidity)
            caps = self._capabilities(client)
            message_id = self._fetch_message_id(client, uid)
            if not message_id:
                raise PermanentActionError(
                    "no_message_id", "message has no Message-ID; write would be irreversible"
                )
            if op == "label":
                if "X-GM-EXT-1" in caps:
                    target = f"{self._ORGANIZE_PARENT}/{label}"
                    client.add_gmail_labels([uid], [target])
                    kind = "gmail"
                elif self._supports_keywords(folder):
                    target = self._to_keyword_atom(slug, label)
                    client.add_flags([uid], [target])
                    kind = "keyword"
                else:
                    raise PermanentActionError(
                        "needs_folder_move", "server has no label mechanism; use folder-sort"
                    )
                return {
                    "op": "label",
                    "mailbox": mailbox,
                    "message_id": message_id,
                    "target": target,
                    "label_kind": kind,
                }
            if op == "move":
                delim = self._hierarchy_delimiter(client)
                # Escape the server delimiter inside the label so a label like
                # "Bills/Utilities" becomes one folder, not unintended nesting.
                safe_label = label.replace(delim, "-") if delim else label
                target = f"{self._ORGANIZE_PARENT}{delim}{safe_label}"
                self.ensure_folder(client, target)
                client.select_folder(mailbox, readonly=False)  # re-select after LIST/CREATE
                self._apply_move(client, uid, target, mailbox, message_id, caps)
                return {
                    "op": "move",
                    "mailbox": mailbox,
                    "message_id": message_id,
                    "target": target,
                }
            if op == "trash":
                # LUM-646: move to the provider's own \Trash special-use folder (the only
                # destructive cleanup action). `mailbox` is the source folder (INBOX for a
                # window pass; the \Junk folder for the spam-empty pass) so the reverse
                # token restores it there. Reversible via reverse_writeback(op="move").
                trash = self._role_folder(client, "trash")
                if not trash:
                    raise PermanentActionError(
                        "no_trash_folder", "server advertises no \\Trash special-use folder"
                    )
                self._apply_move(client, uid, trash, mailbox, message_id, caps)
                return {
                    "op": "move",
                    "mailbox": mailbox,
                    "message_id": message_id,
                    "target": trash,
                }
            raise PermanentActionError("unknown_op", f"unknown organize op: {op!r}")

    def reverse_writeback(
        self,
        *,
        op: str,
        mailbox: str,
        message_id: str | None = None,
        target: str | None = None,
        flag: str | None = None,
        client=None,
    ) -> None:
        """Undo a prior :meth:`apply_writeback`. Relocates the message by Message-ID
        (stable across UIDVALIDITY changes), so it works even though the original
        UID is gone after a move. A caller-supplied ``client`` (batch undo) is reused;
        otherwise one session is opened and closed.
        """
        if client is not None:
            self._reverse_writeback_on(
                client,
                op=op,
                mailbox=mailbox,
                message_id=message_id,
                target=target,
                flag=flag,
            )
            return
        conn = self._connect_writeback()
        try:
            self._reverse_writeback_on(
                conn,
                op=op,
                mailbox=mailbox,
                message_id=message_id,
                target=target,
                flag=flag,
            )
        finally:
            self._safe_logout(conn)

    def _reverse_writeback_on(
        self,
        client,
        *,
        op: str,
        mailbox: str,
        message_id: str | None,
        target: str | None,
        flag: str | None,
    ) -> None:
        if True:
            if op == "move":
                # The message now lives in ``target`` — find it and move it back.
                client.select_folder(target, readonly=False)
                uid = self._resolve_unique_uid(client, message_id)
                caps = self._capabilities(client)
                if "MOVE" in caps:
                    client.move([uid], mailbox)
                elif "UIDPLUS" in caps:
                    client.copy([uid], mailbox)
                    client.add_flags([uid], ["\\Deleted"])
                    client.expunge([uid])
                else:
                    raise PermanentActionError("unsupported_move", "server lacks MOVE and UIDPLUS")
                return

            # flag / keyword / gmail-label reverse — message is still in mailbox.
            client.select_folder(mailbox, readonly=False)
            uid = self._resolve_unique_uid(client, message_id)
            if op in ("mark_read", "flag"):
                client.remove_flags([uid], [flag])
            elif op == "label":
                caps = self._capabilities(client)
                if "X-GM-EXT-1" in caps:
                    client.remove_gmail_labels([uid], [target])
                else:
                    client.remove_flags([uid], [target])

    # ---- write-back helpers -------------------------------------------

    @staticmethod
    def _assert_uidvalidity(folder: dict, expected: int) -> None:
        raw = folder.get(b"UIDVALIDITY")
        if raw is None:
            raw = folder.get("UIDVALIDITY")
        current = int(raw) if raw is not None else 0
        if not expected:
            return  # no stored UIDVALIDITY to compare against (non-imap signal)
        if not current:
            # A valid mailbox always reports UIDVALIDITY (RFC 3501). A missing/0
            # value means we cannot confirm the UID still identifies the same
            # message — fail closed rather than act on a possibly-reused UID
            # (LUM-623 code-review #5).
            raise PermanentActionError(
                "stale", "server did not report UIDVALIDITY; cannot confirm UID identity"
            )
        if current != expected:
            raise PermanentActionError(
                "stale", f"UIDVALIDITY changed {expected} -> {current}; UID no longer valid"
            )

    @staticmethod
    def _capabilities(client) -> set[str]:
        # Do NOT swallow transport errors here — an empty set is read as "server
        # lacks MOVE/UIDPLUS/labels" and turns a transient failure into a
        # permanent dead-letter. Let the exception propagate so the queue retries
        # (LUM-623 code-review #2).
        caps = client.capabilities()
        out: set[str] = set()
        for c in caps or ():
            s = c.decode() if isinstance(c, (bytes, bytearray)) else str(c)
            out.add(s.upper())
        return out

    @staticmethod
    def _supports_keywords(folder: dict) -> bool:
        permflags = folder.get(b"PERMANENTFLAGS") or folder.get("PERMANENTFLAGS") or ()
        for f in permflags:
            s = f.decode() if isinstance(f, (bytes, bytearray)) else str(f)
            if s == "\\*":
                return True
        return False

    def _fetch_message_id(self, client, uid) -> str | None:
        # Transport errors propagate (transient → retried); only a genuinely
        # absent Message-ID header returns None (LUM-623 code-review #2).
        data = client.fetch([uid], ["BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)]"]) or {}
        raw = data.get(uid)
        if raw is None:
            try:
                raw = data.get(int(uid))
            except (TypeError, ValueError):
                raw = None
        if not raw:
            return None
        val = None
        for k, v in raw.items():
            key = k if isinstance(k, (bytes, bytearray)) else str(k).encode()
            if b"MESSAGE-ID" in key.upper():
                val = v
                break
        if val is None:
            return None
        if isinstance(val, (bytes, bytearray)):
            val = val.decode("utf-8", "replace")
        mid = email.message_from_string(str(val)).get("Message-ID")
        return str(mid).strip() if mid else None

    @staticmethod
    def _search_message_id(client, message_id: str | None) -> int | None:
        # Transport errors propagate (transient → retried). Returns the first
        # match — only safe where "any match" is the question (idempotency
        # check); reverse relocation must use _resolve_unique_uid instead.
        if not message_id:
            return None
        hits = client.search(["HEADER", "Message-ID", message_id])
        return int(hits[0]) if hits else None

    def _resolve_unique_uid(self, client, message_id: str | None) -> int:
        """Resolve the single UID in the selected folder matching ``message_id``.

        Raises :class:`PermanentActionError` when there is no match
        (``reverse_not_found``) or more than one (``reverse_ambiguous``) — a
        reverse must never guess which of several same-Message-ID messages to
        touch (LUM-623 code-review #6).
        """
        if not message_id:
            raise PermanentActionError("reverse_not_found", "no Message-ID to locate message")
        hits = client.search(["HEADER", "Message-ID", message_id])
        if not hits:
            raise PermanentActionError("reverse_not_found", "message not found for reverse")
        if len(hits) > 1:
            raise PermanentActionError(
                "reverse_ambiguous", "multiple messages share this Message-ID; refusing to guess"
            )
        return int(hits[0])

    def _message_in_folder(self, client, folder: str, message_id: str | None) -> bool:
        if not message_id:
            return False
        client.select_folder(folder, readonly=True)
        return self._search_message_id(client, message_id) is not None

    @staticmethod
    def _safe_logout(client) -> None:
        try:
            client.logout()
        except Exception:
            pass

    @staticmethod
    def _body_to_text(raw) -> str:
        """Delegates to `services.email_body.extract_text` (LUM-810).

        **One extractor, one behaviour** for the read view and for ingestion.
        This used to collect `text/plain` parts only, so HTML-only mail — very
        common — reached the classifier and the summariser as an empty string,
        and an HTML-only single-part reached them as raw markup. Keeping a
        second extractor here would let the read view and the stored summary
        disagree about what a message says.
        """
        from services.email_body import extract_text

        return extract_text(raw)

    # ---- helpers ------------------------------------------------------

    def _parse_headers(self, data: dict) -> dict[str, str]:
        return self._parse_header_fields(data, _HEADER_FIELDS, resp_key=_HEADER_RESP_KEY)

    def _parse_header_fields(
        self, data: dict, fields, *, resp_key: bytes | None = None
    ) -> dict[str, str]:
        """Parse a ``BODY[HEADER.FIELDS (...)]`` FETCH response into ``{field: value}``.

        Shared by the ingest header parse (``_parse_headers``) and the LUM-651
        reply thread-header re-fetch — one parser, two field sets. ``resp_key`` is
        the preferred bytes key for the requested field set; falls back to any
        ``HEADER.FIELDS`` key (tolerates a fake/alternate client's plain-string
        key). Only the requested ``fields`` are extracted; absent fields are
        omitted from the result.
        """
        raw = data.get(resp_key) if resp_key is not None else None
        if raw is None:
            for k, v in data.items():
                if isinstance(k, (bytes, str)) and b"HEADER.FIELDS" in (
                    k if isinstance(k, bytes) else k.encode()
                ):
                    raw = v
                    break
        if raw is None:
            return {}
        if isinstance(raw, str):
            raw = raw.encode("utf-8", "replace")
        msg: Message = email.message_from_bytes(raw)
        out: dict[str, str] = {}
        for field in fields:
            val = msg.get(field)
            if val:
                out[field] = str(val)
        return out

    def _build_signal(self, uid: int, uidvalidity: int, headers: dict[str, str]) -> Signal:
        from datetime import datetime
        from datetime import timezone

        subject = headers.get("Subject", "") or "(no subject)"
        # PII-free, per-mailbox-unique, UIDVALIDITY-safe dedup url.
        url = f"imap://{self.source.id}/{uidvalidity}/{uid}"
        # LUM-676: signals.signal_id is a UUID PRIMARY KEY (no default) — an empty
        # string fails the Postgres cast in _persist (silently swallowed → the email
        # row never persists) AND collapses every email onto one Qdrant point
        # (point_id = uuid5("signal::"+signal_id)). Derive a DETERMINISTIC id from
        # the url (unique per mailbox message, stable across re-polls) so persistence,
        # ON CONFLICT dedup, Qdrant uniqueness, and the LUM-623 signal_id lookup all work.
        signal_id = str(uuid.uuid5(uuid.NAMESPACE_URL, url))
        return Signal(
            signal_id=signal_id,
            source_id=str(self.source.id),
            title=subject,
            url=url,
            published_at=None,
            content_summary="",
            raw_content="",  # metadata mode — no body fetched
            entities=[],
            topics=[],
            importance_score=0.0,
            relevance_score=0.0,
            notified=False,
            created_at=datetime.now(timezone.utc),
            user_id=self.source.user_id,
            source_type="imap",
            category="",
            headers=headers,
        )
