# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Capability egress + trust policy (LUM-613, pillar b of LUM-507).

Pure policy over the capability manifest + registry — no `tethered`, no
adapters. Owns:

* the **community-tier trust predicate** (`is_community_capability`), derived
  from an operator-maintained, *origin-pinned* first-party allowlist keyed on
  the manifest ``id`` (NOT the author-declared ``license_mode``, NOT the
  author-chosen compose service name);
* the **fail-closed community-dispatch gate** predicate
  (`is_community_dispatch_allowed`);
* the **in-process untrusted-plugin refusal** (`assert_first_party_plugin`,
  ADR-170 §0: untrusted code is out-of-process only).

Honesty note (do not soften): Core cannot stop an out-of-process capability
*container* from exfiltrating — its egress is its own process. This module
*declares* and *gates dispatch*; the hard container-network guarantee is
LUM-618. See ADR-153.

Trust is bound to ``(id, expected base_url)`` rather than ``id`` alone because
the registry's manifest fetch is unauthenticated (`_fetch_one`): a neighbour
answering ``/capabilities`` with a first-party ``id`` from a different origin
must NOT inherit first-party trust. LUM-614 (signing) replaces this with
cryptographic identity.
"""

import logging
import os
from pathlib import Path

# Re-export the manifest-level validator so tests/registry have one import site.
from models.capability import normalise_external_endpoints  # noqa: F401  (re-export)

_log = logging.getLogger(__name__)

# Runtime-loaded trust oracle. Ships inside the orchestrator package (must be
# present in the image), NOT under scripts/ (dev/CI only). Override for tests.
_DEFAULT_FIRST_PARTY_FILE = Path(__file__).resolve().parent.parent / "first_party_capabilities.txt"
_FIRST_PARTY_ENV = "LUMOGIS_FIRST_PARTY_CAPABILITIES_FILE"

_first_party_cache: dict[str, str] | None = None

# LUM-618 "containment present" marker. Operator-maintained list of capability
# ids asserted to run network-contained (isolated Docker network + egress proxy).
# Ships inside the orchestrator package (must be in the image). The compose-policy
# Pass C verifies the wiring matches; this file is the runtime dispatch signal.
_DEFAULT_CONTAINED_FILE = Path(__file__).resolve().parent.parent / "contained_capabilities.txt"
_CONTAINED_ENV = "LUMOGIS_CONTAINED_CAPABILITIES_FILE"

_contained_cache: frozenset[str] | None = None
_contained_mtime: float | None = None


class UntrustedInProcessPluginError(RuntimeError):
    """Raised when a non-first-party module is offered to the in-process loader.

    Threat model (honest): this guards against *accidental/legitimate*
    third-party in-process loading and future-loader regression — untrusted
    code must run out-of-process (ADR-170 §0). It is NOT a defence against an
    attacker who can already write into ``plugins/`` (that is host code
    execution, out of scope for this control).
    """


def normalise_base_url(base_url: str) -> str:
    return (base_url or "").strip().rstrip("/").lower()


def _first_party_file() -> Path:
    override = os.environ.get(_FIRST_PARTY_ENV, "").strip()
    return Path(override) if override else _DEFAULT_FIRST_PARTY_FILE


def load_first_party_capabilities(*, refresh: bool = False) -> dict[str, str]:
    """Load the ``id -> expected base_url`` first-party allowlist.

    Lines are ``<capability_id> <expected_base_url>`` (whitespace-separated);
    ``#`` comments and blank lines are ignored. A missing file yields an empty
    map — which means *everything* classifies community (fail-safe: no
    capability is accidentally trusted). Cached; pass ``refresh=True`` (or set
    the override env) in tests.
    """
    global _first_party_cache
    if _first_party_cache is not None and not refresh:
        return _first_party_cache
    mapping: dict[str, str] = {}
    path = _first_party_file()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        _log.warning(
            "first_party_capabilities file not found at %s — every OOP capability "
            "will classify community (fail-closed)",
            path,
        )
        _first_party_cache = mapping
        return mapping
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 2:
            _log.warning("first_party_capabilities: ignoring malformed line %r", raw)
            continue
        cap_id, base_url = parts[0], parts[1]
        mapping[cap_id] = normalise_base_url(base_url)
    _first_party_cache = mapping
    return mapping


def is_community_capability(
    *, capability_id: str, base_url: str, first_party: dict[str, str]
) -> bool:
    """True iff a discovered capability is community/untrusted (LUM-613).

    First-party requires BOTH the ``id`` in the allowlist AND discovery at that
    id's pinned origin. Any other id, or a first-party id from an unexpected
    origin (shadow attempt), classifies community. Deployment-independent
    (native + Docker) because it never keys on the compose service name.
    """
    pinned = first_party.get(capability_id)
    if pinned is None:
        return True
    return normalise_base_url(base_url) != pinned


def _contained_file() -> Path:
    override = os.environ.get(_CONTAINED_ENV, "").strip()
    return Path(override) if override else _DEFAULT_CONTAINED_FILE


def load_contained_capabilities(*, refresh: bool = False) -> frozenset[str]:
    """Load the set of capability ids asserted network-contained (LUM-618/LUM-621).

    One ``id`` per line; ``#`` comments and blank lines ignored. A missing file
    yields an empty set — which means *no* community capability is treated as
    contained (fail-closed: containment must be positively asserted).

    Cached with **mtime reload** (LUM-621): editing the marker takes effect on
    the next call without an orchestrator restart. If the file disappears after
    a successful load, the last good set is kept (match stop-entity behaviour).
    Pass ``refresh=True`` in tests to force a re-read.
    """
    global _contained_cache, _contained_mtime
    path = _contained_file()
    if refresh:
        _contained_cache = None
        _contained_mtime = None
    try:
        mtime = path.stat().st_mtime
    except OSError:
        if _contained_cache is not None:
            _log.warning(
                "contained_capabilities file disappeared at %s — keeping last loaded set",
                path,
            )
            return _contained_cache
        _log.warning(
            "contained_capabilities file not found at %s — no community capability "
            "will be treated as contained (fail-closed)",
            path,
        )
        _contained_cache = frozenset()
        _contained_mtime = None
        return _contained_cache

    if _contained_cache is not None and mtime == _contained_mtime:
        return _contained_cache

    ids: set[str] = set()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        _log.warning(
            "contained_capabilities file not readable at %s — no community capability "
            "will be treated as contained (fail-closed)",
            path,
        )
        _contained_cache = frozenset()
        _contained_mtime = mtime
        return _contained_cache
    for raw in text.splitlines():
        # Strip inline comments first (`acme.cap  # note` → `acme.cap`); ids never
        # contain '#' (see is_grantable_capability_id), so this is safe and avoids
        # silently dropping an id an operator annotated inline.
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        # After comment-stripping a valid line is a single bare id; anything with
        # embedded whitespace is malformed — skip it fail-closed.
        if len(line.split()) != 1:
            _log.warning("contained_capabilities: ignoring malformed line %r", raw)
            continue
        ids.add(line)
    _contained_cache = frozenset(ids)
    _contained_mtime = mtime
    return _contained_cache


def is_dispatch_allowed(
    *,
    is_community: bool,
    capability_id: str | None,
    contained_ids: frozenset[str],
    legacy_opt_in: bool,
) -> bool:
    """LUM-618 dispatch gate (supersedes :func:`is_community_dispatch_allowed`).

    A capability is dispatchable iff it is not community, OR it is positively
    asserted network-contained (its id is in ``contained_ids``, verified in CI
    by compose-policy Pass C), OR the deprecated global escape hatch is set.
    Fail-closed: an uncontained community capability without the legacy flag is
    refused.
    """
    if not is_community:
        return True
    if capability_id is not None and capability_id in contained_ids:
        return True
    return legacy_opt_in


def is_community_dispatch_allowed(*, is_community: bool, opt_in: bool) -> bool:
    """Back-compat shim for the LUM-613 gate signature (superseded by
    :func:`is_dispatch_allowed`).

    Preserves the exact original behaviour by passing ``capability_id=None`` and
    an empty ``contained_ids`` — so the middle (containment) clause is always
    false and the predicate collapses to ``not is_community or opt_in``. All
    in-repo dispatch call sites now use :func:`is_dispatch_allowed` directly; this
    shim is retained only for API stability (external / not-yet-migrated callers).
    """
    return is_dispatch_allowed(
        is_community=is_community,
        capability_id=None,
        contained_ids=frozenset(),
        legacy_opt_in=opt_in,
    )


def assert_first_party_plugin(module_name: str, *, first_party: frozenset[str]) -> None:
    """Refuse a non-first-party in-process plugin module (ADR-170 §0)."""
    if module_name not in first_party:
        raise UntrustedInProcessPluginError(
            f"in-process plugin {module_name!r} is not first-party; untrusted "
            "capabilities must run out-of-process (OOP-only, ADR-170 §0)"
        )
