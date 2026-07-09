# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Tests for POST /settings/ollama-pull qdrant_init_warning (LUM-452)."""

import os
import unittest
from unittest.mock import MagicMock
from unittest.mock import patch

from routes.admin import QDRANT_INIT_WARNING_MSG
from routes.admin import OllamaPullRequest
from routes.admin import ollama_pull


class TestOllamaPullQdrantWarning(unittest.TestCase):
    def _pull(self, name: str):
        request = MagicMock()
        request.app.state = MagicMock()
        request.app.state.embedding_ready = False
        return ollama_pull(request, OllamaPullRequest(name=name))

    def test_embedding_pull_qdrant_init_failure_returns_warning(self):
        embedder = MagicMock()
        embedder.ping.return_value = True
        embedder.vector_size = 768
        vs = MagicMock()
        vs.create_collection.side_effect = RuntimeError("qdrant down")

        with (
            patch("ollama_client.pull_model"),
            patch("routes.admin._sync_librechat_config"),
            patch("config.get_embedder", return_value=embedder),
            patch("config.get_vector_store", return_value=vs),
            patch.dict(os.environ, {"EMBEDDING_MODEL": "nomic-embed-text"}),
        ):
            response = self._pull("nomic-embed-text")

        self.assertEqual(response["status"], "pulled")
        self.assertEqual(response["name"], "nomic-embed-text")
        self.assertEqual(response["qdrant_init_warning"], QDRANT_INIT_WARNING_MSG)

    def test_non_embedding_pull_no_warning(self):
        with (
            patch("ollama_client.pull_model"),
            patch("routes.admin._sync_librechat_config"),
            patch.dict(os.environ, {"EMBEDDING_MODEL": "nomic-embed-text"}),
        ):
            response = self._pull("tinyllama")

        self.assertEqual(response["status"], "pulled")
        self.assertEqual(response["name"], "tinyllama")
        self.assertIsNone(response["qdrant_init_warning"])


if __name__ == "__main__":
    unittest.main()
