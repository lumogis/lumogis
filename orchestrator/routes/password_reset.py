# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Public forgot-password routes (LUM-65).

Unauthenticated and internet-reachable, so the request endpoint is built
around one property: **a caller must not be able to tell whether an address
has an account.** Three things together buy that, and all three are load-bearing:

* identical response bodies (``{"ok": true}``) on both branches;
* a ``_DUMMY_HASH``-class argon2 verify on the unknown-email branch, mirroring
  what ``verify_credentials`` already does on the login path, so the CPU cost
  is comparable; and
* a wall-clock floor both branches pay before responding.

The floor only works because **neither branch does network I/O**. The mail is
handed to a durable outbox inside the same transaction as the token and sent
later by a background drain — a floor cannot mask a branch that runs to a 30s
SMTP timeout, and that gap would widen exactly when the operator's relay is
unhealthy, which is when nobody is looking.
"""

from __future__ import annotations

import logging
import time

from auth import auth_enabled
from csrf import _proxied_client_ip
from fastapi import APIRouter
from fastapi import HTTPException
from fastapi import Request
from fastapi import status
from models.auth import AckOk
from models.password_reset import PasswordResetRedeemBody
from models.password_reset import PasswordResetRequestBody
from models.password_reset import PasswordResetStatusResponse
from rate_limit import FailureRateLimiter
from rate_limit import RequestRateLimiter
from services.users import PasswordPolicyViolationError

import config
from services import altcha_challenge
from services import password_reset as reset_service
from services import system_mail
from services import users as users_svc

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/password-reset", tags=["password-reset"])

# RequestRateLimiter (every attempt counts), not FailureRateLimiter: this
# endpoint answers 200 whether or not the address exists, so a failure-only
# limiter would never fire and the endpoint would be a free mail cannon aimed
# at any address an attacker chooses.
#
# The limiter is single-key (`rate_limit.RequestRateLimiter.check(key)`), so the
# two ceilings need two instances: per-IP bounds one attacker, per-email bounds
# how much mail any single victim can be made to receive from many IPs.
_request_ip_limiter = RequestRateLimiter(max_requests=10, window_seconds=60.0)
_request_email_limiter = RequestRateLimiter(max_requests=5, window_seconds=60.0)
_status_limiter = RequestRateLimiter(max_requests=60, window_seconds=60.0)
_redeem_limiter = FailureRateLimiter()

# Both request branches take at least this long. Sized above the spread between
# an argon2 verify and a token mint + outbox insert.
_MIN_REQUEST_SECONDS = 0.400

_TOKEN_INVALID_DETAIL = "Reset link is invalid or expired"


def _reset_limiters_for_tests() -> None:
    _request_ip_limiter.clear()
    _request_email_limiter.clear()
    _status_limiter.clear()
    _redeem_limiter.clear()
    altcha_challenge.reset_for_tests()


def _client_ip(request: Request) -> str:
    return _proxied_client_ip(request)


def _email_key(email: str) -> str:
    """Rate-limit key for an address: normalized, hashed, length-bounded.

    Hashed because the limiter's store is an in-process dict that keeps keys
    for the window — there is no reason for it to hold plaintext addresses.
    Bounded because the key is attacker-supplied and the store never evicts by
    count.
    """
    import hashlib

    return hashlib.sha256(email.strip().lower()[:320].encode("utf-8")).hexdigest()


def _too_many_requests() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="too many requests; try again in a minute",
        headers={"Retry-After": "60"},
    )


def _disabled() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"code": "email_reset_disabled"},
    )


@router.get("/status", response_model=PasswordResetStatusResponse)
def password_reset_status(request: Request) -> PasswordResetStatusResponse:
    """Whether the forgot-password page should offer the email form."""
    if not _status_limiter.check(_client_ip(request)):
        raise _too_many_requests()
    return PasswordResetStatusResponse(email_reset_enabled=system_mail.email_reset_enabled())


@router.post("/request", response_model=AckOk)
def request_password_reset(body: PasswordResetRequestBody, request: Request) -> AckOk:
    """Ask for a reset link. Answers identically for known and unknown addresses."""
    started = time.monotonic()

    if not system_mail.email_reset_enabled():
        # Before any per-address work: this is a property of the deployment,
        # not of the address, so it leaks nothing.
        raise _disabled()

    # Proof of work first — an unsolved request must never reach a user lookup
    # or cost a rate-limit slot.
    try:
        solved = altcha_challenge.verify(body.altcha)
    except altcha_challenge.AltchaNotConfigured:
        raise _disabled() from None
    if not solved:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "altcha_invalid"},
        )

    ip = _client_ip(request)
    if not _request_ip_limiter.check(ip) or not _request_email_limiter.check(
        _email_key(str(body.email))
    ):
        raise _too_many_requests()

    _dispatch_or_pad(str(body.email))
    _pay_floor(started)
    return AckOk()


def _dispatch_or_pad(email: str) -> None:
    """Mint + enqueue for a real account; otherwise burn a comparable argon2.

    A disabled account is treated exactly like an unknown one — mailing a
    reset link to a deactivated member would be a way to bring the account
    back to life through a channel the admin did not intend.
    """
    user = users_svc.get_user_by_email(email)
    if user is None or user.disabled:
        users_svc.spend_dummy_verify()
        return

    ms = config.get_metadata_store()
    try:
        # `isolated_transaction`, never `transaction()`: this runs on a sync
        # route in the shared AnyIO threadpool, and `transaction()` toggles
        # autocommit on the one process-wide connection. Two concurrent resets —
        # or one reset alongside any other transactional caller — made the loser
        # raise, and the bare `except` below turned that into a cheerful
        # `{"ok": true}` with no token minted and no mail queued. Proved against
        # a real Postgres in `tests/integration/test_postgres_store_concurrency_live.py`.
        with ms.isolated_transaction() as tx:
            minted = reset_service.mint(tx, user.id)
            system_mail.enqueue_password_reset(
                tx,
                to_addr=user.email,
                reset_url=reset_service.build_reset_url(minted.plaintext),
            )
    except Exception:
        # Never surface this: a failure here must look exactly like the
        # unknown-address branch. The transaction rolled back, so no token
        # exists without the mail that carries it.
        _log.exception("password reset: mint/enqueue failed")


def _pay_floor(started: float) -> None:
    elapsed = time.monotonic() - started
    if elapsed < _MIN_REQUEST_SECONDS:
        time.sleep(_MIN_REQUEST_SECONDS - elapsed)


@router.post("/redeem", response_model=AckOk)
def redeem_password_reset(body: PasswordResetRedeemBody, request: Request) -> AckOk:
    """Consume a reset link and set the new password."""
    # Gated on `auth_enabled()` alone, deliberately — NOT on
    # `email_reset_enabled()`. That predicate ANDs four conjuncts, and three are
    # preconditions for *minting and sending* a link, not for redeeming one
    # already in a user's inbox: a public origin (to build the URL), an ALTCHA
    # HMAC key (for the request form's proof of work), and a passing SMTP Send
    # test (to deliver). None is required to verify a hash and write a password.
    #
    # Gating on all four meant an admin rotating the relay password — routine
    # maintenance — cleared `last_test_ok_at` and silently killed every
    # outstanding link, locking out exactly the people mid-recovery. The failure
    # was invisible: the same "invalid or expired" page as a forged token.
    #
    # An admin who *does* want to kill live links changes the account password;
    # `_apply_new_password` invalidates outstanding `prst_` tokens on every
    # channel. Residual exposure is bounded regardless — 30-minute TTL, single
    # use, hashed at rest. (LUM-65 P1 #5)
    #
    # Kept as a comment rather than in the docstring: FastAPI publishes the
    # docstring as the endpoint's OpenAPI `description`, and defect rationale is
    # not something to ship to every API consumer.
    if not auth_enabled():
        raise _disabled()

    ip = _client_ip(request)
    # Prefix rather than the whole token: enough to bound guessing against one
    # link without putting redeemable material in the limiter's key space.
    token_key = body.token[:24]
    if not _redeem_limiter.check(ip, token_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too many failed attempts; try again in a minute",
            headers={"Retry-After": "60"},
        )

    try:
        reset_service.redeem(body.token, body.new_password)
    except PasswordPolicyViolationError as exc:
        # 422 follows the invite redeem precedent (routes/invites.py), this
        # endpoint's public-token twin — not the 400 used by the authenticated
        # /me/password surface.
        _redeem_limiter.record_failure(ip, token_key)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except reset_service.ResetTokenInvalidError:
        _redeem_limiter.record_failure(ip, token_key)
        raise HTTPException(status_code=404, detail=_TOKEN_INVALID_DETAIL) from None

    _redeem_limiter.record_success(ip, token_key)
    return AckOk()
