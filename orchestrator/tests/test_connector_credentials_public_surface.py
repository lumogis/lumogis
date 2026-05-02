# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Pin the public surface of ``services.connector_credentials``.

After the helper extraction into ``services/_credential_internals.py``
(plan §`Modified files` D8.6) the per-user tier module continues to
re-export the names downstream callers historically imported from it.
A missed re-export must surface as a unit-test failure here rather
than as a downstream ``ImportError`` at request time.
"""

from __future__ import annotations

EXPECTED_PUBLIC_SURFACE: set[str] = {
    "ACTION_CRED_PUT",
    "ACTION_CRED_DELETED",
    "ACTION_CRED_ROTATED",
    "CredentialRecord",
    "ConnectorNotConfigured",
    "CredentialUnavailable",
    "get_record",
    "list_records",
    "get_payload",
    "put_payload",
    "delete_payload",
    "resolve",
    "count_rows_by_key_version",
    "reencrypt_all_to_current_version",
    "get_current_key_version",
    "reset_for_tests",
}


def test_connector_credentials_module_re_exports_full_historical_surface():
    from services import connector_credentials as ccs

    public = {n for n in dir(ccs) if not n.startswith("_")}
    missing = EXPECTED_PUBLIC_SURFACE - public
    assert not missing, f"public surface regressed; missing names: {sorted(missing)}"
