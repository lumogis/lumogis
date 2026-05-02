# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Visibility helpers for the personal/shared/system memory-scope model.

A single source of truth for the read-side rule that every user-facing
retrieval surface must apply:

    (scope = 'personal' AND user_id = $me) OR scope IN ('shared','system')

Three siblings, one per backend:

* :func:`visible_filter` — Postgres ``(clause, params)`` tuple suitable
  for splicing into a ``WHERE`` clause.
* :func:`visible_qdrant_filter` — Qdrant filter dict (the exact shape
  pinned in plan §8 / arbitration D3.4).
* :func:`visible_cypher_fragment` — Cypher fragment + params dict for
  FalkorDB queries.

Admin god-mode is **not** baked into these helpers. The four admin-only
surfaces that need a cross-user view (``/admin/*``, ``/review-queue``,
``/action-log``, ``/audit-log``, ``/admin/users``) call the explicit
``admin_unfiltered_*()`` siblings below so the bypass surface is
enumerable in code review (one ``rg admin_unfiltered_`` lists every
admin-bypass site). See plan §2.8 for the explicit invariant: an admin
calling a normal retrieval surface with ``?scope=personal`` MUST see
only their own personal rows, NOT cross-user personal data.

See also: ``.cursor/plans/personal_shared_system_memory_scopes.plan.md``
``.cursor/adrs/personal_shared_system_memory_scopes.md``
"""
from __future__ import annotations

from typing import Literal, Optional

from auth import UserContext

Scope = Literal["personal", "shared", "system"]
DEFAULT_SCOPE: Scope = "personal"

_VALID_SCOPES: frozenset[str] = frozenset({"personal", "shared", "system"})


def _validate_scope_filter(scope_filter: Optional[str]) -> Optional[Scope]:
    """Reject any value not in the locked literal set.

    Closes the ``?scope=`` injection surface at the helper boundary so
    every caller does not need to re-validate. Returns the validated
    value (typed as :data:`Scope`) or ``None`` if no filter was given.
    """
    if scope_filter is None:
        return None
    if scope_filter not in _VALID_SCOPES:
        raise ValueError(
            f"invalid scope_filter={scope_filter!r}; "
            f"must be one of {sorted(_VALID_SCOPES)} or None"
        )
    return scope_filter  # type: ignore[return-value]


# ─── Postgres ────────────────────────────────────────────────────────────────


def visible_filter(
    user: UserContext,
    scope_filter: Optional[str] = None,
) -> tuple[str, tuple]:
    """Return ``(parenthesised SQL clause, params tuple)``.

    Default (``scope_filter=None``) returns the household union::

        ((scope = 'personal' AND user_id = %s) OR scope IN ('shared','system'))

    Narrowed paths::

        scope_filter='personal' →  (scope = 'personal' AND user_id = %s)
        scope_filter='shared'   →  (scope = 'shared')
        scope_filter='system'   →  (scope = 'system')

    Always parenthesised so callers can ``AND`` it with their own
    predicates without precedence accidents. Pass-through to a fixed
    set of ``%s`` placeholders — never f-strings into the params.
    """
    sf = _validate_scope_filter(scope_filter)
    me = user.user_id
    if sf == "personal":
        return ("(scope = 'personal' AND user_id = %s)", (me,))
    if sf == "shared":
        return ("(scope = 'shared')", ())
    if sf == "system":
        return ("(scope = 'system')", ())
    # Default: union of personal-mine + shared + system.
    return (
        "((scope = 'personal' AND user_id = %s) OR scope IN ('shared','system'))",
        (me,),
    )


def admin_unfiltered_filter(
    scope_filter: Optional[str] = None,
) -> tuple[str, tuple]:
    """Admin-only: return a clause that does NOT filter on ``user_id``.

    Used exclusively by the admin/audit/review surfaces enumerated in
    plan §2.8. Every call site MUST be tagged ``# ADMIN-BYPASS:`` on
    the line above (acceptance criterion #4 grep-gate).
    """
    sf = _validate_scope_filter(scope_filter)
    if sf is None:
        return ("(TRUE)", ())
    return ("(scope = %s)", (sf,))


# ─── Qdrant ──────────────────────────────────────────────────────────────────


def visible_qdrant_filter(
    user: UserContext,
    scope_filter: Optional[str] = None,
) -> dict:
    """Return a Qdrant filter dict (canonical shape per plan §8).

    Default (``scope_filter=None``)::

        {
          "should": [
            {"must": [
              {"key": "scope",   "match": {"value": "personal"}},
              {"key": "user_id", "match": {"value": <me>}},
            ]},
            {"key": "scope", "match": {"any": ["shared", "system"]}},
          ]
        }

    Narrowed paths:

    * ``scope_filter='personal'`` →
      ``{"must": [scope==personal, user_id==me]}``
    * ``scope_filter='shared'``   → ``{"must": [scope==shared]}``
    * ``scope_filter='system'``   → ``{"must": [scope==system]}``

    All call sites compose this filter into Qdrant's top-level
    ``filter`` argument via ``Filter(**visible_qdrant_filter(user, ...))``.
    Composition with caller-supplied filters (e.g. document-type
    narrowing) MUST AND-merge under a parent ``must`` — never
    alongside the helper's ``should`` (which would broaden visibility).
    """
    sf = _validate_scope_filter(scope_filter)
    me = user.user_id
    if sf == "personal":
        return {
            "must": [
                {"key": "scope", "match": {"value": "personal"}},
                {"key": "user_id", "match": {"value": me}},
            ]
        }
    if sf == "shared":
        return {"must": [{"key": "scope", "match": {"value": "shared"}}]}
    if sf == "system":
        return {"must": [{"key": "scope", "match": {"value": "system"}}]}
    return {
        "should": [
            {
                "must": [
                    {"key": "scope", "match": {"value": "personal"}},
                    {"key": "user_id", "match": {"value": me}},
                ]
            },
            {"key": "scope", "match": {"any": ["shared", "system"]}},
        ]
    }


def admin_unfiltered_qdrant_filter(
    scope_filter: Optional[str] = None,
) -> Optional[dict]:
    """Admin-only Qdrant filter; ``None`` means "no filter at all".

    Tag the call site with ``# ADMIN-BYPASS:`` per plan §8.
    """
    sf = _validate_scope_filter(scope_filter)
    if sf is None:
        return None
    return {"must": [{"key": "scope", "match": {"value": sf}}]}


# ─── FalkorDB / Cypher ───────────────────────────────────────────────────────


def visible_cypher_fragment(
    user: UserContext,
    alias: str = "n",
    scope_filter: Optional[str] = None,
) -> tuple[str, dict]:
    """Return ``(Cypher fragment, params dict)`` usable inside ``WHERE``.

    Default::

        ((n.scope = 'personal' AND n.user_id = $vis_me)
         OR n.scope IN ['shared','system'])

    Always parenthesised. ``alias`` is the node/relationship variable
    the fragment will be applied to; defaults to ``n``. Params are
    bound under the prefix ``vis_*`` to avoid colliding with caller
    bindings.
    """
    sf = _validate_scope_filter(scope_filter)
    me = user.user_id
    a = alias
    if sf == "personal":
        return (f"({a}.scope = 'personal' AND {a}.user_id = $vis_me)", {"vis_me": me})
    if sf == "shared":
        return (f"({a}.scope = 'shared')", {})
    if sf == "system":
        return (f"({a}.scope = 'system')", {})
    return (
        f"(({a}.scope = 'personal' AND {a}.user_id = $vis_me) "
        f"OR {a}.scope IN ['shared','system'])",
        {"vis_me": me},
    )


def admin_unfiltered_cypher_fragment(
    alias: str = "n",
    scope_filter: Optional[str] = None,
) -> tuple[str, dict]:
    """Admin-only Cypher fragment; tag the call site ``# ADMIN-BYPASS:``."""
    sf = _validate_scope_filter(scope_filter)
    if sf is None:
        return ("(TRUE)", {})
    return (f"({alias}.scope = $vis_scope)", {"vis_scope": sf})


# ─── Authored-by-me (per-user export) ────────────────────────────────────────
#
# The export-side filter is intentionally NARROWER than visible_filter:
# visible_filter returns the household-visible union (personal-mine +
# shared + system); authored_by_filter returns ONLY rows the user
# authored ((scope IN ('personal','shared') AND user_id = $me)). System
# rows are excluded; shared rows authored by other users are excluded.
#
# Why this is correct under the personal_shared_system_memory_scopes
# (B6) model: when a user publishes a personal note, the resulting
# "shared" projection row preserves the original author's ``user_id``
# (via ``published_from``). So a (user_id=alice, scope='shared') row IS
# Alice's own shared content; she gets it back on export. A
# (user_id=bob, scope='shared') row that Alice can READ via
# visible_filter is NOT in Alice's export — it belongs to Bob.
#
# Used by ``services/user_export.py``; lives in this module to keep all
# scope-filtering rules in one greppable location.


def authored_by_filter(user_id: str) -> tuple[str, tuple]:
    """Return ``(parenthesised SQL clause, params tuple)`` for per-user export.

    Always parameterised; never f-strings into params. Caller composes
    with ``AND`` against any per-table predicates.
    """
    return (
        "(scope IN ('personal', 'shared') AND user_id = %s)",
        (user_id,),
    )


def authored_by_qdrant_filter(user_id: str) -> dict:
    """Qdrant filter dict for the per-user export read.

    Equivalent to the SQL helper above. Note the live ``qdrant_store
    .scroll_collection`` only accepts a ``user_id`` exact-match filter,
    so the export path uses this dict only as documentation today and
    filters scope client-side (see ``services.user_export`` Qdrant
    section). Shipped here for symmetry and so a future
    ``scroll_collection(scroll_filter=...)`` keyword can land without
    re-deriving the rule.
    """
    return {
        "must": [
            {"key": "scope", "match": {"any": ["personal", "shared"]}},
            {"key": "user_id", "match": {"value": user_id}},
        ]
    }


def authored_by_cypher_fragment(
    user_id: str, alias: str = "n"
) -> tuple[str, dict]:
    """Cypher fragment + params dict for per-user FalkorDB extraction.

    Note: FalkorDB nodes/edges in v1 do NOT carry a ``scope`` property
    (the model lives only in Postgres + Qdrant), so this fragment only
    constrains ``user_id``. Returned shape mirrors
    ``visible_cypher_fragment`` so callers can substitute uniformly.
    """
    return (f"({alias}.user_id = $vis_me)", {"vis_me": user_id})


__all__ = [
    "Scope",
    "DEFAULT_SCOPE",
    "visible_filter",
    "visible_qdrant_filter",
    "visible_cypher_fragment",
    "admin_unfiltered_filter",
    "admin_unfiltered_qdrant_filter",
    "admin_unfiltered_cypher_fragment",
    "authored_by_filter",
    "authored_by_qdrant_filter",
    "authored_by_cypher_fragment",
]
