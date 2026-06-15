# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Tests for GET /settings/ollama-discovery response shape (LUM-423)."""

import os
import unittest
from unittest.mock import MagicMock
from unittest.mock import patch


class TestOllamaDiscovery(unittest.TestCase):
    def test_ollama_discovery_includes_embedding_and_default_model(self):
        from services.admin_ollama import build_ollama_discovery

        local_models = [{"name": "llama3.2:3b", "size": 1_000_000}]
        catalog = [{"name": "phi3", "description": "test"}]
        all_models = {
            "llama": {
                "model": "llama3.2:3b",
                "base_url": "http://ollama:11434",
            },
        }
        store = MagicMock()

        with (
            patch("ollama_client.list_local_models", return_value=local_models),
            patch("ollama_client.fetch_catalog", return_value=catalog),
            patch("config.get_all_models_config", return_value=all_models),
            patch("config.get_metadata_store", return_value=store),
            patch("services.admin_ollama._safe_get_setting", return_value="llama"),
            patch("services.admin_ollama._safe_is_enabled", return_value=True),
            patch.dict(os.environ, {"EMBEDDING_MODEL": "nomic-embed-text"}),
        ):
            response = build_ollama_discovery()

        self.assertIn("embedding_model", response)
        self.assertIn("default_model", response)
        self.assertEqual(response["embedding_model"], "nomic-embed-text")
        self.assertEqual(response["default_model"], "llama")
        self.assertEqual(response["alias_map"], {"llama3.2:3b": "llama"})
        self.assertEqual(len(response["local"]), 1)
        self.assertIn("display_name", response["local"][0])
        self.assertFalse(response["catalog"][0]["installed"])


if __name__ == "__main__":
    unittest.main()
