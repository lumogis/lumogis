# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Visibility helpers (KG-service mirror of orchestrator/visibility.py).

Same contract, narrowed to the backends the KG service actually owns:

* :func:`visible_cypher_fragment` — primary surface for FalkorDB reads
  in ``graph/query.py`` and ``graph/viz_routes.py``.
* :func:`visible_filter` — Postgres surface needed by the deterministic
  entity-resolution path (``graph/query.py::resolve_entity_by_name``)
  which queries the canonical ``entities`` table directly via the KG
  service's own metadata store. Mirrors
  ``orchestrator.visibility.visible_filter`` exactly.
* :func:`visible_qdrant_filter` — Qdrant payload filter for semantic entity
  search in ``graph/query.py`` (LUM-210); mirrors Core's contract.
* :func:`admin_unfiltered_cypher_fragment` — for the ``/mgm`` admin
  surfaces.

Qdrant helpers are mirrored for **``visible_qdrant_filter``** only (entity
semantic search in CONTEXT_BUILDING). Other Qdrant-only helpers remain on
the Core side unless the KG service needs them.

Drift discipline: this module's API surface is checked by
``orchestrator/tests/test_phase3_grep_gate.py`` (extended in plan
§2.5 acceptance #4). If you add a new helper to the orchestrator
side, mirror it here with the same name and docstring or remove it
from the cross-service contract.

See also: ``.cursor/plans/personal_shared_system_memory_scopes.plan.md``
``.cursor/adrs/personal_shared_system_memory_scopes.md``
"""

from __future__ import annotations

import re
from typing import Literal
from typing import Optional

from auth import UserContext

_QUALIFIER_RE = re.compile(r"[a-z_][a-z0-9_]*")

Scope = Literal["personal", "shared", "system"]
DEFAULT_SCOPE: Scope = "personal"

_VALID_SCOPES: frozenset[str] = frozenset({"personal", "shared", "system"})


def _get_allows_shared_from_db(user_id: str) -> bool:
    """Mirror ``orchestrator.services.users.get_allows_shared`` (LUM-577).

    The KG service has no users service module but shares the household
    Postgres; when ``UserContext.allows_shared`` is unset we read the row
    here so CONTEXT_BUILDING and viz routes honour personal-only members.
    """
    import config

    ms = config.get_metadata_store()
    row = ms.fetch_one("SELECT allows_shared FROM users WHERE id = %s", (user_id,))
    if row is None:
        return True
    return bool(row.get("allows_shared", True))


def _effective_allows_shared(user: UserContext) -> bool:
    """Mirror orchestrator: only authenticated admins bypass ``allows_shared``."""
    if user.role == "admin" and user.is_authenticated:
        return True
    if user.allows_shared is not None:
        return user.allows_shared
    return _get_allows_shared_from_db(user.user_id)


def _validate_scope_filter(scope_filter: Optional[str]) -> Optional[Scope]:
    if scope_filter is None:
        return None
    if scope_filter not in _VALID_SCOPES:
        raise ValueError(
            f"invalid scope_filter={scope_filter!r}; must be one of {sorted(_VALID_SCOPES)} or None"
        )
    return scope_filter  # type: ignore[return-value]


def visible_filter(
    user: UserContext,
    scope_filter: Optional[str] = None,
) -> tuple[str, tuple]:
    """Return ``(SQL clause, params tuple)`` for a Postgres ``WHERE``.

    Identical contract to ``orchestrator.visibility.visible_filter``;
    used by the KG service's deterministic entity resolver
    (``graph/query.py::resolve_entity_by_name``) when reading the
    canonical ``entities`` table out of the household-shared Postgres.
    """
    sf = _validate_scope_filter(scope_filter)
    me = user.user_id
    allows_shared = _effective_allows_shared(user)
    if sf == "personal":
        return ("(scope = 'personal' AND user_id = %s)", (me,))
    if sf == "shared":
        if not allows_shared:
            return ("(FALSE)", ())
        return ("(scope = 'shared')", ())
    if sf == "system":
        return ("(scope = 'system')", ())
    if allows_shared:
        return (
            "((scope = 'personal' AND user_id = %s) OR scope IN ('shared','system'))",
            (me,),
        )
    return (
        "((scope = 'personal' AND user_id = %s) OR scope = 'system')",
        (me,),
    )


def not_hidden_sql(viewer_id: str, qualifier: str = "") -> tuple[str, tuple]:
    """Return ``(SQL clause, params)``: the entry is not hidden by ``viewer_id`` (LUM-966).

    Mirrors ``orchestrator.visibility.not_hidden_sql`` exactly (a test compares the
    two outputs). A member's **Hide** of another member's shared entry lives in
    ``entity_hidden``, keyed by the owner's source row. Hide is a per-viewer view
    preference, **not** access control.
    """
    if qualifier and not _QUALIFIER_RE.fullmatch(qualifier):
        raise ValueError(f"invalid qualifier={qualifier!r}; must match [a-z_][a-z0-9_]*")
    p = f"{qualifier}." if qualifier else ""
    return (
        "NOT EXISTS (SELECT 1 FROM entity_hidden h WHERE h.user_id = %s "
        f"AND h.entity_key = coalesce({p}published_from, {p}entity_id))",
        (viewer_id,),
    )


# ─── Qdrant ──────────────────────────────────────────────────────────────────
# Mirrored from ``orchestrator/visibility.py`` — CONTEXT_BUILDING semantic path
# (LUM-210) searches the ``entities`` collection with the same household filter.


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
    ``filter`` argument. Composition with caller-supplied filters MUST
    AND-merge under a parent ``must`` — never alongside the helper's
    ``should`` (which would broaden visibility).
    """
    sf = _validate_scope_filter(scope_filter)
    me = user.user_id
    allows_shared = _effective_allows_shared(user)
    if sf == "personal":
        return {
            "must": [
                {"key": "scope", "match": {"value": "personal"}},
                {"key": "user_id", "match": {"value": me}},
            ]
        }
    if sf == "shared":
        if not allows_shared:
            return {"must": [{"key": "user_id", "match": {"value": "__no_shared_access__"}}]}
        return {"must": [{"key": "scope", "match": {"value": "shared"}}]}
    if sf == "system":
        return {"must": [{"key": "scope", "match": {"value": "system"}}]}
    if allows_shared:
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
    return {
        "should": [
            {
                "must": [
                    {"key": "scope", "match": {"value": "personal"}},
                    {"key": "user_id", "match": {"value": me}},
                ]
            },
            {"key": "scope", "match": {"value": "system"}},
        ]
    }


def visible_cypher_fragment(
    user: UserContext,
    alias: str = "n",
    scope_filter: Optional[str] = None,
) -> tuple[str, dict]:
    """Return ``(Cypher fragment, params dict)`` usable inside ``WHERE``.

    Identical contract to ``orchestrator.visibility.visible_cypher_fragment``.
    """
    sf = _validate_scope_filter(scope_filter)
    me = user.user_id
    a = alias
    allows_shared = _effective_allows_shared(user)
    if sf == "personal":
        return (f"({a}.scope = 'personal' AND {a}.user_id = $vis_me)", {"vis_me": me})
    if sf == "shared":
        if not allows_shared:
            return ("(FALSE)", {})
        return (f"({a}.scope = 'shared')", {})
    if sf == "system":
        return (f"({a}.scope = 'system')", {})
    if allows_shared:
        return (
            f"(({a}.scope = 'personal' AND {a}.user_id = $vis_me) "
            f"OR {a}.scope IN ['shared','system'])",
            {"vis_me": me},
        )
    return (
        f"(({a}.scope = 'personal' AND {a}.user_id = $vis_me) OR {a}.scope = 'system')",
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


__all__ = [
    "Scope",
    "DEFAULT_SCOPE",
    "visible_filter",
    "visible_qdrant_filter",
    "visible_cypher_fragment",
    "admin_unfiltered_cypher_fragment",
]
