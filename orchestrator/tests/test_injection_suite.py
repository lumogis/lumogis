# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-141 — CI gate: the injection suite must stay green against live defences.

Non-``known_gap`` cases are HARD requirements (a regression reds the build).
``known_gap`` cases are expected-fail: when the real defence lands and the case
starts passing, ``xfail(strict=True)`` fails here, forcing reclassification.
"""

from __future__ import annotations

import pytest
from services.safety_playground import INJECTION_TEST_CASES
from services.safety_playground import _case_result
from services.safety_playground import run_injection_suite

_RAN_AT = "2026-07-14T00:00:00+00:00"

_KNOWN_GAPS = [c for c in INJECTION_TEST_CASES if c.known_gap]


def test_suite_has_min_coverage():
    vectors = {c.vector for c in INJECTION_TEST_CASES}
    assert len(INJECTION_TEST_CASES) >= 20
    assert len(vectors) == 5  # every injection surface represented


def test_no_hard_failures_against_live_defences():
    res = run_injection_suite(ran_at=_RAN_AT)
    hard = [(r.name, r.expected, r.actual) for r in res.results if not r.passed and not r.known_gap]
    assert hard == [], f"injection-defence regression(s): {hard}"


@pytest.mark.parametrize("case", _KNOWN_GAPS, ids=[c.name for c in _KNOWN_GAPS])
@pytest.mark.xfail(strict=True, reason="known injection gap — flips red when the defence lands")
def test_known_gap_still_open(case):
    # Asserting the gap case passes; it currently does NOT (xfail). When the
    # real fix lands it will pass, and strict xfail turns that into a failure,
    # forcing the case to be reclassified (known_gap=False).
    assert _case_result(case).passed
