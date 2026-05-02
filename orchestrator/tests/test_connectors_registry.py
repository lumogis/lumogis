# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for ``connectors.registry``'s canonical mapping invariants.

Pins the post-refactor surface (single :data:`CONNECTORS` mapping of id
to :class:`ConnectorSpec`) introduced by the
``connector_registry_canonical_mapping`` chunk:

* :data:`connectors.registry.CONNECTORS`
* :class:`connectors.registry.ConnectorSpec`
* :func:`connectors.registry.iter_registered_with_descriptions`
* :func:`connectors.registry.register` (now requires ``description``)

The substrate-level tests for ``REGISTERED_CONNECTORS`` /
``require_registered()`` semantics live in
``test_connector_credentials_service.py`` and are not duplicated here.
"""

from __future__ import annotations

import dataclasses

import pytest
from connectors import registry as connectors_registry
from connectors.registry import CONNECTORS
from connectors.registry import ConnectorSpec


def test_canonical_mapping_shape() -> None:
    """:data:`CONNECTORS` is the single source of truth — pin its shape.

    Every value MUST be a :class:`ConnectorSpec`, ``spec.id`` MUST equal
    its dict key (no copy/paste drift between the key and the embedded
    id), and ``spec.description`` MUST be a non-empty string. A failure
    here would otherwise surface as either a 500 from the
    ``GET /api/v1/me/connector-credentials/registry`` route or, worse,
    as a UI dropdown row whose displayed id doesn't match what gets
    sent on PUT.
    """
    assert CONNECTORS, "CONNECTORS must declare at least one connector"
    for cid, spec in CONNECTORS.items():
        assert isinstance(spec, ConnectorSpec), (
            f"CONNECTORS[{cid!r}] must be a ConnectorSpec, got {type(spec).__name__}"
        )
        assert spec.id == cid, (
            f"ConnectorSpec.id must equal its CONNECTORS key; key={cid!r}, spec.id={spec.id!r}"
        )
        assert isinstance(spec.description, str) and spec.description.strip(), (
            f"CONNECTORS[{cid!r}].description must be a non-empty string"
        )


def test_connector_spec_is_frozen_dataclass() -> None:
    """:class:`ConnectorSpec` is intentionally immutable.

    The canonical mapping is a module-level singleton and accidental
    in-place mutation of a :class:`ConnectorSpec` would silently change
    every consumer (UI dropdown copy, the registry route's wire shape,
    every error message that names the connector). ``frozen=True`` on
    the dataclass enforces that.
    """
    assert dataclasses.is_dataclass(ConnectorSpec)
    spec = next(iter(CONNECTORS.values()))
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.description = "mutated"  # type: ignore[misc]


def test_registered_connectors_is_derived_from_canonical_mapping() -> None:
    """:data:`REGISTERED_CONNECTORS` MUST be the keys of :data:`CONNECTORS`.

    Backward-compat alias — the refactor's structural promise is that
    "registered" and "described" can no longer drift, because both
    derive from the same dict. Pin that they really are equal at module
    load time (regression guard against someone re-introducing a
    parallel set literal).
    """
    assert connectors_registry.REGISTERED_CONNECTORS == frozenset(CONNECTORS.keys())


def test_iter_registered_with_descriptions_shape_and_sort_order() -> None:
    """Pin the wire shape returned by the registry endpoint.

    Output is a list of ``{"id": str, "description": str}`` dicts with
    no extra keys (even if :class:`ConnectorSpec` later grows fields),
    sorted ascending by ``id``. The two seed connectors
    (``testconnector`` + ``ntfy``) both appear with their canonical
    descriptions — guards against a silent description rename breaking
    the wire copy the UI dropdown depends on.
    """
    items = connectors_registry.iter_registered_with_descriptions()

    assert isinstance(items, list)
    assert len(items) == len(CONNECTORS)
    for item in items:
        assert set(item.keys()) == {"id", "description"}, (
            f"unexpected keys on registry item: {item!r}"
        )
        assert isinstance(item["id"], str) and item["id"]
        assert isinstance(item["description"], str) and item["description"]

    ids = [item["id"] for item in items]
    assert ids == sorted(ids), f"items must be sorted by id ASC; got {ids!r}"

    by_id = {item["id"]: item["description"] for item in items}
    assert "testconnector" in by_id
    assert by_id["testconnector"].startswith("Synthetic plumbing test")
    assert "ntfy" in by_id
    assert "ntfy" in by_id["ntfy"].lower()


def test_register_requires_description() -> None:
    """:func:`register` MUST refuse to add a connector without metadata.

    This is the structural guarantee the refactor exists to provide:
    there is no longer any code path through which a new connector id
    can enter :data:`CONNECTORS` without a human-readable description
    attached. ``description`` is keyword-only so callers cannot
    silently pass an empty string positionally either.
    """
    with pytest.raises(TypeError):
        connectors_registry.register("brandnew_no_desc")  # type: ignore[call-arg]

    with pytest.raises(ValueError) as excinfo:
        connectors_registry.register("brandnew_blank_desc", description="")
    assert "description" in str(excinfo.value)

    with pytest.raises(ValueError):
        connectors_registry.register("brandnew_whitespace_desc", description="   ")

    assert "brandnew_no_desc" not in CONNECTORS
    assert "brandnew_blank_desc" not in CONNECTORS
    assert "brandnew_whitespace_desc" not in CONNECTORS


def test_register_then_iter_includes_new_connector() -> None:
    """Happy-path :func:`register` adds a fully-formed entry.

    ``register()`` MUST update both :data:`CONNECTORS` and the derived
    :data:`REGISTERED_CONNECTORS` frozenset (it is rebound — see the
    function docstring), and the new entry MUST flow straight through
    to :func:`iter_registered_with_descriptions`. Cleans up after
    itself so the global registry is unchanged once the test ends.
    """
    cid = "brandnew_with_desc"
    desc = "ephemeral test fixture"
    assert cid not in CONNECTORS

    try:
        connectors_registry.register(cid, description=desc)

        assert cid in CONNECTORS
        assert CONNECTORS[cid] == ConnectorSpec(id=cid, description=desc)
        assert cid in connectors_registry.REGISTERED_CONNECTORS

        items = connectors_registry.iter_registered_with_descriptions()
        by_id = {item["id"]: item["description"] for item in items}
        assert by_id.get(cid) == desc
    finally:
        CONNECTORS.pop(cid, None)
        connectors_registry.REGISTERED_CONNECTORS = frozenset(CONNECTORS.keys())


def test_direct_canonical_mutation_is_immediately_visible() -> None:
    """Regression guard: bypassing :func:`register` still uses one source.

    A reader who edits :data:`CONNECTORS` directly (e.g. a test fixture
    inserting a throwaway spec) MUST see the change reflected in
    :func:`iter_registered_with_descriptions` without needing to touch
    a second structure. This is the post-refactor analogue of the old
    "registered without description" test — the failure mode it guarded
    is now structurally impossible, but the spirit of the test (one
    source of truth, no parallel updates required) remains valuable.
    """
    cid = "directly_inserted"
    desc = "directly inserted spec"
    assert cid not in CONNECTORS

    try:
        CONNECTORS[cid] = ConnectorSpec(id=cid, description=desc)

        items = connectors_registry.iter_registered_with_descriptions()
        by_id = {item["id"]: item["description"] for item in items}
        assert by_id.get(cid) == desc
    finally:
        CONNECTORS.pop(cid, None)
