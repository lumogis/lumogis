# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Local operator entrypoint — reset a user's password with shell access.

Use when the household admin is locked out and no other admin can sign in.
This is **not** an HTTP surface; trust boundary is OS access to the deployment.

Usage
-----

From the ``orchestrator`` directory (same as other ``scripts.*`` modules)::

    python -m scripts.reset_password --email user@example.com

    python -m scripts.reset_password --user-id <uuid-hex>

Omit ``--password`` to be prompted securely via :func:`getpass.getpass`.

The script validates the same minimum length as login / bootstrap (12 characters),
updates the stored argon2 hash, clears ``refresh_token_jti`` for that user, and
prints a short JSON line **without** echoing the password.

Exit codes: ``0`` success, ``1`` operational failure, ``2`` usage / argparse error.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from getpass import getpass

import services.users as users_svc

_log = logging.getLogger("scripts.reset_password")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m scripts.reset_password",
        description=(
            "Reset a Lumogis user password (local shell only). Specify --email or --user-id."
        ),
    )
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--email", help="Account email (case-insensitive match)")
    g.add_argument("--user-id", dest="user_id", help="``users.id`` hex string")
    p.add_argument(
        "--password",
        help="New password (avoid on shared shells; prefer omitting for prompt)",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    new_pw = args.password
    if new_pw is None:
        new_pw = getpass("New password: ")
        confirm = getpass("Confirm new password: ")
        if new_pw != confirm:
            _log.error("reset_password: passwords do not match")
            return 1

    try:
        users_svc.cli_reset_password(
            email=args.email,
            user_id=args.user_id,
            new_password=new_pw,
        )
    except ValueError as exc:
        _log.error("reset_password: %s", exc)
        return 2
    except users_svc.PasswordPolicyViolationError as exc:
        _log.error("reset_password: %s", exc)
        return 1
    except LookupError:
        _log.error("reset_password: user not found")
        return 1

    # Deliberately do not print password or hash.
    print(json.dumps({"ok": True, "message": "password updated"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
