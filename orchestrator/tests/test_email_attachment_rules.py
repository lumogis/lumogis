# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
""" "Always file this" rules (LUM-727) — ``services/email_attachment_rules.py``.

The rules service is what keeps the review list from asking the same question
every month. Four behaviours here are contracts rather than conveniences:

* **A sender rule beats a category rule.** Naming a sender is the more specific
  statement, and a user who wrote both meant the narrower one to win.
* **Lookup fails CLOSED.** A degraded lookup returns "no match", so the user sees
  a candidate they expected to be filed — visible and recoverable. Failing *open*
  would file something no rule matched, which is the behaviour LUM-727 exists to
  remove.
* **Category rules key on the taxonomy SLUG.** LUM-629 made labels user-editable;
  keying on a label would detach every rule the first time someone renamed
  "Bills & finance".
* **A rule that can never fire is surfaced, not swept.** The user deleted a
  category, not a filing instruction.
"""

from __future__ import annotations

import pytest

import config
from services import email_attachment_rules as mod

SOURCE = "11111111-1111-1111-1111-111111111111"
OTHER = "22222222-2222-2222-2222-222222222222"


class _Store:
    """Enough of the metadata store to exercise the three statements this
    module issues, keyed on the SQL rather than on argument counts — the shapes
    differ, and branching on ``len(params)`` is how a test double starts lying."""

    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.deleted: list[tuple] = []
        self.inserted: list[tuple] = []
        self.selects = 0
        self.fail_select = False

    def fetch_all(self, sql, params=None):
        assert "FROM email_attachment_rule" in sql
        if self.fail_select:
            raise RuntimeError("postgres is down")
        self.selects += 1
        user_id = params[0]
        return [dict(r) for r in self.rows if r["user_id"] == user_id]

    def fetch_one(self, sql, params=None):
        if sql.strip().upper().startswith("INSERT"):
            user_id, source_id, match_type, match_value = params
            self.inserted.append(params)
            clash = any(
                r["user_id"] == user_id
                and str(r["source_id"]) == str(source_id)
                and r["match_type"] == match_type
                and r["match_value"] == match_value
                for r in self.rows
            )
            if clash:
                return None  # ON CONFLICT DO NOTHING returns no row
            row = {
                "id": f"rule-{len(self.rows)}",
                "user_id": user_id,
                "source_id": source_id,
                "match_type": match_type,
                "match_value": match_value,
                "action": "file",
                "created_at": "2026-07-26T00:00:00Z",
            }
            self.rows.append(row)
            return {k: row[k] for k in ("id", "match_type", "match_value", "created_at")}
        if sql.strip().upper().startswith("DELETE"):
            rule_id, user_id = params
            self.deleted.append(params)
            for r in list(self.rows):
                if str(r["id"]) == str(rule_id) and r["user_id"] == user_id:
                    self.rows.remove(r)
                    return {"id": r["id"]}
            return None
        raise AssertionError(f"unexpected statement: {sql!r}")


def _rule(**kw):
    base = {
        "id": "r1",
        "user_id": "u1",
        "source_id": SOURCE,
        "match_type": "sender",
        "match_value": "billing@utility.example",
        "action": "file",
        "created_at": "2026-07-26T00:00:00Z",
    }
    base.update(kw)
    return base


@pytest.fixture(autouse=True)
def _clean_cache():
    mod.invalidate_cache()
    yield
    mod.invalidate_cache()


@pytest.fixture
def store(monkeypatch):
    st = _Store()
    monkeypatch.setattr(config, "get_metadata_store", lambda: st)
    return st


@pytest.fixture
def taxonomy(monkeypatch):
    """The known slugs, controlled per test."""
    slugs = {"value": ["bills", "receipts"]}

    def _load(user_id):
        return [{"slug": s, "label": s.title()} for s in slugs["value"]]

    from services import email_taxonomy

    monkeypatch.setattr(email_taxonomy, "load_taxonomy", _load)
    return slugs


# --- normalisation ----------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("  Billing@Utility.Example  ", "billing@utility.example"),
        ("BILLING@UTILITY.EXAMPLE", "billing@utility.example"),
        ("", ""),
        (None, ""),
    ],
)
def test_senders_normalise_the_same_way_the_sibling_does(raw, expected):
    """Matches ``email_sender_rules.set_rule`` exactly — lowercased and trimmed.

    Deliberately NOT ``actions.handlers.smtp_send._normalize_addr``: a
    ``services/`` module importing from ``actions/handlers/`` is the wrong
    dependency direction. The sibling normalises inline and so does this.
    """
    assert mod.normalise_sender(raw) == expected


def test_a_rule_is_stored_normalised_so_a_shouted_address_still_matches(store, taxonomy):
    mod.create_rule("u1", SOURCE, "sender", "  BILLING@Utility.Example ")

    assert store.rows[0]["match_value"] == "billing@utility.example"
    assert mod.matches("u1", SOURCE, from_addr="Billing@UTILITY.example", category="") is not None


# --- matching ---------------------------------------------------------------


def test_a_sender_rule_matches_its_sender(store):
    store.rows.append(_rule())

    hit = mod.matches("u1", SOURCE, from_addr="billing@utility.example", category="other")

    assert hit is not None and hit["match_type"] == "sender"


def test_an_unmatched_sender_files_nothing(store):
    store.rows.append(_rule())

    assert mod.matches("u1", SOURCE, from_addr="someone@else.example", category="other") is None


def test_a_category_rule_matches_its_slug(store):
    store.rows.append(_rule(match_type="category", match_value="bills"))

    hit = mod.matches("u1", SOURCE, from_addr="anyone@example.com", category="bills")

    assert hit is not None and hit["match_type"] == "category"


def test_a_sender_rule_beats_a_category_rule(store):
    """Naming a sender is the more specific statement."""
    store.rows.append(_rule(id="cat", match_type="category", match_value="bills"))
    store.rows.append(_rule(id="snd", match_type="sender", match_value="billing@utility.example"))

    hit = mod.matches("u1", SOURCE, from_addr="billing@utility.example", category="bills")

    assert hit["id"] == "snd"


def test_a_rule_for_another_mailbox_does_not_fire(store):
    """Rules are per-source. The same sender writing to a second mailbox the user
    never configured is not covered by the first mailbox's decision."""
    store.rows.append(_rule(source_id=OTHER))

    assert mod.matches("u1", SOURCE, from_addr="billing@utility.example", category="") is None


def test_another_users_rule_does_not_fire(store):
    store.rows.append(_rule(user_id="u2"))

    assert mod.matches("u1", SOURCE, from_addr="billing@utility.example", category="") is None


def test_an_empty_sender_never_matches_an_empty_stored_value(store):
    """A signal with no ``from_addr`` must not collide with a malformed rule.

    Creation refuses an empty value, so this is defence against a row that
    arrived some other way — and the failure it prevents (file EVERY attachment
    whose sender we could not parse) is exactly the one LUM-727 removed.
    """
    store.rows.append(_rule(match_value=""))

    assert mod.matches("u1", SOURCE, from_addr="", category="") is None


def test_an_empty_category_never_matches(store):
    store.rows.append(_rule(match_type="category", match_value=""))

    assert mod.matches("u1", SOURCE, from_addr="a@b.example", category="") is None


def test_a_degraded_lookup_fails_closed(store):
    """A database blip must leave the attachment PENDING, not file it.

    The user sees a candidate they expected to be filed — visible and
    recoverable. The other direction files something no rule matched.
    """
    store.rows.append(_rule())
    store.fail_select = True

    assert mod.matches("u1", SOURCE, from_addr="billing@utility.example", category="") is None


# --- the cache --------------------------------------------------------------


def test_rules_are_read_once_per_sweep_not_once_per_candidate(store):
    """``matches`` is called per candidate; a round trip each time would make a
    200-attachment sweep 200 queries."""
    store.rows.append(_rule())

    for _ in range(5):
        mod.matches("u1", SOURCE, from_addr="billing@utility.example", category="")

    assert store.selects == 1


def test_a_new_rule_is_visible_immediately_not_after_the_ttl(store, taxonomy):
    """Write invalidates. Otherwise "always file this" appears not to work for
    twenty seconds, which reads as the feature being broken."""
    assert mod.matches("u1", SOURCE, from_addr="new@sender.example", category="") is None

    mod.create_rule("u1", SOURCE, "sender", "new@sender.example")

    assert mod.matches("u1", SOURCE, from_addr="new@sender.example", category="") is not None


def test_a_deleted_rule_stops_firing_immediately(store):
    store.rows.append(_rule())
    assert mod.matches("u1", SOURCE, from_addr="billing@utility.example", category="") is not None

    mod.delete_rule("u1", "r1")

    assert mod.matches("u1", SOURCE, from_addr="billing@utility.example", category="") is None


def test_one_users_write_does_not_flush_another_users_cache(store, taxonomy):
    store.rows.append(_rule(user_id="u2", id="r2"))
    mod.matches("u2", SOURCE, from_addr="billing@utility.example", category="")
    before = store.selects

    mod.create_rule("u1", SOURCE, "sender", "someone@else.example")
    mod.matches("u2", SOURCE, from_addr="billing@utility.example", category="")

    assert store.selects == before, "u1's write should not evict u2's cached rules"


# --- creation ---------------------------------------------------------------


def test_creating_a_rule_returns_it(store, taxonomy):
    out = mod.create_rule("u1", SOURCE, "sender", "billing@utility.example")

    assert out["match_type"] == "sender"
    assert out["match_value"] == "billing@utility.example"
    assert out["id"]


@pytest.mark.parametrize("match_type", ["subject", "domain", "", "file", "SENDER"])
def test_an_unsupported_match_type_is_refused(store, match_type):
    with pytest.raises(ValueError):
        mod.create_rule("u1", SOURCE, match_type, "anything")


@pytest.mark.parametrize("value", ["", "   "])
def test_an_empty_match_value_is_refused(store, value):
    """A rule with no value would match nothing forever, or — worse, if the
    guard were dropped — everything."""
    with pytest.raises(ValueError):
        mod.create_rule("u1", SOURCE, "sender", value)


def test_a_category_rule_naming_an_unknown_slug_is_refused(store, taxonomy):
    """Refused at creation rather than stored and silently never firing.

    A rule the user can see in their list but which never acts is indistinguishable
    from a broken feature.
    """
    with pytest.raises(ValueError):
        mod.create_rule("u1", SOURCE, "category", "not-a-real-slug")

    assert store.rows == []


def test_a_category_rule_naming_a_known_slug_is_stored(store, taxonomy):
    out = mod.create_rule("u1", SOURCE, "category", "bills")

    assert out["match_value"] == "bills"


def test_creating_the_same_rule_twice_raises_rather_than_reporting_success(store, taxonomy):
    """``ON CONFLICT DO NOTHING`` returns no row; the route turns this into 409
    instead of pretending it created something."""
    mod.create_rule("u1", SOURCE, "sender", "billing@utility.example")

    with pytest.raises(FileExistsError):
        mod.create_rule("u1", SOURCE, "sender", "billing@utility.example")

    assert len(store.rows) == 1


def test_the_same_rule_in_a_different_mailbox_is_not_a_conflict(store, taxonomy):
    mod.create_rule("u1", SOURCE, "sender", "billing@utility.example")

    mod.create_rule("u1", OTHER, "sender", "billing@utility.example")  # must not raise


# --- listing ----------------------------------------------------------------


def test_listing_returns_only_this_mailboxs_rules(store, taxonomy):
    store.rows.append(_rule(id="mine"))
    store.rows.append(_rule(id="theirs", source_id=OTHER))

    ids = [r["id"] for r in mod.list_rules("u1", SOURCE)]

    assert ids == ["mine"]


def test_listing_returns_only_the_callers_rules(store, taxonomy):
    store.rows.append(_rule(id="mine"))
    store.rows.append(_rule(id="bobs", user_id="u2"))

    ids = [r["id"] for r in mod.list_rules("u1", SOURCE)]

    assert ids == ["mine"]


def test_a_category_rule_whose_slug_was_deleted_is_flagged_stale(store, taxonomy):
    """Nothing sweeps rules when a taxonomy category is deleted
    (``email_taxonomy.put_taxonomy`` sweeps affected *signals* only), so a rule
    can outlive its slug and silently never match again. Surfaced, not swept."""
    store.rows.append(_rule(match_type="category", match_value="bills"))
    taxonomy["value"] = ["receipts"]

    assert mod.list_rules("u1", SOURCE)[0]["stale"] is True


def test_a_live_category_rule_is_not_stale(store, taxonomy):
    store.rows.append(_rule(match_type="category", match_value="bills"))

    assert mod.list_rules("u1", SOURCE)[0]["stale"] is False


def test_a_sender_rule_is_never_stale(store, taxonomy):
    """Senders are not enumerable — an address the user has not heard from in a
    year is not a broken rule."""
    store.rows.append(_rule())
    taxonomy["value"] = []

    assert mod.list_rules("u1", SOURCE)[0]["stale"] is False


@pytest.fixture
def broken_taxonomy(monkeypatch):
    from services import email_taxonomy

    def _boom(user_id):
        raise RuntimeError("taxonomy unavailable")

    monkeypatch.setattr(email_taxonomy, "load_taxonomy", _boom)


def test_a_taxonomy_blip_does_not_condemn_a_working_rule(store, broken_taxonomy):
    """**Unreadable is not the same as empty.**

    Conflating them marks every category rule stale on a transient blip, telling
    the user their whole rule set is broken when nothing changed — and the rules
    are in fact still firing, because ``matches`` never consults the taxonomy.
    """
    store.rows.append(_rule(match_type="category", match_value="bills"))

    rows = mod.list_rules("u1", SOURCE)

    assert len(rows) == 1 and rows[0]["stale"] is False


def test_a_taxonomy_blip_still_refuses_to_create_a_category_rule(store, broken_taxonomy):
    """The same distinction, resolved the OTHER way.

    Listing treats unknown as not-stale because condemning a live rule is the
    worse error; creation treats unknown as refuse because storing an
    unvalidated slug risks a rule that never fires. Refusing is recoverable —
    the user retries.
    """
    with pytest.raises(ValueError):
        mod.create_rule("u1", SOURCE, "category", "bills")

    assert store.rows == []


def test_a_taxonomy_blip_does_not_block_a_sender_rule(store, broken_taxonomy):
    """Sender rules do not involve the taxonomy at all, so its availability must
    not gate them."""
    out = mod.create_rule("u1", SOURCE, "sender", "billing@utility.example")

    assert out["match_value"] == "billing@utility.example"


# --- deletion ---------------------------------------------------------------


def test_deleting_a_rule_reports_that_it_deleted_one(store):
    store.rows.append(_rule())

    assert mod.delete_rule("u1", "r1") is True


def test_deleting_an_unknown_rule_reports_nothing_deleted(store):
    assert mod.delete_rule("u1", "nope") is False


def test_another_users_rule_cannot_be_deleted(store):
    """Scoped in the DELETE's own WHERE clause, so ownership is not a separate
    check that a later caller can forget to make."""
    store.rows.append(_rule(user_id="u2"))

    assert mod.delete_rule("u1", "r1") is False
    assert len(store.rows) == 1
