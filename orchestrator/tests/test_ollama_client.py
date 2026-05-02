# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Tests for ollama_client helpers."""

import json
from unittest.mock import MagicMock
from unittest.mock import patch

import httpx
import ollama_client
import pytest


class TestListLocalModels:
    def test_returns_models_on_success(self):
        fake_resp = MagicMock()
        fake_resp.json.return_value = {"models": [{"name": "qwen2.5:7b", "size": 4_500_000_000}]}
        fake_resp.raise_for_status = MagicMock()
        with patch("ollama_client.httpx.get", return_value=fake_resp):
            result = ollama_client.list_local_models()
        assert result == [{"name": "qwen2.5:7b", "size": 4_500_000_000}]

    def test_returns_empty_on_connection_error(self):
        with patch("ollama_client.httpx.get", side_effect=httpx.ConnectError("refused")):
            result = ollama_client.list_local_models()
        assert result == []

    def test_returns_empty_on_http_error(self):
        fake_resp = MagicMock()
        fake_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "503", request=MagicMock(), response=MagicMock()
        )
        with patch("ollama_client.httpx.get", return_value=fake_resp):
            result = ollama_client.list_local_models()
        assert result == []


class TestFetchCatalog:
    def test_parses_list_response(self, tmp_path, monkeypatch):
        # VERIFY-PLAN: updated to match implementation — fetch_catalog prepends
        # fallback entries not present in the live registry, so tests must isolate
        # from the real fallback file to avoid spurious extra rows.
        fb_file = tmp_path / "fallback.json"
        fb_file.write_text("[]")
        monkeypatch.setattr(ollama_client, "_FALLBACK_CATALOG_PATH", fb_file)

        fake_resp = MagicMock()
        fake_resp.raise_for_status = MagicMock()
        fake_resp.json.return_value = [
            {
                "name": "llama3.2",
                "description": "Fast model",
                "tags": ["3b"],
                "pull_count": 1000,
                "updated_at": "2025-01-01",
            },
        ]
        with patch("ollama_client.httpx.get", return_value=fake_resp):
            result = ollama_client.fetch_catalog()
        assert len(result) == 1
        assert result[0]["name"] == "llama3.2"
        assert result[0]["pulls"] == 1000

    def test_parses_dict_response(self, tmp_path, monkeypatch):
        # VERIFY-PLAN: updated to match implementation — see test_parses_list_response.
        fb_file = tmp_path / "fallback.json"
        fb_file.write_text("[]")
        monkeypatch.setattr(ollama_client, "_FALLBACK_CATALOG_PATH", fb_file)

        fake_resp = MagicMock()
        fake_resp.raise_for_status = MagicMock()
        fake_resp.json.return_value = {
            "models": [
                {
                    "name": "gemma3",
                    "description": "Google Gemma",
                    "tags": [],
                    "pull_count": 500,
                    "updated_at": "",
                }
            ]
        }
        with patch("ollama_client.httpx.get", return_value=fake_resp):
            result = ollama_client.fetch_catalog()
        assert result[0]["name"] == "gemma3"

    def test_falls_back_on_network_error(self, tmp_path, monkeypatch):
        fallback = [
            {
                "name": "fallback-model",
                "description": "fallback",
                "tags": [],
                "pulls": 0,
                "updated_at": "",
            }
        ]
        fb_file = tmp_path / "fallback.json"
        fb_file.write_text(json.dumps(fallback))
        monkeypatch.setattr(ollama_client, "_FALLBACK_CATALOG_PATH", fb_file)

        with patch("ollama_client.httpx.get", side_effect=httpx.ConnectError("unreachable")):
            result = ollama_client.fetch_catalog()
        assert result == fallback

    def test_falls_back_on_bad_json(self, tmp_path, monkeypatch):
        fallback = [{"name": "fb", "description": "", "tags": [], "pulls": 0, "updated_at": ""}]
        fb_file = tmp_path / "fallback.json"
        fb_file.write_text(json.dumps(fallback))
        monkeypatch.setattr(ollama_client, "_FALLBACK_CATALOG_PATH", fb_file)

        fake_resp = MagicMock()
        fake_resp.raise_for_status = MagicMock()
        fake_resp.json.side_effect = ValueError("bad json")
        with patch("ollama_client.httpx.get", return_value=fake_resp):
            result = ollama_client.fetch_catalog()
        assert result[0]["name"] == "fb"

    def test_merges_capabilities_and_training_cutoff_from_fallback(self, tmp_path, monkeypatch):
        """Live registry rows must carry curated capabilities and training_cutoff from fallback."""
        fallback = [
            {
                "name": "qwen2.5",
                "description": "Alibaba Qwen 2.5",
                "capabilities": ["multilingual", "reasoning"],
                "training_cutoff": "~Mid 2024",
                "tags": ["7b", "14b"],
            }
        ]
        fb_file = tmp_path / "fallback.json"
        fb_file.write_text(json.dumps(fallback))
        monkeypatch.setattr(ollama_client, "_FALLBACK_CATALOG_PATH", fb_file)

        fake_resp = MagicMock()
        fake_resp.raise_for_status = MagicMock()
        fake_resp.json.return_value = [
            {
                "name": "qwen2.5:7b",
                "description": "registry blurb",
                "pull_count": 9999,
                "updated_at": "2025-01-01",
            },
        ]
        with patch("ollama_client.httpx.get", return_value=fake_resp):
            result = ollama_client.fetch_catalog()

        row = next((r for r in result if r["name"] == "qwen2.5"), None)
        assert row is not None, "qwen2.5 not found in merged result"
        assert row["capabilities"] == ["multilingual", "reasoning"]
        assert row["training_cutoff"] == "~Mid 2024"
        # fallback description should win over registry blurb
        assert row["description"] == "Alibaba Qwen 2.5"

    def test_live_rows_without_fallback_omit_capabilities(self, tmp_path, monkeypatch):
        """Models present in live registry but absent from fallback must not have spurious capabilities."""
        fb_file = tmp_path / "fallback.json"
        fb_file.write_text("[]")
        monkeypatch.setattr(ollama_client, "_FALLBACK_CATALOG_PATH", fb_file)

        fake_resp = MagicMock()
        fake_resp.raise_for_status = MagicMock()
        fake_resp.json.return_value = [
            {"name": "unknown-model:latest", "description": "", "pull_count": 1, "updated_at": ""},
        ]
        with patch("ollama_client.httpx.get", return_value=fake_resp):
            result = ollama_client.fetch_catalog()

        assert len(result) == 1
        assert "capabilities" not in result[0]
        assert "training_cutoff" not in result[0]


class TestPullModel:
    def test_calls_ollama_pull(self):
        fake_resp = MagicMock()
        fake_resp.is_success = True
        with patch("ollama_client.httpx.post", return_value=fake_resp) as mock_post:
            ollama_client.pull_model("qwen2.5:7b")
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        assert "qwen2.5:7b" in str(call_kwargs)

    def test_raises_on_http_error(self):
        fake_resp = MagicMock()
        fake_resp.is_success = False
        fake_resp.status_code = 500
        fake_resp.text = ""
        fake_resp.json.return_value = {"error": "pull model manifest: not found"}
        with patch("ollama_client.httpx.post", return_value=fake_resp):
            with pytest.raises(RuntimeError, match="not found"):
                ollama_client.pull_model("no-such-model")
