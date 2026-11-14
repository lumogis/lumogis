# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""The paperless live-fixture guard's own branch logic (LUM-309, relocated by LUM-753).

Lives in `orchestrator/tests/` rather than `orchestrator/tests/integration/`
because it needs **no backend** — it tests `require_paperless`'s skip-vs-fail
decision directly, and a test that requires nothing does not belong in a
directory whose membership rule is "requires a real external backend".

That placement is not incidental. If this test stayed in the integration tree
it would be the single exception to that rule, and the check enforcing it
would need an allowlist — which is an erosion path: one entry becomes
precedent. Moving it keeps the rule absolute.

Two consequences worth stating, because neither is obvious from the diff:
`make compose-test-paperless` no longer runs this test (it selects
`-m paperless_live`, and this module carries no marks), and `make test` now
*gains* it — before LUM-753 removed the `-m "not paperless_live"` deselection,
it was excluded from the default lane entirely.
"""

from __future__ import annotations

import pytest
from tests.integration._paperless_guard import PAPERLESS_REQUIRE_ENV
from tests.integration._paperless_guard import require_paperless


def test_an_unreachable_paperless_fails_when_the_fixture_is_required(monkeypatch):
    """The guard itself — otherwise the anti-rot mechanism is the one untested thing."""
    # pytest.fail / pytest.skip raise Failed / Skipped, which descend from
    # BaseException rather than Exception — `pytest.raises(Exception)` does not
    # catch them, and a test written that way fails with the guard's own
    # message, which reads confusingly like the fixture being down.
    monkeypatch.setenv(PAPERLESS_REQUIRE_ENV, "1")
    with pytest.raises(pytest.fail.Exception) as required:
        require_paperless(RuntimeError("connection refused"))
    assert "would report green while validating nothing" in str(required.value)

    monkeypatch.delenv(PAPERLESS_REQUIRE_ENV, raising=False)
    with pytest.raises(pytest.skip.Exception):
        require_paperless(RuntimeError("connection refused"))
