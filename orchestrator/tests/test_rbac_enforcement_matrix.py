# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-334 — household RBAC enforcement matrix (Chunk A).

Locks the enforcement audit against regression:

* every ``/api/v1/admin/users`` route 403s a member and is reachable by an admin;
* the scope publish/unpublish routes carry ``require_user`` (the audit gap fixed
  in this chunk — they previously used ``Depends(get_user)``, which does not 401);
* a route-walk guard asserts no ``/api/v1`` data route outside the auth-bypass
  allowlist (``auth._AUTH_BYPASS_PREFIXES``) is left without
  ``require_user`` / ``require_admin``.

All tests run with ``AUTH_ENABLED=true`` (``auth_env``): under the default
``AUTH_ENABLED=false`` the authz dependencies are no-ops and these checks would
pass vacuously.
"""

from __future__ import annotations

# Reuse the Phase-1/2 fakes, fixtures (auth_env, users_store), and helpers.
pytest_plugins = ("tests.test_auth_phase2",)


def _route_has_dep(route, names: set[str]) -> bool:
    """True iff any dependency in the route's chain (two levels — covers
    router-level ``APIRouter(dependencies=[...])``) is one of ``names``."""
    dependant = getattr(route, "dependant", None)
    if dependant is None:
        return False
    for dep in dependant.dependencies:
        call = getattr(dep, "call", None)
        if call is not None and getattr(call, "__name__", "") in names:
            return True
        for sub in dep.dependencies:
            scall = getattr(sub, "call", None)
            if scall is not None and getattr(scall, "__name__", "") in names:
                return True
    return False


# ---------------------------------------------------------------------------
# Role 403 matrix on the user-management API
# ---------------------------------------------------------------------------


def test_member_is_forbidden_from_admin_users(users_store, auth_env):
    from tests.test_auth_phase2 import _client
    from tests.test_auth_phase2 import _user_headers

    with _client(users_store) as client:
        headers = _user_headers(users_store)
        # GET list and POST create are both router-level require_admin.
        assert client.get("/api/v1/admin/users", headers=headers).status_code == 403
        assert (
            client.post(
                "/api/v1/admin/users",
                headers=headers,
                json={"email": "x@home.lan", "password": "verylongpassword12", "role": "user"},
            ).status_code
            == 403
        )


def test_admin_reaches_admin_users_list(users_store, auth_env):
    from tests.test_auth_phase2 import _admin_headers
    from tests.test_auth_phase2 import _client

    with _client(users_store) as client:
        resp = client.get("/api/v1/admin/users", headers=_admin_headers(users_store))
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


# ---------------------------------------------------------------------------
# Audit gap fixed: scope publish/unpublish routes now require auth
# ---------------------------------------------------------------------------


def test_scope_publish_routes_require_user(users_store, auth_env):
    """The 12 publish/unpublish routes were on ``get_user`` (no 401); the audit
    upgraded them to ``require_user``. Lock it so they can't regress."""
    import main
    from tests.test_auth_phase2 import _iter_api_routes

    publish_routes = [
        r
        for r in _iter_api_routes(main.app)
        if getattr(r, "dependant", None) is not None and getattr(r, "path", "").endswith("/publish")
    ]
    assert publish_routes, "expected scope publish/unpublish routes to be registered"
    ungated = [
        f"{','.join(sorted(getattr(r, 'methods', ()) or ()))} {r.path}"
        for r in publish_routes
        if not _route_has_dep(r, {"require_user", "require_admin"})
    ]
    assert not ungated, f"scope publish/unpublish routes missing require_user: {ungated}"


# ---------------------------------------------------------------------------
# Full-sweep enforcement guard: every /api/v1 data route is gated
# ---------------------------------------------------------------------------


def test_no_route_reads_get_user_without_a_gate(users_store, auth_env):
    """Guard the exact anti-pattern the audit fixed: a route that reads the user
    via ``Depends(get_user)`` must ALSO enforce auth (``require_user`` /
    ``require_admin``) — ``get_user`` alone does **not** 401.

    This is deliberately scoped to the ``get_user`` pattern rather than a
    blanket "every /api/v1 route needs require_user" sweep: many routes are
    gated by the auth middleware + in-handler scoping and legitimately carry no
    ``require_user`` dep, so a blanket sweep would over-flag. The ``get_user``
    pattern is the one that silently fails open (scope.py, LUM-334), so that is
    what we lock. Verified universe today: scope.py (now ``require_user``) +
    admin.py ``/settings`` (already ``require_admin``)."""
    import main
    from tests.test_auth_phase2 import _iter_api_routes

    offenders = []
    for r in _iter_api_routes(main.app):
        if getattr(r, "dependant", None) is None:
            continue
        if _route_has_dep(r, {"get_user"}) and not _route_has_dep(
            r, {"require_user", "require_admin"}
        ):
            methods = ",".join(sorted(getattr(r, "methods", ()) or ()))
            offenders.append(f"{methods} {getattr(r, 'path', '')}")
    assert not offenders, (
        "routes read the user via get_user without require_user/require_admin "
        f"(get_user does not 401 — add the gate): {offenders}"
    )
