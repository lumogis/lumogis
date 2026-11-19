# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
#
# Idempotent INBOX_OWNER_USER_ID seed for RC ingest-path watcher E2E tests.
# Intended for `docker compose exec` against lumogis-test only — see
# scripts/seed-public-rc-ingest-owner.sh.
#
# After seeding, integration-public-rc.sh force-recreates orchestrator so
# watchers start with owner configured. Blast radius: /healthz ingest_paths_watch
# becomes "ok" for the whole lumogis-test session (acceptable for RC).

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import auth as auth_mod
import services.users as users_svc

from services import host_env

_log = logging.getLogger("scripts.seed_public_rc_ingest_owner")
_PROJECT_ENV = Path("/project/.env")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

    if not auth_mod.auth_enabled():
        _log.info("AUTH_ENABLED=false — skipping ingest owner seed")
        return 0

    email = (os.environ.get("LUMOGIS_BOOTSTRAP_ADMIN_EMAIL") or "").strip()
    if not email:
        email = (os.environ.get("LUMOGIS_WEB_SMOKE_EMAIL") or "").strip()
    if not email:
        _log.error("LUMOGIS_BOOTSTRAP_ADMIN_EMAIL / LUMOGIS_WEB_SMOKE_EMAIL unset")
        return 2

    user = users_svc.get_user_by_email(email)
    if user is None:
        _log.error("bootstrap/smoke user not found email=%s", email)
        return 2

    if not _PROJECT_ENV.is_file():
        _log.error("/project/.env missing — cannot seed INBOX_OWNER_USER_ID")
        return 1

    content = _PROJECT_ENV.read_text()
    existing = ""
    for line in content.splitlines():
        if line.strip().startswith("INBOX_OWNER_USER_ID="):
            existing = line.split("=", 1)[1].strip()
            break

    if existing == user.id:
        _log.info("INBOX_OWNER_USER_ID already set for user_id=%s", user.id)
        print(json.dumps({"ok": True, "skipped": True, "user_id": user.id}, sort_keys=True))
        return 0

    try:
        host_env.update(
            lambda text: host_env.rewrite_key(text, "INBOX_OWNER_USER_ID", user.id), _PROJECT_ENV
        )
    except OSError:
        _log.exception("failed to write INBOX_OWNER_USER_ID to /project/.env")
        return 1

    _log.info("set INBOX_OWNER_USER_ID=%s (email=%s)", user.id, email)
    print(json.dumps({"ok": True, "user_id": user.id}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
