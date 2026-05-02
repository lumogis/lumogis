# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
#
# Idempotent denied-action seed for RC approvals workflow tests.
# Intended for `docker compose exec` against lumogis-test — see
# scripts/seed-public-rc-approvals-fixture.sh.

from __future__ import annotations

import json
import logging
import os

import auth as auth_mod
import config
import permissions as perm
import services.users as users_svc

_log = logging.getLogger("scripts.seed_public_rc_approvals_fixture")

_MARKER = "RC_APPROVALS_SEED_MARKER"
_CONNECTOR = "filesystem-mcp"
_ACTION_TYPE = "integration_probe_read"


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

    if not auth_mod.auth_enabled():
        _log.info("AUTH_ENABLED=false — skipping approvals fixture seed")
        return 0

    email = (os.environ.get("LUMOGIS_WEB_SMOKE_EMAIL") or "").strip()
    if not email:
        _log.error("LUMOGIS_WEB_SMOKE_EMAIL is unset")
        return 2

    user = users_svc.get_user_by_email(email)
    if user is None:
        _log.error("smoke user not found email=%s — run seed-public-rc-smoke-user first", email)
        return 2

    ms = config.get_metadata_store()
    try:
        ms.execute(
            "DELETE FROM action_log WHERE user_id = %s AND input_summary = %s",
            (user.id, _MARKER),
        )
    except Exception:
        _log.exception("failed to clear prior seed rows")
        return 1

    perm.log_action(
        connector=_CONNECTOR,
        action_type=_ACTION_TYPE,
        mode="ASK",
        allowed=False,
        user_id=user.id,
        input_summary=_MARKER,
        result_summary="rc_seed",
    )
    print(json.dumps({"ok": True, "user_id": user.id, "marker": _MARKER}, sort_keys=True))
    _log.info("seeded denied action_log row user_id=%s connector=%s", user.id, _CONNECTOR)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
