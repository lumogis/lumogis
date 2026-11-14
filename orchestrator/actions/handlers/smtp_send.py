# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""SMTP send actions — reply + compose-new, sent as the user (LUM-650).

The outbound sibling of ``imap_writeback.py``. Two act-tier actions routed
through the shipped proposal → Approvals → executor pipeline:

* ``email_reply``   — reply to a message Lumogis surfaced (recovers the mailbox +
  original sender from the signal; re-fetches the original Subject + Message-ID
  over IMAP so "Re: …" + ``In-Reply-To`` are correct, degrading gracefully when
  LUM-645/646 already moved/trashed the original).
* ``email_compose`` — a fresh message from an owned mailbox to explicit ``to``.

Both use ``action_type = "email_send"`` — **non-hard-limited** (decision #11): the
hard-limited ``first_contact`` action_type stays reserved for a future autonomous
sender. The attended first-contact protection here is a create-time annotation
(``_annotate_send`` → the proposal's ``first_contact`` flag) that FORCES Ask, so a
send to a brand-new address is never auto-approved even in DO mode.

Security invariants (see also ``adapters/email_smtp_sender.py``):

* Identity (``user_id``) comes from the executor, never the payload
  (``extra="forbid"``). The ownership gate runs BEFORE any connect/credential
  load — a member cannot send from another member's mailbox via a shared signal.
* From is ALWAYS the mailbox's authenticated account (the input models have no
  ``from`` field) — anti-spoofing, and required by provider submission.
* Credentials are read at send time and never logged, never written to the
  sent-log or the ActionResult.
"""

from __future__ import annotations

import logging
import os
import re
import time
from email.utils import parseaddr
from typing import Optional

from actions.errors import PermanentActionError
from actions.handlers.imap_writeback import _load_owned_source
from actions.handlers.imap_writeback import _parse_imap_url
from actions.handlers.imap_writeback import _signal_url
from actions.registry import register_action
from models.actions import ActionResult
from models.actions import ActionSpec
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import field_validator
from pydantic import model_validator
from services.send_outcome import RECIPIENTS_REFUSED_PREFIX

import config

_log = logging.getLogger(__name__)

_CONNECTOR = "smtp"
_ACTION_TYPE = "email_send"  # NON-hard-limited (decision #11); Approvals-gated

# LUM-651: pragmatic caps on the reply References chain (tuning, not a correctness
# bound — folded headers can validly be longer). A chain past either cap keeps the
# thread root + the most-recent ids and drops from the middle (jwz-style). The
# direct-parent link (In-Reply-To) is set independently, so truncation never
# breaks it — worst case is slightly-degraded deep-thread nesting.
_MAX_REFERENCE_IDS = 20
_MAX_REFERENCE_BYTES = 2048

# A deliberately-basic addr-spec shape check (local@domain, no whitespace/control
# chars). It rejects a malformed recipient at proposal CREATE (422) rather than
# letting it reach SMTP and fail as a send-time refusal; the adapter's CR/LF
# rejection is the second, header-injection-specific line of defence.
_ADDR_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# LUM-698: a combined ceiling across to+cc+bcc, enforced at proposal CREATE
# (422) rather than at send. Without it a pathological recipient list would
# blow up both `_is_first_contact`'s ANY(%s) and the SMTP envelope.
_MAX_RECIPIENTS = 100

# LUM-710: a batch executes as this many sequential SMTP transactions, and since
# LUM-710 that happens on the batch-queue worker rather than the execute request
# thread — so the cap no longer doubles as a latency bound and can be sized for
# how many subscriptions a real inbox has rather than for how long a request may
# block. What still bounds it: the worker paces sends
# (``EMAIL_UNSUBSCRIBE_PACE_SECONDS``) so a batch does not look like a burst to a
# consumer SMTP provider, and the whole run must finish inside
# ``BATCH_QUEUE_STUCK_AFTER_SECONDS`` (1800 s default) or the sweeper reclaims it.
# At 50 targets and the default 2 s pace the worst case is ~3 minutes — an order
# of magnitude of headroom. Raise the pace and this must come down with it;
# ``test_email_unsubscribe_batch.py`` pins the arithmetic.
MAX_UNSUBSCRIBE_BATCH = 50

# The batch-queue `kind` the approved batch is enqueued under (LUM-710). Declared
# here rather than in the handler module because the enqueue side needs it and
# the handler side imports this module — the other direction would be a cycle.
UNSUBSCRIBE_BATCH_KIND = "email_unsubscribe"

# LUM-695: a per-message count cap on attachment REFERENCES. The byte cap is
# separate and lives in the builder, where the whole message is visible — ten
# separately-legal 20 MB files are only over the limit once they are on the
# same message.
_MAX_ATTACHMENTS = 10


def _check_recipient_cap(*lists: list[str]) -> None:
    total = sum(len(x or []) for x in lists)
    if total > _MAX_RECIPIENTS:
        raise ValueError(f"too many recipients: {total} (max {_MAX_RECIPIENTS} across to+cc+bcc)")


def _validate_recipient(addr: str) -> str:
    a = (addr or "").strip()
    if any(ch in a for ch in "\r\n") or any(ord(ch) < 32 for ch in a):
        raise ValueError("recipient contains a control character")
    if not _ADDR_RE.match(a):
        raise ValueError(f"malformed recipient address: {a!r}")
    return a


class EmailReplyInput(BaseModel):
    """Reply to a surfaced message. The mailbox + original sender are recovered
    from the signal — never client-set. No ``from`` field (From is invariant).

    ``cc``/``bcc`` (LUM-698) are the only client-set recipients here: the primary
    recipient is still always the original sender, derived from the signal."""

    model_config = ConfigDict(extra="forbid")

    signal_id: str
    body: str
    subject: Optional[str] = None  # default "Re: <original subject>"
    cc: list[str] = Field(default_factory=list)
    bcc: list[str] = Field(default_factory=list)
    # LUM-695: REFERENCES to staged files, never bytes. `action_proposals.payload`
    # is JSONB returned verbatim to the approval card; base64 there would bloat the
    # row, cross the wire twice, and render as a wall of noise where a summary
    # belongs. Ownership of each id is checked at PROPOSE time (a Pydantic model
    # cannot query the DB); at execute time a missing id is a PermanentActionError.
    attachment_ids: list[str] = Field(default_factory=list, max_length=_MAX_ATTACHMENTS)

    @field_validator("cc", "bcc")
    @classmethod
    def _check_optional_recipients(cls, v: list[str]) -> list[str]:
        return [_validate_recipient(a) for a in v]

    @model_validator(mode="after")
    def _cap_recipients(self) -> "EmailReplyInput":
        # +1 for the signal-derived primary recipient.
        _check_recipient_cap(self.cc, self.bcc, [""])
        return self


class EmailComposeInput(BaseModel):
    """Compose a fresh message from an owned mailbox. No ``from`` field."""

    model_config = ConfigDict(extra="forbid")

    source_id: str
    to: list[str] = Field(min_length=1)
    subject: str
    body: str
    cc: list[str] = Field(default_factory=list)
    bcc: list[str] = Field(default_factory=list)
    # LUM-695: REFERENCES to staged files, never bytes. `action_proposals.payload`
    # is JSONB returned verbatim to the approval card; base64 there would bloat the
    # row, cross the wire twice, and render as a wall of noise where a summary
    # belongs. Ownership of each id is checked at PROPOSE time (a Pydantic model
    # cannot query the DB); at execute time a missing id is a PermanentActionError.
    attachment_ids: list[str] = Field(default_factory=list, max_length=_MAX_ATTACHMENTS)

    # WIDENED (LUM-698) rather than joined by a second validator: a same-named
    # sibling method would shadow this one and silently drop `to` validation.
    @field_validator("to", "cc", "bcc")
    @classmethod
    def _check_recipients(cls, v: list[str]) -> list[str]:
        return [_validate_recipient(a) for a in v]

    @model_validator(mode="after")
    def _cap_recipients(self) -> "EmailComposeInput":
        _check_recipient_cap(self.to, self.cc, self.bcc)
        return self


class EmailForwardInput(BaseModel):
    """Forward a surfaced message to explicit recipients (LUM-698).

    Keyed on ``signal_id`` (not ``source_id``) because a forward is *of a
    surfaced message* — the mailbox and the original content are recovered from
    the signal exactly as reply does. No ``from`` field: the original sender is
    NOT preserved in From (that would be spoofing); they appear only inside the
    quoted header block of the body."""

    model_config = ConfigDict(extra="forbid")

    signal_id: str
    to: list[str] = Field(min_length=1)
    body: str  # the user's own note; may be "" (the forwarded block is the message)
    subject: Optional[str] = None  # default "Fwd: <original subject>"
    cc: list[str] = Field(default_factory=list)
    bcc: list[str] = Field(default_factory=list)
    # LUM-695: REFERENCES to staged files, never bytes. `action_proposals.payload`
    # is JSONB returned verbatim to the approval card; base64 there would bloat the
    # row, cross the wire twice, and render as a wall of noise where a summary
    # belongs. Ownership of each id is checked at PROPOSE time (a Pydantic model
    # cannot query the DB); at execute time a missing id is a PermanentActionError.
    attachment_ids: list[str] = Field(default_factory=list, max_length=_MAX_ATTACHMENTS)
    # LUM-712: indexes of the ORIGINAL message's attachments to carry along.
    # Indexes, not bytes — the signal already identifies the message, so the
    # handler re-fetches at execute time exactly as it does for staged files.
    forward_part_indexes: list[int] = Field(default_factory=list, max_length=_MAX_ATTACHMENTS)

    @field_validator("to", "cc", "bcc")
    @classmethod
    def _check_recipients(cls, v: list[str]) -> list[str]:
        return [_validate_recipient(a) for a in v]

    @model_validator(mode="after")
    def _cap_recipients(self) -> "EmailForwardInput":
        _check_recipient_cap(self.to, self.cc, self.bcc)
        return self


class ResolvedTarget(BaseModel):
    """One unsubscribe target, resolved SERVER-SIDE at propose time.

    Built by ``POST /api/v1/email/unsubscribe/propose`` from the stored
    ``email_unsubscribe_target`` row — never by the client, which posts sender
    addresses only. Keeping the resolved targets in the action payload is what
    lets the Approvals card show the approver the exact recipients (the payload
    is returned verbatim by ``PendingProposalItem``), and it guarantees the
    approved payload is the one that sends: resolving at execute instead would
    let a message arriving between approval and execute change where the mail
    goes.
    """

    model_config = ConfigDict(extra="forbid")

    from_addr: str  # the SENDER being unsubscribed from (audit trail)
    mailto_addr: str  # the unsubscribe recipient, read from the stored row
    subject: str = "unsubscribe"

    @field_validator("from_addr", "mailto_addr")
    @classmethod
    def _check_addrs(cls, v: str) -> str:
        return _validate_recipient(v)

    @field_validator("subject")
    @classmethod
    def _clean_subject(cls, v: str) -> str:
        # build_message rejects CR/LF too, but failing at validation gives a 422
        # instead of a mid-batch PermanentActionError.
        if any(c in v for c in "\r\n"):
            raise ValueError("subject must not contain control characters")
        return v[:200] or "unsubscribe"


class EmailUnsubscribeInput(BaseModel):
    """A whole batch is ONE proposal, reviewed once.

    Every unsubscribe recipient is an address the user has never emailed, so a
    per-target gate would mean twenty prompts for twenty senders. The cap is
    MAX_UNSUBSCRIBE_BATCH rather than _MAX_RECIPIENTS because execution is that
    many sequential SMTP transactions, paced.

    **This is also the batch-queue payload model** (LUM-710): the enqueued job
    stores exactly this, and ``batch_queue._run_one_tick`` re-validates it on
    claim. That is not incidental reuse — it means every recipient validator
    above (addr-spec shape, CR/LF rejection, the ``extra="forbid"`` that keeps a
    ``from`` field off the payload) is re-applied to data that has round-tripped
    through JSONB, so a row edited in the database cannot become a send.
    """

    model_config = ConfigDict(extra="forbid")

    source_id: str
    targets: list[ResolvedTarget] = Field(min_length=1, max_length=MAX_UNSUBSCRIBE_BATCH)


class EmailSaveDraftInput(BaseModel):
    """Save a reply/compose draft to the mailbox's Drafts folder (LUM-652).

    Two mutually-exclusive shapes, enforced at create (a bad shape is a 422, never a
    dead-lettered proposal at execute): a **reply** draft carries ``signal_id`` (the
    mailbox, original sender, and threading are recovered from the signal — no ``to``);
    a **compose** draft carries ``source_id`` + non-empty ``to`` + ``subject``. No
    ``from`` field (From is the mailbox's own account)."""

    model_config = ConfigDict(extra="forbid")

    signal_id: Optional[str] = None
    source_id: Optional[str] = None
    to: Optional[list[str]] = None
    subject: Optional[str] = None
    body: str
    cc: list[str] = Field(default_factory=list)
    bcc: list[str] = Field(default_factory=list)
    # LUM-695: REFERENCES to staged files, never bytes. `action_proposals.payload`
    # is JSONB returned verbatim to the approval card; base64 there would bloat the
    # row, cross the wire twice, and render as a wall of noise where a summary
    # belongs. Ownership of each id is checked at PROPOSE time (a Pydantic model
    # cannot query the DB); at execute time a missing id is a PermanentActionError.
    attachment_ids: list[str] = Field(default_factory=list, max_length=_MAX_ATTACHMENTS)
    # LUM-712: indexes of the ORIGINAL message's attachments to carry along.
    # Indexes, not bytes — the signal already identifies the message, so the
    # handler re-fetches at execute time exactly as it does for staged files.
    forward_part_indexes: list[int] = Field(default_factory=list, max_length=_MAX_ATTACHMENTS)

    @field_validator("to", "cc", "bcc")
    @classmethod
    def _check_recipients(cls, v):
        if v is None:
            return v
        return [_validate_recipient(a) for a in v]

    @model_validator(mode="after")
    def _exactly_one_shape(self) -> "EmailSaveDraftInput":
        """THREE legal shapes (LUM-698 widened this from two):

        * **reply-draft**   — ``signal_id``, **no** ``to``
        * **forward-draft** — ``signal_id`` **+ non-empty** ``to``   (NEW)
        * **compose-draft** — ``source_id`` + non-empty ``to`` + ``subject``

        Note for future readers (LUM-695 / reply-all): "a reply draft carries no
        recipients" now means no ``to`` specifically — ``cc``/``bcc`` are legal on
        every shape, and ``signal_id`` + ``to`` is the **forward** shape, not an
        invalid reply.
        """
        is_signal = self.signal_id is not None
        is_compose = self.source_id is not None
        if is_signal == is_compose:
            raise ValueError("provide exactly one of {signal_id} or {source_id}")
        if is_compose:
            if not self.to:
                raise ValueError("compose-draft requires a non-empty recipient list")
            if not (self.subject and self.subject.strip()):
                raise ValueError("compose-draft requires a subject")
        # signal-based: `to` absent → reply-draft; `to` non-empty → forward-draft.
        # An explicitly-empty list is neither, and is a client bug worth naming.
        elif self.to is not None and not self.to:
            raise ValueError(
                "signal-based draft: omit `to` for a reply-draft, or supply a "
                "non-empty `to` for a forward-draft"
            )
        _check_recipient_cap(self.to or [], self.cc, self.bcc)
        return self


# ---------------------------------------------------------------------------
# Address helpers (normalized for the first-contact match + sent-log storage)
# ---------------------------------------------------------------------------


def _normalize_addr(addr: str) -> str:
    """Lowercased bare addr-spec (display name stripped) — the canonical form for
    the first-contact comparison and for what we persist to ``email_sent_log``."""
    _, spec = parseaddr(addr or "")
    return spec.strip().lower()


def _signal_row(signal_id: str) -> Optional[dict]:
    ms = config.get_metadata_store()
    return ms.fetch_one(
        "SELECT from_addr, title FROM signals WHERE signal_id = %s LIMIT 1",
        (signal_id,),
    )


# ---------------------------------------------------------------------------
# First-contact detection (create-time annotation + flag)
# ---------------------------------------------------------------------------


def _is_first_contact(user_id: str, to_addrs: list[str]) -> bool:
    """True if ANY recipient has neither been received from nor sent to before.

    Normalized (case + display-name) on both sides. A recipient that is unknown
    on both the received (``signals.from_addr``) and sent (``email_sent_log``)
    sides makes this a first contact.
    """
    targets = sorted({_normalize_addr(a) for a in to_addrs if _normalize_addr(a)})
    if not targets:
        # No resolvable recipient → treat as first contact (fail closed).
        return True
    ms = config.get_metadata_store()
    known: set[str] = set()

    received = ms.fetch_all(
        "SELECT DISTINCT lower(from_addr) AS a FROM signals "
        "WHERE user_id = %s AND lower(from_addr) = ANY(%s)",
        (user_id, targets),
    )
    known.update(str(r["a"]) for r in (received or []) if r.get("a"))

    # LUM-698: a past CC counts as contacted (you demonstrably corresponded with
    # them). `bcc_addrs` is DELIBERATELY excluded and unindexed — querying it
    # would turn this gate into a side channel over addresses the user kept
    # private. Accepted cost: a BCC-only correspondent forces Ask forever.
    # Do NOT add bcc_addrs here.
    sent = ms.fetch_all(
        "SELECT DISTINCT lower(t) AS a FROM email_sent_log, "
        "unnest(to_addrs || cc_addrs) AS t "
        "WHERE user_id = %s AND lower(t) = ANY(%s)",
        (user_id, targets),
    )
    known.update(str(r["a"]) for r in (sent or []) if r.get("a"))

    return any(t not in known for t in targets)


def _recipients_for_annotation(payload: dict, user_id: str) -> list[str]:
    """The recipient list a proposal would send to, from the raw payload.

    Compose carries ``to`` directly (the caller's own input). Reply's recipient is
    the original sender, recovered from the signal's ``from_addr`` — but scoped to a
    signal the caller **owns** (``signals.user_id``): a reply is only valid on your
    own-mailbox signals (LUM-650), so a non-owned/shared ``signal_id`` resolves to no
    recipient here → the caller fails closed (``first_contact=True``) rather than
    letting first_contact — newly exposed on the wire (LUM-652) — become an oracle on
    a signal_id the caller does not own. Returns ``[]`` when it cannot be determined."""

    def _as_list(key: str) -> list[str]:
        v = payload.get(key)
        return [str(x) for x in v] if isinstance(v, list) else []

    # LUM-698: cc/bcc are recipients too, so force-Ask must consider them. This
    # applies the EXISTING documented principle ("ANY recipient ... neither
    # received from nor sent to"), not a new rule.
    extra = _as_list("cc") + _as_list("bcc")

    to = _as_list("to")
    if to:
        # compose + forward carry `to` directly.
        return to + extra
    signal_id = payload.get("signal_id")
    if isinstance(signal_id, str) and signal_id:
        ms = config.get_metadata_store()
        row = ms.fetch_one(
            "SELECT from_addr FROM signals WHERE signal_id = %s AND user_id = %s LIMIT 1",
            (signal_id, user_id),
        )
        if row and row.get("from_addr"):
            return [str(row["from_addr"]), *extra]
    # No resolvable PRIMARY recipient: a cc/bcc-only list still fails closed via
    # the caller when empty, but if cc/bcc were supplied they are real recipients.
    return extra


def _annotate_send(payload: dict, user_id: str) -> dict:
    """Create-time risk hook (called by the generic proposal producer BEFORE the
    Ask/Do decision). Returns ONLY the boolean ``first_contact`` — never the
    recipient address (it runs before the execute-time ownership gate, so it must
    not surface pre-ownership data). **Fails closed:** any uncertainty (no
    resolvable recipient, lookup error) returns ``{"first_contact": true}`` so a
    first-contact send is never downgraded to auto-approve on an error path."""
    try:
        recipients = _recipients_for_annotation(payload, user_id)
        out = (
            {"first_contact": True}
            if not recipients
            else {"first_contact": _is_first_contact(user_id, recipients)}
        )
    except Exception:  # noqa: BLE001 - fail closed
        _log.warning("smtp_send: _annotate_send failed; failing closed (force Ask)")
        out = {"first_contact": True}

    # LUM-695: a file count + total size for the approval card. The user must
    # SEE that they are about to send files, and that is only true if the card
    # says so. Filenames are deliberately NOT included — the annotation is
    # computed before the execute-time ownership gate, so it carries only
    # approval-relevant scalars, never content.
    #
    # Deliberately AFTER the fail-closed paths join back up, not inside the try:
    # those paths are the ones already asking the approver to look hardest, so
    # they are the worst place to silently drop the file count.
    out.update(_attachment_summary(payload, user_id))
    return out


def _attachment_summary(payload: dict, user_id: str) -> dict:
    """``{attachment_count, attachment_bytes}`` for the approval card, or ``{}``.

    Never raises: a summary that could not be computed must not turn a valid
    send proposal into a create-time failure, and its absence degrades the card
    to what it showed before LUM-695 rather than to something wrong.
    """
    ids = payload.get("attachment_ids")
    ids = [str(i) for i in ids] if isinstance(ids, list) else []
    # LUM-712: re-attached originals are files the user is about to send too, so
    # the card must count them. Leaving them out would under-report exactly the
    # case where the user is forwarding someone else's document onward.
    fwd = payload.get("forward_part_indexes")
    fwd = [i for i in fwd] if isinstance(fwd, list) else []
    total_count = len(ids) + len(fwd)
    if not total_count:
        return {}
    try:
        from services import email_attachment_staging as staging

        staged_bytes = staging.total_size(user_id, ids) if ids else 0
        out = {"attachment_count": total_count}
        if not fwd:
            # Only staged files — the byte total is exact.
            out["attachment_bytes"] = staged_bytes
        elif ids:
            # Mixed: the original's sizes are known only at execute time, so
            # report the part we can stand behind rather than a wrong total.
            out["attachment_bytes"] = staged_bytes
            out["attachment_bytes_partial"] = True
        return out
    except Exception:  # noqa: BLE001
        _log.warning("smtp_send: attachment summary failed; card shows the count only")
        return {"attachment_count": total_count}


# ---------------------------------------------------------------------------
# Credential resolution + sent-log
# ---------------------------------------------------------------------------


def _resolve_smtp(source_id: str, user_id: str):
    """Load the mailbox's SMTP submission params, refusing when send is not
    enabled for it (the ``smtp`` opt-in is absent)."""
    from services.email_oauth import OAuthRefreshError

    from services import connector_credentials as ccs
    from services import email_credentials

    try:
        return email_credentials.load_smtp_connection(user_id, instance=source_id)
    except ccs.ConnectorNotConfigured as exc:
        raise PermanentActionError(
            "send_not_enabled", "Lumogis is not enabled to send from this mailbox"
        ) from exc
    except OAuthRefreshError as exc:
        # xoauth2 token refresh failed (or the None-token guard fired). Catch this
        # SPECIFICALLY — NOT the broad ccs.CredentialUnavailable it subclasses —
        # so pre-existing app-password CredentialUnavailable failures (Fernet
        # decrypt, malformed payload) keep propagating raw (retry-with-backoff)
        # exactly as before. str(exc) carries status+provider only (no token).
        # The failure stays PERMANENT for every kind, deliberately — though since
        # LUM-762 that is for the ERROR CODE, not the disposal. Every failure of a
        # claimed proposal now dead-letters on the first attempt; the zombie
        # `approved` row this comment used to warn about cannot be created, because
        # `fail_execution` and its `executing → approved` edge are gone and
        # `_CLAIM_BY_ID_SQL` filters `run_after`. Raising Permanent here still
        # matters: it is what gives the composer a precise code to render instead
        # of a generic failure. Only the *code* varies, so a transient blip stops
        # being reported as an auth failure (LUM-757).
        kind = getattr(exc, "kind", "credential")
        transient = kind in ("network", "egress_blocked")
        code = "smtp_send_unavailable" if transient else "smtp_auth_failed"
        raise PermanentActionError(code, str(exc)) from exc
    except ValueError as exc:
        # An "Other" mailbox with no submission host, or a malformed payload — a
        # config condition a retry cannot fix.
        raise PermanentActionError("smtp_misconfigured", str(exc)) from exc


def _finalize_send(
    *,
    result,
    user_id: str,
    source_id: str,
    to_addrs: list[str],
    subject: str,
    in_reply_to: Optional[str],
    action: str,
    cc_addrs: Optional[list[str]] = None,
    bcc_addrs: Optional[list[str]] = None,
    proposal_id: int | None = None,
) -> ActionResult:
    """Record the sent-log for the recipients that were ACCEPTED, then surface a
    partial refusal as a permanent failure (plan §Error handling contract).

    LUM-793: the sent-log row carries ``proposal_id``. Note what that means on the
    PARTIAL path below — a row is written *and* the action then fails permanently,
    so a recovery surface reading the sent log will find evidence of a send for a
    proposal that is `dead`. That is not a contradiction; it is the partial. Any
    consumer treating the presence of a row as "fully sent" is wrong, which is why
    ``services/send_outcome`` has a ``partially_sent`` state.

    On a full success ``result.refused`` is empty → all recipients are logged and
    the action succeeds. On a partial refusal the accepted set (``to_addrs`` minus
    the refused) DID receive the message, so it is logged (audit truth + so those
    addresses count as contacted), and the action fails permanently naming the
    refused set. A TOTAL refusal never reaches here (the adapter raised)."""
    cc_addrs = list(cc_addrs or [])
    bcc_addrs = list(bcc_addrs or [])
    refused = set(result.refused or ())

    # LUM-698: the adapter refuses across the FULL envelope (to+cc+bcc), so the
    # reconciliation must span all three. Reconciling only `to_addrs` would
    # silently drop a refused cc/bcc from both the accepted-set logging and the
    # partial-refusal error — the send would look fully successful.
    def _accepted(addrs: list[str]) -> list[str]:
        return [a for a in addrs if _normalize_addr(a) not in refused]

    accepted_to = _accepted(to_addrs)
    accepted_cc = _accepted(cc_addrs)
    accepted_bcc = _accepted(bcc_addrs)
    if accepted_to or accepted_cc or accepted_bcc:
        _record_sent_log(
            user_id=user_id,
            source_id=source_id,
            to_addrs=accepted_to,
            subject=subject,
            message_id=result.message_id,
            in_reply_to=in_reply_to,
            action=action,
            cc_addrs=accepted_cc,
            bcc_addrs=accepted_bcc,
            proposal_id=proposal_id,
        )
    if refused:
        # LUM-793: the PREFIX is imported, not spelled, because `dead_letter`
        # persists `str(exc)` and not the `recipients_refused` code — so this
        # prose is the only record on the proposal row that a partial happened,
        # and `send_outcome` parses it back out. Editing the wording here without
        # the constant would silently downgrade every future partial to "sent".
        raise PermanentActionError(
            "recipients_refused",
            f"{RECIPIENTS_REFUSED_PREFIX}{', '.join(sorted(refused))}",
        )
    return ActionResult(success=True, output=f"smtp:{action}")


def _record_sent_log(
    *,
    user_id: str,
    source_id: str,
    to_addrs: list[str],
    subject: str,
    message_id: str,
    in_reply_to: Optional[str],
    action: str,
    cc_addrs: Optional[list[str]] = None,
    bcc_addrs: Optional[list[str]] = None,
    proposal_id: int | None = None,
) -> None:
    """Record the send. to/cc/bcc are kept DISTINCT (migration 064): flattening
    them would both misreport what was sent and put BCC addresses into the set
    `_is_first_contact` queries."""
    ms = config.get_metadata_store()

    def _norm(addrs) -> list[str]:
        return [_normalize_addr(a) for a in (addrs or []) if _normalize_addr(a)]

    ms.execute(
        "INSERT INTO email_sent_log "
        "(user_id, source_id, to_addrs, subject, message_id, in_reply_to, action, "
        "cc_addrs, bcc_addrs, proposal_id) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            user_id,
            source_id,
            _norm(to_addrs),
            subject,
            message_id or "",
            in_reply_to,
            action,
            _norm(cc_addrs),
            _norm(bcc_addrs),
            proposal_id,
        ),
    )


# ---------------------------------------------------------------------------
# Reply original-header re-fetch (bounded, read-only, rides imap_egress_scope)
# ---------------------------------------------------------------------------


def _clean_header_token(value: Optional[str]) -> str:
    """Strip CR/LF and other control chars from a single header token (a
    ``Message-ID``) so a crafted original message cannot inject a header via the
    reply's ``In-Reply-To`` / ``References`` tail. Returns ``""`` for falsy input."""
    if not value:
        return ""
    return "".join(ch for ch in str(value) if ord(ch) >= 32).strip()


def _build_references(
    parent_references: Optional[str],
    parent_in_reply_to: Optional[str],
    parent_message_id: str,
) -> str:
    """Construct the reply's ``References`` per RFC 5322 §3.6.4.

    ``base`` = the parent's ``References`` (if any), else its ``In-Reply-To`` (its
    single-id chain), else empty; then append the parent's own ``Message-ID`` last.
    The result is ALWAYS whitespace-normalized (``" ".join(...split())``): a real
    thread's ``References`` is folded across lines and ``email.message_from_bytes``
    preserves the folding ``\\r\\n `` in the value, so normalization is required for
    correctness on ordinary long threads (and it flattens any injected CR/LF to
    inert spaces as a bonus). Returns ``""`` when there is no parent ``Message-ID``
    (the caller then omits ``References``)."""
    if not parent_message_id:
        return ""
    base = parent_references or parent_in_reply_to or ""
    chain = base + " " + parent_message_id
    # Strip ALL control chars (keeping space/tab as separators), then collapse
    # whitespace. `split()` alone only handles whitespace-class controls (CR/LF/
    # FF/VT); a non-whitespace control (e.g. NUL) would otherwise survive here and
    # trip build_message's CR/LF backstop → the reply would dead-letter instead of
    # degrading. Stripping them keeps the "hostile original never blocks the reply"
    # contract (the result is inert tokens, never a header).
    chain = "".join(ch for ch in chain if ord(ch) >= 32 or ch in "\t ")
    return " ".join(chain.split())


def _truncate_references(refs: str) -> str:
    """Cap a pathologically long ``References`` (tuning, not correctness). Keep the
    thread **root** (first id) + the most-recent ids, dropping from the middle
    (jwz-style): first the id-count cap, then, if still over the byte cap, drop the
    oldest non-root ids until under it — always preserving root + the direct parent
    (last id). Input is already whitespace-normalized by ``_build_references``."""
    ids = refs.split()
    if len(ids) <= _MAX_REFERENCE_IDS and len(refs) <= _MAX_REFERENCE_BYTES:
        return refs
    if len(ids) > _MAX_REFERENCE_IDS:
        ids = [ids[0], *ids[-(_MAX_REFERENCE_IDS - 1) :]]
    out = " ".join(ids)
    # Byte cap: drop the oldest non-root id until under the limit (never drop the
    # root at [0] or the direct parent at [-1]).
    while len(out) > _MAX_REFERENCE_BYTES and len(ids) > 2:
        ids.pop(1)
        out = " ".join(ids)
    return out


def fetch_original_headers(source, uidvalidity: int, uid: int) -> Optional[dict]:
    """Re-fetch the original's named headers, or ``None`` on any miss.

    The single home of the **UIDVALIDITY compare-and-degrade** rule, shared by the
    send path (:func:`_refetch_original`) and by LUM-705's reply-all route. Two
    copies of "compare the stored uidvalidity, degrade on mismatch" is how one of
    them silently stops degrading — and on the reply-all route that failure puts a
    *stranger's* thread participants into the user's Cc field, because a
    UIDVALIDITY reset means the stored uid now identifies a different message.

    ``None`` also covers the ordinary miss: LUM-645/646 may have moved the message
    to a ``Lumogis/<label>`` folder or ``\\Trash``, so the uid is no longer in
    INBOX. Never raises — a re-fetch failure must not hard-fail the caller.
    """
    try:
        from adapters.email_imap_source import EmailImapSource

        adapter = EmailImapSource(source)
        cur_uidvalidity, headers = adapter.fetch_thread_headers(int(uid))
        if cur_uidvalidity != uidvalidity or not headers:
            return None
        return headers
    except Exception as exc:  # noqa: BLE001 - degrade gracefully, never hard-fail
        _log.info("email: original re-fetch miss (%s); degrading", type(exc).__name__)
        return None


def _refetch_original(
    source, uidvalidity: int, uid: int
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Re-fetch the original message's ``(subject, message_id, references)`` over IMAP.

    ``message_id`` is sanitized (``_clean_header_token``); ``references`` is the
    constructed + normalized + truncated reply chain, or ``None``. Returns
    ``(None, None, None)`` when the original is gone — LUM-645/646 may have moved
    it to a ``Lumogis/<label>`` folder or ``\\Trash``, so the uid is no longer in
    INBOX. The caller then degrades gracefully (subject from the signal, no
    threading headers). Never raises — a re-fetch failure must not hard-fail the
    reply."""
    headers = fetch_original_headers(source, uidvalidity, uid)
    if headers is None:
        return (None, None, None)
    try:
        subject = headers.get("Subject") or None
        message_id = _clean_header_token(headers.get("Message-ID")) or None
        references = None
        if message_id:
            built = _build_references(
                headers.get("References"), headers.get("In-Reply-To"), message_id
            )
            references = _truncate_references(built) or None
        return (subject, message_id, references)
    except Exception as exc:  # noqa: BLE001 - degrade gracefully, never hard-fail
        _log.info("smtp_send: reply re-fetch miss (%s); degrading", type(exc).__name__)
        return (None, None, None)


def _resolve_attachment_parts(attachment_ids: list[str], *, user_id: str) -> list:
    """Turn payload REFERENCES into adapter ``AttachmentPart``s at execute time.

    This is where staged bytes finally enter the process — after approval, in
    the executor, and nowhere earlier. A missing, consumed, foreign or swept id
    raises :class:`PermanentActionError`, **not** an HTTP status: this runs via
    the executor, where there is no HTTP surface at all, so a 422 here would be
    a contract that could never be honoured. Dead-lettering gives the user the
    same retry affordance as any other send failure.

    All-or-nothing. A send that quietly dropped one attachment would be worse
    than one that refused, because the user already approved a message they
    believed carried it.
    """
    ids = [str(a) for a in (attachment_ids or [])]
    if not ids:
        return []

    from adapters.email_smtp_sender import AttachmentPart

    from services import email_attachment_staging as staging

    try:
        resolved = staging.resolve_all(user_id, ids)
        return [
            AttachmentPart(
                filename=r.filename,
                mime_type=r.mime_type,
                data=r.path.read_bytes(),
            )
            for r in resolved
        ]
    except staging.StagedAttachmentUnavailable as exc:
        raise PermanentActionError(
            "attachment_unavailable", "an attachment is no longer available"
        ) from exc
    except OSError as exc:
        # The row survived but the blob did not (a sweep mid-send, or a disk
        # fault). Same outcome: refuse rather than send a message missing a file.
        raise PermanentActionError(
            "attachment_unavailable", "an attachment could not be read"
        ) from exc


def _resolve_original_attachment_parts(
    part_indexes: list[int],
    *,
    source,
    uid: int,
    uidvalidity: int,
    signal_id: str,
    user_id: str,
) -> list:
    """Re-fetch an ORIGINAL message's own attachments so a forward carries them.

    The payload holds part INDEXES, not bytes — the same discipline staged files
    follow, and it costs nothing here because the signal already identifies the
    message. Resolution happens at execute time, in one IMAP session for all of
    them (``fetch_attachment_parts``), not one per file.

    A part that cannot be re-fetched is a :class:`PermanentActionError`, matching
    the staged-file posture: a forward silently missing the document it was sent
    to pass on is worse than one that refuses and can be retried.
    """
    wanted = [int(i) for i in (part_indexes or [])]
    if not wanted:
        return []

    from adapters.email_imap_source import AttachmentFetchError
    from adapters.email_imap_source import EmailImapSource
    from adapters.email_smtp_sender import AttachmentPart

    try:
        parts = EmailImapSource(source).fetch_attachment_parts(
            uid=uid,
            part_indexes=wanted,
            message_id=_original_message_id(signal_id, user_id),
            folder_hint=_original_folder_hint(signal_id, user_id),
            uidvalidity=uidvalidity,
        )
    except AttachmentFetchError as exc:
        raise PermanentActionError(
            "attachment_unavailable",
            f"an attachment on the forwarded message could not be read ({exc.code})",
        ) from exc

    return [AttachmentPart(filename=p.filename, mime_type=p.mime_type, data=p.data) for p in parts]


def _original_message_id(signal_id: str, user_id: str) -> str | None:
    """The stored ``Message-ID``, so a moved original is still locatable."""
    from services.email_message_locate import message_id_for

    try:
        return message_id_for(signal_id, user_id)
    except Exception:  # noqa: BLE001 - a missing hint only costs a wider search
        return None


def _original_folder_hint(signal_id: str, user_id: str) -> str | None:
    """Where organize moved the original, when it did."""
    from services.email_message_locate import organize_folder_for

    try:
        return organize_folder_for(signal_id, user_id)
    except Exception:  # noqa: BLE001
        return None


def _provider_total_bytes(conn) -> int | None:
    """The sending mailbox's provider attachment ceiling, in bytes, or ``None``.

    Takes the connection rather than a host string so a connection shape without
    a host (an "Other" mailbox, or a narrower object) reads as *unknown provider*
    instead of raising. ``None`` leaves the global cap in force — inventing a
    limit for a server whose policy we do not know would refuse valid sends.

    Never raises: a registry lookup failing must not turn a valid send into a
    dead-lettered one; it just costs the more conservative global limit.
    """
    try:
        from services.email_providers import max_attachment_mb_for_host

        mb = max_attachment_mb_for_host(getattr(conn, "host", "") or "")
        return mb * 1024 * 1024 if mb else None
    except Exception:  # noqa: BLE001
        return None


def _consume_attachments(attachment_ids: list[str], *, user_id: str) -> None:
    """Discard staged files after a successful send — the fast path.

    The proposal-state sweep is the backstop; this is what returns the disk
    promptly. Best-effort: a send that succeeded must not be reported as failed
    because cleanup did not.
    """
    ids = [str(a) for a in (attachment_ids or [])]
    if not ids:
        return
    try:
        from services import email_attachment_staging as staging

        staging.discard(user_id, ids)
    except Exception:  # noqa: BLE001 - the message is already sent
        _log.warning("smtp_send: staged attachment discard failed after a successful send")


def _re_subject(subject: Optional[str]) -> str:
    s = (subject or "").strip()
    if not s:
        return "Re:"
    return s if s.lower().startswith("re:") else f"Re: {s}"


def _fwd_subject(subject: Optional[str]) -> str:
    """``Fwd: <subject>``, idempotent on an existing ``Fwd:``/``FW:`` prefix."""
    s = (subject or "").strip()
    if not s:
        return "Fwd:"
    low = s.lower()
    if low.startswith("fwd:") or low.startswith("fw:"):
        return s
    return f"Fwd: {s}"


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def _email_reply(input: dict, *, user_id: str, proposal_id: int | None = None) -> ActionResult:
    parsed = EmailReplyInput.model_validate(input)  # extra=forbid

    url = _signal_url(parsed.signal_id)
    if url is None:
        raise PermanentActionError("not_found", "signal not found")
    recovered = _parse_imap_url(url)
    if recovered is None:
        raise PermanentActionError("not_imap_signal", "signal is not an IMAP message")
    source_id, uidvalidity, uid = recovered

    # Ownership gate BEFORE any connect/credential load. A shared (non-owned)
    # signal is owned by another member's source → denied here (reply is only
    # available on your own-mailbox signals; compose-new for shared mail).
    source = _load_owned_source(source_id, user_id)

    sig = _signal_row(parsed.signal_id) or {}
    reply_to = str(sig.get("from_addr") or "").strip()
    if not reply_to:
        raise PermanentActionError("no_reply_to", "original sender address unknown")

    # Re-fetch original Subject + Message-ID + References (bounded, read-only).
    # Degrade gracefully if the original was moved/trashed by LUM-645/646, or if
    # it carries no Message-ID (can't thread) — reply omits the threading headers.
    orig_subject, orig_message_id, orig_references = _refetch_original(source, uidvalidity, uid)
    if orig_subject is None:
        orig_subject = str(sig.get("title") or "")
    subject = parsed.subject or _re_subject(orig_subject)

    conn = _resolve_smtp(source_id, user_id)
    from adapters.email_smtp_sender import SmtpSender

    sender = SmtpSender()
    # LUM-695: staged bytes enter here and nowhere earlier — the payload carried
    # only ids. Raises PermanentActionError if any is gone (no HTTP surface here).
    attachments = _resolve_attachment_parts(parsed.attachment_ids, user_id=user_id)
    msg = sender.build_message(
        from_addr=conn.username,
        to=[reply_to],
        subject=subject,
        body=parsed.body,
        in_reply_to=orig_message_id,  # None on a re-fetch miss (graceful)
        references=orig_references,  # RFC 5322 §3.6.4 chain (LUM-651); None on miss
        cc=parsed.cc,
        bcc=parsed.bcc,
        attachments=attachments,
        max_total_bytes=_provider_total_bytes(conn),
    )
    result = sender.send(
        msg,
        host=conn.host,
        port=conn.port,
        username=conn.username,
        app_password=conn.password,
        user_id=user_id,
        auth_type=conn.auth_type,
        access_token=conn.oauth_access_token,
    )
    out = _finalize_send(
        result=result,
        user_id=user_id,
        source_id=source_id,
        to_addrs=[reply_to],
        subject=subject,
        in_reply_to=orig_message_id,
        action="reply",
        cc_addrs=parsed.cc,
        bcc_addrs=parsed.bcc,
        proposal_id=proposal_id,
    )
    _consume_attachments(parsed.attachment_ids, user_id=user_id)
    return out


def _email_compose(input: dict, *, user_id: str, proposal_id: int | None = None) -> ActionResult:
    parsed = EmailComposeInput.model_validate(input)  # extra=forbid

    # Ownership gate BEFORE any connect/credential load.
    _load_owned_source(parsed.source_id, user_id)

    conn = _resolve_smtp(parsed.source_id, user_id)
    from adapters.email_smtp_sender import SmtpSender

    sender = SmtpSender()
    attachments = _resolve_attachment_parts(parsed.attachment_ids, user_id=user_id)
    msg = sender.build_message(
        from_addr=conn.username,
        to=parsed.to,
        subject=parsed.subject,
        body=parsed.body,
        cc=parsed.cc,
        bcc=parsed.bcc,
        attachments=attachments,
        max_total_bytes=_provider_total_bytes(conn),
    )
    result = sender.send(
        msg,
        host=conn.host,
        port=conn.port,
        username=conn.username,
        app_password=conn.password,
        user_id=user_id,
        auth_type=conn.auth_type,
        access_token=conn.oauth_access_token,
    )
    out = _finalize_send(
        result=result,
        user_id=user_id,
        source_id=parsed.source_id,
        to_addrs=list(parsed.to),
        subject=parsed.subject,
        in_reply_to=None,
        action="compose",
        cc_addrs=parsed.cc,
        bcc_addrs=parsed.bcc,
        proposal_id=proposal_id,
    )
    _consume_attachments(parsed.attachment_ids, user_id=user_id)
    return out


def _email_forward(input: dict, *, user_id: str, proposal_id: int | None = None) -> ActionResult:
    """Forward a surfaced message to explicit recipients (LUM-698).

    Recovers the mailbox + original subject from the signal (like reply), but
    sends to client-supplied recipients (like compose). **No threading headers:**
    a forward starts a new thread, so unlike ``_email_reply`` this deliberately
    omits ``In-Reply-To``/``References``.

    The forwarded *content* is carried in ``body`` — the composer prefixes the
    quoted original (``services/email_quote.build_quote(style="forward")``), so
    the same text the user reviewed is exactly what is sent. Attachments on the
    original are NOT forwarded (LUM-695 owns attachments)."""
    parsed = EmailForwardInput.model_validate(input)  # extra=forbid

    url = _signal_url(parsed.signal_id)
    if url is None:
        raise PermanentActionError("not_found", "signal not found")
    recovered = _parse_imap_url(url)
    if recovered is None:
        raise PermanentActionError("not_imap_signal", "signal is not an IMAP message")
    source_id, uidvalidity, uid = recovered

    # Ownership gate BEFORE any connect/credential load (same as reply).
    source = _load_owned_source(source_id, user_id)

    orig_subject, _orig_message_id, _orig_references = _refetch_original(source, uidvalidity, uid)
    if orig_subject is None:
        sig = _signal_row(parsed.signal_id) or {}
        orig_subject = str(sig.get("title") or "")
    subject = parsed.subject or _fwd_subject(orig_subject)

    conn = _resolve_smtp(source_id, user_id)
    from adapters.email_smtp_sender import SmtpSender

    sender = SmtpSender()
    # Staged files the user picked, plus the original's own attachments they chose
    # to carry. Both are references in the payload; both resolve to bytes here.
    attachments = _resolve_attachment_parts(parsed.attachment_ids, user_id=user_id)
    attachments += _resolve_original_attachment_parts(
        parsed.forward_part_indexes,
        source=source,
        uid=uid,
        uidvalidity=uidvalidity,
        signal_id=parsed.signal_id,
        user_id=user_id,
    )
    msg = sender.build_message(
        from_addr=conn.username,  # From is ALWAYS us — never the original sender
        to=parsed.to,
        subject=subject,
        body=parsed.body,
        cc=parsed.cc,
        bcc=parsed.bcc,
        attachments=attachments,
        max_total_bytes=_provider_total_bytes(conn),
    )
    result = sender.send(
        msg,
        host=conn.host,
        port=conn.port,
        username=conn.username,
        app_password=conn.password,
        user_id=user_id,
        auth_type=conn.auth_type,
        access_token=conn.oauth_access_token,
    )
    out = _finalize_send(
        result=result,
        user_id=user_id,
        source_id=source_id,
        to_addrs=list(parsed.to),
        subject=subject,
        in_reply_to=None,  # a forward is not a reply
        action="forward",
        cc_addrs=parsed.cc,
        bcc_addrs=parsed.bcc,
        proposal_id=proposal_id,
    )
    _consume_attachments(parsed.attachment_ids, user_id=user_id)
    return out


def _email_save_draft(input: dict, *, user_id: str) -> ActionResult:
    """Build the same MIME as Send (reusing ``build_message`` + LUM-651 threading)
    and APPEND it to the mailbox's SPECIAL-USE Drafts folder. From is the IMAP
    account username, so this needs no SMTP opt-in — a draft is an IMAP-only op."""
    parsed = EmailSaveDraftInput.model_validate(input)  # extra=forbid + shape rule
    from adapters.email_imap_source import EmailImapSource
    from adapters.email_smtp_sender import SmtpSender

    if parsed.signal_id is not None:
        # Signal-based: reply-draft (no `to`) or FORWARD-draft (non-empty `to`).
        # The forward branch mirrors `_email_forward` exactly — Fwd: subject, no
        # threading headers — so a saved forward and a sent forward are the same
        # message (LUM-698).
        is_forward_draft = bool(parsed.to)
        url = _signal_url(parsed.signal_id)
        if url is None:
            raise PermanentActionError("not_found", "signal not found")
        recovered = _parse_imap_url(url)
        if recovered is None:
            raise PermanentActionError("not_imap_signal", "signal is not an IMAP message")
        source_id, uidvalidity, uid = recovered
        source = _load_owned_source(source_id, user_id)  # ownership gate

        sig = _signal_row(parsed.signal_id) or {}
        reply_to = str(sig.get("from_addr") or "").strip()
        if not reply_to and not is_forward_draft:
            # A forward needs no original sender (you choose the recipients).
            raise PermanentActionError("no_reply_to", "original sender address unknown")

        orig_subject, orig_message_id, orig_references = _refetch_original(source, uidvalidity, uid)
        if orig_subject is None:
            orig_subject = str(sig.get("title") or "")
        if is_forward_draft:
            subject = parsed.subject or _fwd_subject(orig_subject)
            to = list(parsed.to or [])
            in_reply_to = None  # a forward starts a new thread
            references = None
        else:
            subject = parsed.subject or _re_subject(orig_subject)
            to = [reply_to]
            in_reply_to = orig_message_id
            references = orig_references
    else:
        # Compose-draft: direct, no threading.
        source_id = parsed.source_id
        source = _load_owned_source(parsed.source_id, user_id)  # ownership gate
        subject = parsed.subject or ""
        to = list(parsed.to or [])
        in_reply_to = None
        references = None

    adapter = EmailImapSource(source)
    # From = the mailbox's own IMAP account (no SMTP credential needed).
    from_addr = adapter.conn.username
    sender = SmtpSender()
    # LUM-695: the draft carries its attachments too, through the SAME builder —
    # `as_bytes()` below serialises the whole multipart/mixed, so a draft the user
    # resumes in their own client still has the files.
    attachments = _resolve_attachment_parts(parsed.attachment_ids, user_id=user_id)
    if parsed.forward_part_indexes:
        # LUM-712: a saved forward and a sent forward must be the same message,
        # so the draft carries the original's attachments too. Only reachable on
        # the signal-based shapes — `source` and `uid` do not exist on a compose
        # draft, and a compose draft has no original to forward from.
        if parsed.signal_id is None:
            raise PermanentActionError(
                "not_a_forward",
                "forward_part_indexes requires a signal-based draft",
            )
        attachments += _resolve_original_attachment_parts(
            parsed.forward_part_indexes,
            source=source,
            uid=uid,
            uidvalidity=uidvalidity,
            signal_id=parsed.signal_id,
            user_id=user_id,
        )
    msg = sender.build_message(
        from_addr=from_addr,
        to=to,
        subject=subject,
        body=parsed.body,
        in_reply_to=in_reply_to,  # None on a compose/forward draft or re-fetch miss
        references=references,
        # NOTE: this message is serialised with `as_bytes()` for the IMAP APPEND,
        # which does NOT strip Bcc. That is intended — the draft lands in the
        # user's OWN mailbox and real clients keep Bcc so the send can be resumed.
        cc=parsed.cc,
        bcc=parsed.bcc,
        attachments=attachments,
        # A draft is an IMAP-only op with no SMTP connection, so the provider is
        # anchored on the IMAP host instead — same mailbox, same ceiling.
        max_total_bytes=_provider_total_bytes(adapter.conn),
    )
    appended = adapter.append_to_drafts(msg.as_bytes(), str(msg["Message-ID"] or ""))
    _consume_attachments(parsed.attachment_ids, user_id=user_id)
    # LUM-681: the reverse locates + deletes the draft by (folder, Message-ID), so the
    # action is reversible — the executor mints a reverse token from this reverse_input.
    reverse_input = {
        "source_id": source_id,
        "folder": appended.get("folder"),
        "message_id": appended.get("message_id"),
    }
    return ActionResult(success=True, output="imap:save_draft", reverse_input=reverse_input)


def _email_save_draft_reverse(input: dict, *, user_id: str) -> ActionResult:
    """Undo a prior ``email_save_draft`` (LUM-681): re-check ownership, then delete the
    appended draft from the Drafts folder by its Message-ID. Only ever reached via
    ``attempt_reverse`` with a token-authenticated audit row (never the producer)."""
    source_id = input.get("source_id")
    folder = input.get("folder")
    message_id = input.get("message_id")
    if not source_id or not folder or not message_id:
        raise PermanentActionError(
            "reverse_bad_input", "reverse_input missing source_id/folder/message_id"
        )
    source = _load_owned_source(str(source_id), user_id)  # re-check ownership on reverse
    from adapters.email_imap_source import EmailImapSource

    EmailImapSource(source).delete_from_drafts(str(folder), str(message_id))
    return ActionResult(success=True, output="imap:reverse:save_draft")


_REGISTERED = False


def _annotate_unsubscribe(payload: dict, user_id: str) -> dict:
    """Create-time risk hook for ``email_unsubscribe``. **Always** force-Ask.

    The ``first_contact`` key is not incidental — ``executor._resolve_force_ask``
    reads ``ann.get("first_contact") is True`` and **nothing else**, so any other
    key is inert. It is also semantically accurate: an unsubscribe ``mailto:`` is
    by construction an address the user has never emailed.

    This matters because ``permissions.py`` resolves Ask/Do mode **per connector**,
    not per action type. Without this hook, a user who set the ``smtp`` connector
    to DO — the ordinary reason being to let Lumogis send on their behalf — would
    get silent, unreviewed bulk unsubscribes: real mail leaving their account, to
    addresses they have never contacted, with no approval step.

    Force-Ask is deliberately *not* the same as hard-limiting: ``executor.execute``
    computes ``allowed = bool(approved) and not is_hard_limited(action_type)``, so a
    hard-limited action is refused even on the approved path, whereas this stays
    approvable. Always attended, never impossible.
    """
    return {"first_contact": True, "bulk_unsubscribe": True}


def run_unsubscribe_batch(
    parsed: EmailUnsubscribeInput,
    *,
    user_id: str,
    pace_seconds: float = 0.0,
) -> tuple[list[str], list[str]]:
    """Send one unsubscribe message **per target**, sequentially. ``(sent, skipped)``.

    Never one message with many recipients: the unsubscribe addresses of every
    other sender would appear in ``To:``, leaking the user's whole subscription
    list to each recipient.

    Shared by the action handler's inline fallback and the batch-queue worker
    (LUM-710), so moving execution off-thread did not fork the send topology, the
    From-is-always-us rule or the per-target stamping into a second copy.

    Assumes the ownership gate has already run — both callers gate before this,
    the worker re-gating at run time because approval and execution are no longer
    the same instant.

    ``pace_seconds`` sleeps **between** sends, never before the first or after the
    last, and never for a skipped target (nothing was submitted, so there is no
    burst to pace). Zero on the inline path, where the caller is a request thread.
    """
    from services import email_unsubscribe as unsub

    conn = _resolve_smtp(parsed.source_id, user_id)
    from adapters.email_smtp_sender import SmtpSender

    sender = SmtpSender()
    sent: list[str] = []
    skipped: list[str] = []
    for target in parsed.targets:
        # Idempotent retry: a batch that died at target 6 of 10 re-runs and sends
        # only the outstanding four. Each target is stamped as its OWN send
        # succeeds, never at the end of the batch. This is what makes the
        # batch-queue's retry-with-backoff safe rather than a duplicate-send loop.
        if unsub.already_unsubscribed(user_id, parsed.source_id, target.from_addr):
            skipped.append(target.from_addr)
            continue
        if sent and pace_seconds > 0:
            time.sleep(pace_seconds)
        msg = sender.build_message(
            from_addr=conn.username,  # From is ALWAYS us — never the sender
            to=[target.mailto_addr],
            subject=target.subject,
            body="unsubscribe",
        )
        sender.send(
            msg,
            host=conn.host,
            port=conn.port,
            username=conn.username,
            app_password=conn.password,
            user_id=user_id,
            auth_type=conn.auth_type,
            access_token=conn.oauth_access_token,
        )
        unsub.mark_unsubscribed(user_id, parsed.source_id, target.from_addr)
        sent.append(target.from_addr)

    return sent, skipped


def _batch_queue_enabled() -> bool:
    """Mirrors ``main.py``'s gate on the worker.

    Read per call, not at import: the action handler is registered at import
    time, long before a test or an operator flips this.
    """
    return os.environ.get("BATCH_QUEUE_ENABLED", "true").lower() not in ("false", "0", "no")


def _email_unsubscribe(input: dict, *, user_id: str) -> ActionResult:
    """Hand the approved batch to the batch queue and return (LUM-710).

    Execution moved off the request thread because the on-thread cost was
    proportional to the batch size: at ~1-2 s per SMTP submission the old
    ten-target cap was the only thing bounding the execute request, and raising
    the cap meant raising the request time with it.

    **When the queue is disabled, this sends inline instead.** ``main.py`` only
    starts the worker when ``BATCH_QUEUE_ENABLED`` is on, so enqueueing
    unconditionally would leave the job pending forever while the audit row
    recorded a successful unsubscribe — the user would believe mail had gone that
    never will. A slow request is the better failure.
    """
    parsed = EmailUnsubscribeInput.model_validate(input)  # extra=forbid

    # Ownership gate BEFORE any connect/credential load — and before enqueue, so
    # a foreign mailbox never reaches the queue at all.
    _load_owned_source(parsed.source_id, user_id)

    if _batch_queue_enabled():
        from services import batch_queue

        job_id = batch_queue.enqueue(
            user_id=user_id,
            kind=UNSUBSCRIBE_BATCH_KIND,
            payload=parsed,
        )
        return ActionResult(
            success=True,
            output=f"queued {len(parsed.targets)} sender(s) for unsubscribe (job {job_id})",
            error="",
        )

    sent, skipped = run_unsubscribe_batch(parsed, user_id=user_id)
    parts = [f"unsubscribed from {len(sent)} sender(s)"]
    if skipped:
        parts.append(f"{len(skipped)} already done")
    return ActionResult(
        success=True,
        output="; ".join(parts),
        error="",
    )


def register_all() -> None:
    """Register ``email_reply`` + ``email_compose`` + ``email_forward``. Idempotent."""
    global _REGISTERED
    if _REGISTERED:
        return
    register_action(
        ActionSpec(
            name="email_reply",
            connector=_CONNECTOR,
            action_type=_ACTION_TYPE,
            is_write=True,
            is_reversible=False,  # you cannot unsend
            handler=_email_reply,
            input_model=EmailReplyInput,
            annotate=_annotate_send,
        )
    )
    register_action(
        ActionSpec(
            name="email_compose",
            connector=_CONNECTOR,
            action_type=_ACTION_TYPE,
            is_write=True,
            is_reversible=False,
            handler=_email_compose,
            input_model=EmailComposeInput,
            annotate=_annotate_send,
        )
    )
    # Save-to-Drafts (LUM-652): an IMAP APPEND, so connector="imap" (its Ask/Do
    # mode is the imap connector's, shared with writeback — distinct from the smtp
    # mode governing send). No `annotate` (a draft in your own mailbox is not a
    # first-contact send). **Reversible (LUM-681):** the reverse locates + deletes the
    # appended draft by (folder, Message-ID) — a saved draft can be un-saved from the
    # Approvals audit trail.
    register_action(
        ActionSpec(
            name="email_forward",
            connector=_CONNECTOR,
            action_type=_ACTION_TYPE,
            is_write=True,
            is_reversible=False,  # you cannot unsend
            handler=_email_forward,
            input_model=EmailForwardInput,
            annotate=_annotate_send,
        )
    )
    register_action(
        ActionSpec(
            name="email_save_draft",
            connector="imap",
            action_type="email_save_draft",
            is_write=True,
            is_reversible=True,
            handler=_email_save_draft,
            input_model=EmailSaveDraftInput,
            reverse_action_name="email_save_draft_reverse",
        )
    )
    # The reverse action — internal-only (input_model=None so the producer can never
    # reach it), never chain-reversible (LUM-681).
    register_action(
        ActionSpec(
            name="email_save_draft_reverse",
            connector="imap",
            action_type="email_save_draft",
            is_write=True,
            is_reversible=False,
            handler=_email_save_draft_reverse,
            input_model=None,
        )
    )
    # LUM-697: unsubscribe is a send, so connector="smtp". Its OWN action_type
    # (not `email_send`, not the hard-limited `mass_communication`) — unsubscribing
    # is a de-escalation, and permanently forcing Ask via hard-limiting would
    # defeat the bulk case entirely. The batch cap makes "mass" structurally
    # impossible instead, and `annotate` keeps every batch attended.
    register_action(
        ActionSpec(
            name="email_unsubscribe",
            connector=_CONNECTOR,
            action_type="email_unsubscribe",
            is_write=True,
            is_reversible=False,  # you cannot un-send
            handler=_email_unsubscribe,
            input_model=EmailUnsubscribeInput,
            annotate=_annotate_unsubscribe,
        )
    )
    _REGISTERED = True
    _log.info(
        "smtp_send: registered email_reply + email_compose + email_forward "
        "+ email_unsubscribe + email_save_draft (+reverse)"
    )


# Register at import so a startup `import actions.handlers.smtp_send` wires the
# actions into the registry the executor reads (mirror imap_writeback).
register_all()
