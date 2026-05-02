# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Ensure KG service models stay byte-identical to Core after ``make sync-vendored``.

The Makefile ``sync-vendored`` target is the only supported way to refresh
``services/lumogis-graph/models/{webhook,capability}.py`` from
``orchestrator/models/``. If this test fails, run from the **repository root**::

    make sync-vendored

and commit both canonical and vendored files together.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# orchestrator/tests/ -> repo root is parents[2]
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _expected_vendored_text(canonical: Path) -> str:
    """Match ``Makefile:sync-vendored`` (``head -n2`` + two lines + ``tail -n+3``)."""
    name = canonical.stem
    text = canonical.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    if len(lines) < 2:
        pytest.fail(f"canonical {canonical} has fewer than 2 lines")
    head2 = lines[:2]
    tail = lines[2:]
    return (
        "".join(head2)
        + f"# VENDORED FROM orchestrator/models/{name}.py — DO NOT EDIT BY HAND.\n"
        + "# Run `make sync-vendored` after changing the canonical Core copy.\n"
        + "".join(tail)
    )


@pytest.mark.parametrize(
    "stem",
    ("webhook", "capability"),
    ids=["webhook", "capability"],
)
def test_vendored_file_matches_canonical_model(stem: str) -> None:
    canonical = _REPO_ROOT / "orchestrator" / "models" / f"{stem}.py"
    vendored = _REPO_ROOT / "services" / "lumogis-graph" / "models" / f"{stem}.py"
    assert canonical.is_file(), f"missing {canonical}"
    assert vendored.is_file(), f"missing {vendored}"
    expected = _expected_vendored_text(canonical)
    actual = vendored.read_text(encoding="utf-8")
    assert actual == expected, (
        f"Vendored copy out of sync: {vendored}\n"
        f"Re-run: `make sync-vendored` from repo root, then commit both files: "
        f"{canonical} and {vendored}."
    )
