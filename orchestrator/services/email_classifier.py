# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Local-only classification of incoming mail (LUM-170).

The headline feature of the email connector: every incoming message gets an
**importance score** plus a **category** from a small fixed taxonomy. Hybrid:
deterministic rules (List-Unsubscribe / bulk headers, sender domain,
known-contact membership) as a fast first pass, and — for email signals only —
the existing local ``process_signal`` LLM pass supplies the residual category
label. **Cloud LLM is never used on email content**: this module owns the
on-box-local guard (:func:`require_local_model`) that ``signal_processor`` calls
before any LLM call for an ``imap`` signal.
"""

from __future__ import annotations

from dataclasses import dataclass

from services import task_models

DEFAULT_SEED_SLUGS: tuple[str, ...] = (
    "school",
    "insurance_renewal",
    "bills_finance",
    "family_personal",
    "promotional_bulk",
    "other",
)
# Back-compat alias (LUM-170 imports/tests). The live taxonomy is now per-user
# and user-editable (LUM-629); DEFAULT_SEED_SLUGS is only the default fallback.
EMAIL_CATEGORIES: tuple[str, ...] = DEFAULT_SEED_SLUGS
_DEFAULT_TAXONOMY: frozenset[str] = frozenset(DEFAULT_SEED_SLUGS)

# The on-box helpers moved to ``services.task_models`` (LUM-877); re-exported so
# existing imports and seams keep working.
_DEFAULT_LOCAL_HOSTS = task_models._DEFAULT_LOCAL_HOSTS
_email_llm_allowlist = task_models._email_llm_allowlist
_model_is_on_box_local = task_models.is_on_box_local

# Lightweight subject/sender keyword hints — a local, deterministic backstop so
# category is useful even in metadata-only mode (no body, weak LLM signal).
_CATEGORY_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("school", ("school", "teacher", "classroom", "homework", "pta", "principal", "daycare")),
    ("insurance_renewal", ("insurance", "policy", "renewal", "premium", "claim", "coverage")),
    ("bills_finance", ("invoice", "bill", "payment", "statement", "receipt", "overdue", "bank")),
)


@dataclass(frozen=True)
class EmailClassification:
    category: str
    importance_score: float
    is_important: bool


def require_local_model(user_id: str | None, *, task: str) -> str | None:
    """Return the model for an email ``task`` iff it is on-box local, else None.

    ``task`` is ``email_classify``, ``email_reply_draft`` or ``email_compose_refine``
    (``services/task_models.py``). Returns ``None`` when the resolved model is a
    cloud or off-box model, including the code default; the caller then skips the
    LLM call entirely (rules-only) and logs ``code=email_body_cloud_blocked``.
    **This is the hard privacy boundary in code.** ``task_models`` keeps the final
    on-box assertion (through its module attribute, so a monkeypatch reaches it).
    """
    return task_models.resolve_on_box_model(task, user_id=user_id)


def _has_bulk_header(headers: dict[str, str]) -> bool:
    lowered = {k.lower(): (v or "") for k, v in (headers or {}).items()}
    if lowered.get("list-unsubscribe"):
        return True
    precedence = lowered.get("precedence", "").lower()
    if precedence in ("bulk", "list", "junk"):
        return True
    if lowered.get("list-id"):
        return True
    return False


def coerce_category(raw: object, taxonomy: frozenset[str] = _DEFAULT_TAXONOMY) -> str:
    """Map an arbitrary label onto the effective taxonomy; unknown → ``other``."""
    if isinstance(raw, str) and raw.strip().lower() in taxonomy:
        return raw.strip().lower()
    return "other"


def classify_rules(
    *,
    title: str,
    headers: dict[str, str],
    sender: str,
    known_contact: bool,
    taxonomy: frozenset[str] = _DEFAULT_TAXONOMY,
    user_rules: tuple = (),
    body: str = "",
) -> str | None:
    """Deterministic, LLM-free first pass. Returns a category or ``None``.

    Precedence (LUM-629): **user-authored rules first** (priority order, already
    filtered to enabled + in-taxonomy), then the built-in confident cases
    (bulk/list → ``promotional_bulk``; subject/sender keyword hint; known
    personal contact → ``family_personal``) — each gated on ``slug in taxonomy``.
    Returns ``None`` when nothing matches (the guarded LLM pass then supplies the
    label).
    """
    if user_rules:
        from services.email_taxonomy import match_rule

        for rule in user_rules:
            cat = rule.get("category")
            if cat in taxonomy and match_rule(
                rule.get("match") or {}, title=title, headers=headers, sender=sender, body=body
            ):
                return cat

    if _has_bulk_header(headers) and not known_contact and "promotional_bulk" in taxonomy:
        return "promotional_bulk"
    hay = f"{title or ''} {sender or ''}".lower()
    for category, words in _CATEGORY_KEYWORDS:
        if category in taxonomy and any(w in hay for w in words):
            return category
    if known_contact and "family_personal" in taxonomy:
        return "family_personal"
    return None


def finalize(
    *,
    llm_data: dict,
    title: str,
    headers: dict[str, str],
    sender: str,
    known_contact: bool,
    threshold: float,
    taxonomy: frozenset[str] = _DEFAULT_TAXONOMY,
    user_rules: tuple = (),
    body: str = "",
) -> EmailClassification:
    """Merge the deterministic rules with the (possibly empty) LLM output.

    ``llm_data`` is ``{}`` when the LLM pass was skipped (cloud/off-box model
    blocked, or no model) — the result is then rules-only. ``body`` is the
    extracted plain-text body (LUM-630), passed to body-keyword rules.
    """
    rule_category = classify_rules(
        title=title,
        headers=headers,
        sender=sender,
        known_contact=known_contact,
        taxonomy=taxonomy,
        user_rules=user_rules,
        body=body,
    )
    category = rule_category or coerce_category(llm_data.get("category"), taxonomy)

    importance = float(llm_data.get("importance_score", 0.0) or 0.0)
    importance = max(0.0, min(1.0, importance))
    # Cap keyed on the mail *being bulk* (not on the final slug) so bulk mail a
    # user rule routes to a custom label still can't start notifying (LUM-629).
    if _has_bulk_header(headers) and not known_contact:
        importance = min(importance, 0.15)
    if known_contact:
        importance = max(importance, 0.6)
    importance = round(max(0.0, min(1.0, importance)), 4)

    return EmailClassification(
        category=category,
        importance_score=importance,
        is_important=importance >= threshold,
    )
