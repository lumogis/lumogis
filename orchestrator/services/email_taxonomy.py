# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""User-editable email category taxonomy + classification rules (LUM-629).

Sparse per-user overlay over a built-in default taxonomy: the absence of a row
for a slug means "use the default". ``is_custom`` / ``is_protected`` are derived
(never stored). Read at runtime by ``email_classifier`` via the ``signal_processor``
call site; edited through the ``/me/email-taxonomy`` + ``/me/email-classification-rules``
routes. Cloud LLM is never involved — this is user config, not email content.

The store mirrors ``services/notifications/preferences.py`` (sparse override rows,
``user_id``-keyed, ``ON CONFLICT`` upsert, per-user settings so ``SCOPE-EXEMPT``).
"""

from __future__ import annotations

import re

from fastapi import HTTPException

import config

# (slug, label) — DEFAULT_SEED taxonomy. ``other`` is the protected floor and
# always last. Renaming EMAIL_CATEGORIES in email_classifier keeps a slug alias;
# this is the (slug,label) source of truth for the UI defaults.
DEFAULT_SEED: tuple[tuple[str, str], ...] = (
    ("school", "School"),
    ("insurance_renewal", "Insurance & renewals"),
    ("bills_finance", "Bills & finance"),
    ("family_personal", "Family & personal"),
    ("promotional_bulk", "Promotional / bulk"),
    ("other", "Other"),
)
_DEFAULT_SLUGS: tuple[str, ...] = tuple(s for s, _ in DEFAULT_SEED)
_DEFAULT_LABELS: dict[str, str] = {s: lbl for s, lbl in DEFAULT_SEED}
_DEFAULT_ORDER: dict[str, int] = {s: i for i, s in enumerate(_DEFAULT_SLUGS)}
PROTECTED_SLUGS: frozenset[str] = frozenset({"other"})

_SLUG_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_MAX_CATEGORIES = 64
_MAX_RULES = 64
_MAX_MATCH_LIST = 16
_MAX_MATCH_ENTRY = 128
_MAX_LABEL = 64
_MATCH_FIELDS = frozenset(
    {"sender", "sender_domain", "subject_contains", "body_contains", "bulk_header"}
)


def _err(code: str, message: str = "") -> HTTPException:
    return HTTPException(status_code=422, detail={"code": code, "message": message or code})


# --------------------------------------------------------------------------- #
# Read path (cached)
# --------------------------------------------------------------------------- #
def _build_cache(user_id: str) -> dict:
    ms = config.get_metadata_store()
    # SCOPE-EXEMPT: email_category_taxonomy is per-user settings (no scope column).
    rows = ms.fetch_all(
        "SELECT slug, label, enabled, sort FROM email_category_taxonomy WHERE user_id = %s",
        (user_id,),
    )
    overrides = {r["slug"]: r for r in (rows or [])}

    categories: list[dict] = []
    # Defaults first (with any override applied), then custom rows.
    for slug in _DEFAULT_SLUGS:
        ov = overrides.get(slug)
        enabled = True if ov is None else bool(ov["enabled"])
        if slug in PROTECTED_SLUGS:
            enabled = True  # protected floor is always enabled
        categories.append(
            {
                "slug": slug,
                "label": (_DEFAULT_LABELS[slug] if ov is None or not ov["label"] else ov["label"]),
                "enabled": enabled,
                "is_custom": False,
                "is_protected": slug in PROTECTED_SLUGS,
                "sort": 0 if ov is None else int(ov["sort"]),
            }
        )
    for slug, ov in overrides.items():
        if slug in _DEFAULT_ORDER:
            continue
        categories.append(
            {
                "slug": slug,
                "label": ov["label"],
                "enabled": bool(ov["enabled"]),
                "is_custom": True,
                "is_protected": False,
                "sort": int(ov["sort"]),
            }
        )

    # Stable order: sort asc, then default order, then slug asc (tie-break).
    categories.sort(key=lambda c: (c["sort"], _DEFAULT_ORDER.get(c["slug"], 10_000), c["slug"]))

    enabled = frozenset(c["slug"] for c in categories if c["enabled"]) | PROTECTED_SLUGS

    # SCOPE-EXEMPT: email_classification_rule is per-user settings (no scope column).
    rule_rows = ms.fetch_all(
        "SELECT rule_id, priority, match, category, enabled "
        "FROM email_classification_rule WHERE user_id = %s ORDER BY priority ASC, rule_id ASC",
        (user_id,),
    )
    rules: list[dict] = []
    for r in rule_rows or []:
        match = r["match"] if isinstance(r["match"], dict) else {}
        rules.append(
            {
                "rule_id": r["rule_id"],
                "priority": int(r["priority"]),
                "match": match,
                "category": r["category"],
                "enabled": bool(r["enabled"]),
            }
        )

    return {"categories": categories, "enabled": enabled, "rules": rules}


def _cached(user_id: str) -> dict:
    hit = config.get_email_taxonomy_cache(user_id)
    if hit is not None:
        return hit
    built = _build_cache(user_id)
    config.set_email_taxonomy_cache(user_id, built)
    return built


def load_taxonomy(user_id: str) -> list[dict]:
    """Effective ordered categories (defaults merged with overrides + customs)."""
    return list(_cached(user_id)["categories"])


def label_map(user_id: str) -> dict[str, str]:
    """``{slug: label}`` for a user's effective taxonomy (LUM-632).

    Resolves a signal's category slug to its human label via the **owner's**
    taxonomy for cross-user display on household-shared signals. Reuses the
    per-user LUM-629 cache (warm = a dict comprehension over cached categories;
    cold = the two ``_build_cache`` queries). Defaults are always present — even
    a since-disabled default resolves to its label; a deleted-custom slug is
    simply absent, and callers fall back to the raw slug.
    """
    return {c["slug"]: c["label"] for c in _cached(user_id)["categories"]}


def enabled_slugs(user_id: str) -> frozenset[str]:
    """Slugs the classifier may emit — always contains the protected ``other``."""
    return _cached(user_id)["enabled"]


def load_rules(user_id: str) -> list[dict]:
    """Enabled rules whose category is currently enabled, priority asc."""
    data = _cached(user_id)
    enabled = data["enabled"]
    return [r for r in data["rules"] if r["enabled"] and r["category"] in enabled]


# --------------------------------------------------------------------------- #
# Matcher (deterministic, no LLM) — used by classifier and reclassify
# --------------------------------------------------------------------------- #
def _extract_domain(addr: str) -> str:
    return addr.rsplit("@", 1)[-1].strip().lower() if "@" in addr else ""


def match_rule(
    match: dict, *, title: str, headers: dict[str, str], sender: str, body: str = ""
) -> bool:
    """AND across present fields, OR within a list, all case-insensitive.

    Missing inputs (no headers/sender/body at reclassify or on metadata mail)
    make sender/domain/bulk/body conditions simply fail to match, so such a rule
    is effectively skipped there. ``body`` is the extracted plain-text body
    (LUM-630) — present only for full-ingested mail.
    """
    active = False
    subject = (title or "").lower()
    sender = (sender or "").lower()
    body_l = (body or "").lower()

    subj = [s.lower() for s in (match.get("subject_contains") or []) if s]
    if subj:
        active = True
        if not any(kw in subject for kw in subj):
            return False

    body_kw = [b.lower() for b in (match.get("body_contains") or []) if b]
    if body_kw:
        active = True
        if not any(kw in body_l for kw in body_kw):
            return False

    senders = [s.lower() for s in (match.get("sender") or []) if s]
    if senders:
        active = True
        if sender not in senders:
            return False

    domains = [d.lower() for d in (match.get("sender_domain") or []) if d]
    if domains:
        active = True
        dom = _extract_domain(sender)
        if not dom or not any(dom == d or dom.endswith("." + d) for d in domains):
            return False

    if match.get("bulk_header"):
        active = True
        from services.email_classifier import _has_bulk_header

        if not _has_bulk_header(headers or {}):
            return False

    return active  # empty match (no active condition) never matches


# --------------------------------------------------------------------------- #
# Write path
# --------------------------------------------------------------------------- #
def _validate_slug(slug: str) -> None:
    if not _SLUG_RE.match(slug or ""):
        raise _err("bad_slug", "slug must match ^[a-z][a-z0-9_]{0,31}$")


def _validate_match(match: dict) -> None:
    extra = set(match or {}) - _MATCH_FIELDS
    if extra:
        raise _err("bad_match_field", f"unknown match field(s): {sorted(extra)}")
    active = False
    for field in ("sender", "sender_domain", "subject_contains", "body_contains"):
        vals = match.get(field) or []
        if not isinstance(vals, list):
            raise _err("bad_match_field", f"{field} must be a list")
        if vals:
            active = True
        if len(vals) > _MAX_MATCH_LIST:
            raise _err("match_too_large", f"{field} exceeds {_MAX_MATCH_LIST} entries")
        for v in vals:
            if not isinstance(v, str) or len(v) > _MAX_MATCH_ENTRY:
                raise _err("match_too_large", f"{field} entry too long")
    if match.get("bulk_header"):
        active = True
    if not active:
        raise _err("rule_empty_match", "a rule must have at least one condition")


def put_taxonomy(user_id: str, patch) -> list[dict]:
    """Apply a taxonomy patch; sweep disabled/deleted slugs → other in one txn."""
    ms = config.get_metadata_store()
    existing = {c["slug"]: c for c in load_taxonomy(user_id)}

    seen: set[str] = set()
    upserts: list[tuple] = []
    sweep_slugs: set[str] = set()

    for item in patch.categories:
        slug = item.slug
        _validate_slug(slug)
        if slug in seen:
            raise _err("duplicate_slug", slug)
        seen.add(slug)
        is_default = slug in _DEFAULT_ORDER
        protected = slug in PROTECTED_SLUGS
        if protected and (item.label is not None or item.enabled is False):
            raise _err("protected_category", f"{slug} cannot be renamed, disabled, or deleted")
        cur = existing.get(slug)
        # Resolve final values (partial-patch: None keeps current).
        label = item.label if item.label is not None else (cur["label"] if cur else None)
        if not is_default and label is None:
            raise _err("label_required", f"custom category {slug} needs a label")
        if label is not None:
            label = label.strip()
            if not label:
                raise _err("empty_label", slug)
            if len(label) > _MAX_LABEL:
                raise _err("label_too_long", slug)
        label = label if label is not None else _DEFAULT_LABELS.get(slug, slug)
        enabled = item.enabled if item.enabled is not None else (cur["enabled"] if cur else True)
        sort = item.sort if item.sort is not None else (cur["sort"] if cur else 0)
        if not enabled:
            sweep_slugs.add(slug)
        upserts.append((user_id, slug, label, bool(enabled), int(sort)))

    for slug in patch.deletes:
        _validate_slug(slug)
        if slug in _DEFAULT_ORDER:
            raise _err("cannot_delete_default", slug)
        sweep_slugs.add(slug)

    # Post-patch effective category count (existing + adds − deletes).
    effective = set(existing) | {u[1] for u in upserts}
    effective -= set(patch.deletes)
    if len(effective) > _MAX_CATEGORIES:
        raise _err("too_many_categories", f"max {_MAX_CATEGORIES}")

    with ms.transaction():
        for uid, slug, label, enabled, sort in upserts:
            ms.execute(
                "INSERT INTO email_category_taxonomy "
                "(user_id, slug, label, enabled, sort, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, NOW()) "
                "ON CONFLICT (user_id, slug) DO UPDATE SET "
                "label = EXCLUDED.label, enabled = EXCLUDED.enabled, sort = EXCLUDED.sort, "
                "updated_at = NOW()",
                (uid, slug, label, enabled, sort),
            )
        for slug in patch.deletes:
            # SCOPE-EXEMPT: email_category_taxonomy is per-user settings (no scope column).
            ms.execute(
                "DELETE FROM email_category_taxonomy WHERE user_id = %s AND slug = %s",
                (user_id, slug),
            )
        for slug in sweep_slugs:
            _sweep_slug_to_other(ms, user_id, slug)

    config.invalidate_email_taxonomy_cache(user_id)
    return load_taxonomy(user_id)


def put_rules(user_id: str, rules) -> list[dict]:
    """Full-replace the user's rule set."""
    ms = config.get_metadata_store()
    # Validate the category against ALL known slugs (enabled OR disabled), not
    # just enabled ones: dead rules (category_disabled) are intentionally kept
    # and the web does a full-replace PUT, so re-submitting a rule whose category
    # is currently disabled must NOT 422. The classify path (load_rules) already
    # skips rules whose category is disabled.
    known = {c["slug"] for c in load_taxonomy(user_id)}
    if len(rules) > _MAX_RULES:
        raise _err("too_many_rules", f"max {_MAX_RULES}")
    seen: set[str] = set()
    prepared: list[tuple] = []
    for r in rules:
        if not r.rule_id or r.rule_id in seen:
            raise _err("duplicate_rule_id", r.rule_id)
        seen.add(r.rule_id)
        match = r.match.model_dump() if hasattr(r.match, "model_dump") else dict(r.match)
        _validate_match(match)
        if r.category not in known:
            raise _err("rule_unknown_category", r.category)
        prepared.append((user_id, r.rule_id, int(r.priority), match, r.category, bool(r.enabled)))

    import json

    with ms.transaction():
        # SCOPE-EXEMPT: email_classification_rule is per-user settings (no scope column).
        ms.execute("DELETE FROM email_classification_rule WHERE user_id = %s", (user_id,))
        for uid, rid, prio, match, cat, en in prepared:
            ms.execute(
                "INSERT INTO email_classification_rule "
                "(user_id, rule_id, priority, match, category, enabled, updated_at) "
                "VALUES (%s, %s, %s, %s::jsonb, %s, %s, NOW())",
                (uid, rid, prio, json.dumps(match), cat, en),
            )
    config.invalidate_email_taxonomy_cache(user_id)
    return list_rules(user_id)


def list_rules(user_id: str) -> list[dict]:
    """All stored rules (including disabled/dead) with ``category_disabled`` flags."""
    ms = config.get_metadata_store()
    enabled = enabled_slugs(user_id)
    # SCOPE-EXEMPT: email_classification_rule is per-user settings (no scope column).
    rows = ms.fetch_all(
        "SELECT rule_id, priority, match, category, enabled "
        "FROM email_classification_rule WHERE user_id = %s ORDER BY priority ASC, rule_id ASC",
        (user_id,),
    )
    out: list[dict] = []
    for r in rows or []:
        match = r["match"] if isinstance(r["match"], dict) else {}
        out.append(
            {
                "rule_id": r["rule_id"],
                "priority": int(r["priority"]),
                "match": match,
                "category": r["category"],
                "enabled": bool(r["enabled"]),
                "category_disabled": r["category"] not in enabled,
            }
        )
    return out


# --------------------------------------------------------------------------- #
# Reclassify + sweep (imap-scoped; signals.source_id is TEXT, sources.id is UUID)
# --------------------------------------------------------------------------- #
# SCOPE-EXEMPT: `sources` is scope-less (plan §2.10) and this subquery restricts
# reclassify to the caller's OWN imap sources — owner-scoped, not a household read.
_IMAP_SOURCE_SUBQUERY = (
    "source_id IN (SELECT id::text FROM sources WHERE user_id = %s AND source_type = 'imap')"
)


def _sweep_slug_to_other(ms, user_id: str, slug: str) -> None:
    if slug == "other":
        return
    # SCOPE-EXEMPT: owner-only sweep of the caller's own imap signals (user_id +
    # own-source subquery); the scope column governs cross-household read visibility,
    # not this owner-scoped reclassify write.
    ms.execute(
        "UPDATE signals SET category = 'other' "
        f"WHERE user_id = %s AND category = %s AND {_IMAP_SOURCE_SUBQUERY}",
        (user_id, slug, user_id),
    )


def reclassify(user_id: str, slug: str) -> dict:
    """Subject-only re-evaluation of the user's imap signals in ``slug``.

    No LLM, no headers/sender (transient). Only moves a signal when the
    deterministic matcher returns a *different, positive* slug — never drains
    unmatched signals into ``other``.
    """
    if slug not in enabled_slugs(user_id):
        raise HTTPException(status_code=404, detail={"code": "unknown_category", "message": slug})

    from services import email_classifier

    ms = config.get_metadata_store()
    taxonomy = enabled_slugs(user_id)
    rules = load_rules(user_id)
    # SCOPE-EXEMPT: reads only the caller's own imap signals (user_id + own-source
    # subquery) for owner-scoped reclassification, not a household-visibility read.
    rows = ms.fetch_all(
        "SELECT signal_id, title FROM signals "
        f"WHERE user_id = %s AND category = %s AND {_IMAP_SOURCE_SUBQUERY}",
        (user_id, slug, user_id),
    )
    scanned = len(rows or [])
    movers: dict[str, list[str]] = {}
    failed = 0
    for row in rows or []:
        try:
            new = email_classifier.classify_rules(
                title=row["title"] or "",
                headers={},
                sender="",
                known_contact=False,
                taxonomy=taxonomy,
                user_rules=rules,
                body="",  # body not persisted — body rules never reclassify (LUM-630)
            )
        except Exception:
            failed += 1
            continue
        if new and new != slug and new in taxonomy:
            movers.setdefault(new, []).append(row["signal_id"])

    moved = 0
    for target, ids in movers.items():
        try:
            # SCOPE-EXEMPT: owner-scoped write — moves only the caller's own signals
            # (user_id) whose ids were gathered from the own-source read above.
            ms.execute(
                "UPDATE signals SET category = %s WHERE user_id = %s AND signal_id = ANY(%s)",
                (target, user_id, ids),
            )
            moved += len(ids)
        except Exception:
            failed += len(ids)
    return {"slug": slug, "scanned": scanned, "moved": moved, "failed": failed}
