# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Clean a model's text before parsing it as JSON.

Small local models often wrap a JSON answer in a markdown code fence
(```json … ```) even when the prompt asks for bare JSON. Every caller that
parses model JSON goes through :func:`strip_code_fence` so the fence is handled
in one place: four call sites used to carry their own copy, and the one that
lacked it (session summaries) stored the raw fenced text as History titles.
"""

from __future__ import annotations


def strip_code_fence(text: str) -> str:
    """Return ``text`` stripped, without any markdown code-fence lines.

    Only acts when the stripped text starts with a fence; any line starting with
    three backticks is then dropped. Text without a leading fence is returned
    stripped and otherwise unchanged.
    """
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = "\n".join(line for line in raw.splitlines() if not line.startswith("```"))
    return raw
