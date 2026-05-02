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
* :func:`admin_unfiltered_cypher_fragment` — for the ``/mgm`` admin
  surfaces.

Qdrant helpers are intentionally NOT mirrored — the KG service does not
read Qdrant.

Drift discipline: this module's API surface is checked by
``orchestrator/tests/test_phase3_grep_gate.py`` (extended in plan
§2.5 acceptance #4). If you add a new helper to the orchestrator
side, mirror it here with the same name and docstring or remove it
from the cross-service contract.

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
    if scope_filter is None:
        return None
    if scope_filter not in _VALID_SCOPES:
        raise ValueError(
            f"invalid scope_filter={scope_filter!r}; "
            f"must be one of {sorted(_VALID_SCOPES)} or None"
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
    if sf == "personal":
        return ("(scope = 'personal' AND user_id = %s)", (me,))
    if sf == "shared":
        return ("(scope = 'shared')", ())
    if sf == "system":
        return ("(scope = 'system')", ())
    return (
        "((scope = 'personal' AND user_id = %s) OR scope IN ('shared','system'))",
        (me,),
    )


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


__all__ = [
    "Scope",
    "DEFAULT_SCOPE",
    "visible_filter",
    "visible_cypher_fragment",
    "admin_unfiltered_cypher_fragment",
]
