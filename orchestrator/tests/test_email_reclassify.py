# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Reclassify guarantees (LUM-629).

The two properties an implementation must never regress: reclassify is (1)
LLM-free (no cloud call on email content) and (2) subject-only (headers/sender
are transient, so it feeds the matcher empty headers + empty sender).
"""

from __future__ import annotations

import pytest
from models.email_taxonomy import CategoryPatchItem
from models.email_taxonomy import RuleItem
from models.email_taxonomy import RuleMatch
from models.email_taxonomy import TaxonomyPatch
from tests.test_email_taxonomy import FakeStore

import config
from services import email_taxonomy as et


@pytest.fixture
def store():
    s = FakeStore()
    config._instances["metadata_store"] = s
    config.invalidate_email_taxonomy_cache()
    yield s
    config._instances.pop("metadata_store", None)
    config.invalidate_email_taxonomy_cache()


def test_reclassify_never_calls_the_llm(store, monkeypatch):
    # If reclassify ever reached the LLM path, this would blow up.
    import services.signal_processor as sp

    def _boom(*a, **k):  # pragma: no cover - must never run
        raise AssertionError("reclassify must not call the LLM")

    monkeypatch.setattr(sp, "_call_llm", _boom)

    et.put_taxonomy(
        "u1", TaxonomyPatch(categories=[CategoryPatchItem(slug="receipts", label="Receipts")])
    )
    et.put_rules(
        "u1",
        [RuleItem(rule_id="r1", match=RuleMatch(subject_contains=["order"]), category="receipts")],
    )
    store.add_signal("u1", "s1", "src1", "Your order shipped", "other")
    result = et.reclassify("u1", "other")
    assert result["moved"] == 1


def test_reclassify_is_subject_only(store, monkeypatch):
    # The matcher must be handed empty headers + empty sender (transient inputs).
    seen = {}

    real = et.match_rule

    def _spy(match, *, title, headers, sender, body=""):
        seen["headers"], seen["sender"], seen["body"] = headers, sender, body
        return real(match, title=title, headers=headers, sender=sender, body=body)

    monkeypatch.setattr(et, "match_rule", _spy)

    et.put_taxonomy(
        "u1", TaxonomyPatch(categories=[CategoryPatchItem(slug="receipts", label="Receipts")])
    )
    et.put_rules(
        "u1",
        [RuleItem(rule_id="r1", match=RuleMatch(subject_contains=["order"]), category="receipts")],
    )
    store.add_signal("u1", "s1", "src1", "Your order shipped", "other")
    et.reclassify("u1", "other")
    assert seen["headers"] == {}
    assert seen["sender"] == ""
    assert seen["body"] == ""  # body rules never reclassify (LUM-630)


def test_reclassify_ignores_non_imap_signals(store):
    # A non-imap signal in the same category is not touched (imap-scoped join).
    store.add_signal("u1", "s1", "src-web", "Your order shipped", "other", imap=False)
    et.put_taxonomy(
        "u1", TaxonomyPatch(categories=[CategoryPatchItem(slug="receipts", label="Receipts")])
    )
    et.put_rules(
        "u1",
        [RuleItem(rule_id="r1", match=RuleMatch(subject_contains=["order"]), category="receipts")],
    )
    result = et.reclassify("u1", "other")
    assert result["scanned"] == 0
    assert store.signals[0]["category"] == "other"
