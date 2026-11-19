# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""``services/entity_name_rules.py`` — per-member kind-correction rules (LUM-967).

A fake store executes the three ``record_retype`` statements' *semantics* on a
dict, so the no-chain invariant is proven against the rules the SQL encodes. The
SQL itself runs against real Postgres in ``integration/test_entity_kind_live.py``.
"""

from __future__ import annotations

import logging
import random
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
from models.entities import ExtractedEntity

import config as _config
from services import entities
from services import entity_merge
from services import entity_name_rules as enr

USER = "alice"
KINDS = ("PERSON", "ORG", "PROJECT", "CONCEPT")


class RecordingStore:
    """Records statements; ``fetch_all`` answers from ``rules``."""

    def __init__(self, rules=None):
        self.calls: list[tuple[str, str, tuple]] = []
        self.rules = rules or []
        self.in_transaction = False

    def transaction(self):  # pragma: no cover - must never be used by record_retype
        raise AssertionError("record_retype must not open its own transaction")

    def execute(self, sql, params=None):
        self.calls.append(("execute", " ".join(sql.split()), params))

    def fetch_all(self, sql, params=None):
        self.calls.append(("fetch_all", " ".join(sql.split()), params))
        return list(self.rules)


class DictRules:
    """Executes the rule statements' *semantics* on a dict of rows.

    Rows are ``{(key, from_type): {action, to, restore, evidence, display}}``.
    Raises on any statement it does not model, so a changed statement in
    ``entity_name_rules`` cannot slip past the invariant tests (LUM-966).
    """

    def __init__(self):
        self.rows: dict[tuple[str, str], dict] = {}
        self.step_1a = 0
        self.step_2b = 0

    # -- convenience views ------------------------------------------------
    @property
    def rules(self) -> dict[tuple[str, str], str]:
        """``{(key, from): to}`` for retypes only (the LUM-967 view)."""
        return {k: r["to"] for k, r in self.rows.items() if r["action"] == "retype"}

    def _row(self, action, to=None, restore=None, evidence=(), display=None):
        return {
            "action": action,
            "to": to,
            "restore": restore,
            "evidence": list(evidence),
            "display": display,
        }

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        if s.startswith("DELETE FROM entity_name_rules"):
            _uid, key, frm = params
            self.rows.pop((key, frm), None)
        elif s.startswith("UPDATE entity_name_rules SET action = 'retype'"):
            to, _uid, key, restore = params
            for k, r in list(self.rows.items()):
                if k[0] == key and r["action"] == "block" and r["restore"] == restore:
                    self.rows[k] = self._row("retype", to=to, display=r["display"])
                    if "NULL, document_evidence" not in s:  # remove_block clears it first
                        # record_retype: 1a waits for the new target (restore == to),
                        # 2b waited for the old source kind (restore == from != to).
                        if restore == to:
                            self.step_1a += 1
                        else:
                            self.step_2b += 1
        elif s.startswith("UPDATE entity_name_rules SET to_type"):
            to, _src, _uid, key, old_to = params
            for k, r in list(self.rows.items()):
                if k[0] == key and r["action"] == "retype" and r["to"] == old_to:
                    r["to"] = to
        elif s.startswith("UPDATE entity_name_rules SET action = 'block'"):
            kind, display, evidence, _src, _uid, key, to = params
            for k, r in list(self.rows.items()):
                if k[0] == key and r["action"] == "retype" and r["to"] == to:
                    self.rows[k] = self._row(
                        "block", restore=kind, evidence=evidence, display=display
                    )
        elif s.startswith("INSERT INTO entity_name_rules") and "'retype'" in s:
            _uid, key, frm, to, _src, display = params
            self.rows[(key, frm)] = self._row("retype", to=to, display=display)
        elif s.startswith("INSERT INTO entity_name_rules") and "'block'" in s:
            _uid, key, kind, _src, display, evidence = params
            old = self.rows.get((key, kind))
            merged = list(dict.fromkeys(list(evidence) + (old["evidence"] if old else [])))
            self.rows[(key, kind)] = self._row("block", evidence=merged[:500], display=display)
        else:  # pragma: no cover
            raise AssertionError(s)

    def fetch_one(self, sql, params=None):
        s = " ".join(sql.split())
        if s.startswith("SELECT display_name, document_evidence"):
            _uid, key, kind = params
            r = self.rows.get((key, kind))
            if r and r["action"] == "block" and r["restore"] is None:
                return {"display_name": r["display"], "document_evidence": r["evidence"]}
            return None
        raise AssertionError(s)  # pragma: no cover

    def fetch_all(self, sql, params=None):
        s = " ".join(sql.split())
        if s.startswith("SELECT from_type, document_evidence"):
            _uid, key, kind = params
            return [
                {"from_type": k[1], "document_evidence": r["evidence"]}
                for k, r in sorted(self.rows.items())
                if k[0] == key and r["action"] == "block" and r["restore"] == kind
            ]
        raise AssertionError(s)  # pragma: no cover


def _step(ms: DictRules, x: str, y: str, name="Meier Pharmacy") -> None:
    enr.record_retype(ms, user_id=USER, name=name, from_type=x, to_type=y, source_entity_id="e1")


def _block(ms: DictRules, kind: str, name="Meier Pharmacy", evidence=("a.pdf",)) -> None:
    """What ``delete_entry`` does for one name: skip a retype starting at the kind."""
    key = enr.name_key(name)
    if ms.rows.get((key, kind), {}).get("action") == "retype":
        return
    enr.record_block(
        ms,
        user_id=USER,
        keys_and_names=[(key, name)],
        entity_type=kind,
        source_entity_id="e1",
        document_evidence=list(evidence),
    )


def _assert_no_chain(rules, latest):
    froms = {k[1] for k in rules}
    for (key, frm), to in rules.items():
        assert frm != to
        assert to not in {f for (k2, f) in rules if k2 == key}, f"chain at {key}: {rules}"
        assert frm != latest, f"a rule starts from the latest choice: {rules}"
    assert froms or not rules


def _assert_invariants(ms: DictRules) -> None:
    """The three invariants of ``entity_name_rules`` (module docstring)."""
    for (key, frm), r in ms.rows.items():
        starts = {f for (k2, f) in ms.rows if k2 == key}
        if r["action"] == "retype":
            # 1 + 2: a retype never points at any rule's from_type (so not at a block).
            assert r["to"] not in starts, f"retype points at a rule start: {ms.rows}"
            assert r["to"] != frm
        else:
            assert r["to"] is None
            if r["restore"] is not None:
                assert r["restore"] != frm
                main = ms.rows.get((key, r["restore"]))
                # 3: a converted block has its main block.
                assert main and main["action"] == "block" and main["restore"] is None, (
                    f"converted block without main block: {ms.rows}"
                )


# ── name_key ─────────────────────────────────────────────────────────────────


def test_name_key_collapses_and_lowers():
    assert enr.name_key("  Meier\tPharmacy ") == "meier pharmacy"
    assert enr.name_key("a\x00b‮ c") == "a\x00b‮ c"  # control chars do not raise


def test_name_key_golden_values():
    # A stable contract: every stored rule depends on these.
    assert enr.name_key("Müller GmbH") == "müller gmbh"
    assert enr.name_key("Müller GmbH") == "müller gmbh"  # decomposed → composed
    assert enr.name_key("ÉCOLE  Polytechnique") == "école polytechnique"
    assert enr.name_key("Straße") == "straße"  # str.lower, not casefold
    assert enr.name_key("   ") == ""


# ── record_retype ────────────────────────────────────────────────────────────


def test_record_retype_statement_order_and_params():
    ms = RecordingStore()
    enr.record_retype(
        ms,
        user_id=USER,
        name=" Meier  Pharmacy",
        from_type="person",
        to_type="org",
        source_entity_id="e1",
    )
    kinds = [c[1].split(" ")[0] for c in ms.calls]
    # 1a (converted blocks waiting for Y), 1b (lift a block on Y), 2a, 2b, 3.
    assert kinds == ["UPDATE", "DELETE", "UPDATE", "UPDATE", "INSERT"]
    assert ms.calls[0][2] == ("ORG", USER, "meier pharmacy", "ORG")
    assert "restore_to_type = %s" in ms.calls[0][1]
    assert ms.calls[1][2] == (USER, "meier pharmacy", "ORG")
    assert ms.calls[2][2] == ("ORG", "e1", USER, "meier pharmacy", "PERSON")
    assert ms.calls[3][2] == ("ORG", USER, "meier pharmacy", "PERSON")
    assert ms.calls[4][2] == (USER, "meier pharmacy", "PERSON", "ORG", "e1", "Meier Pharmacy")
    # A converted block turned back into a correction drops the delete's evidence
    # (steps 1a and 2b): a later delete must not offer a re-read of documents that
    # never mentioned it.
    assert "document_evidence = '{}'" in ms.calls[0][1]
    assert "document_evidence = '{}'" in ms.calls[3][1]
    assert all("user_id = %s" in c[1] or "user_id," in c[1] for c in ms.calls)
    assert "ON CONFLICT (user_id, name_norm, from_type) DO UPDATE" in ms.calls[4][1]


def test_record_retype_invariant_simulated():
    ms = DictRules()
    k = "meier pharmacy"
    _step(ms, "PERSON", "ORG")
    assert ms.rules == {(k, "PERSON"): "ORG"}
    _step(ms, "ORG", "PERSON")  # change back
    assert ms.rules == {(k, "ORG"): "PERSON"}

    ms = DictRules()
    _step(ms, "PERSON", "PROJECT")
    _step(ms, "PROJECT", "ORG")
    assert ms.rules == {(k, "PERSON"): "ORG", (k, "PROJECT"): "ORG"}

    # Two entries with the same name and different stored kinds: independent rules.
    ms = DictRules()
    _step(ms, "PERSON", "ORG")
    _step(ms, "PROJECT", "CONCEPT")
    assert ms.rules == {(k, "PERSON"): "ORG", (k, "PROJECT"): "CONCEPT"}
    _step(ms, "ORG", "PROJECT")
    assert ms.rules == {(k, "PERSON"): "PROJECT", (k, "ORG"): "PROJECT"}
    _assert_no_chain(ms.rules, "PROJECT")


OPS = ("retype", "block", "remove_block", "lift_blocks")


def _random_op(rng: random.Random, ms: DictRules) -> None:
    name = rng.choice(("Meier Pharmacy", "Total"))
    key = enr.name_key(name)
    x, y = rng.sample(KINDS, 2)
    op = rng.choice(OPS)
    if op == "retype":
        _step(ms, x, y, name=name)
    elif op == "block":
        _block(ms, x, name=name, evidence=(f"{rng.randrange(5)}.pdf",))
    elif op == "remove_block":
        enr.remove_block(ms, user_id=USER, name_norm=key, entity_type=x)
    else:
        enr.lift_blocks(ms, user_id=USER, keys=[key], entity_type=x)


def test_record_retype_invariant_randomised():
    """200 seeded steps of retype / block / remove_block / lift_blocks (LUM-966 U12, U20)."""
    step_1a = step_2b = 0
    for seed in range(967, 977):
        rng = random.Random(seed)
        ms = DictRules()
        for _ in range(200):
            _random_op(rng, ms)
            _assert_invariants(ms)
        step_1a += ms.step_1a
        step_2b += ms.step_2b
    # VERIFY-PLAN: 1a and 2b counted separately; the plan asks for each at least once.
    assert step_1a > 0, "the random walks never exercised step 1a"
    assert step_2b > 0, "the random walks never exercised step 2b"


def test_record_retype_to_a_blocked_kind_lifts_the_block():
    """U10: a correction to Y lifts a block on (name, Y)."""
    ms = DictRules()
    _block(ms, "ORG")
    _step(ms, "PERSON", "ORG")
    assert ms.rows == {("meier pharmacy", "PERSON"): ms.rows[("meier pharmacy", "PERSON")]}
    assert ms.rules == {("meier pharmacy", "PERSON"): "ORG"}


def test_record_retype_revives_converted_blocks():
    """U11: converted blocks waiting for Y (1a) or X (2b) become retypes to Y."""
    k = "meier pharmacy"
    ms = DictRules()
    _step(ms, "PERSON", "ORG")  # PERSON -> ORG
    _block(ms, "ORG", evidence=())  # delete at ORG: PERSON blocked, restore ORG
    assert ms.rows[(k, "PERSON")]["action"] == "block"
    assert ms.rows[(k, "PERSON")]["restore"] == "ORG"
    _step(ms, "PROJECT", "ORG")  # a correction to ORG: lifts the block (1b), revives (1a)
    assert ms.rules == {(k, "PERSON"): "ORG", (k, "PROJECT"): "ORG"}

    ms = DictRules()
    _step(ms, "PERSON", "ORG")
    _block(ms, "ORG", evidence=())
    _step(ms, "ORG", "CONCEPT")  # 3 replaces the block on ORG; 2b revives PERSON -> CONCEPT
    assert ms.rules == {(k, "PERSON"): "CONCEPT", (k, "ORG"): "CONCEPT"}
    _assert_invariants(ms)


# ── record_block / remove_block / lift_blocks ────────────────────────────────


def test_record_block_converts_retypes_pointing_at_the_kind():
    """U6: retype (n, F→K) becomes block (n, F) restore K; F→J (J≠K) untouched."""
    k = "meier pharmacy"
    ms = DictRules()
    _step(ms, "PERSON", "ORG")
    _step(ms, "PROJECT", "CONCEPT")
    _block(ms, "ORG", evidence=("a.pdf",))
    assert ms.rows[(k, "ORG")]["action"] == "block" and ms.rows[(k, "ORG")]["restore"] is None
    assert ms.rows[(k, "PERSON")]["action"] == "block"
    assert ms.rows[(k, "PERSON")]["restore"] == "ORG"
    assert ms.rows[(k, "PROJECT")] == {
        "action": "retype",
        "to": "CONCEPT",
        "restore": None,
        "evidence": [],
        "display": "Meier Pharmacy",
    }
    _assert_invariants(ms)


def test_record_block_sql_is_one_upsert_and_one_conversion_per_key():
    ms = RecordingStore()
    enr.record_block(
        ms,
        user_id=USER,
        keys_and_names=[("a", "A"), ("b", "B")],
        entity_type=" person ",
        source_entity_id="e1",
        document_evidence=["x.pdf", "x.pdf", "y.pdf"],
    )
    assert [c[1].split(" ")[0] for c in ms.calls] == ["INSERT", "UPDATE", "INSERT", "UPDATE"]
    assert ms.calls[0][2] == (USER, "a", "PERSON", "e1", "A", ["x.pdf", "y.pdf"])
    assert "ON CONFLICT (user_id, name_norm, from_type) DO UPDATE" in ms.calls[0][1]
    assert "restore_to_type = NULL" in ms.calls[0][1]  # a converted block becomes a main block
    assert "LIMIT 500" in ms.calls[0][1]


def test_record_block_on_converted_block_makes_it_a_main_block():
    """U15: a delete at K over a converted block (restore J) makes (key, K) a main block."""
    k = "meier pharmacy"
    ms = DictRules()
    _step(ms, "ORG", "PERSON")  # ORG -> PERSON
    _block(ms, "PERSON", evidence=("a.pdf",))  # ORG is now a converted block, restore PERSON
    assert ms.rows[(k, "ORG")]["restore"] == "PERSON"
    _block(ms, "ORG", evidence=("b.pdf",))  # delete at ORG
    assert ms.rows[(k, "ORG")]["restore"] is None
    assert (
        ms.rows[(k, "ORG")]["evidence"] == ["b.pdf"] or "b.pdf" in ms.rows[(k, "ORG")]["evidence"]
    )
    enr.remove_block(ms, user_id=USER, name_norm=k, entity_type="PERSON")
    assert (k, "ORG") in ms.rows and ms.rows[(k, "ORG")]["action"] == "block"
    _assert_invariants(ms)


def test_record_block_unions_evidence_and_caps_it():
    """U7."""
    ms = DictRules()
    _block(ms, "PERSON", evidence=("a.pdf", "b.pdf"))
    _block(ms, "PERSON", evidence=("b.pdf", "c.pdf"))
    assert ms.rows[("meier pharmacy", "PERSON")]["evidence"] == ["b.pdf", "c.pdf", "a.pdf"]
    sql = RecordingStore()
    enr.record_block(
        sql,
        user_id=USER,
        keys_and_names=[("a", "A")],
        entity_type="PERSON",
        source_entity_id="e1",
        document_evidence=[f"{i}.pdf" for i in range(700)],
    )
    assert len(sql.calls[0][2][5]) == 500


def test_remove_block_restores_converted_corrections():
    """U8."""
    k = "meier pharmacy"
    ms = DictRules()
    _step(ms, "PERSON", "ORG")
    _block(ms, "ORG", evidence=("a.pdf",))
    removed = enr.remove_block(ms, user_id=USER, name_norm=k, entity_type="org")
    assert removed is not None
    assert removed.display_name == "Meier Pharmacy"
    assert removed.restored == [("PERSON", "ORG")]
    assert removed.document_evidence == ["a.pdf"]
    assert ms.rules == {(k, "PERSON"): "ORG"}
    assert (k, "ORG") not in ms.rows
    _assert_invariants(ms)


def test_remove_block_missing_rule_is_none():
    """U9, and a converted block is not addressable."""
    ms = DictRules()
    assert enr.remove_block(ms, user_id=USER, name_norm="nope", entity_type="PERSON") is None
    _step(ms, "PERSON", "ORG")
    _block(ms, "ORG")
    assert (
        enr.remove_block(ms, user_id=USER, name_norm="meier pharmacy", entity_type="PERSON") is None
    )


def test_lift_blocks_returns_only_keys_that_had_a_block():
    """U20."""
    k = "meier pharmacy"
    ms = DictRules()
    _step(ms, "ORG", "PERSON")
    _block(ms, "PERSON", evidence=())
    lifted = enr.lift_blocks(ms, user_id=USER, keys=[k, "other", k], entity_type="PERSON")
    assert lifted == [k]
    assert ms.rules == {(k, "ORG"): "PERSON"}


def test_remove_retype_only_deletes_a_retype():
    ms = RecordingStore()
    ms.fetch_one = lambda sql, params=None: (
        ms.calls.append(("fetch_one", " ".join(sql.split()), params)) or {"n": 1}
    )
    assert enr.remove_retype(ms, user_id=USER, name_norm="k", from_type="PERSON") is True
    assert "action = 'retype'" in ms.calls[0][1]


# ── apply_name_rules ─────────────────────────────────────────────────────────


@pytest.fixture
def store():
    ms = RecordingStore(
        rules=[
            {
                "name_norm": "meier pharmacy",
                "from_type": "PERSON",
                "action": "retype",
                "to_type": "ORG",
            },
            {"name_norm": "total", "from_type": "PERSON", "action": "block", "to_type": None},
            {"name_norm": "total", "from_type": "CONCEPT", "action": "block", "to_type": None},
            {"name_norm": "total", "from_type": "ORG", "action": "block", "to_type": None},
        ]
    )
    _config._instances["metadata_store"] = ms
    yield ms
    _config._instances.clear()


def _e(name, kind="PERSON"):
    return ExtractedEntity(name=name, entity_type=kind)


def test_apply_rewrites_matching_name_and_kind(store):
    ents = [_e("Meier  Pharmacy"), _e("Meier Pharmacy", "PROJECT"), _e("Dr Meier")]
    out = enr.apply_name_rules(ents, USER)
    assert out is ents
    assert [e.entity_type for e in ents] == ["ORG", "PROJECT", "PERSON"]


def test_apply_lowercase_emitted_kind_matches(store):
    (e,) = enr.apply_name_rules([_e("meier pharmacy", "person")], USER)
    assert e.entity_type == "ORG"


def test_apply_drops_a_blocked_name_at_its_kind_only():
    """U1."""
    ms = RecordingStore(
        rules=[{"name_norm": "total", "from_type": "PERSON", "action": "block", "to_type": None}]
    )
    _config._instances["metadata_store"] = ms
    try:
        out = enr.apply_name_rules([_e("Total"), _e("Total", "ORG"), _e("Meier")], USER)
    finally:
        _config._instances.clear()
    assert [(e.name, e.entity_type) for e in out] == [("Total", "ORG"), ("Meier", "PERSON")]


def test_apply_block_drop_logs_no_name_or_key_at_any_level(store, caplog):
    """D11 (extraction half): a dropped name is logged as a count only."""
    # VERIFY-PLAN: added — D11 only covered delete_entry's logs.
    with caplog.at_level(logging.DEBUG, logger="services.entity_name_rules"):
        out = enr.apply_name_rules([_e("Total"), _e("TOTAL", "ORG"), _e("Meier")], USER)
    assert [e.name for e in out] == ["Meier"]
    assert caplog.records, "the drop path should log its count"
    assert "total" not in caplog.text.lower()


def test_apply_keeps_the_same_list_when_nothing_is_dropped(store):
    ents = [_e("Meier"), _e("Meier Pharmacy")]
    assert enr.apply_name_rules(ents, USER) is ents


def test_apply_drops_blank_and_padded_kinds_by_kind_key(store):
    """U19: an extractor emitting '' or 'concept ' still hits a block written at CONCEPT."""
    out = enr.apply_name_rules(
        [_e("Total", ""), _e("Total", "concept "), _e("Total", "PERSON ")], USER
    )
    assert out == []
    assert enr.kind_key("") == "CONCEPT"
    assert enr.kind_key(None) == "CONCEPT"
    assert enr.kind_key(" person ") == "PERSON"
    assert entity_merge._kind(" person ") == "PERSON"
    assert entity_merge._kind(None) == "CONCEPT"


def test_apply_converted_and_main_blocks_both_drop(store):
    """U3."""
    out = enr.apply_name_rules([_e("Total", "PERSON"), _e("Total", "ORG")], USER)
    assert out == []


def test_apply_one_query_per_batch(store):
    enr.apply_name_rules([_e(f"n{i}") for i in range(5)], USER)
    fetches = [c for c in store.calls if c[0] == "fetch_all"]
    assert len(fetches) == 1
    assert fetches[0][2][0] == USER
    assert fetches[0][2][1] == ["n0", "n1", "n2", "n3", "n4"]
    assert "action =" not in fetches[0][1]  # U5: both actions in one read


def test_apply_empty_batch_no_query(store):
    assert enr.apply_name_rules([], USER) == []
    assert store.calls == []


def test_apply_dedupes_keys_and_drops_empty(store):
    enr.apply_name_rules([_e("A"), _e("a "), _e("")], USER)
    (call,) = [c for c in store.calls if c[0] == "fetch_all"]
    assert call[2] == (USER, ["a"])


def test_apply_lookup_failure_retries_once_then_raises(store, caplog):
    """U4: fail closed (replaces the LUM-967 fail-open test)."""
    ents = [_e("Meier Pharmacy")]
    store.fetch_all = MagicMock(side_effect=RuntimeError("table missing"))
    with caplog.at_level(logging.WARNING, logger="services.entity_name_rules"):
        with pytest.raises(enr.NameRulesUnavailable):
            enr.apply_name_rules(ents, USER)
    assert store.fetch_all.call_count == 2
    assert "lookup failed user_id=alice" in caplog.text
    assert "Meier" not in caplog.text and "meier" not in caplog.text

    with patch.object(_config, "get_metadata_store", side_effect=RuntimeError("no store")):
        with pytest.raises(enr.NameRulesUnavailable):
            enr.apply_name_rules(ents, USER)


def test_apply_lookup_recovers_on_the_inline_retry(store):
    good = list(store.rules)
    store.fetch_all = MagicMock(side_effect=[RuntimeError("blip"), good])
    (e,) = enr.apply_name_rules([_e("Meier Pharmacy")], USER)
    assert e.entity_type == "ORG"


# ── store_entities hook ──────────────────────────────────────────────────────


def test_store_entities_applies_rules_before_quality_gate():
    order: list[str] = []
    seen = {}

    def _rules(ents, user_id):
        order.append("rules")
        for e in ents:
            e.entity_type = "ORG"
        return ents

    def _gate(ents, user_id):
        order.append("gate")
        seen["types"] = [e.entity_type for e in ents]
        return [], 0

    with (
        patch.object(entities.entity_name_rules, "apply_name_rules", side_effect=_rules),
        patch.object(entities.entity_quality, "score_and_filter_entities", side_effect=_gate),
    ):
        assert entities.store_entities([_e("Meier Pharmacy")], "ev1", "session", USER) == []
    assert order == ["rules", "gate"]
    assert seen["types"] == ["ORG"]


def test_store_entities_skip_quality_gate_does_not_apply_rules():
    ms = MagicMock()
    ms.fetch_one.return_value = None
    ms.fetch_all.return_value = []
    with (
        patch.object(entities.entity_name_rules, "apply_name_rules") as rules,
        patch.object(entities.config, "get_metadata_store", return_value=ms),
        patch.object(entities.config, "get_embedder", side_effect=RuntimeError("stop")),
    ):
        with pytest.raises(RuntimeError, match="stop"):
            entities.store_entities(
                [_e("Meier Pharmacy")], "ev1", "session", USER, skip_quality_gate=True
            )
    rules.assert_not_called()
