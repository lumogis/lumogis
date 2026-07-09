# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
#
# Idempotent document-chat fixture seed (LUM-503). Lands a known document for the
# smoke user — file_index row + Qdrant `documents` chunks — by driving the real
# ingestion path (`services.ingest.ingest_file`), so query/doc vectors are
# comparable and scoped retrieval actually fires.
#
# Intended for `docker compose exec` against lumogis-test only — see
# scripts/seed-document-chat-fixture.sh. Reuses the production embedder + vector
# store; blast radius is the smoke user's personal scope on the test stack.

from __future__ import annotations

import json
import logging
from pathlib import Path

import auth as auth_mod
import services.users as users_svc
from services.ingest import ingest_file

import config

_log = logging.getLogger("scripts.seed_document_chat_fixture")

# Distinctive, deterministic content so the integration test can query for a
# sentence that reliably retrieves. Keep it short — a few chunks is plenty.
FIXTURE_DIR = Path("/tmp/lumogis-fixtures")
FIXTURE_PATH = FIXTURE_DIR / "lum503-document-chat.md"
FIXTURE_TEXT = """# LUM-503 Document Chat Fixture

This document exists to verify document-scoped chat end to end.

The secret pangram for this fixture is: the quick brown fox jumps over the lazy dog.

The lease term begins on the first of January and renews annually unless either
party gives sixty days written notice before the renewal date.

Tenants are responsible for routine maintenance under fifty dollars; the landlord
covers structural repairs and the heating system.
"""


def _resolve_smoke_user():
    import os

    email = (os.environ.get("LUMOGIS_WEB_SMOKE_EMAIL") or "").strip()
    if not email:
        email = (os.environ.get("LUMOGIS_BOOTSTRAP_ADMIN_EMAIL") or "").strip()
    if not email:
        _log.error("LUMOGIS_WEB_SMOKE_EMAIL / LUMOGIS_BOOTSTRAP_ADMIN_EMAIL unset")
        return None, None
    user = users_svc.get_user_by_email(email)
    if user is None:
        _log.error("smoke/bootstrap user not found email=%s", email)
        return None, email
    return user, email


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

    if not auth_mod.auth_enabled():
        _log.info("AUTH_ENABLED=false — skipping document-chat fixture seed")
        return 0

    user, email = _resolve_smoke_user()
    if user is None:
        return 2

    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    FIXTURE_PATH.write_text(FIXTURE_TEXT)

    # force=True: re-embed/re-upsert chunks even when the hash is unchanged, so a
    # re-run after a Qdrant wipe re-lands the points (file_index INSERT is
    # ON CONFLICT (user_id, file_path) DO UPDATE — idempotent either way).
    result = ingest_file(str(FIXTURE_PATH), user_id=user.id, force=True)

    row = config.get_metadata_store().fetch_one(
        "SELECT id FROM file_index WHERE file_path = %s AND user_id = %s",
        (str(FIXTURE_PATH), user.id),
    )
    if not row or row.get("id") is None:
        _log.error("file_index row not found after ingest path=%s user=%s", FIXTURE_PATH, user.id)
        return 1

    document_id = int(row["id"])
    _log.info(
        "seeded document-chat fixture document_id=%s chunks=%s (email=%s)",
        document_id,
        result.chunk_count,
        email,
    )
    print(
        json.dumps(
            {
                "ok": True,
                "document_id": document_id,
                "file_path": str(FIXTURE_PATH),
                "chunk_count": result.chunk_count,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
