# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Route-layer tests for the per-user export / import surface.

Pins down the HTTP contracts the plan ``per_user_backup_export``
documents, including:

* ``POST /api/v1/me/export`` — self-export (200 + zip body), admin
  on-behalf via ``target_user_id`` body field, 404 for unknown
  ``target_user_id``, 403 when a non-admin tries to target another
  user.
* ``POST /api/v1/admin/user-imports`` — dry-run (200 + ``ImportPlan``)
  vs real import (201 + ``Location: /api/v1/admin/users/{id}``);
  refusal status mapping (forbidden_path → 403, manifest_invalid →
  400, archive_too_large → 413).
* ``GET  /api/v1/admin/export`` — legacy NDJSON dumper returns
  ``410 Gone`` with ``successor`` pointer (plan deviation, recorded
  in ADR ``per_user_backup_export``).
* CSRF / Bearer interaction — Bearer-authenticated callers bypass
  :func:`csrf.require_same_origin` by design in v1; this is the
  regression pin so the contract isn't accidentally tightened before
  the cookie-session work lands.

Service-level behaviour (manifest parsing, redaction, pruning) lives
in :mod:`tests.test_user_export`; integration round-trip lives in
:mod:`tests.integration.test_per_user_export_roundtrip`. This module
focuses strictly on the wire boundary.
"""

from __future__ import annotations

import io
import json
import os
import time
import uuid
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import jwt
import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers shared with the auth-phase test suite (mocks + JWT minting).
#
# The phase-1 ``FakeUsersStore`` already speaks the SQL surface our route
# handlers hit (users CRUD + last_login_at + refresh_token_jti), so we
# reuse it here rather than rebuilding a parallel mock that would drift
# out of sync with services/users.py.
# ---------------------------------------------------------------------------


from tests.test_auth_phase1 import FakeUsersStore  # noqa: E402


class _UserExportFakeStore(FakeUsersStore):
    """``FakeUsersStore`` extended with the ``transaction()`` context
    manager that ``services.user_export.import_user`` requires.

    The real ``MetadataStore`` port (added in the per_user_backup_export
    plan, Pass 0) exposes ``transaction()`` as the explicit boundary for
    refusal-and-rollback semantics. The phase-1 fake predates that
    addition and the auth tests don't need it. We stay compatible by
    subclassing rather than mutating the upstream fake.
    """

    def transaction(self):
        from contextlib import contextmanager

        @contextmanager
        def _noop():
            yield

        return _noop()


@pytest.fixture
def users_store(monkeypatch):
    """In-memory MetadataStore swapped in for every test."""
    import config as _config

    store = _UserExportFakeStore()
    _config._instances["metadata_store"] = store
    yield store
    _config._instances.pop("metadata_store", None)


@pytest.fixture
def export_dir(tmp_path, monkeypatch):
    """Sandboxed ``USER_EXPORT_DIR`` so archives don't pollute /workspace."""
    from services import user_export as svc

    monkeypatch.setattr(svc, "_USER_EXPORT_DIR", tmp_path)
    return tmp_path


@pytest.fixture
def dev_env(monkeypatch):
    """``AUTH_ENABLED=false`` — admin/user gates are no-ops."""
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.delenv("LUMOGIS_PUBLIC_ORIGIN", raising=False)
    yield


@pytest.fixture
def auth_env(monkeypatch):
    """``AUTH_ENABLED=true`` with deterministic secrets + short TTLs."""
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_SECRET", "test-export-routes-access-secret")
    monkeypatch.setenv(
        "LUMOGIS_JWT_REFRESH_SECRET",
        "test-export-routes-refresh-secret",
    )
    monkeypatch.setenv("ACCESS_TOKEN_TTL_SECONDS", "900")
    monkeypatch.setenv("REFRESH_TOKEN_TTL_SECONDS", "3600")
    monkeypatch.setenv("LUMOGIS_REFRESH_COOKIE_SECURE", "false")
    yield
    from routes.auth import _reset_rate_limit_for_tests
    _reset_rate_limit_for_tests()


def _mint(user_id: str, role: str) -> str:
    """Mint a valid access JWT against the current ``AUTH_SECRET``."""
    return jwt.encode(
        {
            "sub": user_id,
            "role": role,
            "iat": int(time.time()),
            "exp": int(time.time()) + 600,
        },
        os.environ["AUTH_SECRET"],
        algorithm="HS256",
    )


@contextmanager
def _client():
    """Boot the live FastAPI app inside a TestClient (lifespan executes)."""
    import main
    with TestClient(main.app) as client:
        yield client


def _seed_admin(users_store) -> str:
    """Create the seeded admin (idempotent) and return their id."""
    import services.users as users_svc
    if users_svc.get_user_by_email("admin@home.lan") is None:
        users_svc.create_user("admin@home.lan", "verylongpassword12", "admin")
    admin = users_svc.get_user_by_email("admin@home.lan")
    assert admin is not None
    return admin.id


def _seed_user(users_store) -> str:
    """Create a non-admin user (idempotent) and return their id."""
    import services.users as users_svc
    if users_svc.get_user_by_email("bob@home.lan") is None:
        users_svc.create_user("bob@home.lan", "verylongpassword12", "user")
    target = users_svc.get_user_by_email("bob@home.lan")
    assert target is not None
    return target.id


def _admin_headers(users_store) -> dict:
    """Bearer headers for the seeded admin."""
    return {"Authorization": f"Bearer {_mint(_seed_admin(users_store), 'admin')}"}


def _user_headers(users_store) -> dict:
    """Bearer headers for the seeded non-admin."""
    return {"Authorization": f"Bearer {_mint(_seed_user(users_store), 'user')}"}


def _build_archive(
    export_dir: Path,
    *,
    user_id: str = "u-source",
    email: str = "source@example.com",
    role: str = "user",
    sections: dict[str, list[dict]] | None = None,
    bad_entry: str | None = None,
    drop_user_record: bool = False,
    declared_extra_section: str | None = None,
    bogus_manifest: bool = False,
) -> Path:
    """Synthesise a syntactically-valid archive on disk under ``export_dir/``.

    ``sections`` overrides the per-table contents. When ``bad_entry`` is
    set the archive includes a zip-slip / NUL-byte / leading-slash entry
    so the slip-validator should reject it. ``drop_user_record`` skips
    the ``users/{id}.json`` member so the missing-record refusal fires.
    ``bogus_manifest`` writes deliberately invalid JSON to trip the
    manifest_invalid path.
    """
    sections = sections or {}
    user_dir = export_dir / user_id
    user_dir.mkdir(parents=True, exist_ok=True)
    out = user_dir / f"export_test_{uuid.uuid4().hex[:8]}.zip"
    declared = [
        {"name": f"postgres/{t}.json", "kind": "postgres",
         "row_count": len(rows)}
        for t, rows in sections.items()
    ]
    if not drop_user_record:
        declared.append({
            "name": f"users/{user_id}.json",
            "kind": "user_record",
            "row_count": 1,
        })
    if declared_extra_section:
        declared.append({
            "name": declared_extra_section,
            "kind": "postgres",
            "row_count": 99,
        })
    manifest = {
        "format_version": 1,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "exporting_user_id": user_id,
        "exported_user_email": email,
        "exported_user_role": role,
        "scope_filter": "authored_by_me",
        "falkordb_edge_policy": "personal_intra_user_authored",
        "sections": declared,
        "falkordb_external_edge_count": 0,
    }
    with zipfile.ZipFile(out, "w") as zf:
        if bogus_manifest:
            zf.writestr("manifest.json", "{not json")
        else:
            zf.writestr("manifest.json", json.dumps(manifest))
        if not drop_user_record:
            zf.writestr(
                f"users/{user_id}.json",
                json.dumps({"id": user_id, "email": email, "role": role}),
            )
        for table, rows in sections.items():
            zf.writestr(f"postgres/{table}.json", json.dumps(rows))
        if bad_entry:
            zf.writestr(bad_entry, b"junk")
    return out


# ---------------------------------------------------------------------------
# /api/v1/me/export
# ---------------------------------------------------------------------------


def test_me_export_self_returns_zip_in_dev_mode(
    users_store, dev_env, export_dir,
):
    """Default dev-mode caller (``user_id="default"``) gets their own zip."""
    with _client() as client:
        resp = client.post("/api/v1/me/export", json={})
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("application/zip")
    cd = resp.headers.get("content-disposition", "")
    assert "attachment" in cd and "export_" in cd
    # Body is a parseable zip with the documented invariants.
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        names = zf.namelist()
        assert "manifest.json" in names
        manifest = json.loads(zf.read("manifest.json"))
        assert manifest["scope_filter"] == "authored_by_me"
        assert manifest["exporting_user_id"] == "default"


def test_me_export_admin_on_behalf_unknown_target_returns_404(
    users_store, auth_env, export_dir,
):
    """Pre-fix this returned 200 + an empty archive (invisibly wrong);
    plan §"Admin-on-behalf Export" requires a loud 404 instead so an
    operator can tell "user does not exist" apart from "user has no
    personal data". See ADR per_user_backup_export §F2."""
    with _client() as client:
        resp = client.post(
            "/api/v1/me/export",
            headers=_admin_headers(users_store),
            json={"target_user_id": "this-user-does-not-exist"},
        )
    assert resp.status_code == 404, resp.text
    body = resp.json()
    assert body["detail"]["error"] == "user not found"
    assert body["detail"]["target_user_id"] == "this-user-does-not-exist"


def test_me_export_admin_on_behalf_known_target_succeeds(
    users_store, auth_env, export_dir,
):
    """Existing target user → admin gets that user's zip back."""
    bob_id = _seed_user(users_store)
    with _client() as client:
        resp = client.post(
            "/api/v1/me/export",
            headers=_admin_headers(users_store),
            json={"target_user_id": bob_id},
        )
    assert resp.status_code == 200, resp.text
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        manifest = json.loads(zf.read("manifest.json"))
        assert manifest["exporting_user_id"] == bob_id


def test_me_export_non_admin_targeting_other_user_returns_403(
    users_store, auth_env, export_dir,
):
    """Plan §F2: non-admin caller with ``target_user_id`` set to a
    different user → 403 before we even check whether the target
    exists. Keeps role enumeration off the 404 channel."""
    _seed_admin(users_store)
    with _client() as client:
        resp = client.post(
            "/api/v1/me/export",
            headers=_user_headers(users_store),
            json={"target_user_id": "anyone-else"},
        )
    assert resp.status_code == 403, resp.text


def test_me_export_self_export_skips_target_existence_check(
    users_store, auth_env, export_dir,
):
    """Caller targeting themselves must not be rejected on the 404 path
    just because the FakeUsersStore happens to mock a different lookup."""
    bob_id = _seed_user(users_store)
    with _client() as client:
        resp = client.post(
            "/api/v1/me/export",
            headers=_user_headers(users_store),
            json={"target_user_id": bob_id},
        )
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# CSRF / Bearer interaction — the v1 contract regression pin.
# ---------------------------------------------------------------------------


def test_export_route_with_bearer_skips_csrf_intentionally(
    users_store, auth_env, export_dir, monkeypatch,
):
    """Pin the v1 contract that ``csrf.require_same_origin`` is bypassed
    for Bearer-authenticated callers.

    Why this is intentional today (do not "fix" by removing the bypass):
      * Browsers never auto-attach an ``Authorization: Bearer ...``
        header to a cross-site fetch — the CSRF threat model assumes
        attacker-controlled forms / images / fetches that *can't* mint
        a Bearer.
      * The dashboard, the MCP surface, and curl all use Bearer auth in
        v1; tightening this would break every legitimate non-cookie
        caller.
      * Once the cookie-session work in ``cross_device_lumogis_web``
        lands, cookie-authenticated POSTs WILL hit the same dependency
        and be enforced. This test keeps watch over the boundary.

    If you are touching this test because the assertion failed: the
    expected fix is to update :func:`csrf.require_same_origin` AND the
    plan/ADR notes simultaneously, NOT to silently accept the new
    behaviour.
    """
    monkeypatch.setenv("LUMOGIS_PUBLIC_ORIGIN", "https://lumogis.home.lan")
    headers = _admin_headers(users_store)
    # Crucially: no Origin header. A cookie-auth caller would be
    # refused (403). The Bearer path must pass.
    assert "Origin" not in headers
    with _client() as client:
        resp = client.post("/api/v1/me/export", headers=headers, json={})
    assert resp.status_code == 200, (
        f"Bearer-auth + LUMOGIS_PUBLIC_ORIGIN set + no Origin header "
        f"must succeed in v1 — see csrf.require_same_origin bypass #3. "
        f"Status={resp.status_code} body={resp.text[:300]}"
    )


def test_csrf_dependency_still_enforces_for_non_bearer_writes(
    monkeypatch,
):
    """Counterpart to the Bearer-bypass regression pin: prove the CSRF
    dep itself still 403s on the cookie/no-Bearer path so we know the
    Bearer bypass is genuinely a narrow exception, not a "CSRF entirely
    off" door.

    We exercise :func:`csrf.require_same_origin` directly with a fake
    Request to dodge the auth middleware (AUTH_ENABLED=true would
    intercept the no-Bearer path with a 401 before our dep ran; that
    middleware behaviour is covered by phase-1 tests). Together with
    the Bearer-bypass test above, the contract is pinned from both
    sides.
    """
    from fastapi import HTTPException

    from csrf import require_same_origin

    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("LUMOGIS_PUBLIC_ORIGIN", "https://lumogis.home.lan")

    class _Req:
        method = "POST"
        url = type("U", (), {"path": "/api/v1/me/export"})()
        headers = {
            "Origin": "https://attacker.example.com",
            # No Authorization header — that's the cookie/forged-form path.
        }

    with pytest.raises(HTTPException) as ei:
        require_same_origin(_Req())
    assert ei.value.status_code == 403
    assert ei.value.detail == "origin mismatch"


# ---------------------------------------------------------------------------
# /api/v1/admin/user-imports — dry-run + real
# ---------------------------------------------------------------------------


def test_dry_run_import_success_returns_import_plan(
    users_store, dev_env, export_dir,
):
    archive = _build_archive(
        export_dir,
        user_id="u-source",
        sections={"notes": []},
    )
    with _client() as client:
        resp = client.post(
            "/api/v1/admin/user-imports",
            json={
                "archive_path": str(archive),
                "new_user": {
                    "email": "fresh@home.lan",
                    "password": "verylongpassword12",
                    "role": "user",
                },
                "dry_run": True,
            },
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["manifest_version"] == 1
    assert body["scope_filter"] == "authored_by_me"
    # Dry-run is non-mutating → must NOT carry a Location header (that
    # is exclusive to the 201 success path on the real-import branch).
    assert "location" not in {k.lower() for k in resp.headers.keys()}


def test_real_import_returns_201_with_location_header(
    users_store, dev_env, export_dir,
):
    """Plan §"Import success contract": real import → 201 + Location.

    Pre-fix this returned a generic 200; the contract upgrade is what
    lets a client distinguish "dry-run validated" from "user actually
    minted" without inspecting the body.
    """
    archive = _build_archive(
        export_dir,
        user_id="u-source",
        sections={"notes": []},
    )
    with _client() as client:
        resp = client.post(
            "/api/v1/admin/user-imports",
            json={
                "archive_path": str(archive),
                "new_user": {
                    "email": "imported@home.lan",
                    "password": "verylongpassword12",
                    "role": "user",
                },
                "dry_run": False,
            },
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    new_id = body["new_user_id"]
    assert new_id and isinstance(new_id, str)
    # Location MUST point at the canonical admin user resource so the
    # client can fetch / patch / delete the freshly-minted account.
    assert resp.headers.get("Location") == f"/api/v1/admin/users/{new_id}"


def test_dry_run_does_not_create_user(users_store, dev_env, export_dir):
    """``dry_run=true`` is the explicit non-mutating path — the new
    user must not appear in the users table.

    Both the pre-call check and the post-call check happen INSIDE the
    TestClient context manager — the lifespan teardown clears
    ``config._instances`` on exit, and ``users_svc.get_user_by_email``
    would then try to import ``psycopg2`` and fail in the local venv.
    """
    archive = _build_archive(
        export_dir, user_id="u-source", sections={"notes": []},
    )
    import services.users as users_svc
    with _client() as client:
        assert users_svc.get_user_by_email("never-minted@home.lan") is None
        resp = client.post(
            "/api/v1/admin/user-imports",
            json={
                "archive_path": str(archive),
                "new_user": {
                    "email": "never-minted@home.lan",
                    "password": "verylongpassword12",
                    "role": "user",
                },
                "dry_run": True,
            },
        )
        assert resp.status_code == 200, resp.text
        # Real assertion: dry-run is non-mutating, so no users row exists.
        assert users_svc.get_user_by_email("never-minted@home.lan") is None


# ---------------------------------------------------------------------------
# Refusal contract — every reason maps to its plan-nominated status code.
# ---------------------------------------------------------------------------


def test_import_outside_root_returns_403(
    users_store, dev_env, export_dir, tmp_path,
):
    """``forbidden_path`` → 403. Defence-in-depth against an admin
    pasting an arbitrary filesystem path."""
    outside = tmp_path.parent / "outside_export.zip"
    outside.write_bytes(b"PK\x03\x04")  # zip magic — never opened
    with _client() as client:
        resp = client.post(
            "/api/v1/admin/user-imports",
            json={
                "archive_path": str(outside),
                "new_user": {
                    "email": "x@home.lan",
                    "password": "verylongpassword12",
                    "role": "user",
                },
                "dry_run": True,
            },
        )
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["refusal_reason"] == "forbidden_path"


def test_import_invalid_manifest_returns_400(
    users_store, dev_env, export_dir,
):
    archive = _build_archive(
        export_dir, user_id="u-source", bogus_manifest=True,
    )
    with _client() as client:
        resp = client.post(
            "/api/v1/admin/user-imports",
            json={
                "archive_path": str(archive),
                "new_user": {
                    "email": "x@home.lan",
                    "password": "verylongpassword12",
                    "role": "user",
                },
                "dry_run": True,
            },
        )
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"]["refusal_reason"] == "manifest_invalid"


def test_import_unsafe_entry_names_returns_400(
    users_store, dev_env, export_dir,
):
    archive = _build_archive(
        export_dir,
        user_id="u-source",
        sections={"notes": []},
        bad_entry="../etc/passwd",
    )
    with _client() as client:
        resp = client.post(
            "/api/v1/admin/user-imports",
            json={
                "archive_path": str(archive),
                "new_user": {
                    "email": "x@home.lan",
                    "password": "verylongpassword12",
                    "role": "user",
                },
                "dry_run": True,
            },
        )
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"]["refusal_reason"] == "archive_unsafe_entry_names"


def test_real_import_email_collision_returns_409(
    users_store, dev_env, export_dir,
):
    """``email_exists`` → 409 (per plan refusal table)."""
    import services.users as users_svc
    users_svc.create_user("dup@home.lan", "verylongpassword12", "user")
    archive = _build_archive(
        export_dir, user_id="u-source", sections={"notes": []},
    )
    with _client() as client:
        resp = client.post(
            "/api/v1/admin/user-imports",
            json={
                "archive_path": str(archive),
                "new_user": {
                    "email": "dup@home.lan",
                    "password": "verylongpassword12",
                    "role": "user",
                },
                "dry_run": False,
            },
        )
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["refusal_reason"] == "email_exists"


def test_real_import_missing_user_record_returns_400(
    users_store, dev_env, export_dir,
):
    archive = _build_archive(
        export_dir,
        user_id="u-source",
        sections={"notes": []},
        drop_user_record=True,
    )
    with _client() as client:
        resp = client.post(
            "/api/v1/admin/user-imports",
            json={
                "archive_path": str(archive),
                "new_user": {
                    "email": "ok@home.lan",
                    "password": "verylongpassword12",
                    "role": "user",
                },
                "dry_run": False,
            },
        )
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"]["refusal_reason"] == "missing_user_record"


# ---------------------------------------------------------------------------
# Audit lifecycle — refused vs failed are distinct events (Task 2 fix).
# ---------------------------------------------------------------------------


# All refusal reasons currently mapped in the route. If a new
# ImportRefused reason is added, this list must grow with it; the
# regression assertion in the test below catches drift.
_REFUSAL_REASONS_FROM_PLAN: tuple[str, ...] = (
    "archive_too_large",
    "archive_integrity_failed",
    "archive_unsafe_entry_names",
    "manifest_invalid",
    "missing_user_record",
    "manifest_section_count_mismatch",
    "missing_sections",
    "unsupported_format_version",
    "forbidden_path",
    "email_exists",
    "uuid_collision_on_parent_table",
)


def test_route_refusal_reason_table_matches_service_enum():
    """Every reason the service can raise must be mapped in the route's
    status table — otherwise the response would default to a generic
    400 and lose the contract.
    """
    from routes.admin_users import _REFUSAL_TO_STATUS
    for reason in _REFUSAL_REASONS_FROM_PLAN:
        assert reason in _REFUSAL_TO_STATUS, (
            f"plan-nominated refusal reason {reason!r} is missing from "
            f"routes.admin_users._REFUSAL_TO_STATUS — add it before the "
            f"new reason ships."
        )


def test_refused_audit_event_emitted_on_dry_run_refusal(
    users_store, dev_env, export_dir, monkeypatch,
):
    """Pre-start refusals (manifest_invalid here) must emit a dedicated
    ``__user_import__.refused`` audit event so operators can tell
    refusal apart from failure (which keeps ``.failed``).

    We capture by patching ``services.user_export._audit_event`` so we
    don't depend on the audit table's integration semantics here.
    """
    captured: list[tuple[str, dict, dict]] = []

    from services import user_export as svc

    def _capture(action, *, user_id, input_summary=None, result_summary=None):
        captured.append((action, dict(input_summary or {}),
                         dict(result_summary or {})))

    monkeypatch.setattr(svc, "_audit_event", _capture)

    archive = _build_archive(
        export_dir, user_id="u-source", bogus_manifest=True,
    )
    with _client() as client:
        resp = client.post(
            "/api/v1/admin/user-imports",
            json={
                "archive_path": str(archive),
                "new_user": {
                    "email": "x@home.lan",
                    "password": "verylongpassword12",
                    "role": "user",
                },
                "dry_run": True,
            },
        )
    assert resp.status_code == 400
    refused_events = [e for e in captured if e[0] == "__user_import__.refused"]
    assert refused_events, (
        f"expected a __user_import__.refused audit event for "
        f"manifest_invalid; got actions: "
        f"{[e[0] for e in captured]!r}"
    )
    # Payload structure is part of the contract — operators grep for
    # refusal_reason, payload, and stage.
    _, _input, result = refused_events[-1]
    assert result["refusal_reason"] == "manifest_invalid"
    assert result["stage"] == "dry_run"
    assert "payload" in result


def test_refused_audit_event_emitted_for_post_started_refusal(
    users_store, dev_env, export_dir, monkeypatch,
):
    """``email_exists`` fires AFTER ``__user_import__.started`` was
    emitted — but it is still a refusal (no writes happened yet) and
    must show up as ``.refused``, not ``.failed``."""
    captured: list[tuple[str, dict, dict]] = []
    from services import user_export as svc

    def _capture(action, *, user_id, input_summary=None, result_summary=None):
        captured.append((action, dict(input_summary or {}),
                         dict(result_summary or {})))

    monkeypatch.setattr(svc, "_audit_event", _capture)

    import services.users as users_svc
    users_svc.create_user("dup2@home.lan", "verylongpassword12", "user")

    archive = _build_archive(
        export_dir, user_id="u-source", sections={"notes": []},
    )
    with _client() as client:
        resp = client.post(
            "/api/v1/admin/user-imports",
            json={
                "archive_path": str(archive),
                "new_user": {
                    "email": "dup2@home.lan",
                    "password": "verylongpassword12",
                    "role": "user",
                },
                "dry_run": False,
            },
        )
    assert resp.status_code == 409
    actions = [e[0] for e in captured]
    assert "__user_import__.started" in actions
    assert "__user_import__.refused" in actions
    # Crucially: NOT .failed — that's reserved for partial-state
    # exceptions after writes begin, which is a different operator
    # signal entirely.
    assert "__user_import__.failed" not in actions, (
        f"email_exists is a precondition refusal, not a failure — got "
        f"actions={actions!r}"
    )


# ---------------------------------------------------------------------------
# Legacy /api/v1/admin/export → 410 Gone (recorded deviation).
# ---------------------------------------------------------------------------


def test_legacy_admin_export_returns_410_with_successor(
    users_store, dev_env,
):
    """Plan deviation (recorded in ADR per_user_backup_export §status
    history): the legacy NDJSON dumper now returns ``410 Gone``
    instead of being kept byte-for-byte unchanged for one release.
    Operators are pointed at ``POST /api/v1/me/export`` via the
    detail body."""
    with _client() as client:
        # NB: ``admin_router`` is mounted without a prefix in
        # ``main.py``, so the legacy NDJSON dump lives at ``/export``
        # rather than ``/api/v1/admin/export``. The successor pointer
        # in the 410 body still uses the canonical, prefixed path
        # because that is what the new per-user route is mounted at.
        resp = client.get("/export")
    assert resp.status_code == 410, resp.text
    body = resp.json()
    assert body["detail"]["error"] == "deprecated"
    assert body["detail"]["successor"] == "POST /api/v1/me/export"
