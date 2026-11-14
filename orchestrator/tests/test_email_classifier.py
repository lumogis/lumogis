# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for services/email_classifier.py (LUM-170).

Covers the fixed taxonomy, deterministic rules, and — the headline privacy
boundary — the on-box-local model guard (cloud OR remote-Ollama must NOT pass).
"""

from __future__ import annotations

from services import email_classifier as ec


def test_taxonomy_is_the_fixed_set():
    assert ec.EMAIL_CATEGORIES == (
        "school",
        "insurance_renewal",
        "bills_finance",
        "family_personal",
        "promotional_bulk",
        "other",
    )


def test_coerce_category_maps_unknown_to_other():
    assert ec.coerce_category("SCHOOL") == "school"
    assert ec.coerce_category("nonsense") == "other"
    assert ec.coerce_category(None) == "other"
    assert ec.coerce_category(123) == "other"


def test_rules_promotional_from_list_unsubscribe():
    cat = ec.classify_rules(
        title="Mega Sale",
        headers={"List-Unsubscribe": "<mailto:x>"},
        sender="promo@shop.com",
        known_contact=False,
    )
    assert cat == "promotional_bulk"


def test_rules_known_contact_bulk_header_not_promotional():
    # A known contact who happens to include List-Unsubscribe is not bulk.
    cat = ec.classify_rules(
        title="Hi",
        headers={"List-Unsubscribe": "<mailto:x>"},
        sender="mum@family.com",
        known_contact=True,
    )
    assert cat == "family_personal"


def test_rules_keyword_hints():
    assert (
        ec.classify_rules(
            title="Your insurance renewal", headers={}, sender="a@b.com", known_contact=False
        )
        == "insurance_renewal"
    )
    assert (
        ec.classify_rules(
            title="Invoice #42 overdue", headers={}, sender="a@b.com", known_contact=False
        )
        == "bills_finance"
    )
    assert (
        ec.classify_rules(title="random", headers={}, sender="a@b.com", known_contact=False) is None
    )


def test_finalize_bulk_deflates_importance():
    r = ec.finalize(
        llm_data={"importance_score": 0.9},
        title="Sale",
        headers={"List-Unsubscribe": "<x>"},
        sender="promo@shop.com",
        known_contact=False,
        threshold=0.4,
    )
    assert r.category == "promotional_bulk"
    assert r.importance_score <= 0.15
    assert r.is_important is False


def test_finalize_known_contact_boosts_importance():
    r = ec.finalize(
        llm_data={},
        title="dinner?",
        headers={},
        sender="mum@family.com",
        known_contact=True,
        threshold=0.4,
    )
    assert r.category == "family_personal"
    assert r.importance_score >= 0.6
    assert r.is_important is True


def test_finalize_uses_llm_category_when_no_rule(monkeypatch):
    r = ec.finalize(
        llm_data={"category": "school", "importance_score": 0.5},
        title="a note",
        headers={},
        sender="x@y.com",
        known_contact=False,
        threshold=0.4,
    )
    assert r.category == "school"


# --- The hard privacy boundary: on-box-local model guard --------------------


def _models(monkeypatch, models: dict, tasks: dict):
    """Patch the model registry and the ``tasks:`` table (LUM-877 seams)."""
    import config

    monkeypatch.setattr(config, "_models_config", models)
    monkeypatch.setattr(config, "_tasks_config", tasks)
    monkeypatch.setattr(config, "_dynamic_ollama_models", lambda: {})


_LLAMA = {"adapter": "openai", "model": "llama3.2:3b", "base_url": "http://ollama:11434/v1"}


def test_require_local_model_allows_on_box(monkeypatch):
    _models(monkeypatch, {"llama": _LLAMA}, {"email_classify": ["llama"]})
    assert ec.require_local_model("u1", task="email_classify") == "llama"


def test_require_local_model_blocks_cloud(monkeypatch):
    # A cloud model listed for an email task is skipped (not_on_box); the on-box
    # code default then serves. The cloud model is never returned.
    cloud = {"adapter": "anthropic", "model": "claude", "api_key_env": "ANTHROPIC_API_KEY"}
    _models(monkeypatch, {"llama": _LLAMA, "claude": cloud}, {"email_classify": ["claude"]})
    assert ec.require_local_model("u1", task="email_classify") == "llama"


def test_require_local_model_blocks_cloud_default(monkeypatch):
    # The email boundary also refuses an off-box CODE DEFAULT (today's rule).
    cloud = {"adapter": "anthropic", "model": "claude", "api_key_env": "ANTHROPIC_API_KEY"}
    _models(monkeypatch, {"llama": cloud}, {})
    assert ec.require_local_model("u1", task="email_classify") is None


def test_require_local_model_blocks_remote_ollama(monkeypatch):
    # "ollama" is in the base_url (config.is_local_model passes) but the host is
    # off-box/public.
    remote = {
        "adapter": "openai",
        "model": "x",
        "base_url": "http://ollama.example.com:11434/v1",
    }
    _models(monkeypatch, {"llama": remote}, {})
    monkeypatch.setattr("services.outbound_http_url._resolved_ips", lambda host: ["93.184.216.34"])
    assert ec.require_local_model("u1", task="email_classify") is None


def test_require_local_model_allowlist_overrides(monkeypatch):
    lan = {"adapter": "openai", "model": "x", "base_url": "http://gpu.lan:11434/ollama/v1"}
    _models(monkeypatch, {"llama": _LLAMA, "ollama-lan": lan}, {"email_classify": ["ollama-lan"]})
    monkeypatch.setenv("EMAIL_LLM_ALLOWLIST", "gpu.lan")
    assert ec.require_local_model("u1", task="email_classify") == "ollama-lan"


# --- LUM-629: dynamic taxonomy + rules precedence + importance cap ----------


def test_user_rule_beats_builtin_keyword():
    tax = frozenset({"school", "bills_finance", "receipts", "other"})
    rules = ({"category": "receipts", "match": {"subject_contains": ["invoice"]}},)
    cat = ec.classify_rules(
        title="Invoice #42",
        headers={},
        sender="a@b.com",
        known_contact=False,
        taxonomy=tax,
        user_rules=rules,
    )
    assert cat == "receipts"


def test_builtin_hint_skipped_when_category_disabled():
    tax = frozenset({"school", "other"})
    assert (
        ec.classify_rules(
            title="Your insurance renewal",
            headers={},
            sender="a@b.com",
            known_contact=False,
            taxonomy=tax,
        )
        is None
    )


def test_coerce_against_custom_taxonomy():
    tax = frozenset({"receipts", "other"})
    assert ec.coerce_category("receipts", tax) == "receipts"
    assert ec.coerce_category("school", tax) == "other"


def test_importance_cap_decoupled_from_slug():
    tax = frozenset({"newsletters", "promotional_bulk", "other"})
    rules = ({"category": "newsletters", "match": {"subject_contains": ["deal"]}},)
    r = ec.finalize(
        llm_data={"importance_score": 0.9},
        title="Big deal inside",
        headers={"List-Unsubscribe": "<mailto:x>"},
        sender="promo@shop.com",
        known_contact=False,
        threshold=0.5,
        taxonomy=tax,
        user_rules=rules,
    )
    assert r.category == "newsletters"
    assert r.importance_score <= 0.15
    assert r.is_important is False


def test_finalize_defaults_preserve_lum170_behaviour():
    r = ec.finalize(
        llm_data={"importance_score": 0.7, "category": "school"},
        title="PTA meeting",
        headers={},
        sender="teacher@school.edu",
        known_contact=False,
        threshold=0.5,
    )
    assert r.category == "school"


def test_classify_rules_body_keyword():
    tax = frozenset({"school", "receipts", "other"})
    rules = ({"category": "receipts", "match": {"body_contains": ["your order"]}},)
    # body present → body rule fires
    assert (
        ec.classify_rules(
            title="Shipping update",
            headers={},
            sender="a@b.com",
            known_contact=False,
            taxonomy=tax,
            user_rules=rules,
            body="Your order has shipped",
        )
        == "receipts"
    )
    # body empty (metadata/reclassify) → body rule inert, no match
    assert (
        ec.classify_rules(
            title="Shipping update",
            headers={},
            sender="a@b.com",
            known_contact=False,
            taxonomy=tax,
            user_rules=rules,
            body="",
        )
        is None
    )
