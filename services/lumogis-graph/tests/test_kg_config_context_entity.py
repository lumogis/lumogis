# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Sanity: KG `config` mirrors Core CONTEXT_BUILDING entity getters (LUM-210)."""


def test_kg_config_entity_getters_import_and_defaults():
    import config as kg_config

    assert kg_config.get_context_entity_budget() >= 1
    assert isinstance(kg_config.get_context_entity_semantic_enabled(), bool)
    assert kg_config.get_context_entity_semantic_topk() >= 1
    assert 0.0 <= kg_config.get_context_entity_semantic_threshold() <= 1.0
    assert isinstance(kg_config.get_context_entity_rank_alpha(), float)
    assert isinstance(kg_config.get_context_entity_rank_beta(), float)
    assert isinstance(kg_config.get_context_entity_rank_gamma(), float)
    assert isinstance(kg_config.get_context_entity_rank_delta(), float)
    assert isinstance(kg_config.get_context_entity_explicit_bonus(), float)
    assert kg_config.get_context_max_edges() >= 1
