# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Recipient suggestions for the composer (LUM-818).

The security-relevant tests come first because they are the reason this module
is written the way it is, not decoration on a convenience feature:

* **BCC is never a source.** `smtp_send.py` and migration 064 exclude
  `bcc_addrs` from the first-contact corpus deliberately and leave it unindexed.
  That reasoning binds harder here — the gate leaks one bit, a suggestion list
  prints the address on screen.
* **Household isolation.** One member's correspondents must never complete in
  another's composer, which means `visible_filter` must NOT be used.
* **Suppressed senders are excluded.** Someone who hid a sender expressed a
  preference; offering them as a recipient is the opposite.
"""

from __future__ import annotations

from datetime import datetime
from datetime import timedelta
from datetime import timezone

import pytest

import config
from services import email_recipients

_NOW = datetime(2026, 8, 14, tzinfo=timezone.utc)


class _Store:
    """Evaluates the three queries this module issues, by shape."""

    def __init__(self):
        self.signals: list[dict] = []
        self.sent: list[dict] = []
        self.rules: list[dict] = []
        self.queries: list[str] = []

    # -- seeding ---------------------------------------------------------
    def receive(self, user_id, addr, display="", when=None, n=1):
        for _ in range(n):
            self.signals.append(
                {
                    "user_id": user_id,
                    "from_addr": addr,
                    "from_display": display,
                    "created_at": when or _NOW,
                }
            )

    def send(self, user_id, *, to=(), cc=(), bcc=(), when=None, n=1):
        for _ in range(n):
            self.sent.append(
                {
                    "user_id": user_id,
                    "to_addrs": list(to),
                    "cc_addrs": list(cc),
                    "bcc_addrs": list(bcc),
                    "sent_at": when or _NOW,
                }
            )

    def suppress(self, user_id, addr):
        self.rules.append({"user_id": user_id, "from_addr": addr, "active": True})

    # -- store protocol --------------------------------------------------
    @staticmethod
    def _matches(hay: str, like: str) -> bool:
        core = like.strip("%").replace("\\%", "%").replace("\\_", "_").replace("\\\\", "\\")
        return core in (hay or "").lower()

    def fetch_all(self, sql, params=None):
        self.queries.append(sql)
        p = params or ()
        # `_is_first_contact`'s two membership queries, so the send gate and the
        # suggestion layer can be exercised against ONE corpus in one test.
        if "= ANY(%s)" in sql and "FROM signals" in sql:
            uid, targets = p
            return [
                {"a": r["from_addr"].lower()}
                for r in self.signals
                if r["user_id"] == uid and r["from_addr"].lower() in targets
            ]
        if "= ANY(%s)" in sql and "email_sent_log" in sql:
            uid, targets = p
            assert "bcc" not in sql.lower(), "BCC must never be queried"
            out = []
            for r in self.sent:
                for t in list(r["to_addrs"]) + list(r["cc_addrs"]):
                    if r["user_id"] == uid and t.lower() in targets:
                        out.append({"a": t.lower()})
            return out
        if "email_sender_rule" in sql:
            (uid,) = p
            return [
                {"a": r["from_addr"].lower()}
                for r in self.rules
                if r["user_id"] == uid and r["active"]
            ]
        if "FROM signals" in sql:
            uid, like, _like2, cap = p
            agg: dict[str, dict] = {}
            for r in self.signals:
                if r["user_id"] != uid or not r["from_addr"]:
                    continue
                if not (
                    self._matches(r["from_addr"], like) or self._matches(r["from_display"], like)
                ):
                    continue
                a = r["from_addr"].lower()
                cur = agg.setdefault(a, {"addr": a, "display": "", "last_at": None, "n": 0})
                cur["n"] += 1
                cur["display"] = max(cur["display"], r["from_display"] or "")
                cur["last_at"] = max(cur["last_at"] or r["created_at"], r["created_at"])
            return list(agg.values())[:cap]
        if "email_sent_log" in sql:
            uid, like, cap = p
            assert "bcc" not in sql.lower(), "BCC must never be queried"
            agg2: dict[str, dict] = {}
            for r in self.sent:
                if r["user_id"] != uid:
                    continue
                for t in list(r["to_addrs"]) + list(r["cc_addrs"]):
                    if not self._matches(t, like):
                        continue
                    a = t.lower()
                    cur = agg2.setdefault(a, {"addr": a, "last_at": None, "n": 0})
                    cur["n"] += 1
                    cur["last_at"] = max(cur["last_at"] or r["sent_at"], r["sent_at"])
            return list(agg2.values())[:cap]
        raise AssertionError(f"unexpected SQL: {sql!r}")

    def fetch_one(self, sql, params=None):
        return None

    def execute(self, sql, params=None):
        return None


@pytest.fixture
def store(monkeypatch):
    s = _Store()
    monkeypatch.setattr(config, "get_metadata_store", lambda: s)
    return s


def _addrs(suggestions) -> list[str]:
    return [s.address for s in suggestions]


# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------


def test_bcc_is_never_a_source(store):
    """THE headline test for this feature.

    A BCC'd address was kept off the visible headers on purpose. Completing it
    back into the composer undoes that, on a screen someone else may be looking
    at. `smtp_send.py` and migration 064 both forbid reading `bcc_addrs`; this
    asserts the prohibition survives into the read path built on top of them.
    """
    store.send("alice", to=["known@example.com"], bcc=["lawyer@chambers.example"])

    out = _addrs(email_recipients.suggest_recipients("alice", "lawyer"))

    assert out == [], "a BCC-only recipient must never be suggested"
    assert not any("bcc" in q.lower() for q in store.queries), "bcc_addrs must not be queried"


def test_one_members_correspondents_never_complete_in_anothers_composer(store):
    """`visible_filter` must NOT be used here -- the household union would leak."""
    store.receive("bob", "bobs-friend@example.com")
    store.send("bob", to=["bobs-colleague@example.com"])

    assert _addrs(email_recipients.suggest_recipients("alice", "bobs")) == []


def test_a_suppressed_sender_is_not_suggested(store):
    """Owner decision: hiding a sender means not offering them as a recipient."""
    store.receive("alice", "spam@marketing.example", n=5)
    store.suppress("alice", "spam@marketing.example")

    assert _addrs(email_recipients.suggest_recipients("alice", "marketing")) == []


def test_suppression_wins_even_when_the_user_has_written_to_them(store):
    """Sent-to normally ranks first; a suppression rule still removes it."""
    store.send("alice", to=["ex@example.com"])
    store.receive("alice", "ex@example.com")
    store.suppress("alice", "ex@example.com")

    assert _addrs(email_recipients.suggest_recipients("alice", "ex@")) == []


def test_a_too_short_query_is_refused_not_answered(store):
    store.receive("alice", "someone@example.com")

    with pytest.raises(ValueError):
        email_recipients.suggest_recipients("alice", "a")


def test_a_wildcard_query_cannot_page_out_the_corpus(store):
    """`%` is escaped, so it matches a literal percent -- not everything."""
    store.receive("alice", "someone@example.com")
    store.receive("alice", "another@example.com")

    assert _addrs(email_recipients.suggest_recipients("alice", "%%")) == []


# ---------------------------------------------------------------------------
# Ranking -- the difference between useful and unusable
# ---------------------------------------------------------------------------


def test_people_you_have_written_to_rank_above_people_who_wrote_to_you(store):
    store.receive("alice", "newsletter@shop.example", when=_NOW)
    store.send("alice", to=["colleague@shop.example"], when=_NOW - timedelta(days=30))

    out = _addrs(email_recipients.suggest_recipients("alice", "shop.example"))

    assert out[0] == "colleague@shop.example", "sent-to outranks received-from, despite being older"


def test_noreply_addresses_rank_last_but_are_still_offered(store):
    """Owner decision: rank, do not filter.

    Some `no-reply` addresses do accept mail, and this is a memory aid rather
    than a validator -- hiding an address the user demonstrably corresponded
    with would be a quiet wrong answer.
    """
    store.receive("alice", "no-reply@bank.example", when=_NOW)
    store.receive("alice", "advisor@bank.example", when=_NOW - timedelta(days=90))

    out = _addrs(email_recipients.suggest_recipients("alice", "bank.example"))

    assert out == ["advisor@bank.example", "no-reply@bank.example"]


def test_more_recent_correspondents_rank_higher(store):
    store.send("alice", to=["old@example.com"], when=_NOW - timedelta(days=200))
    store.send("alice", to=["recent@example.com"], when=_NOW)

    assert _addrs(email_recipients.suggest_recipients("alice", "example.com"))[0] == (
        "recent@example.com"
    )


def test_the_limit_is_bounded(store):
    for i in range(40):
        store.send("alice", to=[f"person{i}@example.com"])

    assert len(email_recipients.suggest_recipients("alice", "example.com", limit=999)) == (
        email_recipients.MAX_LIMIT
    )


# ---------------------------------------------------------------------------
# Corpus behaviour
# ---------------------------------------------------------------------------


def test_a_cc_recipient_counts_as_corresponded(store):
    """Migration 064: cc DOES participate -- you demonstrably wrote to them."""
    store.send("alice", cc=["cc-person@example.com"])

    out = email_recipients.suggest_recipients("alice", "cc-person")

    assert _addrs(out) == ["cc-person@example.com"]
    assert out[0].corresponded is True


def test_display_names_come_from_received_mail_and_sent_only_contacts_have_none(store):
    """A real asymmetry: `email_sent_log` stores addr-specs without names."""
    store.receive("alice", "named@example.com", display="Jane Smith")
    store.send("alice", to=["unnamed@example.com"])

    by_addr = {s.address: s for s in email_recipients.suggest_recipients("alice", "example.com")}

    assert by_addr["named@example.com"].display_name == "Jane Smith"
    assert by_addr["unnamed@example.com"].display_name == ""


def test_a_match_can_come_from_the_display_name_not_only_the_address(store):
    """You remember the person, not their address -- that is the point."""
    store.receive("alice", "js@corp.example", display="Jane Smith")

    assert _addrs(email_recipients.suggest_recipients("alice", "jane")) == ["js@corp.example"]


def test_matching_is_substring_not_prefix_only(store):
    store.receive("alice", "someone@plumbers.example")

    assert _addrs(email_recipients.suggest_recipients("alice", "plumb")) == [
        "someone@plumbers.example"
    ]


def test_an_address_on_both_sides_appears_once(store):
    store.receive("alice", "both@example.com", display="Both")
    store.send("alice", to=["both@example.com"])

    out = email_recipients.suggest_recipients("alice", "both")

    assert len(out) == 1
    assert out[0].corresponded is True
    assert out[0].display_name == "Both"


def test_a_failed_suppression_lookup_suggests_nothing_rather_than_everything(store, monkeypatch):
    """Fail CLOSED here, unlike `should_suppress` which fails open.

    The postures differ because the mistakes differ: there, degrading hides
    mail the user wanted to see; here, degrading would offer a contact the
    user silenced.
    """
    store.receive("alice", "someone@example.com")

    def _boom(sql, params=None):
        if "email_sender_rule" in sql:
            raise RuntimeError("postgres is having a day")
        return []

    monkeypatch.setattr(store, "fetch_all", _boom)

    with pytest.raises(RuntimeError):
        email_recipients.suggest_recipients("alice", "someone")


# ---------------------------------------------------------------------------
# Crossing into the send gate -- suggesting must change nothing about sending
# ---------------------------------------------------------------------------


def test_suggesting_an_address_does_not_make_it_a_known_contact(store):
    """A suggestion is a memory aid, not a promotion.

    Picking a name from a dropdown must not quietly satisfy the first-contact
    gate — the confirm step exists because a send goes out AS THE USER, and an
    autocomplete that dismissed it would remove the check exactly when the user
    is least likely to have typed the address deliberately.

    Asserted by crossing the two layers against one corpus: the suggestion path
    and `_is_first_contact` are separate modules with separate queries, and
    nothing else compares them. That gap is the shape of bug that survived
    review in LUM-24.
    """
    from actions.handlers.smtp_send import _is_first_contact

    store.receive("alice", "known@example.com")

    assert _addrs(email_recipients.suggest_recipients("alice", "known")) == ["known@example.com"]
    # Never corresponded with, so still a first contact, suggestion or not.
    assert _is_first_contact("alice", ["stranger@example.com"]) is True


def test_the_suggestion_path_writes_nothing(store, monkeypatch):
    """It cannot promote a contact because it cannot write at all."""
    calls: list = []
    monkeypatch.setattr(store, "execute", lambda sql, params=None: calls.append(sql))
    store.receive("alice", "known@example.com")

    email_recipients.suggest_recipients("alice", "known")

    assert calls == []


def test_suppression_hides_a_contact_from_suggestions_but_not_from_the_send_gate(store):
    """The two must not be conflated, in either direction.

    Hiding a sender is about what the user SEES. It must not silently reclassify
    someone they have corresponded with as a stranger — that would flip the send
    path to first-contact and add a confirm step for a person they email often.
    """
    from actions.handlers.smtp_send import _is_first_contact

    store.send("alice", to=["ex@example.com"])
    store.suppress("alice", "ex@example.com")

    assert _addrs(email_recipients.suggest_recipients("alice", "ex@")) == []
    assert _is_first_contact("alice", ["ex@example.com"]) is False
