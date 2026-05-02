# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Action-type → risk-tier mapping for the v1 web façade.

A flat, additive lookup. Hard-limited action_types (from
:data:`actions.executor._HARD_LIMITED`) always resolve to
``RiskTier.hard_limit`` — no override. Everything else is sourced from
the static :data:`_TIER_MAP` curated in this module. Unknown action
types fall back to :attr:`RiskTier.medium` with a single WARNING log
per process per action_type, so an operator notices a missing mapping
without flooding logs.

Plan ``cross_device_lumogis_web`` §Permission model — risk tier is a
client-only UI cue (badge colour, default focus on Cancel for high-risk
modals); it is **not** a security gate. The actual gate is
:func:`actions.executor.is_hard_limited` + the per-user permission row.
"""

from __future__ import annotations

import logging

from actions.executor import is_hard_limited
from models.api_v1 import RiskTier

_log = logging.getLogger(__name__)

# Curated mapping. Add new action_types here as connectors land. Keep
# the file flat (no nested categories) so a quick grep confirms the tier
# without reading code.
_TIER_MAP: dict[str, RiskTier] = {
    # Read-only queries / lookups → low.
    "read": RiskTier.low,
    "read_file": RiskTier.low,
    "list": RiskTier.low,
    "list_files": RiskTier.low,
    "search": RiskTier.low,
    "lookup": RiskTier.low,
    "fetch": RiskTier.low,
    "get": RiskTier.low,
    # Reversible writes → medium.
    "write": RiskTier.medium,
    "write_file": RiskTier.medium,
    "create": RiskTier.medium,
    "update": RiskTier.medium,
    "edit": RiskTier.medium,
    "draft": RiskTier.medium,
    "save": RiskTier.medium,
    "tag": RiskTier.medium,
    # Non-reversible writes (still allowed but worth a confirmation) → high.
    "delete": RiskTier.high,
    "remove": RiskTier.high,
    "send": RiskTier.high,
    "publish": RiskTier.high,
    "post": RiskTier.high,
    "rename": RiskTier.high,
    "move": RiskTier.high,
}

# Per-process dedup so we WARNING once per unknown type, not once per row.
_warned_unknown: set[str] = set()


def risk_tier_for(action_type: str) -> RiskTier:
    """Resolve the UI-side risk tier for ``action_type``.

    Resolution order:

    1. ``action_type`` is hard-limited → :attr:`RiskTier.hard_limit`.
    2. Exact match in :data:`_TIER_MAP` → that tier.
    3. Unknown → :attr:`RiskTier.medium` (defensive default), with a
       one-shot WARNING log per process per action_type so the gap is
       visible during dev without log spam.
    """
    if is_hard_limited(action_type):
        return RiskTier.hard_limit

    tier = _TIER_MAP.get(action_type)
    if tier is not None:
        return tier

    if action_type not in _warned_unknown:
        _warned_unknown.add(action_type)
        _log.warning(
            "api_v1_risk: unknown action_type=%r; defaulting to RiskTier.medium. "
            "Add an explicit row to services.api_v1_risk._TIER_MAP.",
            action_type,
        )
    return RiskTier.medium


def elevation_eligible(action_type: str) -> bool:
    """``False`` if the action_type is hard-limited; ``True`` otherwise.

    Mirrors :func:`actions.executor.is_hard_limited` so the v1 façade
    has a single import surface for the predicate (UI uses this to
    disable the "Always allow" button on hard-limited rows).
    """
    return not is_hard_limited(action_type)


__all__ = ["risk_tier_for", "elevation_eligible"]
