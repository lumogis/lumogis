# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""The unit lane tests the product, not the operator's machine (LUM-804).

``orchestrator/main.py`` calls ``load_dotenv()`` at import. The repo-root
``.env`` is a real, populated operator file, so running pytest from the repo
root loaded the live configuration into the suite — and the suite's verdict then
depended on **where it was launched from**:

===============================  ==========  =================
working tree                     ``.env``    result
===============================  ==========  =================
repo root                        present     **296 failed**
a ``git worktree`` (same commit) absent      **0 failed**
===============================  ==========  =================

Same commit, same command, same interpreter. The difference was an untracked
file. ``AUTH_ENABLED=true`` turned 296 tests expecting ``200`` into ``401``.

That it failed *loudly* was luck. Any variable in that 29 KB file whose wrong
value does not happen to trip an assertion changes behaviour **silently**, and
the suite still reports green — which is the LUM-788 defect class one layer
down: the environment quietly deciding what "the tests pass" means.

``tests/conftest.py::_NEUTRALISED_OPERATOR_ENV`` is the fix. These tests pin it,
because a list nothing asserts against is one edit away from being wrong again.
"""

from __future__ import annotations

import os

import pytest
from tests.conftest import _NEUTRALISED_OPERATOR_ENV


def test_the_unit_lane_does_not_inherit_operator_env():
    """None of the neutralised variables is visible to a test by default.

    Asserted over the whole list rather than just ``AUTH_ENABLED`` so a variable
    added to the tuple without a working ``delenv`` cannot pass silently.
    """
    leaked = {v: os.environ[v] for v in _NEUTRALISED_OPERATOR_ENV if v in os.environ}
    assert leaked == {}, (
        f"operator env leaked into the unit lane: {sorted(leaked)}. "
        "The autouse _override_config fixture should have removed these; the "
        "suite is now testing this machine's configuration rather than the "
        "product (LUM-804)."
    )


def test_auth_is_off_by_default_in_the_unit_lane():
    """The specific property whose absence produced the 296 failures.

    Deliberately asserted through ``auth.auth_enabled()`` rather than by reading
    the variable: what broke was the *resolved* posture, and the resolution rule
    (absent → ``false``) is the thing that makes neutralising the variable
    equivalent to setting it to ``false``.
    """
    from auth import auth_enabled

    assert auth_enabled() is False


def test_a_test_that_wants_auth_can_still_turn_it_on(monkeypatch):
    """Neutralising must not break the 74 modules that opt in.

    ``monkeypatch.setenv`` runs after the autouse fixture, so opting in still
    works. Without this, the fix above would trade one broken lane for another.
    """
    from auth import auth_enabled

    assert auth_enabled() is False
    monkeypatch.setenv("AUTH_ENABLED", "true")
    assert auth_enabled() is True


@pytest.mark.parametrize("var", _NEUTRALISED_OPERATOR_ENV)
def test_each_neutralised_variable_is_individually_absent(var):
    """Per-variable so a failure names the offender rather than the whole set."""
    assert var not in os.environ
