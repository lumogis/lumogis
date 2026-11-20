# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""strip_code_fence: one place that cleans model JSON before parsing."""

import json

import pytest
from services.llm_json import parse_json_list
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


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('[{"a": 1}]', [{"a": 1}]),
        ('```json\n[{"a": 1}]\n```', [{"a": 1}]),
        ('Here you go: [{"a": 1}] Done.', [{"a": 1}]),
        ('[{"a": 1,}, {"b": [2,],},]', [{"a": 1}, {"b": [2]}]),
        ("[]", []),
    ],
)
def test_parse_json_list_forgives_small_slips(text, expected):
    assert parse_json_list(text) == expected


@pytest.mark.parametrize("text", ["", "sorry", '{"a": 1}', '[{"a": "b\']', None])
def test_parse_json_list_returns_none_when_there_is_no_array(text):
    assert parse_json_list(text) is None
