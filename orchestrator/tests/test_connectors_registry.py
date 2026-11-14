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

    Operator-managed ids are the one documented exclusion (LUM-65): they are
    registered so their own admin route works, but every write surface this
    listing feeds refuses them, so a picker entry could only ever 422.
    """
    items = connectors_registry.iter_registered_with_descriptions()

    assert isinstance(items, list)
    assert len(items) == len(CONNECTORS) - len(connectors_registry.OPERATOR_MANAGED_CONNECTORS)
    assert connectors_registry.OPERATOR_MANAGED_CONNECTORS, (
        "expected at least one operator-managed connector; if this set is now "
        "empty the exclusion above is untested"
    )
    listed_ids = {item["id"] for item in items}
    assert listed_ids.isdisjoint(connectors_registry.OPERATOR_MANAGED_CONNECTORS)
    for item in items:
        # LUM-834 chunk B widened this projection from {id, description} to
        # add `fields`. The assertion stays an EQUALITY on purpose: it is the
        # guard the module docstring describes — "adding a field to
        # ConnectorSpec does NOT auto-leak it to the route" — and it fired
        # correctly when `fields` was added. Widening it to a subset check
        # would retire the guard instead of updating it.
        assert set(item.keys()) == {"id", "description", "fields"}, (
            f"unexpected keys on registry item: {item!r}"
        )
        assert isinstance(item["id"], str) and item["id"]
        assert isinstance(item["description"], str) and item["description"]
        assert isinstance(item["fields"], list)
        for f in item["fields"]:
            assert set(f.keys()) == {"name", "label", "is_secret"}, (
                f"unexpected keys on field descriptor: {f!r}"
            )
            assert isinstance(f["name"], str) and f["name"]
            assert isinstance(f["label"], str) and f["label"]
            assert isinstance(f["is_secret"], bool)
            # No value ever crosses this boundary. The registry publishes the
            # SHAPE of a credential; the payload lives encrypted in a column
            # nothing on this path reads.
            assert "value" not in f

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


# ---------------------------------------------------------------------------
# LUM-834 chunk B — the field schema, and the register() bug it exposed
# ---------------------------------------------------------------------------


def test_every_in_scope_connector_declares_a_field_schema():
    """The G4 deliverable, asserted against the REGISTRY rather than a count.

    The three-copy pin below only walks connectors that HAVE a schema, so an
    omitted one is invisible to it — which is exactly how "the five `llm_*`"
    (there are six) would have shipped five of six with a green suite, leaving
    Mistral's card reading "this connector's shape is not described".

    Deriving the expected set by subtraction means adding a connector without a
    schema fails here rather than on a user's screen.
    """
    from connectors import registry as reg

    #: Out of scope, with the reason per id — not a count.
    excluded = {
        reg.IMAP,  # no card: configured per-mailbox from Email signals
        reg.SMTP,  # same
        reg.SYSTEM_SMTP,  # operator-managed; hidden from GET /registry
        reg.TESTCONNECTOR,  # synthetic plumbing connector, no real payload
    }
    missing = sorted(
        cid for cid, spec in reg.CONNECTORS.items() if cid not in excluded and not spec.fields
    )
    assert missing == [], f"connectors with no declared field schema: {missing}"


def test_the_registry_schema_matches_each_loaders_payload_keys():
    """The three-copy pin: the registry and each loader's `_PAYLOAD_KEYS` agree.

    Two copies of per-connector field knowledge exist by necessity (the loader
    validates, the registry describes). A comment saying "must not drift"
    enforces nothing; this does.
    """
    import re
    from pathlib import Path

    from connectors import registry as reg

    services = Path(__file__).resolve().parent.parent / "services"
    checked = 0
    for connector, module in (
        (reg.CALDAV, "caldav_credentials"),
        (reg.PAPERLESS, "paperless_credentials"),
    ):
        src = (services / f"{module}.py").read_text(encoding="utf-8")
        m = re.search(r"_PAYLOAD_KEYS[^=]*=\s*\(([^)]*)\)", src)
        assert m, f"could not find _PAYLOAD_KEYS in {module}.py"
        loader_keys = set(re.findall(r'"([a-z_]+)"', m.group(1)))
        assert loader_keys, f"parsed no keys from {module}.py"
        assert set(reg.field_names(connector)) == loader_keys, (
            f"{connector}: registry declares {reg.field_names(connector)!r} "
            f"but the loader reads {sorted(loader_keys)!r}"
        )
        checked += 1
    assert checked == 2, "the loop asserted nothing"


def test_secret_field_names_is_a_subset_of_the_declared_fields():
    from connectors import registry as reg

    for cid, spec in reg.CONNECTORS.items():
        assert set(reg.secret_field_names(cid)) <= set(reg.field_names(cid)), cid


def test_registering_a_connector_twice_preserves_its_field_schema():
    """`register()` REBUILDS the spec, so anything it does not accept is dropped.

    Before this chunk it took only (name, description) and silently dropped
    `multi_instance` — a latent bug, because the docstring blesses
    re-registering to fix a typo. Adding `fields` without widening the
    signature would have made it worse: a plugin connector could never declare
    a schema, so its card would permanently read "shape not described".
    """
    from connectors import registry as reg

    name = "lum834_temp_connector"
    fields = (reg.ConnectorField(name="api_key", label="API key", is_secret=True),)
    try:
        reg.register(name, description="temp", multi_instance=True, fields=fields)
        assert reg.CONNECTORS[name].fields == fields
        assert reg.CONNECTORS[name].multi_instance is True

        # The "fix a typo in the description" path must not wipe either.
        reg.register(name, description="temp, fixed", multi_instance=True, fields=fields)
        assert reg.CONNECTORS[name].description == "temp, fixed"
        assert reg.CONNECTORS[name].fields == fields, "re-registering dropped the field schema"
        assert reg.CONNECTORS[name].multi_instance is True, (
            "re-registering dropped multi_instance — the latent bug this pins"
        )
    finally:
        reg.CONNECTORS.pop(name, None)
        reg.REGISTERED_CONNECTORS = frozenset(reg.CONNECTORS.keys())


def test_the_registry_schema_matches_each_typed_web_form():
    """The other half of the three-copy pin — and the half that was missing.

    `test_the_registry_schema_matches_each_loaders_payload_keys` covers only the
    two connectors that declare `_PAYLOAD_KEYS` (caldav, paperless). That left
    ntfy's three fields and the six `llm_*` schemas pinned against **nothing**:
    a registry field name could drift from what the form actually submits and
    the card would render "not set" beside a field that is set, with a green
    suite — the exact defect class the pin exists to prevent.

    Reads the payload literal out of the TSX rather than restating it here, for
    the reason chunk A's cross-language pin gives: a test that hard-codes its
    expectation cannot catch the form writing the wrong key.
    """
    import re
    from pathlib import Path

    from connectors import registry as reg

    forms = (
        Path(__file__).resolve().parents[2] / "clients/lumogis-web/src/features/credentials/forms"
    )

    checked: list[str] = []

    # ntfy — `onChange({ url, topic, token: token.trim() })`
    src = (forms / "NtfyForm.tsx").read_text(encoding="utf-8")
    m = re.search(r"onChange\(\{([^}]*)\}\)", src)
    assert m, "could not find the submitted payload literal in NtfyForm.tsx"
    ntfy_keys = set(re.findall(r"(\w+)\s*(?::|,|$)", m.group(1).strip()))
    ntfy_keys = {k for k in ntfy_keys if k not in {"trim"}}
    assert set(reg.field_names(reg.NTFY)) == ntfy_keys, (
        f"ntfy: registry declares {reg.field_names(reg.NTFY)!r}, form submits {sorted(ntfy_keys)!r}"
    )
    checked.append(reg.NTFY)

    # Every llm_* — one shared form, `onChange({ api_key: apiKey })`
    src = (forms / "LlmApiKeyForm.tsx").read_text(encoding="utf-8")
    m = re.search(r"onChange\(\{\s*(\w+)\s*:", src)
    assert m, "could not find the submitted payload key in LlmApiKeyForm.tsx"
    llm_key = m.group(1)
    for cid in sorted(c for c in reg.CONNECTORS if c.startswith("llm_")):
        assert reg.field_names(cid) == [llm_key], (
            f"{cid}: registry declares {reg.field_names(cid)!r}, form submits {[llm_key]!r}"
        )
        checked.append(cid)

    # Guards the guard: if the regexes above stopped matching, the loop would
    # assert nothing and pass.
    assert len(checked) == 7, f"expected ntfy + six llm_*, pinned {checked!r}"
