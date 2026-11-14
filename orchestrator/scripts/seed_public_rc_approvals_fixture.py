# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
#
# Idempotent denied-action seed for RC approvals workflow tests.
# Intended for `docker compose exec` against lumogis-test — see
# scripts/seed-public-rc-approvals-fixture.sh.
#
# The seeded row is the denial the product really produces: a write by a
# registered action connector (``email_reply`` -> ``smtp`` / ``email_send``)
# under ASK. A read-only tool connector such as ``filesystem-mcp`` never
# produces a denial and Approvals refuses to flip it (LUM-962, ADR 220).
# This script never writes ``connector_permissions``: Core caches modes
# in-process, so the test resets the mode through the API instead.

from __future__ import annotations

import json
import logging
import os

import actions.handlers.smtp_send  # noqa: F401  (registers email_reply at import)
import auth as auth_mod
import permissions as perm
import services.users as users_svc
from actions.executor import is_hard_limited
from actions.registry import get_action

import config

_log = logging.getLogger("scripts.seed_public_rc_approvals_fixture")

_MARKER = "RC_APPROVALS_SEED_MARKER"
_SEED_ACTION = "email_reply"


def _seed_target() -> tuple[str, str]:
    """Return ``(connector, action_type)`` for the seeded denial.

    Derived from the live action registry, never hard-coded, and refused when
    it is not a state ``check_permission`` can really produce.
    """
    spec = get_action(_SEED_ACTION)
    if spec is None:
        raise RuntimeError(f"seed action {_SEED_ACTION!r} is not registered")
    if not spec.is_write:
        raise RuntimeError(f"seed action {_SEED_ACTION!r} is not a write; ASK never denies it")
    if is_hard_limited(spec.action_type):
        raise RuntimeError(
            f"seed action_type {spec.action_type!r} is hard-limited; Approvals cannot switch it"
        )
    return spec.connector, spec.action_type


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

    try:
        connector, action_type = _seed_target()
    except RuntimeError as exc:
        _log.error("refusing to seed: %s", exc)
        return 1

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
        connector=connector,
        action_type=action_type,
        mode="ASK",
        allowed=False,
        user_id=user.id,
        input_summary=_MARKER,
        result_summary="rc_seed",
    )
    print(json.dumps({"ok": True, "user_id": user.id, "marker": _MARKER}, sort_keys=True))
    _log.info(
        "seeded denied action_log row user_id=%s connector=%s action_type=%s",
        user.id,
        connector,
        action_type,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
