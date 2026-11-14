# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Transactional system mail — configuration, durable outbox, drain (LUM-65).

**Sole writer** of the ``system_smtp`` credential row. The generic credential
routers refuse that connector (see
``routes.connector_credentials._reject_operator_managed``), so configuration
has exactly one path in and one shape.

Reads go through :func:`credential_tiers.system_get_payload` — the *system tier
directly*, never :func:`credential_tiers.resolve_runtime_credential`, whose
user → household → system precedence would let a household member's row shadow
the relay the admin proved with a Send test.

**Mail never leaves on a request.** :func:`enqueue` writes an outbox row inside
the caller's transaction, so committing a password-reset request also commits
the mail it owes; :func:`drain_outbox` (an APScheduler job) does the SMTP. That
is what lets the public request path answer in constant time for known and
unknown addresses alike — a wall-clock floor cannot mask a branch that runs to
a 30-second SMTP timeout — and it keeps an unauthenticated caller from holding
one of the shared AnyIO threadpool slots that ``POST /auth/login`` needs.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from typing import Any

from actions.audit import write_audit
from connectors.registry import SYSTEM_SMTP
from models.actions import AuditEntry

import config
from services import credential_tiers as cts

_log = logging.getLogger(__name__)

# Retry ladder for a failed submission, in minutes.
#
# **Expiry, not rung count, is the real terminator** (LUM-65 P1 #7). The ladder
# used to be (1, 5, 15, 60, 240) — roughly 82 minutes to exhaust — against a
# token TTL of 30 minutes by default. Two things followed, both bad. The rungs
# past the TTL delivered a link that was already dead, so a user who finally got
# the mail clicked it and was told it was invalid. And `purge_expired` reaped
# the row at the TTL first, so the dead-letter audit at the end of the ladder
# never fired: a relay outage dropped mail with no record anywhere.
#
# `ttl_minutes()` is operator-configurable in [5, 120], so no fixed ladder can
# fit it. The rungs are therefore short enough to be useful at the minimum TTL,
# and `_expired()` stops the ladder for real whenever the content has outlived
# its token — which is what makes the ladder and the purge agree by
# construction rather than by tuning two constants to match.
_RETRY_BACKOFF_MINUTES = (1, 5, 15)

#: One MORE than the number of rungs, deliberately — **not** `len(ladder)`.
#:
#: A rung is a *wait between* attempts, so N rungs describe N+1 attempts.
#: Setting this to `len(...)` made the final rung dead code: `_reschedule`
#: indexes `[attempts - 1]`, so attempts=3 would have used the 15-minute rung —
#: except attempts=3 already satisfied `>= MAX_ATTEMPTS` and dead-lettered
#: first. The outbox gave up ~6 minutes in (1 + 5) while the token stayed valid
#: for another 24, so a relay restart lost the mail outright.
#:
#: With the +1 the schedule is 1 → 5 → 15, giving up ~21 minutes in, which is
#: the intended "as many retries as fit inside the token's life". Pinned by
#: `test_retry_ladder_schedule_uses_every_rung_and_fits_the_ttl`, which walks
#: the real drain rather than recomputing the arithmetic — the earlier test
#: recomputed it and agreed with the bug.
MAX_ATTEMPTS = len(_RETRY_BACKOFF_MINUTES) + 1

_DRAIN_BATCH = 10

_RESET_SUBJECT = "Reset your Lumogis password"


class SystemMailNotConfigured(Exception):
    """No ``system_smtp`` row, or it is missing required fields."""


@dataclass(frozen=True)
class SystemSmtpConfig:
    host: str
    port: int
    username: str
    password: str
    from_addr: str
    last_test_ok_at: datetime | None


# ---------------------------------------------------------------------------
# Configuration (system tier, single writer)
# ---------------------------------------------------------------------------


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def load_config() -> SystemSmtpConfig | None:
    """Decrypt the ``system_smtp`` payload, or ``None`` when unconfigured."""
    payload = cts.system_get_payload(SYSTEM_SMTP)
    if not payload:
        return None
    host = str(payload.get("host") or "").strip()
    from_addr = str(payload.get("from_addr") or "").strip()
    if not host or not from_addr:
        return None
    try:
        port = int(payload.get("port") or 587)
    except (TypeError, ValueError):
        port = 587
    return SystemSmtpConfig(
        host=host,
        port=port,
        username=str(payload.get("username") or ""),
        password=str(payload.get("password") or ""),
        from_addr=from_addr,
        last_test_ok_at=_parse_dt(payload.get("last_test_ok_at")),
    )


def load_raw_payload() -> dict[str, Any] | None:
    """Raw decrypted payload — for the admin PUT's read-modify-write only."""
    return cts.system_get_payload(SYSTEM_SMTP)


def save_payload(payload: dict[str, Any], *, actor: str) -> None:
    """Write the ciphertext and evict the cached sender.

    Cache eviction belongs with the write: otherwise a Send test after a host
    change validates the *previous* relay and enables email reset for a
    configuration that has never delivered anything.
    """
    cts.system_put_payload(SYSTEM_SMTP, payload, actor=actor)
    config.reset_system_smtp_sender()


def delete_config(*, actor: str) -> bool:
    deleted = cts.system_delete_payload(SYSTEM_SMTP, actor=actor)
    config.reset_system_smtp_sender()
    return deleted


def public_origin_set() -> bool:
    from services.user_invites import public_origin

    return bool(public_origin())


def altcha_hmac_set() -> bool:
    from services import altcha_challenge

    return bool(altcha_challenge.hmac_secret())


def email_reset_enabled() -> bool:
    """Every precondition for the email reset path, evaluated together.

    Auth on (there are no accounts to recover otherwise), SMTP configured, a
    Send test that actually passed, an absolute public origin to build the link
    from, and an Altcha HMAC key so the public request path has its proof of
    work. Any one missing keeps Pass-1 UX (ask an admin / use the CLI), which
    is honest rather than a form that cannot work.
    """
    from auth import auth_enabled

    if not auth_enabled():
        return False
    if not public_origin_set() or not altcha_hmac_set():
        return False
    cfg = load_config()
    return bool(cfg and cfg.last_test_ok_at)


# ---------------------------------------------------------------------------
# Outbox
# ---------------------------------------------------------------------------


def enqueue(ms, *, to_addr: str, subject: str, body: str) -> str:
    """Queue one message. Takes the store so it joins the caller's transaction.

    A committed caller transaction means the mail is durably owed — there is no
    window in which a reset token exists but its mail does not.
    """
    row_id = str(uuid.uuid4())
    ms.execute(
        "INSERT INTO system_mail_outbox (id, to_addr, subject, body) VALUES (%s, %s, %s, %s)",
        (row_id, to_addr, subject, body),
    )
    return row_id


def enqueue_password_reset(ms, *, to_addr: str, reset_url: str) -> str:
    """Queue the reset mail. Plaintext, two lines, one absolute URL."""
    from services import password_reset

    minutes = password_reset.ttl_minutes()
    body = (
        "Someone asked to reset the Lumogis password for this address.\n"
        f"{reset_url}\n"
        f"The link expires in {minutes} minutes. "
        "If this was not you, no action is needed and the password is unchanged.\n"
    )
    return enqueue(ms, to_addr=to_addr, subject=_RESET_SUBJECT, body=body)


def _emit_audit(action: str, *, user_id: str, result_summary: dict | None = None) -> None:
    try:
        write_audit(
            AuditEntry(
                action_name=action,
                connector="auth",
                mode="system",
                input_summary=json.dumps({}),
                result_summary=json.dumps(result_summary or {}, default=str),
                executed_at=datetime.now(timezone.utc),
                user_id=user_id,
            )
        )
    except Exception:
        _log.exception("audit write for %s failed", action)


def _claim_due_rows(ms, limit: int) -> list[dict]:
    """Atomically claim due rows and push their next attempt out.

    ``FOR UPDATE SKIP LOCKED`` so two workers draining concurrently take
    disjoint work instead of double-sending.
    """
    return (
        ms.fetch_all(
            "UPDATE system_mail_outbox SET attempts = attempts + 1, "
            "next_attempt_at = NOW() + INTERVAL '1 minute' * %s "
            "WHERE id IN ("
            "  SELECT id FROM system_mail_outbox "
            "  WHERE sent_at IS NULL AND next_attempt_at <= NOW() "
            "  ORDER BY next_attempt_at "
            "  FOR UPDATE SKIP LOCKED LIMIT %s"
            ") RETURNING id, to_addr, subject, body, attempts, created_at",
            (_RETRY_BACKOFF_MINUTES[0], limit),
        )
        or []
    )


def _expired(created_at, *, now: datetime | None = None) -> bool:
    """True once a queued message has outlived the token its body carries.

    Sending after this point delivers a link that is already dead, which reads
    to the recipient as a broken product rather than a slow one.

    Missing or unparseable ``created_at`` returns False — a row of unknown age
    stays on the normal ladder and is bounded by :data:`MAX_ATTEMPTS` anyway.
    Treating unknown as expired would let one bad timestamp discard live mail.
    """
    if created_at is None:
        return False
    if not isinstance(created_at, datetime):
        return False
    stamp = created_at if created_at.tzinfo else created_at.replace(tzinfo=timezone.utc)
    from services import password_reset

    reference = now or datetime.now(timezone.utc)
    return reference - stamp >= timedelta(minutes=password_reset.ttl_minutes())


def _delete_row(ms, row_id: str) -> None:
    ms.execute("DELETE FROM system_mail_outbox WHERE id = %s", (row_id,))


def _reschedule(ms, row_id: str, attempts: int, error: str) -> None:
    idx = min(max(attempts - 1, 0), len(_RETRY_BACKOFF_MINUTES) - 1)
    ms.execute(
        "UPDATE system_mail_outbox SET next_attempt_at = NOW() + INTERVAL '1 minute' * %s, "
        "last_error = %s WHERE id = %s",
        (_RETRY_BACKOFF_MINUTES[idx], error[:500], row_id),
    )


def drain_outbox(limit: int = _DRAIN_BATCH) -> int:
    """Send every due row. Returns how many were delivered.

    Rows are DELETEd on success rather than stamped: ``body`` holds a plaintext
    reset URL, so the row is secret-bearing and there is no reason to retain it
    once the mail is out. A row that exhausts :data:`MAX_ATTEMPTS` is deleted
    too, with an audit trail, so a dead relay cannot accumulate live links.

    **Every write goes through its own `isolated_transaction`.** This runs as an
    APScheduler job, on a thread that does not own the process-wide connection.
    Plain writes here were silently enrolled in whatever transaction a request
    thread happened to have open, and rolled back with it: the claim and the
    post-send DELETE would vanish, so an already-delivered reset link was mailed
    again and the secret-bearing `body` outlived its own delete. Nothing raised.
    Reproduced against a real Postgres in
    ``tests/integration/test_postgres_store_concurrency_live.py``.

    The SMTP submission deliberately stays outside every transaction — holding
    one open across a 30s relay timeout would block the connection for the whole
    send.
    """
    ms = config.get_metadata_store()
    cfg = load_config()
    if cfg is None:
        # Nothing to send with. Rows stay queued — an operator who configures
        # SMTP later still gets the backlog out, bounded by the token TTL.
        return 0

    sender = config.get_system_smtp_sender()
    sent = 0
    # Claimed and committed up front: the attempt bump must survive a crash
    # mid-send, otherwise a restart re-sends immediately and forever.
    with ms.isolated_transaction() as tx:
        claimed = _claim_due_rows(tx, limit)
    for row in claimed:
        row_id = str(row["id"])
        attempts = int(row.get("attempts") or 1)

        # Expiry is checked BEFORE the send, not only after a failure.
        #
        # It was originally only on the failure branch, which meant a row whose
        # token had already expired was still handed to the relay whenever the
        # send *succeeded* — the precise defect the check was added to prevent.
        # The reachable case is mundane: a reset requested before SMTP is
        # configured sits queued (`cfg is None` returns early, and the purge
        # sweep only runs hourly), the operator finishes the Send test an hour
        # later, and the next tick cheerfully delivers a dead link. The
        # recipient then sees "invalid or expired" — the same page a forged
        # token produces — with no way to tell the mail was simply stale.
        if _expired(row.get("created_at")):
            with ms.isolated_transaction() as tx:
                _delete_row(tx, row_id)
            _log.error(
                "system_mail: dead-lettered outbox row (token_expired) before send "
                "after %d attempt(s) — its link had already expired",
                attempts,
            )
            _emit_audit(
                "__system_mail__.dead_lettered",
                user_id="system",
                result_summary={
                    "result": "not_sent",
                    "cause": "token_expired",
                    "attempts": attempts,
                },
            )
            continue

        try:
            sender.send(
                host=cfg.host,
                port=cfg.port,
                username=cfg.username,
                password=cfg.password,
                from_addr=cfg.from_addr,
                to_addr=str(row["to_addr"]),
                subject=str(row["subject"]),
                body=str(row["body"]),
            )
        except Exception as exc:  # noqa: BLE001 - every failure is retryable here
            reason = str(exc)
            if attempts >= MAX_ATTEMPTS:
                with ms.isolated_transaction() as tx:
                    _delete_row(tx, row_id)
                cause = "attempts_exhausted"
                _log.error(
                    "system_mail: dead-lettered outbox row (%s) after %d attempts: %s",
                    cause,
                    attempts,
                    reason,
                )
                _emit_audit(
                    # Was `__password_reset__.requested` — the verb for a user
                    # asking for a link, recorded here for the opposite event.
                    # An operator reading the audit log saw a stream of
                    # successful-looking requests while their relay was down.
                    "__system_mail__.dead_lettered",
                    user_id="system",
                    result_summary={
                        "result": "send_failed",
                        "cause": cause,
                        "attempts": attempts,
                    },
                )
            else:
                with ms.isolated_transaction() as tx:
                    _reschedule(tx, row_id, attempts, reason)
                _log.warning(
                    "system_mail: send attempt %d/%d failed, will retry: %s",
                    attempts,
                    MAX_ATTEMPTS,
                    reason,
                )
            continue
        with ms.isolated_transaction() as tx:
            _delete_row(tx, row_id)
        sent += 1
    return sent


def purge_expired(ms=None) -> int:
    """Drop unsent rows older than the reset TTL (secret-bearing hygiene).

    Like :func:`drain_outbox`, this is a scheduler job and must not write through
    the shared connection. When ``ms`` is supplied the caller owns the
    transaction semantics and the handle is used as given; otherwise the write
    runs on a dedicated one.
    """
    from services import password_reset

    sql = (
        "DELETE FROM system_mail_outbox WHERE sent_at IS NULL "
        "AND created_at < NOW() - INTERVAL '1 minute' * %s RETURNING id"
    )
    params = (password_reset.ttl_minutes(),)
    if ms is not None:
        rows = ms.fetch_all(sql, params) or []
    else:
        with config.get_metadata_store().isolated_transaction() as tx:
            rows = tx.fetch_all(sql, params) or []

    # Reaping here is a safety net, not the normal path — the drain now
    # dead-letters an expired row itself. Anything this catches was never
    # attempted at all (SMTP unconfigured, so `drain_outbox` returned early),
    # which is exactly the state an operator needs told. Silence was the
    # original defect: mail vanished with no log line and no audit row.
    if rows:
        _log.warning(
            "system_mail: purged %d unsent outbox row(s) older than the token TTL "
            "— they were never attempted; is SMTP configured?",
            len(rows),
        )
        _emit_audit(
            "__system_mail__.dead_lettered",
            user_id="system",
            result_summary={
                "result": "purged_unsent",
                "cause": "token_expired",
                "count": len(rows),
            },
        )
    return len(rows)


# ---------------------------------------------------------------------------
# Send test — the gate that turns email reset on
# ---------------------------------------------------------------------------


def send_test(*, to_addr: str, actor: str) -> datetime:
    """Send a probe synchronously and stamp ``last_test_ok_at`` on success.

    Synchronous on purpose, unlike reset mail: this is an authenticated admin
    who is waiting for the answer, and the answer is the whole point. Nothing
    is stamped unless the relay actually accepted the message, so
    ``email_reset_enabled`` can never be true for an unproven configuration.
    """
    cfg = load_config()
    if cfg is None:
        raise SystemMailNotConfigured("system SMTP is not configured")

    sender = config.get_system_smtp_sender()
    sender.send(
        host=cfg.host,
        port=cfg.port,
        username=cfg.username,
        password=cfg.password,
        from_addr=cfg.from_addr,
        to_addr=to_addr,
        subject="Lumogis system mail test",
        body=(
            "This is a test message from Lumogis.\n"
            "Receiving it confirms the system mailer can deliver, which is what "
            "enables the forgot-password email flow.\n"
        ),
    )

    stamped = datetime.now(timezone.utc)
    payload = load_raw_payload() or {}
    payload["last_test_ok_at"] = stamped.isoformat()
    save_payload(payload, actor=actor)
    _emit_audit(
        "__system_mail__.tested",
        user_id=actor,
        result_summary={"ok": True},
    )
    return stamped


def drain_interval_seconds() -> int:
    """Scheduler cadence; overridable so tests do not wait 15 seconds."""
    raw = os.environ.get("LUMOGIS_SYSTEM_MAIL_DRAIN_SECONDS", "").strip()
    try:
        return max(1, int(raw)) if raw else 15
    except ValueError:
        return 15


def next_attempt_after(attempts: int) -> timedelta:
    """Exposed for tests/docs: the backoff step for a given attempt count."""
    idx = min(max(attempts - 1, 0), len(_RETRY_BACKOFF_MINUTES) - 1)
    return timedelta(minutes=_RETRY_BACKOFF_MINUTES[idx])
