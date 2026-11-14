# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for services/email_taxonomy.py (LUM-629).

Covers the sparse-overlay store, the deterministic matcher, validation, the
per-label reclassify (keep-current on no-match), and the disable/delete sweep.
Uses a functional in-memory fake MetadataStore that understands the specific
queries the service issues (the real Postgres path is exercised in CI compose).
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from models.email_taxonomy import CategoryPatchItem
from models.email_taxonomy import RuleItem
from models.email_taxonomy import RuleMatch
from models.email_taxonomy import TaxonomyPatch

import config
from services import email_taxonomy as et


class FakeStore:
    """In-memory store keyed by (user_id, slug) / (user_id, rule_id) + signals."""

    def __init__(self):
        self.tax: dict[tuple[str, str], dict] = {}
        self.rules: dict[tuple[str, str], dict] = {}
        self.signals: list[dict] = []
        self.imap_sources: set[tuple[str, str]] = set()
        self.fail_next_sweep = False
        self._in_txn = False
        self._txn_snapshot = None

    # -- helpers -------------------------------------------------------------
    def add_signal(self, user_id, signal_id, source_id, title, category, imap=True):
        self.signals.append(
            {
                "signal_id": signal_id,
                "user_id": user_id,
                "source_id": source_id,
                "title": title,
                "category": category,
            }
        )
        if imap:
            self.imap_sources.add((user_id, source_id))

    def _is_imap(self, row):
        return (row["user_id"], row["source_id"]) in self.imap_sources

    # -- MetadataStore surface ----------------------------------------------
    def ping(self) -> bool:
        return True

    def close(self) -> None:
        pass

    def fetch_one(self, query: str, params: tuple | None = None) -> dict | None:
        return None

    def fetch_all(self, query: str, params: tuple | None = None) -> list[dict]:
        q = query.lower()
        p = params or ()
        if "from email_category_taxonomy" in q:
            uid = p[0]
            return [
                {"slug": s, "label": v["label"], "enabled": v["enabled"], "sort": v["sort"]}
                for (u, s), v in self.tax.items()
                if u == uid
            ]
        if "from email_classification_rule" in q:
            uid = p[0]
            rows = [{"rule_id": rid, **v} for (u, rid), v in self.rules.items() if u == uid]
            rows.sort(key=lambda r: (r["priority"], r["rule_id"]))
            return rows
        if "from signals" in q:
            uid, slug = p[0], p[1]
            return [
                {"signal_id": r["signal_id"], "title": r["title"]}
                for r in self.signals
                if r["user_id"] == uid and r["category"] == slug and self._is_imap(r)
            ]
        return []

    def execute(self, query: str, params: tuple | None = None) -> None:
        q = query.lower()
        p = params or ()
        if "insert into email_category_taxonomy" in q:
            uid, slug, label, enabled, sort = p
            self.tax[(uid, slug)] = {"label": label, "enabled": enabled, "sort": sort}
        elif "delete from email_category_taxonomy" in q:
            self.tax.pop((p[0], p[1]), None)
        elif "insert into email_classification_rule" in q:
            uid, rid, prio, match, cat, en = p
            import json

            self.rules[(uid, rid)] = {
                "priority": prio,
                "match": json.loads(match) if isinstance(match, str) else match,
                "category": cat,
                "enabled": en,
            }
        elif "delete from email_classification_rule" in q:
            uid = p[0]
            for k in [k for k in self.rules if k[0] == uid]:
                del self.rules[k]
        elif "update signals set category = 'other'" in q:
            if self.fail_next_sweep:
                raise RuntimeError("simulated sweep failure")
            uid, slug = p[0], p[1]
            for r in self.signals:
                if r["user_id"] == uid and r["category"] == slug and self._is_imap(r):
                    r["category"] = "other"
        elif "update signals set category = %s" in q:
            target, uid, ids = p
            for r in self.signals:
                if r["user_id"] == uid and r["signal_id"] in ids:
                    r["category"] = target

    @contextmanager
    def transaction(self):
        # Snapshot for rollback on failure (mirrors autocommit-off semantics).
        import copy

        snap = (copy.deepcopy(self.tax), copy.deepcopy([dict(s) for s in self.signals]))
        try:
            yield
        except Exception:
            self.tax, self.signals = snap[0], snap[1]
            raise


@pytest.fixture
def store(monkeypatch):
    s = FakeStore()
    config._instances["metadata_store"] = s
    config.invalidate_email_taxonomy_cache()
    yield s
    config._instances.pop("metadata_store", None)
    config.invalidate_email_taxonomy_cache()


# --------------------------------------------------------------------------- #
# Read path
# --------------------------------------------------------------------------- #
def test_defaults_when_no_rows(store):
    cats = et.load_taxonomy("u1")
    slugs = [c["slug"] for c in cats]
    assert slugs == list(et._DEFAULT_SLUGS)  # six defaults, other last
    assert cats[-1]["slug"] == "other"
    assert cats[-1]["is_protected"] is True
    assert all(c["enabled"] for c in cats)
    assert et.enabled_slugs("u1") == frozenset(et._DEFAULT_SLUGS)


def test_rename_default_keeps_slug(store):
    et.put_taxonomy(
        "u1", TaxonomyPatch(categories=[CategoryPatchItem(slug="school", label="Kids")])
    )
    cats = {c["slug"]: c for c in et.load_taxonomy("u1")}
    assert cats["school"]["label"] == "Kids"
    assert cats["school"]["is_custom"] is False
    assert "school" in et.enabled_slugs("u1")


def test_disable_default_excluded_and_swept(store):
    store.add_signal("u1", "s1", "src1", "Homework due", "school")
    et.put_taxonomy(
        "u1", TaxonomyPatch(categories=[CategoryPatchItem(slug="school", enabled=False)])
    )
    assert "school" not in et.enabled_slugs("u1")
    # Orphan sweep moved the signal to other.
    assert store.signals[0]["category"] == "other"


def test_custom_add(store):
    et.put_taxonomy(
        "u1",
        TaxonomyPatch(categories=[CategoryPatchItem(slug="receipts", label="Receipts", sort=10)]),
    )
    cats = {c["slug"]: c for c in et.load_taxonomy("u1")}
    assert cats["receipts"]["is_custom"] is True
    assert "receipts" in et.enabled_slugs("u1")


def test_other_always_enabled_floor(store):
    # Even a stored disabled 'other' row cannot remove it from enabled_slugs.
    store.tax[("u1", "other")] = {"label": "Other", "enabled": False, "sort": 0}
    config.invalidate_email_taxonomy_cache()
    assert "other" in et.enabled_slugs("u1")


# --------------------------------------------------------------------------- #
# label_map (LUM-632) — slug->label for cross-user resolution
# --------------------------------------------------------------------------- #
def test_label_map_defaults(store):
    m = et.label_map("u1")
    # Every default slug present with its default label.
    for slug, label in et.DEFAULT_SEED:
        assert m[slug] == label


def test_label_map_custom_and_renamed(store):
    et.put_taxonomy(
        "u1",
        TaxonomyPatch(
            categories=[
                CategoryPatchItem(slug="a_landlord", label="Landlord", sort=10),
                CategoryPatchItem(slug="school", label="Kids"),
            ]
        ),
    )
    m = et.label_map("u1")
    assert m["a_landlord"] == "Landlord"  # custom
    assert m["school"] == "Kids"  # renamed default keeps slug


def test_label_map_disabled_default_still_resolves(store):
    # A since-disabled default must still resolve to its label (defaults are
    # always present in the effective taxonomy).
    store.add_signal("u1", "s1", "src1", "Homework", "school")
    et.put_taxonomy(
        "u1", TaxonomyPatch(categories=[CategoryPatchItem(slug="school", enabled=False)])
    )
    m = et.label_map("u1")
    assert m["school"] == "School"


# --------------------------------------------------------------------------- #
# Validation (422)
# --------------------------------------------------------------------------- #
def _put_raises(store, patch, code):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        et.put_taxonomy("u1", patch)
    assert exc.value.detail["code"] == code


def test_protected_other_cannot_be_renamed(store):
    _put_raises(
        store,
        TaxonomyPatch(categories=[CategoryPatchItem(slug="other", label="Junk")]),
        "protected_category",
    )


def test_protected_other_cannot_be_disabled(store):
    _put_raises(
        store,
        TaxonomyPatch(categories=[CategoryPatchItem(slug="other", enabled=False)]),
        "protected_category",
    )


def test_bad_slug_charset(store):
    _put_raises(
        store,
        TaxonomyPatch(categories=[CategoryPatchItem(slug="Bad Slug!", label="x")]),
        "bad_slug",
    )


def test_custom_needs_label(store):
    _put_raises(
        store, TaxonomyPatch(categories=[CategoryPatchItem(slug="newcat")]), "label_required"
    )


def test_empty_label(store):
    _put_raises(
        store,
        TaxonomyPatch(categories=[CategoryPatchItem(slug="receipts", label="   ")]),
        "empty_label",
    )


def test_duplicate_slug_in_patch(store):
    patch = TaxonomyPatch(
        categories=[
            CategoryPatchItem(slug="receipts", label="A"),
            CategoryPatchItem(slug="receipts", label="B"),
        ]
    )
    _put_raises(store, patch, "duplicate_slug")


def test_cannot_delete_default(store):
    _put_raises(store, TaxonomyPatch(deletes=["school"]), "cannot_delete_default")


# --------------------------------------------------------------------------- #
# Rules
# --------------------------------------------------------------------------- #
def test_put_rules_and_category_disabled_flag(store):
    et.put_taxonomy(
        "u1", TaxonomyPatch(categories=[CategoryPatchItem(slug="receipts", label="Receipts")])
    )
    et.put_rules(
        "u1",
        [
            RuleItem(
                rule_id="r1",
                priority=10,
                match=RuleMatch(subject_contains=["receipt"]),
                category="receipts",
            )
        ],
    )
    rules = et.list_rules("u1")
    assert rules[0]["category_disabled"] is False
    # Disable the category → the rule becomes dead but is still listed & flagged.
    et.put_taxonomy(
        "u1", TaxonomyPatch(categories=[CategoryPatchItem(slug="receipts", enabled=False)])
    )
    assert et.list_rules("u1")[0]["category_disabled"] is True
    assert et.load_rules("u1") == []  # dead rule skipped on classify path


def test_dead_rule_can_be_resubmitted(store):
    # A rule whose category got disabled must still be re-savable (web does a
    # full-replace PUT) — regression for the enabled_slugs-too-strict bug.
    et.put_taxonomy(
        "u1", TaxonomyPatch(categories=[CategoryPatchItem(slug="receipts", label="Receipts")])
    )
    et.put_rules(
        "u1", [RuleItem(rule_id="r1", match=RuleMatch(subject_contains=["x"]), category="receipts")]
    )
    et.put_taxonomy(
        "u1", TaxonomyPatch(categories=[CategoryPatchItem(slug="receipts", enabled=False)])
    )
    # Re-PUT the same (now dead) rule + a new one — must NOT 422.
    et.put_rules(
        "u1",
        [
            RuleItem(rule_id="r1", match=RuleMatch(subject_contains=["x"]), category="receipts"),
            RuleItem(rule_id="r2", match=RuleMatch(subject_contains=["y"]), category="school"),
        ],
    )
    rules = {r["rule_id"]: r for r in et.list_rules("u1")}
    assert rules["r1"]["category_disabled"] is True
    assert rules["r2"]["category_disabled"] is False


def test_rule_unknown_category_rejected(store):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        et.put_rules(
            "u1", [RuleItem(rule_id="r1", match=RuleMatch(subject_contains=["x"]), category="nope")]
        )
    assert exc.value.detail["code"] == "rule_unknown_category"


def test_empty_match_rejected(store):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        et.put_rules("u1", [RuleItem(rule_id="r1", match=RuleMatch(), category="other")])
    assert exc.value.detail["code"] == "rule_empty_match"


# --------------------------------------------------------------------------- #
# Matcher
# --------------------------------------------------------------------------- #
def test_match_rule_fields():
    assert et.match_rule(
        {"subject_contains": ["ReCeIpT"]}, title="Your Receipt", headers={}, sender=""
    )
    assert et.match_rule({"sender": ["a@b.com"]}, title="", headers={}, sender="A@B.com")
    assert et.match_rule(
        {"sender_domain": ["amazon.com"]}, title="", headers={}, sender="x@mail.amazon.com"
    )
    # AND across fields: subject present but no match → False.
    assert not et.match_rule(
        {"subject_contains": ["nope"], "sender": ["a@b.com"]},
        title="hi",
        headers={},
        sender="a@b.com",
    )
    # bulk_header cannot fire without headers (reclassify context).
    assert not et.match_rule({"bulk_header": True}, title="x", headers={}, sender="")
    # empty match never matches.
    assert not et.match_rule({}, title="x", headers={}, sender="a@b.com")


def test_match_rule_body_contains():
    m = {"body_contains": ["field trip"]}
    # matches when the body is present (full-ingest)...
    assert et.match_rule(
        m, title="Notice", headers={}, sender="", body="Reminder: the FIELD TRIP is Friday"
    )
    # ...and is inert when the body is empty (metadata / HTML-only / reclassify).
    assert not et.match_rule(m, title="Notice", headers={}, sender="", body="")
    # AND with subject: both must hold.
    m2 = {"body_contains": ["invoice"], "subject_contains": ["acme"]}
    assert et.match_rule(m2, title="ACME statement", headers={}, sender="", body="invoice #5")
    assert not et.match_rule(m2, title="other", headers={}, sender="", body="invoice #5")


def test_reclassify_competing_subject_rule_recaptures(store):
    # A signal in 'school' (as if a body rule routed it at ingest) is recaptured
    # by a subject rule at reclassify — documents the accepted subject-only
    # inversion (moves to a valid slug, not corruption).
    et.put_rules(
        "u1",
        [
            RuleItem(
                rule_id="s1",
                priority=50,
                match=RuleMatch(subject_contains=["invoice"]),
                category="bills_finance",
            )
        ],
    )
    store.add_signal("u1", "sig1", "src1", "Invoice for term fees", "school")
    result = et.reclassify("u1", "school")
    assert result["moved"] == 1
    assert store.signals[0]["category"] == "bills_finance"


# --------------------------------------------------------------------------- #
# Reclassify
# --------------------------------------------------------------------------- #
def test_reclassify_no_match_keeps_slug(store):
    # A signal whose subject matches nothing stays put — NOT moved to other.
    store.add_signal("u1", "s1", "src1", "zzz unmatched subject", "school")
    result = et.reclassify("u1", "school")
    assert result == {"slug": "school", "scanned": 1, "moved": 0, "failed": 0}
    assert store.signals[0]["category"] == "school"


def test_reclassify_moves_on_subject_rule(store):
    et.put_taxonomy(
        "u1", TaxonomyPatch(categories=[CategoryPatchItem(slug="receipts", label="Receipts")])
    )
    et.put_rules(
        "u1",
        [
            RuleItem(
                rule_id="r1",
                priority=10,
                match=RuleMatch(subject_contains=["your order"]),
                category="receipts",
            )
        ],
    )
    store.add_signal("u1", "s1", "src1", "Your order has shipped", "other")
    result = et.reclassify("u1", "other")
    assert result["moved"] == 1
    assert store.signals[0]["category"] == "receipts"


def test_reclassify_unknown_slug_404(store):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        et.reclassify("u1", "does_not_exist")
    assert exc.value.status_code == 404


def test_sweep_transaction_rolls_back_on_failure(store):
    # If the sweep UPDATE fails after the taxonomy write, neither is committed.
    store.add_signal("u1", "s1", "src1", "Homework", "school")
    store.fail_next_sweep = True
    from fastapi import HTTPException

    with pytest.raises((RuntimeError, HTTPException)):
        et.put_taxonomy(
            "u1", TaxonomyPatch(categories=[CategoryPatchItem(slug="school", enabled=False)])
        )
    config.invalidate_email_taxonomy_cache()
    # Rolled back: category still enabled AND signal not swept.
    assert "school" in et.enabled_slugs("u1")
    assert store.signals[0]["category"] == "school"


def test_cross_user_isolation(store):
    et.put_taxonomy(
        "u1", TaxonomyPatch(categories=[CategoryPatchItem(slug="receipts", label="Receipts")])
    )
    # u2 sees only defaults; u1's custom category is invisible.
    u2_slugs = [c["slug"] for c in et.load_taxonomy("u2")]
    assert "receipts" not in u2_slugs
    assert "receipts" in [c["slug"] for c in et.load_taxonomy("u1")]
