# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Taxonomy-aware email LLM prompt (LUM-631).

The on-box email prompt enumerates the user's enabled category slugs (comma-
joined, `other` excluded) so the local model can propose a category; the
non-email prompt is unchanged and the boundary is untouched.
"""

from __future__ import annotations

import services.signal_processor as sp


class _CaptureLLM:
    def __init__(self):
        self.prompt = None

    def chat(self, *, messages, system, max_tokens):
        self.prompt = messages[0]["content"]

        class _R:
            text = (
                '{"content_summary":"s","topics":[],"entities":[],'
                '"importance_score":0.4,"category":"receipts"}'
            )

        return _R()


def _run(monkeypatch, categories):
    cap = _CaptureLLM()
    monkeypatch.setattr(sp.config, "get_llm_provider", lambda *a, **k: cap)
    monkeypatch.setattr(sp.config, "get_model_config", lambda name: {})
    monkeypatch.setattr("services.privacy_mode.resolve_job_model", lambda m, u: m)
    data = sp._call_llm(
        "Title", "Body", user_id="u1", model_override="llama", categories=categories
    )
    return cap.prompt, data


def test_email_prompt_contains_comma_joined_slugs(monkeypatch):
    prompt, data = _run(monkeypatch, ["school", "receipts", "bills_finance", "other"])
    # comma-joined enumeration, NOT a python-list repr
    assert (
        "school, bills_finance, receipts" in prompt or "bills_finance, receipts, school" in prompt
    )
    assert "[['" not in prompt and "']" not in prompt
    # 'other' is not enumerated (only appears in the "or \"other\"" clause)
    assert "one of [" in prompt
    assert prompt.count("other") == 1  # only the "or other" clause
    assert '"category"' in prompt
    # the model's proposed custom slug flows back
    assert data["category"] == "receipts"


def test_non_email_prompt_unchanged(monkeypatch):
    prompt, _ = _run(monkeypatch, None)
    # byte-for-byte the base template rendered — no category clause
    assert '"category"' not in prompt
    assert prompt == sp._PROCESS_PROMPT_TEMPLATE.format(title="Title", content="Body")


def test_email_prompt_formats_without_brace_error(monkeypatch):
    # Guards the .format() literal-brace hazard: the escaped {{...}} example +
    # {slugs} placeholder must render without KeyError/ValueError.
    prompt, _ = _run(monkeypatch, ["school", "other"])
    assert "{slugs}" not in prompt and "{title}" not in prompt and "{content}" not in prompt
    assert "Fairview Primary" in prompt  # the escaped {{...}} example survived
    assert '"category":"school"' in prompt  # example carries the category key
