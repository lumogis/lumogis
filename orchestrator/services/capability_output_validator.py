# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Validate a capability invoke's ``output`` against its declared schema (LUM-41).

Output-schema validation is **mandatory when a non-trivial schema is declared**
(ADR-169 refinement): an unenforced ``output_schema`` would be exactly the
"documentary field" anti-pattern the invoke contract removes. A loosely-typed
tool declares a *trivial* schema (bare ``{"type": "string"}`` / ``{"type":
"object"}`` / ``{}``) and is not validated.

Triviality rule: a schema is trivial iff it declares no *constraining* keyword
(see :data:`_CONSTRAINING_KEYS`) — i.e. only a bare ``type`` (or nothing).

Compiled ``jsonschema`` validators are cached **content-addressed** by a hash of
the canonical schema JSON, NOT by tool identity: the capability registry
replaces a service's manifest in place on every refresh, so a name-keyed cache
would serve a stale validator when a tool's schema changes.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import jsonschema
from jsonschema.protocols import Validator

_CONSTRAINING_KEYS: frozenset[str] = frozenset(
    {
        "properties",
        "required",
        "items",
        "enum",
        "const",
        "additionalProperties",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "pattern",
        "format",
        "minItems",
        "maxItems",
        "minLength",
        "maxLength",
        "anyOf",
        "allOf",
        "oneOf",
        "not",
    }
)

# Content-addressed cache: sha256(canonical schema JSON) -> compiled validator.
_VALIDATOR_CACHE: dict[str, Validator] = {}


class OutputSchemaError(Exception):
    """Raised when an invoke ``output`` fails its declared non-trivial schema."""


def is_trivial_schema(output_schema: dict[str, Any] | None) -> bool:
    """True when the schema imposes no constraint (validation is skipped)."""
    if not output_schema:
        return True
    return not (set(output_schema) & _CONSTRAINING_KEYS)


def _canonical(output_schema: dict[str, Any]) -> str:
    return json.dumps(output_schema, sort_keys=True, separators=(",", ":"))


def _validator_for(output_schema: dict[str, Any]) -> Validator:
    key = hashlib.sha256(_canonical(output_schema).encode("utf-8")).hexdigest()
    cached = _VALIDATOR_CACHE.get(key)
    if cached is not None:
        return cached
    cls = jsonschema.validators.validator_for(output_schema)
    cls.check_schema(output_schema)
    validator = cls(output_schema)
    _VALIDATOR_CACHE[key] = validator
    return validator


def validate_output(output: Any, output_schema: dict[str, Any] | None) -> None:
    """Validate ``output`` against ``output_schema``; no-op for a trivial schema.

    Raises :class:`OutputSchemaError` on mismatch (or on an unusable schema —
    an author's malformed schema must not silently pass output through).
    """
    if is_trivial_schema(output_schema):
        return
    assert output_schema is not None  # narrowed by is_trivial_schema
    try:
        validator = _validator_for(output_schema)
    except jsonschema.exceptions.SchemaError as exc:
        raise OutputSchemaError(f"invalid output_schema: {exc.message}") from exc
    errors = sorted(validator.iter_errors(output), key=lambda e: list(e.path))
    if errors:
        first = errors[0]
        loc = "/".join(str(p) for p in first.path) or "<root>"
        raise OutputSchemaError(f"output does not match schema at {loc}: {first.message}")


def reset_cache_for_tests() -> None:
    """Clear the compiled-validator cache (test hygiene)."""
    _VALIDATOR_CACHE.clear()
