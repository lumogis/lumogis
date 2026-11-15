# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""strip_code_fence: one place that cleans model JSON before parsing."""

import json

import pytest
from services.llm_json import strip_code_fence


@pytest.mark.parametrize(
    "text, expected",
    [
        ('{"a": 1}', '{"a": 1}'),
        ('  {"a": 1}\n', '{"a": 1}'),
        ('```json\n{"a": 1}\n```', '{"a": 1}'),
        ("```\n[1, 2]\n```", "[1, 2]"),
        ('\n\n```json\n{"a": 1}\n```\n', '{"a": 1}'),
        ("", ""),
        (None, ""),
        # No leading fence: inner backticks are left alone.
        ('{"code": "```x```"}', '{"code": "```x```"}'),
    ],
)
def test_strip_code_fence(text, expected):
    assert strip_code_fence(text) == expected


def test_fenced_json_parses():
    assert json.loads(strip_code_fence('```json\n{"summary": "ok"}\n```')) == {"summary": "ok"}
