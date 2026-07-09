# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Feature-flag registry — env-var gates for experimental, disabled-by-default work.

LUM-126. Experimental subsystems (consolidation agent, proactive pipes,
write-back MCP tools, graph consolidation, context compaction) land
incrementally. This module lets that code merge to ``main`` in a
**disabled-by-default** state so it is built, tested, and reviewable without
shipping to users prematurely — the public AGPL repo gets clean production
behaviour, and contributors flip a single env var to develop against a flag.

Design notes (see issue questions):

- **All flags default to ``False``.** A flag is enabled only when its env var
  is explicitly truthy (``true``/``1``/``yes``/``on``), matching the parsing
  used by :func:`config.is_injection_sanitiser_enabled`.
- **Read from the environment on each call** rather than frozen at import.
  In production the environment is fixed before boot, so this is equivalent to
  a static read; reading live keeps the admin endpoint honest and makes the
  flags trivially testable (``monkeypatch.setenv``) with no mid-session
  mutation in real deployments.
- The set of flags is **closed**: :func:`is_enabled` raises on an unknown key
  so a typo in a call site fails loudly instead of silently reading ``False``.

To add a flag, append a :class:`FeatureFlag` to ``_FLAG_LIST`` below and
document it in ``config/test.env.example`` + ``CONTRIBUTING.md``.
"""

from __future__ import annotations

from dataclasses import dataclass

_TRUTHY = frozenset({"true", "1", "yes", "on"})

# Env-var prefix for every feature flag. Keeps the namespace greppable and
# makes "is this an experimental gate?" answerable from the variable name alone.
FLAG_ENV_PREFIX = "LUMOGIS_FF_"


@dataclass(frozen=True)
class FeatureFlag:
    """A single experimental gate.

    ``key`` is the stable identifier used in code (:func:`is_enabled`); the env
    var read at runtime is ``FLAG_ENV_PREFIX + key`` unless ``env_var`` is set.
    """

    key: str
    description: str
    default: bool = False
    env_var: str = ""

    def resolved_env_var(self) -> str:
        return self.env_var or (FLAG_ENV_PREFIX + self.key)


# Registry — keep alphabetical by key. Every flag defaults to False.
_FLAG_LIST: tuple[FeatureFlag, ...] = (
    FeatureFlag(
        key="CONSOLIDATION_AGENT",
        description="Sleep-time consolidation agent — background entity summarisation (LUM-109).",
    ),
    FeatureFlag(
        key="CONTEXT_COMPACTION",
        description="Three-layer context compaction (microcompact / autocompact / full) (LUM-122).",
    ),
    FeatureFlag(
        key="EGRESS_GUARD",
        description=(
            "In-process egress allowlist on LLM adapter calls — defense-in-depth (LUM-553)."
        ),
    ),
    FeatureFlag(
        key="GRAPH_CONSOLIDATION",
        description="Graph consolidation pipeline — Memify-pattern community summaries (LUM-106).",
    ),
    FeatureFlag(
        key="PROACTIVE_PIPES",
        description="Proactive routine engine — pipe.md spec + heartbeat daemon (LUM-110).",
    ),
    FeatureFlag(
        key="TEMPORAL_KG",
        description=("Two-axis temporal validity on KG edges + contradiction detection (LUM-104)."),
    ),
    FeatureFlag(
        key="WRITE_BACK_MCP",
        description="LLM-writable graph primitives — write-back MCP tools (LUM-108).",
    ),
)

_FLAGS: dict[str, FeatureFlag] = {f.key: f for f in _FLAG_LIST}


class UnknownFeatureFlag(KeyError):
    """Raised when a flag key is not in the registry (catches typos at call sites)."""


def _truthy(raw: str | None) -> bool:
    if raw is None:
        return False
    return raw.strip().lower() in _TRUTHY


def _read_env(env_var: str):
    # Imported lazily so tests can monkeypatch ``os.environ`` freely and so the
    # module has no import-time environment dependency.
    import os

    return os.environ.get(env_var)


def all_flag_keys() -> list[str]:
    """Registry keys, sorted (stable order for the admin endpoint and tests)."""
    return sorted(_FLAGS)


def get_flag(key: str) -> FeatureFlag:
    try:
        return _FLAGS[key]
    except KeyError:
        raise UnknownFeatureFlag(key) from None


def is_enabled(key: str) -> bool:
    """Return whether the named flag is currently enabled.

    Raises :class:`UnknownFeatureFlag` for an unregistered key.
    """
    flag = get_flag(key)
    raw = _read_env(flag.resolved_env_var())
    if raw is None:
        return flag.default
    return _truthy(raw)


def enabled_flags() -> set[str]:
    """Keys of all currently-enabled flags."""
    return {k for k in _FLAGS if is_enabled(k)}


def snapshot() -> list[dict[str, object]]:
    """Current state of every flag — feeds the admin visibility endpoint.

    Never includes secrets: only flag metadata and a boolean state.
    """
    out: list[dict[str, object]] = []
    for key in all_flag_keys():
        flag = _FLAGS[key]
        out.append(
            {
                "key": key,
                "env_var": flag.resolved_env_var(),
                "description": flag.description,
                "default": flag.default,
                "enabled": is_enabled(key),
            }
        )
    return out
