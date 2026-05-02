# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Operator entrypoint — re-seal every connector credential row across
all three tier tables to the current primary Fernet key.

ADRs: ``per_user_connector_credentials`` (per-user tier),
``credential_scopes_shared_system`` (household + instance/system tiers).

When to run
-----------

After **prepending** a new primary key to ``LUMOGIS_CREDENTIAL_KEYS``
(newest-first CSV) and restarting the orchestrator container so the
new key takes effect:

    LUMOGIS_CREDENTIAL_KEYS="<NEW_KEY>,<OLD_KEY>"

then, from inside the orchestrator container:

    python -m scripts.rotate_credential_key

What it does
------------

1. Imports :mod:`services.connector_credentials`.
2. Calls
   :func:`services.connector_credentials.reencrypt_all_to_current_version`
   with the configured ``--actor`` (default: ``system``) and the
   selected ``--tables`` tuple (default: all three credential
   tables). The service walks each requested table, skipping rows
   whose ``key_version`` already matches the current primary key
   fingerprint and re-sealing the rest via
   :meth:`cryptography.fernet.MultiFernet.rotate`. Per-row processing
   is independent across tables — a failure in the household walk
   never rolls back per-user updates, and vice versa.
3. Prints the aggregated JSON summary to stdout::

       {
         "rotated": <int>, "skipped": <int>, "failed": <int>,
         "by_tier": {
           "user":      {"rotated": N, "skipped": N, "failed": N},
           "household": {...},     # only present if walked
           "system":    {...}      # only present if walked
         }
       }

4. Exits non-zero **iff** total ``failed > 0`` so the operator's
   wrapper (cron job, CI, manual invocation) sees a clear failure
   signal.

Operator-staged rotation
------------------------

For very large per-user tables the operator may stage rotation by
calling the script multiple times with ``--tables``. Example: rotate
the per-user table first (the largest), confirm success, then rotate
the two tier tables on a follow-up call::

    python -m scripts.rotate_credential_key --tables user_connector_credentials
    python -m scripts.rotate_credential_key \
        --tables household_connector_credentials,instance_system_connector_credentials

Generating a new key
--------------------

    python3 -c "from cryptography.fernet import Fernet; \\
        print(Fernet.generate_key().decode())"

The script intentionally does *not* generate, install, or rotate
``LUMOGIS_CREDENTIAL_KEY[S]`` itself — key material handling is the
operator's responsibility. Once the new key is the only key in the
CSV (i.e. every row reports ``rotated`` or ``skipped`` and no row
remains sealed under an old key), the old key may be removed from
``LUMOGIS_CREDENTIAL_KEYS`` and the container restarted.

Safety properties
-----------------

* **Per-row atomicity.** The service re-encrypts and updates each row
  in its own statement; a failure on row N never rolls back rows
  ``1..N-1``. Re-running the script after fixing the bad row picks up
  exactly where the previous run stopped.
* **Defence-in-depth verification.** The service re-decrypts every
  freshly-rotated token before issuing the UPDATE; verification
  failures count as ``failed`` and never mutate the row.
* **No plaintext on the wire.** Plaintext payloads are never
  decrypted by this script — :meth:`MultiFernet.rotate` operates
  directly on ciphertext.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

import services.connector_credentials as ccs

_log = logging.getLogger("scripts.rotate_credential_key")


_DEFAULT_TABLES = (
    "user_connector_credentials",
    "household_connector_credentials",
    "instance_system_connector_credentials",
)
_VALID_TABLES = frozenset(_DEFAULT_TABLES)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.rotate_credential_key",
        description=(
            "Re-encrypt every connector credential row across the "
            "per-user, household, and instance/system tier tables to "
            "the current primary Fernet key (LUMOGIS_CREDENTIAL_KEYS[0])."
        ),
    )
    parser.add_argument(
        "--actor",
        default="system",
        help=(
            "Identity recorded as ``updated_by`` on rotated rows and "
            "in audit_log entries. Defaults to 'system'. Operators "
            "running this manually may pass an admin:<id> actor."
        ),
    )
    parser.add_argument(
        "--tables",
        default=",".join(_DEFAULT_TABLES),
        help=(
            "CSV of table names to rotate. Defaults to all three "
            "credential tables: "
            "user_connector_credentials,"
            "household_connector_credentials,"
            "instance_system_connector_credentials. Pass a subset to "
            "stage rotation for very large tables. Unknown table "
            "names are rejected with a non-zero exit."
        ),
    )
    return parser


def _parse_tables(raw: str) -> tuple[str, ...]:
    """Parse the ``--tables`` CSV; reject unknown / blank entries."""
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if not parts:
        raise SystemExit(
            "rotate_credential_key: --tables must be a non-empty CSV "
            "of table names"
        )
    unknown = [p for p in parts if p not in _VALID_TABLES]
    if unknown:
        raise SystemExit(
            f"rotate_credential_key: unknown table(s) in --tables: "
            f"{unknown!r}. Valid: {sorted(_VALID_TABLES)!r}"
        )
    return tuple(parts)


def main(argv: list[str] | None = None) -> int:
    """Run the rotation. Returns the process exit code."""
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    tables = _parse_tables(args.tables)
    summary = ccs.reencrypt_all_to_current_version(
        actor=args.actor,
        tables=tables,
    )
    print(json.dumps(summary, sort_keys=True))

    failed = int(summary.get("failed", 0))
    if failed > 0:
        _log.error(
            "rotate_credential_key: %d row(s) failed to re-encrypt "
            "across tables=%s; fix the underlying issue and re-run.",
            failed, tables,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
