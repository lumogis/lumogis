# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""ALTCHA proof-of-work service tests (LUM-65).

These exist because the two ways to get this wrong both fail **open** — the
CAPTCHA looks wired up and admits every request:

* calling the v1 API surface (``max_number`` / ``hmac_key`` / ``expires``) on a
  library whose top-level functions are v2, and
* treating ``verify_solution``'s return value as a boolean when it is an object
  with no ``__bool__`` (always truthy).

Both are asserted against the real pinned library, not a stub.
"""

from __future__ import annotations

import base64
import json

import pytest

from services import altcha_challenge

_TEST_HMAC = "test-altcha-hmac-key-at-least-32-chars"


@pytest.fixture(autouse=True)
def _cheap_challenges(monkeypatch: pytest.MonkeyPatch):
    """Keep the proof of work cheap so the suite stays fast."""
    monkeypatch.setenv("LUMOGIS_ALTCHA_HMAC_KEY", _TEST_HMAC)
    monkeypatch.setenv("LUMOGIS_ALTCHA_COST", "200")
    altcha_challenge.reset_for_tests()
    yield
    altcha_challenge.reset_for_tests()


def _solve(challenge_dict: dict) -> str:
    """Solve a challenge the way the browser widget does; return the payload."""
    import altcha

    challenge = altcha.Challenge.from_dict(challenge_dict)
    solution = altcha.solve_challenge(challenge)
    assert solution is not None, "test challenge was unsolvable"
    payload = {"challenge": challenge.to_dict(), "solution": solution.to_dict()}
    return base64.b64encode(json.dumps(payload).encode()).decode()


def test_create_challenge_when_hmac_set_then_returns_v2_wire_shape() -> None:
    challenge = altcha_challenge.create_challenge()

    # v2 nests the KDF parameters and signs them; a flat v1-shaped body
    # (challenge/salt/maxnumber at the top level) is unsolvable by a v2 widget.
    assert set(challenge) == {"parameters", "signature"}
    params = challenge["parameters"]
    assert params["algorithm"] == "SHA-256"
    assert params["cost"] == 200
    assert params["expiresAt"] > 0
    assert challenge["signature"]


def test_create_challenge_when_hmac_unset_then_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail closed — never invent an ephemeral key a second worker cannot verify."""
    monkeypatch.delenv("LUMOGIS_ALTCHA_HMAC_KEY", raising=False)

    with pytest.raises(altcha_challenge.AltchaNotConfigured):
        altcha_challenge.create_challenge()


def test_verify_when_solution_correct_then_true() -> None:
    payload = _solve(altcha_challenge.create_challenge())

    assert altcha_challenge.verify(payload) is True


def test_verify_when_solution_wrong_then_false() -> None:
    """Guards the truthiness trap.

    ``verify_solution`` returns a ``VerifySolutionResult`` with no ``__bool__``,
    so an implementation written as ``if not verify_solution(...)`` accepts
    every payload — including this deliberately wrong one — and no other test
    in the suite would notice.
    """
    challenge = altcha_challenge.create_challenge()
    import altcha

    parsed = altcha.Challenge.from_dict(challenge)
    solved = altcha.solve_challenge(parsed)
    assert solved is not None
    tampered = solved.to_dict()
    tampered["counter"] = int(tampered["counter"]) + 1
    payload = base64.b64encode(
        json.dumps({"challenge": challenge, "solution": tampered}).encode()
    ).decode()

    assert altcha_challenge.verify(payload) is False


def test_verify_when_payload_garbage_then_false() -> None:
    assert altcha_challenge.verify("not-a-payload") is False
    assert altcha_challenge.verify("") is False


def test_verify_when_signature_tampered_then_false() -> None:
    challenge = altcha_challenge.create_challenge()
    payload_b64 = _solve(challenge)
    decoded = json.loads(base64.b64decode(payload_b64))
    decoded["challenge"]["parameters"]["cost"] = 1
    tampered = base64.b64encode(json.dumps(decoded).encode()).decode()

    assert altcha_challenge.verify(tampered) is False


def test_verify_when_solution_replayed_then_second_use_rejected() -> None:
    """One proof of work must buy one request, not a rate-limit window of them."""
    payload = _solve(altcha_challenge.create_challenge())

    assert altcha_challenge.verify(payload) is True
    assert altcha_challenge.verify(payload) is False


def test_verify_when_hmac_unset_then_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _solve(altcha_challenge.create_challenge())
    monkeypatch.delenv("LUMOGIS_ALTCHA_HMAC_KEY", raising=False)

    with pytest.raises(altcha_challenge.AltchaNotConfigured):
        altcha_challenge.verify(payload)


def test_verify_when_signed_by_a_different_key_then_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _solve(altcha_challenge.create_challenge())
    monkeypatch.setenv("LUMOGIS_ALTCHA_HMAC_KEY", "a-completely-different-hmac-key-x")

    assert altcha_challenge.verify(payload) is False
