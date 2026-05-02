# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Acceptance #4 grep gate for personal/shared/system memory scopes.

Plan: ``.cursor/plans/personal_shared_system_memory_scopes.plan.md`` §10 #4.

Every user-scoped read in the orchestrator and KG service MUST go through
``visible_filter(user)`` (Postgres) / ``visible_qdrant_filter`` (Qdrant) /
``visible_cypher_fragment`` (FalkorDB), or — if it is a deliberate admin
god-mode read — be tagged with the comment marker ``# ADMIN-BYPASS:`` on
or immediately above the SQL line. Any other raw ``WHERE user_id = …``
predicate is a household-privacy regression.

The gate is regex-based. It mirrors plan §10 #4 verbatim::

    r"WHERE\\s+(\\w+\\.)?user_id\\s*(=|IN)\\s*(%s|\\$\\d+|:\\w+|%\\(\\w+\\)s)"

Allowed exceptions (codified per the plan body):

1. The two ``visibility.py`` helpers — they are the only files allowed to
   emit the predicate.
2. Any line whose preceding ``ADMIN_BYPASS_LOOKBACK`` lines contain the
   marker ``# ADMIN-BYPASS:`` (admin/audit/review surfaces, plan §2.8).
3. Reads against tables explicitly excluded from scope per plan §2.10
   (``sources``, ``relevance_profiles``, ``users``, ``connector_permissions``,
   ``routines``, ``feedback_log``, ``app_settings``,
   ``kg_settings``, ``constraint_violations``, ``edge_scores``,
   ``known_distinct_entity_pairs``, ``review_decisions``, ``dedup_candidates``,
   ``deduplication_runs``, ``schema_migrations``). These tables have **no**
   ``scope`` column, so ``visible_filter`` cannot apply; the query must
   carry the marker ``# SCOPE-EXEMPT:`` on or immediately above the SQL
   line. (This marker is plan-implied: the plan only enumerates one
   escape hatch — ``# ADMIN-BYPASS:`` — but explicitly excludes scope-less
   tables in §2.10. Without a second marker the gate would force admin-
   bypass tagging on per-user reads of e.g. ``relevance_profiles``, which
   is semantically wrong.) ``routine_do_tracking`` was lifted to per-user
   in migration ``016`` (per_user_connector_permissions); both
   ``connector_permissions`` and ``routine_do_tracking`` remain
   scope-less and use the ``# SCOPE-EXEMPT:`` comment-tag marker
   convention.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCOPED_ROOTS = (
    "orchestrator/services",
    "orchestrator/routes",
    "services/lumogis-graph",
)
_HELPERS_ALLOWED = {
    "orchestrator/visibility.py",
    "services/lumogis-graph/visibility.py",
}
_ADMIN_TAG = "# ADMIN-BYPASS:"
_EXEMPT_TAG = "# SCOPE-EXEMPT:"
_LOOKBACK = 6

_USER_ID_FILTER = re.compile(
    r"WHERE\s+(\w+\.)?user_id\s*(=|IN)\s*(%s|\$\d+|:\w+|%\(\w+\)s)"
)


def _iter_scoped_files() -> Iterable[Path]:
    for root in _SCOPED_ROOTS:
        base = _REPO_ROOT / root
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            rel = str(path.relative_to(_REPO_ROOT))
            if rel in _HELPERS_ALLOWED:
                continue
            if "/tests/" in rel or rel.endswith("/tests"):
                continue
            yield path


def _line_is_string_payload(line: str) -> bool:
    """Heuristic: the matched line is a SQL fragment inside a Python string,
    not a comment or docstring narration."""
    stripped = line.lstrip()
    if stripped.startswith("#"):
        return False
    return '"' in line or "'" in line


def _scan_one(path: Path) -> list[tuple[int, str]]:
    """Return [(lineno, content), ...] of untagged hits in ``path``."""
    rel = str(path.relative_to(_REPO_ROOT))
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    hits: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        if not _USER_ID_FILTER.search(line):
            continue
        if not _line_is_string_payload(line):
            continue
        # Look back up to _LOOKBACK lines for either escape-hatch tag.
        window_start = max(0, i - _LOOKBACK)
        window = lines[window_start : i + 1]
        if any(_ADMIN_TAG in w or _EXEMPT_TAG in w for w in window):
            continue
        hits.append((i + 1, line.rstrip()))
        # Annotate the offending file in the failure message for fast triage.
        hits[-1] = (i + 1, f"{rel}: {line.rstrip()}")
    return hits


def test_no_raw_user_id_filter_outside_admin() -> None:
    """Plan §10 acceptance #4: zero untagged ``WHERE user_id`` predicates
    in ``orchestrator/services/``, ``orchestrator/routes/``, and
    ``services/lumogis-graph/``.

    Failures here usually mean either:

    * a user-scoped read forgot to switch to ``visible_filter(user)``
      (the household-visibility regression the plan exists to prevent),
      OR
    * the read IS a legitimate admin god-mode / scope-exempt read but
      forgot the ``# ADMIN-BYPASS:`` / ``# SCOPE-EXEMPT:`` tag immediately
      above (or on) the offending line.

    Fix the SQL or add the correct tag with a one-line justification.
    """
    all_hits: list[str] = []
    for path in _iter_scoped_files():
        for _, fmt in _scan_one(path):
            all_hits.append(fmt)

    if all_hits:
        msg_lines = [
            "Acceptance #4 grep gate failed: untagged raw user_id filters detected.",
            "Replace with visible_filter(user) — or, if this is a deliberate admin /",
            "scope-exempt read, add `# ADMIN-BYPASS: <reason>` or",
            "`# SCOPE-EXEMPT: <reason>` on the line above (within "
            f"{_LOOKBACK} lines).",
            "",
            f"Untagged hits ({len(all_hits)}):",
        ]
        msg_lines.extend(f"  {h}" for h in all_hits)
        pytest.fail("\n".join(msg_lines))
