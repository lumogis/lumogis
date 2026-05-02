# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Tests for the hot-reload KG settings system.

Coverage:
  - GET /kg/settings returns all keys with correct types and sources
  - POST /kg/settings with valid key/value updates the DB and invalidates cache
  - POST /kg/settings with unknown key returns 400
  - POST /kg/settings with invalid type returns 400
  - POST /kg/settings with out-of-range value returns 400
  - DELETE /kg/settings/{key} removes from DB and returns default
  - Cache TTL: second read within 30s returns cached value without extra DB call
  - Cache invalidation: write invalidates cache, next read hits DB
  - DB unavailable: getter returns env var default, no exception
  - entity_quality.score_and_filter_entities uses getter not env var directly
"""

import threading
import time
import types
import unittest
from unittest.mock import MagicMock, patch, call

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_meta_store(rows: list[dict] | None = None, raise_on_fetch: bool = False):
    """Return a mock MetadataStore backed by the given rows."""
    ms = MagicMock()
    if raise_on_fetch:
        ms.fetch_all.side_effect = Exception("DB unavailable")
    else:
        ms.fetch_all.return_value = rows or []
    ms.fetch_one.return_value = None
    ms.execute.return_value = None
    return ms


def _reset_config_cache():
    """Force the settings cache to expire so the next read hits the DB."""
    import config
    config.invalidate_settings_cache()


# ---------------------------------------------------------------------------
# 1. GET /kg/settings — all keys, correct types, correct sources
# ---------------------------------------------------------------------------

class TestKgSettingsGet(unittest.TestCase):

    def setUp(self):
        _reset_config_cache()

    def _get_response(self, db_rows: list[dict]):
        """Call kg_settings_get with a patched metadata store."""
        from routes.admin import kg_settings_get, _SETTING_META
        with patch("config.get_metadata_store", return_value=_make_meta_store(db_rows)):
            return kg_settings_get()

    def test_all_keys_present(self):
        from routes.admin import _SETTING_META
        response = self._get_response([])
        keys = {s["key"] for s in response["settings"]}
        self.assertEqual(keys, set(_SETTING_META.keys()))

    def test_source_default_when_no_db_row(self):
        response = self._get_response([])
        for s in response["settings"]:
            self.assertEqual(s["source"], "default", f"key={s['key']} should be default")

    def test_source_database_when_row_exists(self):
        response = self._get_response([
            {"key": "entity_quality_lower", "value": "0.40"},
        ])
        by_key = {s["key"]: s for s in response["settings"]}
        self.assertEqual(by_key["entity_quality_lower"]["source"], "database")
        self.assertAlmostEqual(by_key["entity_quality_lower"]["value"], 0.40)

    def test_value_types_are_correct(self):
        response = self._get_response([])
        for s in response["settings"]:
            if s["type"] == "float":
                self.assertIsInstance(s["value"], float, f"key={s['key']}")
            elif s["type"] == "int":
                self.assertIsInstance(s["value"], int, f"key={s['key']}")
            elif s["type"] == "bool":
                self.assertIsInstance(s["value"], bool, f"key={s['key']}")

    def test_description_non_empty(self):
        response = self._get_response([])
        for s in response["settings"]:
            self.assertTrue(len(s["description"]) > 10, f"key={s['key']} has empty description")


# ---------------------------------------------------------------------------
# 2. POST /kg/settings — valid update
# ---------------------------------------------------------------------------

class TestKgSettingsPost(unittest.TestCase):

    def setUp(self):
        _reset_config_cache()

    def test_valid_update_returns_ok(self):
        from routes.admin import kg_settings_post, KgSettingsUpsertRequest, KgSettingsUpsertItem
        ms = _make_meta_store()
        with patch("config.get_metadata_store", return_value=ms):
            body = KgSettingsUpsertRequest(settings=[
                KgSettingsUpsertItem(key="entity_quality_lower", value="0.40"),
                KgSettingsUpsertItem(key="graph_min_mention_count", value="3"),
            ])
            resp = kg_settings_post(body)
        self.assertEqual(resp["status"], "ok")
        self.assertIn("entity_quality_lower", resp["updated"])
        self.assertIn("graph_min_mention_count", resp["updated"])

    def test_valid_update_calls_db_upsert(self):
        from routes.admin import kg_settings_post, KgSettingsUpsertRequest, KgSettingsUpsertItem
        ms = _make_meta_store()
        with patch("config.get_metadata_store", return_value=ms):
            body = KgSettingsUpsertRequest(settings=[
                KgSettingsUpsertItem(key="entity_quality_lower", value="0.40"),
            ])
            kg_settings_post(body)
        ms.execute.assert_called_once()
        args = ms.execute.call_args[0]
        self.assertIn("entity_quality_lower", args[1])

    def test_post_invalidates_cache(self):
        """After a POST, the settings cache loaded_at should be reset."""
        import config
        from routes.admin import kg_settings_post, KgSettingsUpsertRequest, KgSettingsUpsertItem
        # Seed the cache with a non-zero loaded_at
        config._settings_cache_loaded_at = time.monotonic()
        ms = _make_meta_store()
        with patch("config.get_metadata_store", return_value=ms):
            body = KgSettingsUpsertRequest(settings=[
                KgSettingsUpsertItem(key="graph_min_mention_count", value="5"),
            ])
            kg_settings_post(body)
        self.assertEqual(config._settings_cache_loaded_at, 0.0)


# ---------------------------------------------------------------------------
# 3. POST /kg/settings — unknown key returns 400
# ---------------------------------------------------------------------------

class TestKgSettingsPostUnknownKey(unittest.TestCase):

    def test_unknown_key_raises_400(self):
        from routes.admin import kg_settings_post, KgSettingsUpsertRequest, KgSettingsUpsertItem
        from fastapi import HTTPException
        ms = _make_meta_store()
        with patch("config.get_metadata_store", return_value=ms):
            body = KgSettingsUpsertRequest(settings=[
                KgSettingsUpsertItem(key="not_a_real_key", value="1"),
            ])
            with self.assertRaises(HTTPException) as ctx:
                kg_settings_post(body)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("unknown key", ctx.exception.detail)


# ---------------------------------------------------------------------------
# 4. POST /kg/settings — invalid type returns 400
# ---------------------------------------------------------------------------

class TestKgSettingsPostInvalidType(unittest.TestCase):

    def test_float_key_with_non_numeric_value(self):
        from routes.admin import kg_settings_post, KgSettingsUpsertRequest, KgSettingsUpsertItem
        from fastapi import HTTPException
        ms = _make_meta_store()
        with patch("config.get_metadata_store", return_value=ms):
            body = KgSettingsUpsertRequest(settings=[
                KgSettingsUpsertItem(key="entity_quality_lower", value="not_a_float"),
            ])
            with self.assertRaises(HTTPException) as ctx:
                kg_settings_post(body)
        self.assertEqual(ctx.exception.status_code, 400)

    def test_int_key_with_float_string(self):
        from routes.admin import kg_settings_post, KgSettingsUpsertRequest, KgSettingsUpsertItem
        from fastapi import HTTPException
        ms = _make_meta_store()
        with patch("config.get_metadata_store", return_value=ms):
            body = KgSettingsUpsertRequest(settings=[
                KgSettingsUpsertItem(key="graph_min_mention_count", value="2.5"),
            ])
            with self.assertRaises(HTTPException) as ctx:
                kg_settings_post(body)
        self.assertEqual(ctx.exception.status_code, 400)


# ---------------------------------------------------------------------------
# 5. POST /kg/settings — out-of-range value returns 400
# ---------------------------------------------------------------------------

class TestKgSettingsPostOutOfRange(unittest.TestCase):

    def test_quality_threshold_above_1(self):
        from routes.admin import kg_settings_post, KgSettingsUpsertRequest, KgSettingsUpsertItem
        from fastapi import HTTPException
        ms = _make_meta_store()
        with patch("config.get_metadata_store", return_value=ms):
            body = KgSettingsUpsertRequest(settings=[
                KgSettingsUpsertItem(key="entity_quality_lower", value="1.5"),
            ])
            with self.assertRaises(HTTPException) as ctx:
                kg_settings_post(body)
        self.assertEqual(ctx.exception.status_code, 400)

    def test_quality_threshold_below_0(self):
        from routes.admin import kg_settings_post, KgSettingsUpsertRequest, KgSettingsUpsertItem
        from fastapi import HTTPException
        ms = _make_meta_store()
        with patch("config.get_metadata_store", return_value=ms):
            body = KgSettingsUpsertRequest(settings=[
                KgSettingsUpsertItem(key="entity_quality_upper", value="-0.1"),
            ])
            with self.assertRaises(HTTPException) as ctx:
                kg_settings_post(body)
        self.assertEqual(ctx.exception.status_code, 400)

    def test_dedup_hour_above_23(self):
        from routes.admin import kg_settings_post, KgSettingsUpsertRequest, KgSettingsUpsertItem
        from fastapi import HTTPException
        ms = _make_meta_store()
        with patch("config.get_metadata_store", return_value=ms):
            body = KgSettingsUpsertRequest(settings=[
                KgSettingsUpsertItem(key="dedup_cron_hour_utc", value="24"),
            ])
            with self.assertRaises(HTTPException) as ctx:
                kg_settings_post(body)
        self.assertEqual(ctx.exception.status_code, 400)

    def test_positive_int_zero_rejected(self):
        from routes.admin import kg_settings_post, KgSettingsUpsertRequest, KgSettingsUpsertItem
        from fastapi import HTTPException
        ms = _make_meta_store()
        with patch("config.get_metadata_store", return_value=ms):
            body = KgSettingsUpsertRequest(settings=[
                KgSettingsUpsertItem(key="graph_min_mention_count", value="0"),
            ])
            with self.assertRaises(HTTPException) as ctx:
                kg_settings_post(body)
        self.assertEqual(ctx.exception.status_code, 400)


# ---------------------------------------------------------------------------
# 6. DELETE /kg/settings/{key}
# ---------------------------------------------------------------------------

class TestKgSettingsDelete(unittest.TestCase):

    def setUp(self):
        _reset_config_cache()

    def test_delete_returns_default(self):
        from routes.admin import kg_settings_delete, _SETTING_META
        ms = _make_meta_store()
        with patch("config.get_metadata_store", return_value=ms):
            resp = kg_settings_delete("entity_quality_lower")
        self.assertEqual(resp["status"], "ok")
        self.assertEqual(resp["key"], "entity_quality_lower")
        self.assertEqual(resp["reverted_to"], _SETTING_META["entity_quality_lower"]["default"])

    def test_delete_calls_db_delete(self):
        from routes.admin import kg_settings_delete
        ms = _make_meta_store()
        with patch("config.get_metadata_store", return_value=ms):
            kg_settings_delete("graph_min_mention_count")
        ms.execute.assert_called_once()
        args = ms.execute.call_args[0]
        self.assertIn("DELETE", args[0])
        self.assertIn("graph_min_mention_count", args[1])

    def test_delete_unknown_key_returns_400(self):
        from routes.admin import kg_settings_delete
        from fastapi import HTTPException
        ms = _make_meta_store()
        with patch("config.get_metadata_store", return_value=ms):
            with self.assertRaises(HTTPException) as ctx:
                kg_settings_delete("not_a_key")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_delete_invalidates_cache(self):
        import config
        from routes.admin import kg_settings_delete
        config._settings_cache_loaded_at = time.monotonic()
        ms = _make_meta_store()
        with patch("config.get_metadata_store", return_value=ms):
            kg_settings_delete("entity_quality_lower")
        self.assertEqual(config._settings_cache_loaded_at, 0.0)


# ---------------------------------------------------------------------------
# 7. Cache TTL — second read within 30s uses cached value (no extra DB call)
# ---------------------------------------------------------------------------

class TestSettingsCacheTTL(unittest.TestCase):

    def setUp(self):
        _reset_config_cache()

    def test_second_read_within_ttl_does_not_hit_db(self):
        import config
        ms = _make_meta_store([{"key": "entity_quality_lower", "value": "0.42"}])
        with patch("config.get_metadata_store", return_value=ms):
            # First call — hits DB
            v1 = config.get_entity_quality_lower()
            # Second call immediately — should be served from cache
            v2 = config.get_entity_quality_lower()
        self.assertAlmostEqual(v1, 0.42)
        self.assertAlmostEqual(v2, 0.42)
        # fetch_all should be called exactly once (first miss)
        self.assertEqual(ms.fetch_all.call_count, 1)

    def test_cache_expires_after_ttl(self):
        import config
        ms = _make_meta_store([{"key": "entity_quality_lower", "value": "0.42"}])
        with patch("config.get_metadata_store", return_value=ms):
            config.get_entity_quality_lower()  # prime cache
            # Manually expire the cache
            config._settings_cache_loaded_at -= config._SETTINGS_TTL + 1
            config.get_entity_quality_lower()  # should re-fetch
        self.assertEqual(ms.fetch_all.call_count, 2)


# ---------------------------------------------------------------------------
# 8. Cache invalidation — write invalidates cache, next read hits DB
# ---------------------------------------------------------------------------

class TestSettingsCacheInvalidation(unittest.TestCase):

    def setUp(self):
        _reset_config_cache()

    def test_invalidate_then_read_hits_db_again(self):
        import config
        ms = _make_meta_store([{"key": "entity_quality_lower", "value": "0.42"}])
        with patch("config.get_metadata_store", return_value=ms):
            config.get_entity_quality_lower()          # fetch 1
            config.invalidate_settings_cache()
            config.get_entity_quality_lower()          # fetch 2 (after invalidation)
        self.assertEqual(ms.fetch_all.call_count, 2)


# ---------------------------------------------------------------------------
# 9. DB unavailable — getter returns env var default, no exception
# ---------------------------------------------------------------------------

class TestSettingsDbUnavailable(unittest.TestCase):

    def setUp(self):
        _reset_config_cache()

    def test_fallback_to_default_when_db_raises(self):
        import config
        ms = _make_meta_store(raise_on_fetch=True)
        with patch("config.get_metadata_store", return_value=ms):
            # Should not raise
            value = config.get_entity_quality_lower()
        self.assertAlmostEqual(value, 0.35)

    def test_fallback_uses_env_var_override(self):
        import config
        ms = _make_meta_store(raise_on_fetch=True)
        with patch("config.get_metadata_store", return_value=ms), \
             patch.dict("os.environ", {"ENTITY_QUALITY_LOWER": "0.45"}):
            _reset_config_cache()
            value = config.get_entity_quality_lower()
        self.assertAlmostEqual(value, 0.45)

    def test_no_exception_on_db_failure(self):
        import config
        ms = _make_meta_store(raise_on_fetch=True)
        with patch("config.get_metadata_store", return_value=ms):
            # None of these should raise
            config.get_entity_quality_lower()
            config.get_entity_quality_upper()
            config.get_graph_min_mention_count()
            config.get_dedup_cron_hour_utc()


# ---------------------------------------------------------------------------
# 10. entity_quality.score_and_filter_entities uses getter, not env var directly
# ---------------------------------------------------------------------------

class TestEntityQualityUsesGetter(unittest.TestCase):

    def setUp(self):
        _reset_config_cache()

    def test_score_and_filter_calls_config_getters(self):
        """score_and_filter_entities must read thresholds from config getters, not os.environ."""
        from services import entity_quality
        from models.entities import ExtractedEntity

        entities = [
            ExtractedEntity(name="Alice Smith", entity_type="PERSON"),
            ExtractedEntity(name="the", entity_type="CONCEPT"),
        ]

        with patch("config.get_entity_quality_lower", return_value=0.35) as mock_lower, \
             patch("config.get_entity_quality_upper", return_value=0.60) as mock_upper, \
             patch("config.get_entity_quality_fail_open", return_value=True) as mock_fo, \
             patch("config.get_stop_entity_set", return_value=set()):
            kept, discarded = entity_quality.score_and_filter_entities(entities, "default")

        mock_lower.assert_called()
        mock_upper.assert_called()
        mock_fo.assert_called()

    def test_db_threshold_overrides_env_var(self):
        """When DB has entity_quality_lower above the max possible score,
        every entity must be discarded — proving the DB-stored value
        overrides the env-var default (0.35)."""
        import config
        from services import entity_quality
        from models.entities import ExtractedEntity

        # _compute_quality clamps scores to [0, 1], so we deliberately set a
        # threshold strictly greater than 1.0 to guarantee no entity can pass.
        # If the env-var default (0.35) were silently used here, both entities
        # (which score 1.0) would be kept and this assertion would fail.
        # VERIFY-PLAN: was 0.99 — that value is < the perfect score of well-
        # formed PERSON / PROJECT names, so the original test was a no-op.
        ms = _make_meta_store([{"key": "entity_quality_lower", "value": "1.5"}])
        with patch("config.get_metadata_store", return_value=ms):
            _reset_config_cache()
            entities = [
                ExtractedEntity(name="Alice Smith", entity_type="PERSON"),
                ExtractedEntity(name="Project Apollo", entity_type="PROJECT"),
            ]
            with patch("config.get_stop_entity_set", return_value=set()):
                kept, discarded = entity_quality.score_and_filter_entities(entities, "default")

        self.assertEqual(len(kept), 0)
        self.assertEqual(discarded, 2)


if __name__ == "__main__":
    unittest.main()
