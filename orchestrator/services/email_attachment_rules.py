# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
""" "Always file this" rules for email attachments (LUM-727).

LUM-727 makes filing a **per-attachment** decision: nothing reaches the document
library unless the user picks it, or a rule here says so. Rules exist for the
recurring case — the monthly invoice from the same sender — so the review list
shrinks toward empty instead of asking the same question every month.

**Deliberately separate from ``email_sender_rule``**, which this module otherwise
mirrors in shape. That table answers *"what to do with the message"* (v1: hide);
this one answers *"what to do with its attachments"*. One table would make the
hide+file contradiction **live** — two rows, both consulted, no defined
precedence. Split, hide simply wins by construction: a suppressed message never
gets a ``signals`` row (``signal_processor`` writes attachment rows *after*
``_persist``), so it never produces an attachment row for a file rule to match.
The file rule is inert, not contradictory.

**Category rules key on the taxonomy slug, never the label.** LUM-629 made the
taxonomy user-editable; keying on a label would silently detach every rule the
first time someone renamed "Bills & finance".
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import config

_log = logging.getLogger(__name__)

ACTIONS = ("file",)
MATCH_TYPES = ("sender", "category")

# Consulted once per candidate during a rule sweep, so it must not be a DB round
# trip each time. Mirrors `email_sender_rules._CACHE_TTL_SECONDS`; invalidated on
# write, so the TTL only matters for a rule set in another worker process — the
# same accepted limitation the sibling caches carry.
_CACHE_TTL_SECONDS = 20.0
_cache: dict[str, tuple[float, list[dict]]] = {}


def invalidate_cache(user_id: Optional[str] = None) -> None:
    """Drop cached rules so a write is visible immediately, not after the TTL."""
    if user_id is None:
        _cache.clear()
    else:
        _cache.pop(user_id, None)


def normalise_sender(addr: str) -> str:
    """Lowercased and trimmed, matching ``email_sender_rules.set_rule`` exactly.

    Not ``actions.handlers.smtp_send._normalize_addr``: a ``services/`` module
    importing from ``actions/handlers/`` is the wrong dependency direction, the
    same rule this package holds for adapters. The sibling normalises inline and
    so does this.
    """
    return (addr or "").strip().lower()


def _rules_for(user_id: str) -> list[dict]:
    hit = _cache.get(user_id)
    if hit is not None and (time.monotonic() - hit[0]) < _CACHE_TTL_SECONDS:
        return hit[1]
    ms = config.get_metadata_store()
    # SCOPE-EXEMPT: email_attachment_rule has no `scope` column (068) — the
    # caller's own filing rules for their own mailbox, not a household read.
    rows = (
        ms.fetch_all(
            "SELECT id::text AS id, source_id::text AS source_id, match_type, "
            "       match_value, action, created_at "
            "FROM email_attachment_rule WHERE user_id = %s",
            (user_id,),
        )
        or []
    )
    out = [dict(r) for r in rows]
    _cache[user_id] = (time.monotonic(), out)
    return out


def matches(user_id: str, source_id: str, *, from_addr: str, category: str) -> Optional[dict]:
    """The rule that fires for this message, or ``None``.

    **A sender rule beats a category rule** when both match: naming a sender is
    the more specific statement, and a user who wrote both meant the narrower one
    to win.

    Never raises. Failing closed here means a rule silently stops working and the
    user sees a candidate they expected to be filed — recoverable, and visible.
    Failing *open* would file something no rule actually matched, which is the
    behaviour LUM-727 exists to remove.
    """
    try:
        rules = _rules_for(user_id)
    except Exception:  # noqa: BLE001 - see docstring; fail closed.
        _log.warning("attachment_rules: lookup degraded to no-match")
        return None

    sender = normalise_sender(from_addr)
    slug = (category or "").strip()
    sender_hit = None
    category_hit = None
    for r in rules:
        if str(r["source_id"]) != str(source_id):
            continue
        if r["match_type"] == "sender" and sender and r["match_value"] == sender:
            sender_hit = r
        elif r["match_type"] == "category" and slug and r["match_value"] == slug:
            category_hit = r
    return sender_hit or category_hit


def list_rules(user_id: str, source_id: str) -> list[dict]:
    """Rules for one mailbox, each flagged ``stale`` when it can no longer fire.

    A user can delete a custom taxonomy category (``email_taxonomy.put_taxonomy``
    sweeps affected *signals* to ``other``) but nothing sweeps rules, so a
    category rule can outlive its slug and then silently never match again. The
    creation path already refuses an unknown slug with a 422; tolerating the same
    state when it arrives by the back door would be inconsistent. Surfaced, not
    swept — the user deleted a category, not a filing instruction.
    """
    known = _known_slugs(user_id)
    out = []
    for r in _rules_for(user_id):
        if str(r["source_id"]) != str(source_id):
            continue
        row = dict(r)
        # `known is None` means the taxonomy could not be read — NOT that it is
        # empty. Conflating them marks every category rule stale on a transient
        # blip, telling the user their whole rule set is broken when nothing
        # changed. Unknown staleness reads as "not stale": the rule still fires
        # (`matches` never consults the taxonomy), so the flag would be a lie in
        # the other direction too.
        row["stale"] = (
            known is not None and r["match_type"] == "category" and r["match_value"] not in known
        )
        out.append(row)
    return out


def _known_slugs(user_id: str) -> Optional[frozenset[str]]:
    """The user's taxonomy slugs, or ``None`` when the taxonomy is unreadable.

    The distinction matters at both call sites and pulls in opposite directions,
    which is why it is returned rather than swallowed: `list_rules` treats
    unknown as not-stale (a transient blip must not condemn a working rule),
    while `create_rule` treats unknown as refuse (storing a slug we could not
    validate risks a rule that never fires).
    """
    from services import email_taxonomy

    try:
        return frozenset(str(c["slug"]) for c in email_taxonomy.load_taxonomy(user_id))
    except Exception:  # noqa: BLE001 - a taxonomy blip must not condemn live rules
        _log.warning("attachment_rules: taxonomy unavailable; no rule marked stale")
        return None


def create_rule(user_id: str, source_id: str, match_type: str, match_value: str) -> dict:
    """Create one rule. Raises ``ValueError`` on a match type or slug we refuse.

    A category rule naming a slug that does not exist can never match, and a rule
    that silently never fires reads as the feature being broken — so it is
    refused at creation rather than stored and ignored.
    """
    if match_type not in MATCH_TYPES:
        raise ValueError(f"unsupported match type: {match_type!r}")
    value = normalise_sender(match_value) if match_type == "sender" else (match_value or "").strip()
    if not value:
        raise ValueError("match value is required")
    if match_type == "category":
        known = _known_slugs(user_id)
        if known is None:
            # Fail closed: storing a slug we could not validate risks a rule the
            # user sees in their list that never fires, which reads as the
            # feature being broken. Refusing is recoverable — they retry.
            raise ValueError("category list is unavailable; try again")
        if value not in known:
            raise ValueError(f"unknown category: {value!r}")

    ms = config.get_metadata_store()
    row = ms.fetch_one(
        "INSERT INTO email_attachment_rule (user_id, source_id, match_type, match_value) "
        "VALUES (%s, %s::uuid, %s, %s) "
        "ON CONFLICT (user_id, source_id, match_type, match_value) DO NOTHING "
        "RETURNING id::text AS id, match_type, match_value, created_at",
        (user_id, str(source_id), match_type, value),
    )
    invalidate_cache(user_id)
    if row is None:
        # DO NOTHING returns no row: the rule already exists. The route maps this
        # to 409 rather than pretending it created something.
        raise FileExistsError(f"{match_type} rule already exists for {value!r}")
    return dict(row)


def delete_rule(user_id: str, rule_id: str) -> bool:
    """Hard delete, matching ``email_sender_rules.clear_rule``.

    **Already-filed documents are untouched.** `filed_by_rule_id` is
    ``ON DELETE SET NULL``, so they lose their attribution and keep their undo
    path. Deleting a rule stops future filing; it is not a retraction.
    """
    ms = config.get_metadata_store()
    row = ms.fetch_one(
        # SCOPE-EXEMPT: the caller's own rule, keyed on their own user_id.
        "DELETE FROM email_attachment_rule WHERE id = %s::uuid AND user_id = %s "
        "RETURNING id::text AS id",
        (str(rule_id), user_id),
    )
    invalidate_cache(user_id)
    return row is not None


def rule_bearing_mailboxes(user_id: str) -> dict[str, dict]:
    """``{source_id: {"source_name": str, "rule_count": int}}`` for this user's
    own IMAP mailboxes that have **at least one** filing rule (LUM-744).

    **One home for a question two surfaces ask.** The paperless overlap check is
    only ever interesting for a mailbox that files something — a mailbox with no
    rules cannot duplicate a document — so both the `/all` advisory route and the
    `POST /sources` connect gate need exactly this set, with the counts, to decide
    what to probe and what to say. The first cut of LUM-744 wrote it twice, and
    two copies of a scoping predicate is how one of them later stops matching the
    other.

    Rule counts come from the cached :func:`_rules_for` (20s TTL, invalidated on
    create and delete), so this costs **one** `sources` read and usually no rules
    query at all.

    Mailboxes the caller does not own are simply absent — the `user_id` predicate
    on the `sources` read is what filters them, and absence rather than a refusal
    is deliberate: confirming an id exists would be an enumeration oracle.
    """
    counts: dict[str, int] = {}
    for rule in _rules_for(user_id):
        sid = str(rule.get("source_id") or "")
        if sid:
            counts[sid] = counts.get(sid, 0) + 1
    if not counts:
        return {}

    ms = config.get_metadata_store()
    # `sources` has no `scope` column, so `visible_filter` cannot apply; the
    # user_id predicate is the isolation, and it is also what keeps ADR-192 D4's
    # per-user scoping intact here.
    rows = (
        # SCOPE-EXEMPT: no `scope` column on `sources`; user_id is the isolation.
        ms.fetch_all(
            "SELECT id::text AS id, name FROM sources "
            "WHERE user_id = %s AND source_type = 'imap' AND id = ANY(%s::uuid[])",
            (user_id, list(counts)),
        )
        or []
    )
    return {
        str(r["id"]): {"source_name": str(r.get("name") or ""), "rule_count": counts[str(r["id"])]}
        for r in rows
        if r.get("id") and str(r["id"]) in counts
    }
