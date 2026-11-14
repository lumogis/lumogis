# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Dispatch-by-connector-id registry answering one question: does this stored
credential actually work? (LUM-834 chunk A.)

**Single responsibility.** A probe decides whether a *saved* credential
authenticates against its server. It never mutates, never returns payload
values, and never returns payload key names — nothing about the stored
credential crosses back out of this module beyond a tri-state verdict.

Three states, not two
---------------------
:attr:`ProbeOutcome.ok` is ``bool | None``. ``None`` means **not attempted** —
no probe exists for this connector, or the connector is tested on a different
surface. Collapsing that into ``False`` would paint a red ✗ on a connector that
is perfectly healthy, which is the recurring "a surface that says the wrong
thing" defect this arc keeps producing. Chunk B's four-state chip
(``Not configured`` → ``Configured (untested)`` → ``Verified`` / ``Failed``) is
built on the tri-state; keep it.

Dispatch is stated per id, never as "everything else"
-----------------------------------------------------
An implicit default is "correct for one member and silently wrong for another".
``smtp`` is the case that proves it: it looks like an unprobed connector, but it
is multi-instance *and* already live-tested by the SMTP opt-in's pre-save
connect test (LUM-650), so "no connection test for this connector" is simply
untrue for it — the same argument that earns ``imap`` its own case.
``system_smtp`` is the other: it is hidden from ``GET /registry`` so no card
renders for it, but it passes registry membership and is therefore reachable by
a direct POST. A ``200 unsupported`` there would make this the one verb on the
credential surface that *answers* where all its siblings refuse.

Bounding is two phases, not one
-------------------------------
:data:`LUMOGIS_CONNECTOR_PROBE_TIMEOUT_S` is a **budget spanning two separately
bounded phases**, not a single timeout. The credential load runs the SSRF check,
which resolves the host over DNS untimed by default (the reason LUM-741 added a
bounded resolver); the probe's own HTTP connect/read is bounded separately by
whatever budget is left. A probe that bounds only the first still hangs forever
on a host that resolves and then never answers.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from connectors.registry import IMAP
from connectors.registry import OPERATOR_MANAGED_CONNECTORS
from connectors.registry import PAPERLESS
from connectors.registry import SMTP

from services import connector_credentials as ccs

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Codes + the fixed user-facing strings they select.
#
# `detail` is chosen by `code` and is NEVER derived from an exception message.
# This is the one place this module deliberately departs from its nearest
# precedent: `routes/signals.py:_safe_probe_error` is `str(exc)[:200]`, which is
# TRUNCATION, not sanitisation. Several `ValueError`s off this credential
# surface are known to embed the configured host, and `outbound_http_url.py`
# can embed a resolved IP — none of which is stopped by keeping the first 200
# characters. Map type → fixed string; the exception never reaches the client.
# ---------------------------------------------------------------------------

CODE_VERIFIED = "verified"
CODE_AUTH_FAILED = "auth_failed"
CODE_UNREACHABLE = "unreachable"
CODE_NOT_CONFIGURED = "not_configured"
CODE_CREDENTIAL_UNAVAILABLE = "credential_unavailable"
CODE_INVALID_CONFIG = "invalid_config"
CODE_UNSUPPORTED = "unsupported"
CODE_TESTED_ELSEWHERE = "tested_elsewhere"
#: LUM-834 chunk B — the credential is stored, and switched off.
#:
#: A NINTH member of a frozen wire Literal, added deliberately (ADR-218 §3
#: froze it precisely so widening it is a visible act). It exists because
#: both of the answers this route could otherwise give are LIES: under
#: ``AUTH_ENABLED=true`` the load would report "No credential saved yet"
#: about a saved credential, and under the default ``AUTH_ENABLED=false``
#: with env vars set it would report "Connected successfully" for a
#: connector the user had just switched off. The second is the actively
#: dangerous one.
CODE_DISABLED = "disabled"

#: code → the exact string the user sees. The wire model pins the key set with
#: a `Literal`, so adding a code without adding its copy fails at import.
_DETAIL: dict[str, str] = {
    CODE_VERIFIED: "Connected successfully.",
    CODE_AUTH_FAILED: "The server rejected these credentials.",
    CODE_UNREACHABLE: "Couldn't reach that address.",
    CODE_NOT_CONFIGURED: "No credential saved yet.",
    CODE_CREDENTIAL_UNAVAILABLE: "Couldn't read the saved credential — re-enter it.",
    CODE_INVALID_CONFIG: "The saved settings aren't usable — check the address.",
    CODE_UNSUPPORTED: "No connection test for this connector.",
    CODE_TESTED_ELSEWHERE: "This connector is tested from its own screen.",
    CODE_DISABLED: "Turned off. Enable it to test.",
}


@dataclass(frozen=True)
class ProbeOutcome:
    """One probe verdict.

    ``ok`` is tri-state: ``True`` verified, ``False`` tried and failed,
    ``None`` **not attempted**.
    """

    ok: bool | None
    code: str
    detail: str


class OperatorManagedConnector(Exception):
    """``system_smtp`` — refused, not answered.

    Raised rather than returned so a caller cannot mistake the refusal for a
    verdict. The route maps it to the same ``422 use_system_mail_admin`` body
    its PUT and DELETE siblings already return.
    """


def _outcome(code: str, ok: bool | None) -> ProbeOutcome:
    return ProbeOutcome(ok=ok, code=code, detail=_DETAIL[code])


def unreachable() -> ProbeOutcome:
    """The fallback verdict for an exception nobody enumerated.

    Exists so the route's broad ``except Exception`` — the thing that actually
    enforces "never 5xx" — has a verdict to return without reaching into this
    module's privates or minting a detail string of its own.
    """
    return _outcome(CODE_UNREACHABLE, False)


#: Total budget for one probe. Read through the shared helper rather than
#: re-implemented: `PAPERLESS_OVERLAP_TIMEOUT_S` had the identical
#: floor/default/junk-tolerance logic, and two copies of a policy is a change
#: that lands in one and misses the other.
PROBE_TIMEOUT_ENV = "LUMOGIS_CONNECTOR_PROBE_TIMEOUT_S"


def probe_deadline_s() -> float:
    """Total budget for one probe, in seconds. Default 5.0, floor 0.5."""
    from adapters.paperless_source import bounded_env_deadline_s

    return bounded_env_deadline_s(PROBE_TIMEOUT_ENV)


# ---------------------------------------------------------------------------
# Probes.
# ---------------------------------------------------------------------------


def _probe_paperless(user_id: str) -> ProbeOutcome:
    """The one populated entry in chunk A.

    Adapter imports are **function-local on purpose.** The AST guard
    ``tests/test_routes_no_adapter_imports.py`` constrains ``orchestrator/routes/``,
    not services, and importing an adapter from a service is the established
    pattern here provided the import is function-local —
    ``services/paperless_overlap.py:462`` does exactly this, and six other
    services follow suit.
    """
    from adapters.paperless_source import PaperlessAuthProbe
    from services.paperless_overlap import OutboundResolveDeadline
    from services.paperless_overlap import _bounded_resolver

    from services import paperless_credentials

    # Phase 1 — the credential load, whose SSRF check resolves over DNS.
    # The deadline is established BEFORE the load and SHARED with phase 2; it is
    # not additional. `_bounded_resolver` is reused rather than copied: a second
    # implementation of "resolve off-thread and abandon the result" is a
    # duplication that drifts, and LUM-741's version already carries the reason
    # it must raise a non-`OSError` (the validator fails OPEN on `OSError`, so a
    # `TimeoutError` there would be an SSRF-validation bypass).
    deadline = time.monotonic() + probe_deadline_s()
    try:
        conn = paperless_credentials.load_connection(
            user_id, resolve_host=_bounded_resolver(deadline)
        )
    except ccs.ConnectorNotConfigured:
        # Nothing was tested — there is nothing to test. `ok=None`, and the
        # caller must not audit it.
        return _outcome(CODE_NOT_CONFIGURED, None)
    except ccs.CredentialUnavailable as exc:
        # Decrypt failed, or the stored payload is structurally wrong. Kept
        # distinct from `invalid_config` because the codebase preserves the
        # distinction deliberately (`caldav_credentials.py:154-160` says it is
        # kept for exactly this kind of caller): collapsing them would tell a
        # user with a rotated key the same thing as a user with a typo'd URL.
        _log.warning("connector_probe.credential_unavailable type=%s", type(exc).__name__)
        return _outcome(CODE_CREDENTIAL_UNAVAILABLE, False)
    except OutboundResolveDeadline as exc:
        _log.warning("connector_probe.resolve_deadline type=%s", type(exc).__name__)
        return _outcome(CODE_UNREACHABLE, False)
    except ValueError as exc:
        # SSRF refusal, an empty field, whitespace — all `ValueError` from the
        # loader, and all "the saved settings are not usable".
        _log.warning("connector_probe.invalid_config type=%s", type(exc).__name__)
        return _outcome(CODE_INVALID_CONFIG, False)

    # Phase 2 — the probe's own request, bounded by what is LEFT of the budget.
    #
    # These two outcomes are logged too. The "exception type only" rule exists
    # because `str(exc)` off this surface embeds the configured host — but the
    # adapter RETURNS a status string rather than raising, so there is no type to
    # log and no message to leak. The code itself is the safe thing to record,
    # and without it an operator debugging "everyone says the test fails" has a
    # 200 response and silence.
    remaining = deadline - time.monotonic()
    status = PaperlessAuthProbe(conn, deadline_s=remaining).check()
    if status == "ok":
        return _outcome(CODE_VERIFIED, True)
    if status == "auth":
        _log.info("connector_probe.rejected connector=%s code=%s", PAPERLESS, CODE_AUTH_FAILED)
        return _outcome(CODE_AUTH_FAILED, False)
    _log.warning("connector_probe.unreachable connector=%s code=%s", PAPERLESS, CODE_UNREACHABLE)
    return _outcome(CODE_UNREACHABLE, False)


#: connector id → probe. A connector absent from this map is never a false
#: negative; it is `unsupported`.
_PROBES = {
    PAPERLESS: _probe_paperless,
}

#: Connectors that HAVE a connection test — just not on this route.
#:
#: `imap` is multi-instance and a connector-level path has nowhere to put a
#: mailbox id; `POST /me/email-sources/test` does it properly (pre-save, folder
#: detection, INBOX count). `smtp` is likewise multi-instance and is live-tested
#: by the SMTP opt-in's pre-save connect test (LUM-650).
_TESTED_ELSEWHERE = frozenset({IMAP, SMTP})


def probe(connector: str, user_id: str) -> ProbeOutcome:
    """The single entry point. Never raises for an ordinary probe failure.

    Raises :class:`OperatorManagedConnector` for ``system_smtp`` only.
    """
    # Derived from the registry's own set rather than re-listed here: a second
    # literal list of operator-managed ids is a duplication that drifts, and
    # drift here would open the one verb its siblings refuse.
    if connector in OPERATOR_MANAGED_CONNECTORS:
        raise OperatorManagedConnector(connector)
    if connector in _TESTED_ELSEWHERE:
        return _outcome(CODE_TESTED_ELSEWHERE, None)

    fn = _PROBES.get(connector)
    if fn is None:
        return _outcome(CODE_UNSUPPORTED, None)

    # LUM-834 chunk B — short-circuit BEFORE the credential load, which is
    # earlier than it looks: `load_connection` is where the env fallback
    # lives, so asking after it would already have produced the wrong
    # answer. `ok=None` (not attempted) is exactly right here — nothing was
    # tried, so nothing failed — and not-attempted outcomes are not audited.
    if ccs.is_disabled(user_id, connector):
        return _outcome(CODE_DISABLED, None)

    outcome = fn(user_id)
    _audit(user_id, connector, outcome)
    return outcome


def _audit(user_id: str, connector: str, outcome: ProbeOutcome) -> None:
    """Record that a test ran — never what it was given.

    Emitted **in the service**, not the route: ``routes/connector_credentials.py``
    states the convention in terms — lifecycle audits are emitted inside the
    service module and routes do not re-emit.

    **Not-attempted outcomes are not audited at all.** Nothing was tested, so
    there is no event; auditing them would fill the log with non-events and make
    "was this credential ever verified?" unanswerable.
    """
    if outcome.ok is None:
        return
    # `key_version` is required by the audit helper. Under `AUTH_ENABLED=false`
    # a probe can succeed off the env fallback with no row at all, which has no
    # version — 0 records that honestly rather than inventing one.
    key_version = 0
    try:
        record = ccs.get_record(user_id, connector)
    except Exception as exc:  # noqa: BLE001 - an audit lookup must never fail a probe
        _log.warning("connector_probe.audit_lookup_failed type=%s", type(exc).__name__)
        record = None
    if record is not None:
        key_version = record.key_version
    ccs._emit_audit(
        user_id,
        connector,
        ccs.ACTION_CRED_TESTED,
        actor="self",
        key_version=key_version,
        ok=bool(outcome.ok),
    )
