# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
#
# Idempotent RC smoke user for isolated compose stacks (AUTH_ENABLED=true).
# Intended for docker compose exec against lumogis-test only — see seed-public-rc-smoke-user.sh.

from __future__ import annotations

import json
import logging
import os

import auth as auth_mod
import services.users as users_svc

_log = logging.getLogger("scripts.ensure_public_rc_smoke_user")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

    if not auth_mod.auth_enabled():
        _log.info("AUTH_ENABLED=false — skipping smoke user ensure")
        return 0

    email = (os.environ.get("LUMOGIS_WEB_SMOKE_EMAIL") or "").strip()
    password = os.environ.get("LUMOGIS_WEB_SMOKE_PASSWORD") or ""

    if not email:
        _log.error("LUMOGIS_WEB_SMOKE_EMAIL is unset")
        return 2
    users_svc.validate_password_policy(password)

    existing = users_svc.get_user_by_email(email)
    if existing is None:
        user = users_svc.create_user(email=email, password=password, role="admin")
        print(
            json.dumps({"ok": True, "created": True, "email": email, "id": user.id}, sort_keys=True)
        )
        _log.info("created smoke admin user email=%s id=%s", email, user.id)
        return 0

    users_svc.cli_reset_password(email=email, user_id=None, new_password=password)
    print(
        json.dumps(
            {"ok": True, "password_reset": True, "email": email, "id": existing.id}, sort_keys=True
        )
    )
    _log.info("reset smoke user password email=%s id=%s", email, existing.id)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except users_svc.PasswordPolicyViolationError as exc:
        _log.error("%s", exc)
        raise SystemExit(1) from exc
    except ValueError as exc:
        _log.error("%s", exc)
        raise SystemExit(2) from exc
    except LookupError as exc:
        _log.error("%s", exc)
        raise SystemExit(1) from exc
