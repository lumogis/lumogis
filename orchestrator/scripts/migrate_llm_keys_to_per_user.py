# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Operator entrypoint — copy plaintext LLM API keys from
``app_settings`` into per-user encrypted ``user_connector_credentials``
rows.

Plan: ``llm_provider_keys_per_user_migration`` (Pass 4.14)

When to run
-----------

Once, per Lumogis deployment, after enabling ``AUTH_ENABLED=true`` for
the first time on a stack that previously stored its six cloud LLM
vendor keys in the global ``app_settings`` table (or in environment
variables that were copied into ``app_settings`` via the legacy
``PUT /api/v1/admin/settings`` ``api_keys`` body). After this migration
each named user resolves their own per-user encrypted row at chat time
via :func:`services.llm_connector_map.effective_api_key`; the legacy
global path becomes inert under auth-on (the route returns 422
``legacy_global_api_keys_disabled`` to any further ``api_keys`` writes).

CLI
---

::

    python -m scripts.migrate_llm_keys_to_per_user \\
        --user-id alice [--user-id bob ...] \\
        [--delete-legacy] [--dry-run] [--actor system]

* ``--user-id`` is **repeatable** and **required** (at least one).
  Each user_id receives the same set of plaintext keys as a per-user
  encrypted row. User-ids are validated against the same character class
  ``users.create_user`` accepts.
* ``--delete-legacy`` removes the plaintext ``app_settings`` row **only
  after** every per-user PUT for that key succeeds. Without this flag the
  legacy rows are left intact so a botched run is recoverable.
* ``--dry-run`` prints the planned writes and exits 0 without touching
  either table.
* ``--actor`` defaults to ``system`` and is recorded in the audit_log
  via :mod:`services.connector_credentials`.

Exit codes
----------

* ``0`` — every planned write succeeded (or dry-run completed). A
  ``skipped_no_source`` count > 0 is **not** an error: env-only
  deployments simply have nothing to migrate for that vendor.
* ``1`` — at least one ``put_payload`` (or, with ``--delete-legacy``,
  the post-copy DELETE) raised. Per-pair operations are independent —
  one failure does not stop the others; the final exit code is the OR
  of every pair's outcome.
* ``2`` — argparse / config error before any DB write (no ``--user-id``,
  bad actor, malformed user-id, or ``LUMOGIS_CREDENTIAL_KEY[S]`` unset).

Output
------

* **stdout** — a single JSON summary line at the end:

  - live mode::

        {"mode": "live", "migrated": N, "skipped_no_source": N,
         "failed": N, "deleted_legacy": N, "users": [...]}

  - dry-run mode::

        {"mode": "dry_run", "would_migrate": N, "skipped_no_source": N,
         "would_delete_legacy": N, "users": [...]}

* **stderr** — one JSON object per ``(user, env)`` pair::

      {"user_id": "...", "connector": "llm_<vendor>",
       "outcome": "migrated"|"skipped_no_source"|"failed",
       "error_class": "<name>"|null,
       "key_present": true|false}

Plaintext is **never** logged. ``key_present`` is the boolean
substitute. ``error_class`` is the exception class name only — never
the message body, since substrate decrypt-failure messages can include
the ciphertext bytes that failed to decode.

Process-cache caveat
--------------------

This script runs in a separate Python process from the live ``uvicorn``
orchestrator. The substrate's ``register_change_listener`` callback
fires inside this process only, evicting this process's empty
``_instances`` cache. **Any per-user LLM adapter cached in the live
orchestrator from a previous key value will keep that value until the
orchestrator container is restarted.** For greenfield migrations this
is a no-op (no adapters are cached yet); when overwriting an existing
per-user key from outside the running process the operator must
restart the orchestrator container for the new key to take effect.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from typing import Any

import services.connector_credentials as ccs
from services.llm_connector_map import LLM_CONNECTOR_BY_ENV
from settings_store import get_setting

_log = logging.getLogger("scripts.migrate_llm_keys_to_per_user")


_USER_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
_ACTOR_RE = re.compile(r"^(self|system|admin:[A-Za-z0-9_\-]{1,64})$")


def _user_id_arg(value: str) -> str:
    if not _USER_ID_RE.match(value):
        raise argparse.ArgumentTypeError(
            f"invalid --user-id {value!r}: must match {_USER_ID_RE.pattern}"
        )
    return value


def _actor_arg(value: str) -> str:
    if not _ACTOR_RE.match(value):
        raise argparse.ArgumentTypeError(
            f"invalid --actor {value!r}: must match {_ACTOR_RE.pattern}"
        )
    return value


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.migrate_llm_keys_to_per_user",
        description=(
            "Copy plaintext LLM API keys from app_settings into "
            "per-user encrypted user_connector_credentials rows."
        ),
    )
    parser.add_argument(
        "--user-id",
        dest="user_ids",
        action="append",
        type=_user_id_arg,
        required=True,
        help=(
            "Target user-id (repeatable; at least one required). Each "
            "named user receives the same set of plaintext keys as a "
            "per-user encrypted row."
        ),
    )
    parser.add_argument(
        "--delete-legacy",
        action="store_true",
        help=(
            "Delete the plaintext app_settings row after every per-user "
            "PUT for that key succeeds. Without this flag the legacy "
            "rows are left intact so a botched run is recoverable."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the planned writes and exit 0 without touching either table.",
    )
    parser.add_argument(
        "--actor",
        type=_actor_arg,
        default="system",
        help=(
            "Identity recorded as updated_by on each new row and in the "
            "audit_log. Defaults to 'system'. Operators running this "
            "manually may pass admin:<user_id>."
        ),
    )
    return parser


def _emit_pair(stream, *, user_id: str, connector: str, outcome: str,
               key_present: bool, error_class: str | None) -> None:
    stream.write(
        json.dumps(
            {
                "user_id": user_id,
                "connector": connector,
                "outcome": outcome,
                "error_class": error_class,
                "key_present": key_present,
            },
            sort_keys=True,
        )
        + "\n"
    )


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    # Fail fast on missing keys: load the MultiFernet now so an unset
    # LUMOGIS_CREDENTIAL_KEY[S] aborts with exit 2 before any DB write.
    try:
        ccs._get_multifernet()  # type: ignore[attr-defined]
    except RuntimeError as exc:
        sys.stderr.write(
            "migrate_llm_keys_to_per_user: credential key unavailable — "
            f"{exc}\nSet LUMOGIS_CREDENTIAL_KEY (single key) or "
            "LUMOGIS_CREDENTIAL_KEYS (newest-first CSV) and re-run.\n"
        )
        return 2

    import config

    store = config.get_metadata_store()

    user_ids: list[str] = list(args.user_ids)
    actor: str = args.actor
    is_dry = bool(args.dry_run)
    delete_legacy = bool(args.delete_legacy)

    migrated = 0
    skipped_no_source = 0
    failed = 0
    deleted_legacy = 0

    for api_key_env, connector_id in sorted(LLM_CONNECTOR_BY_ENV.items()):
        try:
            raw = get_setting(api_key_env, store)
        except Exception as exc:
            failed += len(user_ids)
            for uid in user_ids:
                _emit_pair(
                    sys.stderr,
                    user_id=uid,
                    connector=connector_id,
                    outcome="failed",
                    key_present=False,
                    error_class=type(exc).__name__,
                )
            continue
        plaintext = (raw or "").strip()
        key_present = bool(plaintext)

        if not key_present:
            skipped_no_source += len(user_ids)
            for uid in user_ids:
                _emit_pair(
                    sys.stderr,
                    user_id=uid,
                    connector=connector_id,
                    outcome="skipped_no_source",
                    key_present=False,
                    error_class=None,
                )
            continue

        per_pair_failures = 0
        for uid in user_ids:
            if is_dry:
                migrated += 1
                _emit_pair(
                    sys.stderr,
                    user_id=uid,
                    connector=connector_id,
                    outcome="migrated",
                    key_present=True,
                    error_class=None,
                )
                continue
            try:
                ccs.put_payload(
                    uid,
                    connector_id,
                    {"api_key": plaintext},
                    actor=actor,
                )
            except Exception as exc:
                failed += 1
                per_pair_failures += 1
                _emit_pair(
                    sys.stderr,
                    user_id=uid,
                    connector=connector_id,
                    outcome="failed",
                    key_present=True,
                    error_class=type(exc).__name__,
                )
                continue
            migrated += 1
            _emit_pair(
                sys.stderr,
                user_id=uid,
                connector=connector_id,
                outcome="migrated",
                key_present=True,
                error_class=None,
            )

        if delete_legacy and per_pair_failures == 0:
            if is_dry:
                deleted_legacy += 1
            else:
                try:
                    store.execute(
                        "DELETE FROM app_settings WHERE key = %s",
                        (api_key_env,),
                    )
                    deleted_legacy += 1
                    _log.info(
                        "deleted plaintext app_settings row key=%s",
                        api_key_env,
                    )
                except Exception as exc:
                    failed += 1
                    _log.error(
                        "DELETE FROM app_settings WHERE key=%s failed: %s",
                        api_key_env,
                        type(exc).__name__,
                    )

    summary: dict[str, Any]
    if is_dry:
        summary = {
            "mode": "dry_run",
            "would_migrate": migrated,
            "skipped_no_source": skipped_no_source,
            "would_delete_legacy": deleted_legacy,
            "users": user_ids,
        }
    else:
        summary = {
            "mode": "live",
            "migrated": migrated,
            "skipped_no_source": skipped_no_source,
            "failed": failed,
            "deleted_legacy": deleted_legacy,
            "users": user_ids,
        }

    print(json.dumps(summary, sort_keys=True))
    if not is_dry:
        sys.stderr.write(
            "Note: restart orchestrator if any --user-id already had "
            "this key set; in-process cache is per-process.\n"
        )

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
