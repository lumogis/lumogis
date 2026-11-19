# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""The quickstart quotes the graph's memory figure from one constant (LUM-989).

``GRAPH_MIN_TOTAL_MEMORY_BYTES`` drives the memory verdict, the copy on the admin
page (``memory.required_bytes``) and the quickstart. If it changes, this fails
until the quickstart is edited too.
"""

from __future__ import annotations

import re
from pathlib import Path

from services import graph_status

_QUICKSTART = (
    Path(__file__).resolve().parent.parent.parent / "docs" / "deployment" / "quickstart.md"
)
_DOCS = Path(__file__).resolve().parent.parent.parent / "docs"


def _gib() -> int:
    return graph_status.GRAPH_MIN_TOTAL_MEMORY_BYTES // 1024**3


def test_the_quickstart_total_equals_the_constant() -> None:
    text = _QUICKSTART.read_text(encoding="utf-8")
    quoted = re.findall(r"plan on about (\d+) GB in total", text)
    assert quoted, "the quickstart no longer says 'plan on about N GB in total'"
    assert {int(n) for n in quoted} == {_gib()}


def test_capabilities_quotes_the_same_total() -> None:
    text = (_DOCS / "capabilities.md").read_text(encoding="utf-8")
    assert f"about {_gib()} GB in total" in text


def test_the_constant_is_a_whole_number_of_gib() -> None:
    assert graph_status.GRAPH_MIN_TOTAL_MEMORY_BYTES % 1024**3 == 0
