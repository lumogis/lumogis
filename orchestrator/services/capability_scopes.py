# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Capability permission-scope helpers (LUM-612, LUM-507 pillar a).

A capability's permission identity is the connector name ``capability.{id}``.
It declares ``permissions_required`` (LUM-41 manifest); Core enforces those
required scopes against the user's granted set at the invocation chokepoint
(``services.execution.ToolExecutor.execute_capability_http``), least-privilege /
fail-closed.

The manifest ``id`` must be **grant-shaped** — the grant route can only address a
``capability.{id}`` connector whose id matches :data:`CAPABILITY_ID_PATTERN`
(kept in sync with ``routes/connector_permissions.py`` ``_CONNECTOR_PATTERN``).
A capability whose id is out of shape is refused at registration rather than
registering, enforcing fail-closed denials, and being permanently ungrantable.
"""

from __future__ import annotations

import re
from typing import Any

# id charset/length that the grant route can address as `capability.{id}`.
# Total connector string `capability.<id>` must fit the route's max_length=64,
# so the id itself is <= 53 chars. Lowercase alnum start, then [a-z0-9._-].
CAPABILITY_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,42}$")

# A required-scope string is `resource:action` (both lowercase snake segments).
# Must match `permissions._normalise_scopes` so a scope a manifest DECLARES can
# always be GRANTED — a malformed required scope would otherwise be enforced
# (fail-closed) yet un-grantable, a silent permanent lockout.
SCOPE_PATTERN = re.compile(r"^[a-z0-9_]+:[a-z0-9_]+$")

_CONNECTOR_PREFIX = "capability."


def capability_connector(capability_id: str) -> str:
    """The permission connector name for a capability id."""
    return f"{_CONNECTOR_PREFIX}{capability_id}"


def is_capability_connector(connector: str) -> bool:
    return connector.startswith(_CONNECTOR_PREFIX)


def capability_id_from_connector(connector: str) -> str:
    """Strip the ``capability.`` prefix (returns the input if absent)."""
    if connector.startswith(_CONNECTOR_PREFIX):
        return connector[len(_CONNECTOR_PREFIX):]
    return connector


def is_grantable_capability_id(capability_id: str) -> bool:
    """True when the id can be addressed as a `capability.{id}` grant connector."""
    return bool(CAPABILITY_ID_PATTERN.fullmatch(capability_id or ""))


def malformed_required_scopes(manifest: Any) -> list[str]:
    """Declared ``permissions_required`` entries that are NOT `resource:action`.

    Empty when the manifest is clean. A non-empty result means the capability
    declares a scope Core could never grant (see :data:`SCOPE_PATTERN`) — the
    registry refuses such a manifest rather than registering a silent lockout.
    """
    raw = getattr(manifest, "permissions_required", None) or []
    return [str(s).strip() for s in raw if not SCOPE_PATTERN.fullmatch(str(s).strip())]


def required_scopes_for(manifest: Any) -> list[str]:
    """Normalise a manifest's declared ``permissions_required`` to a scope list."""
    raw = getattr(manifest, "permissions_required", None) or []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        s = str(item).strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out
