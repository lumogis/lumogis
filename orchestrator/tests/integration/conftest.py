# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Make the real-Postgres lane's absence LOUD (LUM-718).

Every test in this directory skips when its backing service is unreachable, which
is right for a laptop and wrong for CI: the lane then reports green while
validating nothing. Two production defects shipped behind exactly that —
LUM-648's snooze flip (a planning error that failed on every execution) and
`email_organize`'s ledger write (a bare `except` relabelling every failure as a
benign duplicate). Both were caught the first time anyone actually ran this lane.

Two mechanisms here, because one is not enough:

1. :func:`require_postgres` — when ``LUMOGIS_REQUIRE_INTEGRATION_DB=1``, an
   unreachable database is a **failure**, not a skip. CI sets it, so a broken or
   missing Postgres service can never again be mistaken for a passing suite.

2. A terminal summary that reports how many tests in this directory skipped and
   why. Even where skipping is legitimate (the full-stack ``*_live`` tests need
   compose, not just a database), a silent skip is what let the lane rot. The
   count belongs in the log where someone reading a green run can see it.

**Membership rule (LUM-753).** A test in this directory **requires a real
external backend**. That rule used to be unwritten and unchecked, so the
contents drifted both ways at once: 30 items needed nothing at all, while 94
always skipped. Two checks now hold the line, both gated on
``LUMOGIS_ASSERT_NO_HERMETIC_INTEGRATION=1`` (``make check-integration-hermeticity``
sets it, having first made every backend unreachable):

* **Nothing here passes without a backend.** The honest inverse of the skip
  count — anything that passes did not need one.
* **Nothing here is switched off unconditionally.** A bare ``@pytest.mark.skip``
  satisfies the first check *vacuously*: it never passes, and it never needs a
  backend either. Three such stubs lived here for months with skip reasons
  describing Postgres and FalkorDB and bodies containing only ``pass`` — see
  LUM-754. The first check alone would have certified them forever.

Neither check proves every test here *exercises* a backend when one is up; see
the plan's "The rule's limits".

**Deselection policy: deselect none (LUM-750).** Every module in this directory
is collected by ``make test`` and allowed to skip. The alternative — marking the
live lane and deselecting it — was rejected: the skip reporter above is the only
standing signal that this coverage is not running, and removing it is the exact
trade LUM-718 exists to reverse. Decisive factor: under LUM-726 nothing runs on
``dev``, so deselecting would leave no surface anywhere that these tests exist.

That policy is uniform. LUM-309 had deselected its own ``paperless_live`` lane
from ``make test`` while sixteen sibling ``*_live.py`` modules stayed collected,
for reasons that did not distinguish them; that deselection was removed. A
marker is for a gate to **select** with — never for hiding from the default lane.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# LUM-309 — the live paperless pipeline lane's real-store fixture. Registered
# here rather than imported by the test module: importing a fixture into the
# module that uses it makes every test signature look like a redefinition of the
# imported name (ruff F811), and conftest is where pytest expects to find
# fixtures anyway. Importing this module has no cost for other lanes — the
# fixture only builds stores when a test actually requests it.
from tests.integration._paperless_live_stores import live_stores  # noqa: F401 - fixture
from tests.integration._pgguard import REQUIRE_ENV  # noqa: F401 - re-export
from tests.integration._pgguard import integration_db_required  # noqa: F401 - re-export

#: Set by `make check-integration-hermeticity`. When "1", a pass or an
#: unconditional skip inside this directory fails the run.
HERMETIC_ASSERT_ENV = "LUMOGIS_ASSERT_NO_HERMETIC_INTEGRATION"

_THIS_DIR = Path(__file__).resolve().parent

#: Nodeids collected under this directory, resolved once at collection time
#: where ``item.path`` is absolute and unambiguous. Everything downstream tests
#: membership of this set rather than re-deriving a path.
#:
#: This indirection is load-bearing. ``report.fspath`` is a string **relative to
#: rootdir**, and ``report.path`` is ``None`` — so resolving a report's path
#: against the process CWD gives the right answer only when pytest happens to be
#: invoked from ``orchestrator/``. Run from the repo root, the same string
#: resolves to the **repo-root** ``tests/integration/`` — a different lane
#: entirely — and every violation is silently missed. A gate that reports green
#: while checking nothing is the exact failure this directory's rule exists to
#: prevent, so it must not depend on where it was launched from.
_nodeids_here: set[str] = set()

#: Accumulated across the session by :func:`pytest_runtest_logreport`.
_passed_here: list[str] = []
_unconditionally_skipped_here: list[str] = []


def _path_in_this_tree(raw: object) -> bool:
    """True only for paths under THIS directory.

    Anchored to this file's own resolved parent, **not** a substring match on
    ``"tests/integration/"``. There are two directories by that name — this one
    (in-process against real backends) and the repo-root ``tests/integration``
    (22 modules driving HTTP against a running Core). A substring test matches
    both, and ``make compose-test-integration`` already collects both in one
    session. Confusing those two directories is the exact defect LUM-753 exists
    to end, so the predicate that enforces it must not commit it.
    """
    if raw is None:
        return False
    try:
        return Path(str(raw)).resolve().is_relative_to(_THIS_DIR)
    except (OSError, ValueError):  # pragma: no cover - defensive
        return False


def pytest_collection_modifyitems(config, items) -> None:
    """Mark this tree's tests, and record any that are switched off outright.

    Every conftest's ``pytest_collection_modifyitems`` receives the **full**
    session item list, not just its own subtree — hence the anchored filter.
    """
    for item in items:
        if not _path_in_this_tree(getattr(item, "path", None)):
            continue
        _nodeids_here.add(item.nodeid)
        item.add_marker(pytest.mark.integration)
        # `skipif` carries a condition and is legitimate; a bare `skip` is a
        # test that never runs anywhere, which is a backlog item wearing a
        # test's clothes (LUM-754).
        if any(mark.name == "skip" for mark in item.iter_markers()):
            _unconditionally_skipped_here.append(item.nodeid)


def pytest_runtest_logreport(report) -> None:
    """Accumulate call-phase passes in this tree.

    Only the call phase and only ``passed`` — ``xfailed``, ``xpassed``,
    ``skipped`` and ``error`` are not passes and must not trip the check.
    """
    if report.when != "call" or report.outcome != "passed":
        return
    # Membership by nodeid against the set built at collection — never by
    # re-resolving `report.fspath`, which is relative to rootdir rather than CWD.
    if report.nodeid in _nodeids_here:
        _passed_here.append(report.nodeid)


def pytest_sessionfinish(session, exitstatus) -> None:
    """Fail the run if this directory broke its membership rule.

    Deliberately **not** in ``pytest_terminal_summary``: that hook receives no
    ``session`` (its signature is ``(terminalreporter, exitstatus, config)``),
    so it cannot set an exit status without reaching into a private attribute —
    and the terminal plugin skips it entirely under ``--no-summary``. A check an
    output-formatting flag can switch off is not a check.
    """
    if os.environ.get(HERMETIC_ASSERT_ENV, "").strip() != "1":
        return
    violations: list[str] = []
    for nodeid in _passed_here:
        violations.append(f"PASSED-WITHOUT-BACKEND: {nodeid}")
    for nodeid in _unconditionally_skipped_here:
        violations.append(f"UNCONDITIONALLY-SKIPPED: {nodeid}")
    if not violations:
        return
    print(
        f"\n{len(violations)} test(s) violate the membership rule for "
        f"{_THIS_DIR.name}/ — a test here must require a real external backend:",
        file=sys.stderr,
    )
    for line in violations:
        print(f"  {line}", file=sys.stderr)
    print(
        "  Move backend-free tests to orchestrator/tests/. Delete stubs and "
        "track the gap in Linear (see LUM-753, LUM-754).",
        file=sys.stderr,
    )
    # Escalate from success only. If the session already failed for another
    # reason — a real test failure, or an INTERNALERROR (exit 3) — that status
    # is more informative than ours and overwriting it would *downgrade* the
    # signal. This check must only ever add a reason to fail, never mask one.
    if session.exitstatus == pytest.ExitCode.OK:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def pytest_terminal_summary(terminalreporter, exitstatus, config) -> None:
    """Report this directory's skips explicitly, so green never hides an empty run."""
    skipped = [
        rep
        for rep in terminalreporter.stats.get("skipped", [])
        if str(getattr(rep, "nodeid", "")) in _nodeids_here
    ]
    if not skipped:
        return
    terminalreporter.write_sep("-", "integration lane (LUM-718)")
    terminalreporter.write_line(
        f"{len(skipped)} integration test(s) SKIPPED — they validated nothing."
    )
    if not integration_db_required():
        terminalreporter.write_line(
            f"  {REQUIRE_ENV} is not set, so an unreachable backend skips silently. "
            "CI sets it to 1; set it locally to turn these into failures."
        )
