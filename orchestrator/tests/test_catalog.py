# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Validate structure and capability slugs in ollama_catalog_fallback.json."""

import ollama_client

_ALLOWED_CAPABILITIES = {"code", "reasoning", "multilingual", "tools", "fast", "vision"}


def test_catalog_loads():
    catalog = ollama_client.get_curated_catalog()
    assert isinstance(catalog, list)
    assert len(catalog) > 0, "Fallback catalog must not be empty"


def test_all_entries_have_name_and_description():
    for entry in ollama_client.get_curated_catalog():
        name = entry.get("name", "")
        assert name, f"Entry missing 'name': {entry!r}"
        assert entry.get("description"), f"Entry '{name}' has no description"


def test_capabilities_slugs_are_valid():
    for entry in ollama_client.get_curated_catalog():
        name = entry.get("name", "<unknown>")
        caps = entry.get("capabilities")
        if caps is None:
            continue  # missing key is fine; explicit null or non-list is not
        assert isinstance(caps, list), (
            f"Entry '{name}': 'capabilities' must be a list, got {type(caps).__name__}"
        )
        invalid = set(caps) - _ALLOWED_CAPABILITIES
        assert not invalid, (
            f"Entry '{name}': unknown capability slug(s) {invalid!r}. "
            f"Allowed: {sorted(_ALLOWED_CAPABILITIES)}"
        )


def test_training_cutoff_is_string_if_present():
    for entry in ollama_client.get_curated_catalog():
        name = entry.get("name", "<unknown>")
        cutoff = entry.get("training_cutoff")
        if cutoff is None:
            continue
        assert isinstance(cutoff, str), (
            f"Entry '{name}': 'training_cutoff' must be a string, got {type(cutoff).__name__}"
        )


def test_all_entries_have_capabilities_post_enrich():
    """Every entry must have a non-null capabilities list now that enrich-catalog is complete."""
    for entry in ollama_client.get_curated_catalog():
        name = entry.get("name", "<unknown>")
        caps = entry.get("capabilities")
        assert caps is not None, (
            f"Entry '{name}' is missing 'capabilities'. "
            "All entries must be enriched (enrich-catalog is done)."
        )
        assert isinstance(caps, list), (
            f"Entry '{name}': 'capabilities' must be a list, got {type(caps).__name__}"
        )
