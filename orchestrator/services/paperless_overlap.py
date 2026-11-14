# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-728 — does paperless watch **this** mailbox?

LUM-729's `external_consumer_guard` answers the coarse question — *does this user
run paperless at all?* — and says in its own docstring that the refinement is
this module:

    It does not ask whether paperless watches *this* mailbox — reading
    paperless's mail rules is LUM-728, which is deferred.

So the boundary is exact. That guard is the cheap precondition; this is the
expensive answer, and it only runs once the cheap one says yes.

**What this buys, stated honestly.** LUM-729 took the false negatives — the
documents lost silently, at about one in three. What is left to win here is
*fewer spurious warnings*. Usability, not data loss. The ADR prices it that way
deliberately, and the implementation should not pretend otherwise.

**Propose, never auto-set** (ADR D1). Detection writes no policy anywhere: it
produces a finding that a consent gate renders. `action` tells you how bad an
overlap is *if the match is right* and says nothing about *whether* it is right,
and the match is least reliable in exactly the configuration where acting on it
would do the most damage.

**Two stages, and the cheap one is genuinely cheap.** Host and port are plaintext
in `sources.url` (`routes/signals.py:365`), so the host comparison costs no
secrets. Only the mailboxes that already match on host get a credential decrypt
to read the username. That decrypt is what separates `high` from `low`, and
without it the `high` tier could never be reached.

**No HTTP lives here.** Services in this codebase do not perform backend I/O; the
bounded read is `adapters.paperless_source.PaperlessMailConfigReader`. That is
not ceremony — the bounded-client requirement is an adapter concern, and inside
matching logic it would be easy to "simplify" away.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable
from typing import Literal
from typing import Optional

import config
from services import connector_credentials as ccs
from services import email_credentials
from services import external_consumer_guard as ecg
from services import paperless_credentials

_log = logging.getLogger(__name__)


class OutboundResolveDeadline(Exception):
    """The bounded DNS resolve for the overlap probe ran out of budget (LUM-741).

    **Deliberately NOT an** :class:`OSError`. ``validate_outbound_connector_base_url``
    treats ``OSError`` from the resolver as "this host has no A/AAAA records" and
    **returns successfully**, falling back to scheme + host-shape checks only::

        try:
            ips = resolver(idna_host)
        except OSError:
            return          # <-- fail OPEN

    :class:`TimeoutError` — the obvious choice — **is** a subclass of ``OSError``
    (and ``socket.timeout`` is an alias of it since 3.10), so raising that would be
    swallowed there and the probe would go on to issue HTTP against a host whose IP
    policy was never evaluated. That turns a slow resolver into an SSRF-validation
    bypass, which LUM-741 explicitly forbids.

    Raising a non-``OSError`` instead propagates out of the validator, out of
    ``load_connection``, and into :func:`overlap_for_source`'s fail-open handler:
    the feature reports "no overlap" (its documented contract) without ever
    reaching the network on an unvalidated host.
    """


def _bounded_resolver(deadline: float):
    """A ``resolve_host`` callable bounded by ``deadline`` (a ``time.monotonic()`` stamp).

    ``socket.getaddrinfo`` takes no timeout and ``socket.setdefaulttimeout`` does
    not apply to it, so bounding it means running it off-thread and abandoning the
    *result* when the budget expires. The worker thread is left to finish on its
    own — there is no portable way to cancel a blocking ``getaddrinfo`` — but it
    holds nothing the caller needs and the pool is never waited on.
    """
    from concurrent import futures

    def _resolve(hostname: str) -> list[str]:
        from services.outbound_http_url import _resolved_ips

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise OutboundResolveDeadline(f"no budget left to resolve {hostname!r}")
        pool = futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="overlap-dns")
        try:
            fut = pool.submit(_resolved_ips, hostname)
            try:
                return fut.result(timeout=remaining)
            except futures.TimeoutError as exc:
                raise OutboundResolveDeadline(
                    f"resolving {hostname!r} exceeded the overlap deadline"
                ) from exc
        finally:
            # Never block on the abandoned worker — that would reinstate the exact
            # unbounded wait this function exists to remove.
            pool.shutdown(wait=False)

    return _resolve


#: The machine-readable key the web branches on. 422 rather than 409, matching
#: `STARVATION_CODE`'s reasoning: a precondition on the request, not a conflict
#: with server state. Reword the copy below freely; never change this without
#: changing `paperlessOverlapWarning.ts`.
OVERLAP_CODE = "paperless_may_also_file"

#: `MailRule.action` — DELETE is the only terminal one. Used for **copy only**;
#: it must never reach control flow (ADR D1 removed action-based defaults, and a
#: test pins that).
_ACTION_DELETE = 1


MESSAGE = (
    "Paperless is already watching this mailbox. A rule here means the same "
    "attachment is filed twice — once by paperless, once by Lumogis — and "
    "neither copy knows about the other."
)
MESSAGE_DELETE = (
    "Paperless is already watching this mailbox, and its rule DELETES mail "
    "after filing. Lumogis will file a copy first, then lose access to the "
    "original."
)
ALTERNATIVE = (
    "You can let paperless keep filing this mailbox and skip the Lumogis rule — "
    "documents paperless files still appear in your Lumogis library."
)
RECOVERY = (
    "If you create the rule anyway, anything filed twice can be removed from "
    "the Lumogis library later. It does not change what paperless holds."
)
#: `low` confidence. Rendered inline, never gates (ADR D3). It is honest about
#: its own uncertainty — "if it's this one" — which a modal cannot be.
ADVISORY = (
    "You also run paperless, and it watches a mailbox on this server. If it's "
    "this one, this rule may file the same document twice."
)

# ─── present tense (LUM-836) ─────────────────────────────────────────────────
#
# The three above are written for a rule the user is ABOUT to create ("A rule
# here means…"). The rules page shows rules that already exist, so the same
# facts need a different tense. Rendering the future-tense copy there describes
# an action the reader is not taking, which reads as a bug in the product rather
# than a warning about it.
MESSAGE_EXISTING = (
    "Paperless is also watching this mailbox. These rules mean the same "
    "attachment is filed twice — once by paperless, once by Lumogis — and "
    "neither copy knows about the other."
)
MESSAGE_EXISTING_DELETE = (
    "Paperless is also watching this mailbox, and its rule DELETES mail after "
    "filing. Lumogis files a copy first, then loses access to the original."
)
ADVISORY_EXISTING = (
    "You also run paperless, and it watches a mailbox on this server. If it's "
    "this one, these rules may be filing the same document twice."
)

#: The `POST /sources` arm (LUM-835). Composed here rather than in the client
#: for the same reason every other string in this module is: the web matches on
#: `code`, never on prose, precisely so wording can move without a contract break.
CONNECT_OVERLAP_MESSAGE = (
    "Paperless will watch a mailbox Lumogis already files attachments from. "
    "Your existing filing rules mean the same attachment is filed twice — once "
    "by paperless, once by Lumogis — and neither copy knows about the other."
)
CONNECT_ALTERNATIVE = (
    "You can remove the Lumogis filing rules on that mailbox and let paperless "
    "own it — documents paperless files still appear in your Lumogis library."
)


@dataclass(frozen=True)
class Overlap:
    """One matched paperless rule. `folder` is **internal** — never serialise it
    into a response; a folder name can be personal."""

    confidence: Literal["high", "low"]
    folder: Optional[str] = None
    action: Optional[int] = None


@dataclass(frozen=True)
class OverlapResult:
    """Three states need three shapes.

    `overlap is None` with `status == "ok"` means **checked, none found**.
    `overlap is None` with any other status means **could not tell**. Collapsing
    those two into a bare `None` was the first draft's contract bug: the GET
    response requires a `status` on every reply, and `None` destroyed it.
    """

    status: Literal["ok", "auth", "unavailable"]
    overlap: Optional[Overlap] = None


def refusal_detail(ov: Overlap) -> dict:
    """The 422 body. Shares its shape with `external_consumer_guard.refusal_detail`
    so the two consent gates cannot drift into telling different stories."""
    return {
        "code": OVERLAP_CODE,
        "message": MESSAGE_DELETE if ov.action == _ACTION_DELETE else MESSAGE,
        "alternative": ALTERNATIVE,
        "recovery": RECOVERY,
    }


def connect_refusal_detail(matches: list[dict]) -> dict:
    """The 422 body for the `POST /sources` arm (LUM-835).

    **`code` is `OVERLAP_CODE`, not `STARVATION_CODE`.** ADR-192 consequence 2
    asked for "a second `code` alongside `external_consumer_may_starve`", and
    `paperlessOverlapWarning.ts` already matches this one. Sending the starvation
    code instead would be parsed by `externalConsumerWarning.ts` and *look* fine
    while telling the user the wrong thing; sending a third, new code would be
    matched by neither and render nothing at all.

    `matches` is a list because a household routinely has several mailboxes with
    filing rules, and "3 rules on Work" reads very differently from "3 rules"
    across two accounts. Each entry is `{source_name, rule_count}` — a display
    name and an integer, no ids, no folders, no usernames.
    """
    return {
        "code": OVERLAP_CODE,
        "message": CONNECT_OVERLAP_MESSAGE,
        "alternative": CONNECT_ALTERNATIVE,
        "recovery": RECOVERY,
        "mailboxes": [
            {"source_name": m.get("source_name", ""), "rule_count": int(m.get("rule_count", 0))}
            for m in matches
        ],
    }


def _mailbox_host_port(url: str) -> Optional[tuple[str, int]]:
    """`imap://host:port/label` → `(host, port)`. No decrypt: both are plaintext
    in `sources.url`. The label is a non-PII nickname and is ignored."""
    from services.imap_source_url import imap_host_port_from_source_url

    return imap_host_port_from_source_url(url)


def _mailbox_username(user_id: str, source_id: str) -> Optional[str]:
    """This mailbox's username, via the one supported accessor (LUM-778).

    **Not a second implementation.** ``mailbox_account_address`` reads the same
    payload field of the same row, and ADR-214 names it *"the one supported way
    to learn a mailbox's own address — do not re-derive it"*. This module used
    to re-derive it, which is the LUM-813 class exactly: two copies of a helper
    with divergent behaviour and callers quietly picking one. They agreed on a
    normal mailbox, so nothing was visibly broken — that is what makes the shape
    worth removing rather than watching.

    The posture the accessor already shares: read the payload **directly**,
    never via ``load_connection`` (which would DNS-resolve and SSRF-validate —
    wrong on an enumeration path, and ``overlap_for_sources`` is now genuinely
    one, calling this once per mailbox).

    What stays local is the **error shape**, which is a caller's decision rather
    than the accessor's. The accessor raises so each caller must say what an
    unknown address means; here it means *skip this mailbox*, matching the
    fail-open posture of everything else on this path — one unreadable
    credential must not turn a whole household's overlap check into a
    traceback. The legacy ``instance=""`` fallback moved **into** the accessor,
    where it is now guarded to the single-mailbox deployment it was written for.
    """
    try:
        return email_credentials.mailbox_account_address(user_id, instance=source_id)
    except Exception as exc:  # noqa: BLE001 - one bad mailbox must not fail the check
        _log.warning(
            "paperless_overlap.username_skip source_id=%s reason=%s",
            source_id,
            type(exc).__name__,
        )
        return None


def _norm_host(host: str) -> str:
    return host.strip().rstrip(".").casefold()


def _folder_compatible(folder: object) -> bool:
    """Lumogis reads INBOX and only INBOX, so compatibility is exactly one case.

    This is why the `.` vs `/` delimiter hazard never arises: `INBOX/Receipts`
    and `INBOX.Receipts` are both simply *not* `inbox`, so no delimiter is ever
    compared. RFC 3501 makes `INBOX` case-insensitive, hence the casefold.

    A missing or empty folder is treated as `INBOX`, matching `MailRule.folder`'s
    own model default — and the error direction is right: a false positive costs
    one click, a false negative costs documents.
    """
    if folder is None:
        return True
    if not isinstance(folder, str):
        return False
    stripped = folder.strip()
    if not stripped:
        return True
    return stripped.casefold() == "inbox"


def _select_rule(rules: list[dict]) -> dict:
    """Deterministic, so the copy is stable across probes: a DELETE rule wins so
    `MESSAGE_DELETE` is shown, ties break on the lowest id."""

    def _key(r: dict) -> tuple[int, int]:
        is_delete = 0 if r.get("action") == _ACTION_DELETE else 1
        try:
            rid = int(r.get("id", 0) or 0)
        except (TypeError, ValueError):
            rid = 0
        return (is_delete, rid)

    return sorted(rules, key=_key)[0]


def _match_source(
    host: str,
    accounts: list[dict],
    rules: list[dict],
    *,
    username_for: Callable[[], Optional[str]],
) -> Optional[Overlap]:
    """Pure matcher: does any paperless rule watch the mailbox on ``host``?

    **``username_for`` is a thunk, not a value.** The username costs a credential
    decrypt, and it is only ever needed to separate `high` from `low` *among
    accounts that already matched on host*. Taking it eagerly would decrypt once
    per mailbox in a batch — discarding the two-stage cost model that is the only
    reason the `high` tier is affordable at all.

    No I/O beyond that thunk, so the batch can call this once per mailbox against
    one shared config fetch.
    """
    want = _norm_host(host)
    host_accounts = [a for a in accounts if _norm_host(str(a.get("imap_server") or "")) == want]
    if not host_accounts:
        return None

    # Exact casefolded compare, with NO local-part normalisation: Gmail's
    # dot-folding is not generalisable, and guessing it wrong produces a
    # false `high` — the one outcome that gates wrongly.
    username = username_for()
    want_user = username.strip().casefold() if username else None
    exact = [
        a
        for a in host_accounts
        if want_user and str(a.get("username") or "").strip().casefold() == want_user
    ]

    ids = {a.get("id") for a in (exact or host_accounts)}
    candidates = [
        r
        for r in rules
        if r.get("account") in ids
        # A rule the user switched off files nothing, so claiming it will
        # file everything twice is false — and it is a `high` gate they
        # cannot make sense of, on a mailbox where nothing is wrong. An
        # absent key means enabled: older paperless may not serialise it,
        # and defaulting to disabled would turn the gate off entirely
        # against those installs.
        and r.get("enabled", True) is not False
        and _folder_compatible(r.get("folder"))
    ]
    if not candidates:
        return None

    rule = _select_rule(candidates)
    confidence: Literal["high", "low"] = "high" if exact else "low"
    return Overlap(
        confidence=confidence,
        folder=rule.get("folder"),
        action=rule.get("action"),
    )


def overlap_for_sources(
    user_id: str,
    source_ids: list[str],
    *,
    assume_consumer: bool = False,
) -> dict[str, OverlapResult]:
    """Overlap for many mailboxes at the cost of one paperless config fetch.

    **This is the entry point.** :func:`overlap_for_source` delegates here so
    there is one code path; a second implementation would drift, and the two
    would disagree about a gate.

    **Cost, counted honestly.** One `_rules_for`-free bulk `sources` read, one
    `fetch_mail_config()` regardless of mailbox count, and a credential decrypt
    only for mailboxes that already matched on host. The per-source
    `source_url_for_imap_instance` is deliberately NOT looped — see
    :func:`services.imap_source_url.source_urls_for_imap_instances`.

    **``assume_consumer`` is a guard bypass and is scoped like one.** Keyword-only,
    never a request field. The single caller that may pass ``True`` is the
    ``POST /sources`` paperless branch (``require_admin``), which is in the act of
    creating the very ``sources`` row :func:`ecg.has_external_consumer` tests for —
    so the guard is false by construction there, and without this the connect-time
    arm could never fire. It skips **exactly** that one predicate: `_enabled()`,
    ownership scoping and the SSRF-validated credential load all still apply.

    **Fails open per source**, matching the single-source contract: a mailbox that
    cannot be resolved reports `unavailable`, and one bad mailbox never collapses
    the batch.
    """
    started = time.monotonic()
    outcome = "unavailable"
    matched = "none"
    n_accounts = n_rules = 0
    ids = [s for s in dict.fromkeys(source_ids) if s]
    results: dict[str, OverlapResult] = {}
    try:
        if not ids:
            outcome = "ok"
            return {}
        if not _enabled():
            # A deliberately disabled check must not read as a permanently broken
            # one — this log line is what `.env.example` tells operators to watch.
            outcome = "disabled"
            return {sid: OverlapResult(status="ok") for sid in ids}
        # The cheap precondition. No paperless ⇒ no HTTP, no decrypt, nothing.
        if not assume_consumer and not ecg.has_external_consumer(user_id):
            outcome = "ok"
            return {sid: OverlapResult(status="ok") for sid in ids}

        urls = _source_urls(user_id, ids)
        hosts: dict[str, str] = {}
        for sid in ids:
            url = urls.get(sid)
            hostport = _mailbox_host_port(url) if url else None
            if hostport is None:
                # Not ours, unparseable, or gone. "Nothing to report" is the same
                # answer the single-source path gives.
                results[sid] = OverlapResult(status="ok")
                continue
            hosts[sid] = hostport[0]
            # Port is parsed for logging only and MUST NOT participate in the
            # match: paperless's `MailAccount.imap_port` is nullable and blank is
            # the common case (it infers 993/143 from `imap_security`). Requiring
            # equality would make the `high` tier unreachable for every
            # blank-port account — a permanently dead gate no fake would catch.
        if not hosts:
            outcome = "ok"
            return results

        from adapters.paperless_source import PaperlessMailConfigReader
        from adapters.paperless_source import _overlap_deadline_s

        # LUM-741 — one deadline for the whole probe, established BEFORE the
        # credential load, because `load_connection` runs the SSRF check and that
        # resolves the host over DNS. The budget is SHARED, not additional. In a
        # batch it is shared across every mailbox too: N mailboxes do not get N
        # deadlines, because they do not make N requests.
        deadline = time.monotonic() + _overlap_deadline_s()
        conn = paperless_credentials.load_connection(
            user_id, resolve_host=_bounded_resolver(deadline)
        )
        remaining = deadline - time.monotonic()
        accounts, rules, status = PaperlessMailConfigReader(
            conn, deadline_s=remaining
        ).fetch_mail_config()
        outcome = status
        n_accounts, n_rules = len(accounts), len(rules)
        if status != "ok":
            for sid in ids:
                results[sid] = OverlapResult(status=status)
            return results

        for sid, host in hosts.items():
            overlap = _match_source(
                host,
                accounts,
                rules,
                username_for=lambda sid=sid: _mailbox_username(user_id, sid),
            )
            results[sid] = OverlapResult(status="ok", overlap=overlap)
            if overlap is not None and matched != "high":
                matched = overlap.confidence
        outcome = "ok"
        return results
    except Exception:  # noqa: BLE001 - fail OPEN; see the docstring
        _log.warning("paperless_overlap.failed sources=%d", len(ids), exc_info=True)
        return {sid: OverlapResult(status="unavailable") for sid in ids}
    finally:
        # Counts and outcomes only — no user id, host, username or folder.
        _log.info(
            "paperless_overlap_probe outcome=%s sources=%d accounts=%d rules=%d "
            "matched=%s elapsed_ms=%d",
            outcome,
            len(ids),
            n_accounts,
            n_rules,
            matched,
            int((time.monotonic() - started) * 1000),
        )


def overlap_for_source(user_id: str, source_id: str) -> OverlapResult:
    """One mailbox. A thin wrapper over :func:`overlap_for_sources`.

    Kept because the rule-creation gate genuinely has one mailbox and reads
    better this way — but it must stay a *wrapper*. Two implementations of this
    check would eventually disagree, and the visible symptom would be a gate that
    fires on one surface and not the other.

    **Fails open in every direction**, and that is deliberate: a broken overlap
    check must never stop a user creating a rule on their own mailbox. The cost
    is that every failure looks like "no overlap", which is why
    `paperless_overlap_probe` is emitted unconditionally — it is the only way to
    tell "nobody overlaps" from "the probe has never once completed".
    """
    return overlap_for_sources(user_id, [source_id]).get(
        source_id, OverlapResult(status="unavailable")
    )


def _enabled() -> bool:
    import os

    return (
        os.environ.get("PAPERLESS_OVERLAP_CHECK_ENABLED", "true") or "true"
    ).strip().lower() not in (
        "0",
        "false",
        "no",
    )


def _source_urls(user_id: str, source_ids: list[str]) -> dict[str, str]:
    from services.imap_source_url import source_urls_for_imap_instances

    return source_urls_for_imap_instances(user_id, source_ids)


# ─── setup-time listener ─────────────────────────────────────────────────────

PAPERLESS = "paperless"


def _on_credential_change(user_id: str, connector: str, action: str, **_kw) -> None:
    """Diagnostics only. **The user-facing half of this lives elsewhere.**

    **What this is not.** An earlier docstring here claimed this covered "the
    other ordering" — paperless arriving after the filing rules — and said the
    matching answer "belongs to the next rule creation". Both were wrong, and the
    second was load-bearing: the LUM-728 plan dropped a daily sweep *because* this
    listener was believed to cover that case. It does not. It emits one INFO line
    and nothing reads it.

    **A credential write is not a connector becoming active**, which is the fact
    that made the first design of LUM-835 unbuildable.
    :func:`external_consumer_guard.has_external_consumer` tests the ``sources``
    table; a paperless credential ``PUT`` creates no ``sources`` row. Anything
    hooked here that expects to see a *connected* paperless will short-circuit —
    silently, returning "nothing to report", looking correct.

    **Where the answer actually is:** ``POST /sources`` with
    ``source_type="paperless", confirm=True`` (LUM-835) refuses with
    ``OVERLAP_CODE`` when the caller has filing rules on a mailbox paperless would
    also watch, and the rules page carries the standing version (LUM-836).

    **DB read only. It does not probe paperless.** This runs on the
    credential-write thread, and that thread must never wait on an external host.
    Kept rather than deleted because it is the one hook that fires at credential
    time, and it is the seat for anything that ever wants that moment.
    """
    if connector != PAPERLESS:
        return
    try:
        ms = config.get_metadata_store()
        # SCOPE-EXEMPT: `email_attachment_rule` is keyed by user_id; this counts
        # only the caller's own rules and never crosses to another member.
        row = ms.fetch_one(
            "SELECT COUNT(*) AS n FROM email_attachment_rule WHERE user_id = %s",
            (user_id,),
        )
        n = int((row or {}).get("n") or 0)
    except Exception as exc:  # noqa: BLE001 - a credential write must never fail here
        _log.warning("paperless_overlap.listener_failed reason=%s", type(exc).__name__)
        return
    if n:
        _log.info(
            "paperless_overlap_connect_notice rules=%d action=%s "
            "(paperless may now receive these documents twice)",
            n,
            action,
        )


def register() -> None:
    """Call once at startup, beside the egress-ACL listener."""
    ccs.register_change_listener(_on_credential_change)
