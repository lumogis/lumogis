# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Lumogis
"""Dashboard settings persistence in app_settings table.

Does not import config; the MetadataStore is always passed in by the caller
to avoid circular imports.
"""

from ports.metadata_store import MetadataStore


def get_setting(key: str, store: MetadataStore) -> str | None:
    """Read one value from app_settings by key. Returns None if not found."""
    row = store.fetch_one("SELECT value FROM app_settings WHERE key = %s", (key,))
    if row is None:
        return None
    return row.get("value")


def put_settings(store: MetadataStore, updates: dict[str, str]) -> None:
    """Upsert key-value pairs into app_settings. Empty string clears the key."""
    for k, v in updates.items():
        store.execute(
            """
            INSERT INTO app_settings (key, value, updated_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
            """,
            (k, v),
        )
