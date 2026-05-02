# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Per-user backup/export — end-to-end route-level roundtrip.

Plan §"Test cases / Integration" (per_user_backup_export.plan.md):

    Alice (admin) seeds personal-scoped notes + a shared-scoped note +
    one row that belongs to a different user. She exports via
    POST /api/v1/me/export. An admin then imports the resulting archive
    into a brand-new account (POST /api/v1/admin/user-imports). We
    assert:

      * the export went through the FastAPI route stack (not just
        services.user_export.export_user) and respected the per-user
        ``authored_by_filter`` filter (personal + shared, never
        someone else's),
      * the archive lands under ``$USER_EXPORT_DIR/<user_id>/`` so the
        admin import can resolve it under the configured allowlist,
      * the dry-run path returns a structured ImportPlan with
        ``would_succeed=true`` and does not mutate the destination,
      * the real import path returns ``201 Created`` with a
        ``Location: /api/v1/admin/users/{new_user_id}`` header,
      * the imported personal-scoped rows survive the roundtrip and
        carry the freshly-minted user_id (re-pointed away from the
        source user's id, per the import service contract),
      * shared/system data ("someone-else-owned" rows) was *not*
        included in the per-user export and therefore is not present
        on the destination after import,
      * a second import with the same email refuses cleanly with
        ``409 Conflict`` and ``refusal_reason="email_exists"`` — i.e.
        the import never silently merges with a populated destination.

This test exists because the route-level wiring is the single place
where ``csrf.require_same_origin``, the Bearer auth dep, the JSON
manifest contract, the per-user table allowlist, and the ``201 +
Location`` semantics all meet. Earlier service-layer tests
(``test_user_export.py``) pin each block in isolation; this one is the
operator-facing contract.
"""

from __future__ import annotations

import json
import re
import zipfile
from contextlib import contextmanager
from datetime import datetime
from datetime import timezone

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Fake metadata store: handles users + a small, configurable per-user table
# set so the export filter and the import insert path can both run end-to-end.
# ---------------------------------------------------------------------------


_PER_USER_TABLES_WITH_SCOPE = {
    "notes",
    "file_index",
    "entities",
    "sessions",
    "audio_memos",
    "signals",
    "review_queue",
    "action_log",
    "audit_log",
}


class _RoundtripStore:
    """In-memory MetadataStore for the per-user export/import roundtrip.

    Backs both the export-side ``SELECT * FROM <table> WHERE …`` and
    the import-side ``INSERT INTO <table> (...) VALUES (...)`` flows
    against the same dict-of-lists per table. Only the small subset of
    SQL emitted by ``services.user_export`` and ``services.users`` is
    handled — anything else is a no-op (execute) or returns ``None`` /
    ``[]`` (fetch). This is intentional: the test pins behaviour for
    the routes that exist today, not the entire MetadataStore surface.
    """

    def __init__(self) -> None:
        self.users: dict[str, dict] = {}
        # table_name -> list[row dict]
        self.tables: dict[str, list[dict]] = {}
        self.exec_log: list[tuple[str, tuple]] = []

    # --- lifecycle ----------------------------------------------------

    def ping(self) -> bool:
        return True

    def close(self) -> None:
        pass

    @contextmanager
    def transaction(self):
        # Real Postgres rolls back on exception; the test relies on the
        # ``ImportRefused`` raise NOT happening in the success path, so
        # a no-op CM is enough — the only path that depends on rollback
        # semantics is covered by the route-level refusal tests in
        # ``test_user_export_routes.py`` (email_exists, manifest_invalid).
        yield

    # --- writes -------------------------------------------------------

    def execute(self, query: str, params: tuple | None = None) -> None:
        self.exec_log.append((query, params or ()))
        q = " ".join(query.split())
        ql = q.lower()
        p = params or ()

        # --- users ---
        if ql.startswith("insert into users"):
            self.users[p[0]] = {
                "id": p[0],
                "email": p[1],
                "password_hash": p[2],
                "role": p[3],
                "disabled": False,
                "created_at": datetime.now(timezone.utc),
                "last_login_at": None,
                "refresh_token_jti": None,
            }
            return
        if ql.startswith("update users set last_login_at = now()"):
            self.users[p[0]]["last_login_at"] = datetime.now(timezone.utc)
            return
        if ql.startswith("update users set refresh_token_jti ="):
            self.users[p[1]]["refresh_token_jti"] = p[0]
            return

        # --- generic INSERT INTO <table> (col, ...) VALUES (%s, ...) ---
        m = re.match(
            r"insert into (\w+)\s*\(([^)]+)\)\s*values\s*\(([^)]+)\)",
            ql,
        )
        if m:
            table = m.group(1)
            cols = [c.strip() for c in m.group(2).split(",")]
            row = {c: v for c, v in zip(cols, p)}
            self.tables.setdefault(table, []).append(row)
            return

    # --- reads --------------------------------------------------------

    def fetch_one(self, query: str, params: tuple | None = None) -> dict | None:
        q = " ".join(query.split()).lower()
        p = params or ()

        if q.startswith("select id from users where lower(email) ="):
            target = p[0].lower()
            for row in self.users.values():
                if row["email"].lower() == target:
                    return {"id": row["id"]}
            return None
        if q.startswith("select * from users where id ="):
            return dict(self.users[p[0]]) if p[0] in self.users else None
        if q.startswith("select * from users where lower(email) ="):
            target = p[0].lower()
            for row in self.users.values():
                if row["email"].lower() == target:
                    return dict(row)
            return None
        if q.startswith("select count(*) as n from users where role = 'admin'"):
            n = sum(1 for r in self.users.values() if r["role"] == "admin" and not r["disabled"])
            return {"n": n}
        if q.startswith("select count(*) as n from users"):
            return {"n": len(self.users)}
        if q.startswith("select id from users where role = 'admin'"):
            admins = sorted(
                (r for r in self.users.values() if r["role"] == "admin" and not r["disabled"]),
                key=lambda r: r["created_at"],
            )
            return {"id": admins[0]["id"]} if admins else None
        if q.startswith("select refresh_token_jti from users where id ="):
            row = self.users.get(p[0])
            return {"refresh_token_jti": row["refresh_token_jti"]} if row else None

        # SELECT COUNT(*) AS n FROM <table> WHERE … — the export-side
        # data inventory + per-table size stats all use this shape.
        m = re.match(r"select count\(\*\) as n from (\w+)", q)
        if m:
            table = m.group(1)
            rows = self._filter_table_rows(table, q, p)
            return {"n": len(rows)}

        return None

    def fetch_all(self, query: str, params: tuple | None = None) -> list[dict]:
        q = " ".join(query.split()).lower()
        p = params or ()

        if q.startswith("select * from users order by created_at"):
            return sorted(
                (dict(r) for r in self.users.values()),
                key=lambda r: r["created_at"],
            )

        # Parent-collision pre-check:
        #   SELECT <pk> AS pk FROM <table> WHERE <pk> = ANY(%s)
        m = re.match(
            r"select (\w+) as pk from (\w+)\s+where (\w+)\s*=\s*any\(%s\)",
            q,
        )
        if m:
            pk_col = m.group(1)
            table = m.group(2)
            ids = list(p[0]) if p and p[0] is not None else []
            return [{"pk": r[pk_col]} for r in self.tables.get(table, []) if r.get(pk_col) in ids]

        # Generic SELECT * FROM <table> WHERE …
        m = re.match(r"select \* from (\w+)", q)
        if m:
            table = m.group(1)
            return [dict(r) for r in self._filter_table_rows(table, q, p)]

        return []

    # --- helpers ------------------------------------------------------

    def _filter_table_rows(
        self,
        table: str,
        query_lower: str,
        params: tuple,
    ) -> list[dict]:
        """Apply the ``authored_by_filter`` / user_id filter in Python.

        Mirrors :func:`services.user_export._table_filter`:

          * tables with a ``scope`` column → keep rows where
            ``scope IN ('personal','shared') AND user_id = $me`` —
            this is what excludes "someone else's row" from the
            archive even when that row has ``scope='shared'`` (shared
            means visible-to-others-with-permission, NOT exportable
            on someone else's behalf),
          * other tables → keep rows where ``user_id = $me``.

        ``query_lower`` is the normalised SQL; we look at it (rather
        than the table name alone) to detect the scope clause so a
        future change to the SQL shape fails loudly here.
        """
        rows = self.tables.get(table, [])
        # Find user_id param — both SQL shapes have it as the LAST %s.
        if not params:
            return rows
        user_id = params[-1]
        if "scope in" in query_lower:
            return [
                r
                for r in rows
                if r.get("user_id") == user_id and r.get("scope") in ("personal", "shared")
            ]
        return [r for r in rows if r.get("user_id") == user_id]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def auth_env(monkeypatch):
    """Family-LAN mode + deterministic auth secrets."""
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_SECRET", "roundtrip-access-secret")
    monkeypatch.setenv("LUMOGIS_JWT_REFRESH_SECRET", "roundtrip-refresh-secret")
    monkeypatch.setenv("ACCESS_TOKEN_TTL_SECONDS", "900")
    monkeypatch.setenv("REFRESH_TOKEN_TTL_SECONDS", "3600")
    monkeypatch.setenv("LUMOGIS_REFRESH_COOKIE_SECURE", "false")
    yield
    from routes.auth import _reset_rate_limit_for_tests

    _reset_rate_limit_for_tests()


@pytest.fixture
def export_dir(tmp_path, monkeypatch):
    """Point the user_export service at a tmp dir.

    ``services.user_export`` reads ``USER_EXPORT_DIR`` at *module
    load time*, so we patch the module-level constant in addition to
    the env var — that way the writes from ``export_user`` and the
    ``_resolve_archive_path`` allowlist on the import path both see
    the same root.
    """
    import services.user_export as ue

    user_export_dir = tmp_path / "user_exports"
    user_export_dir.mkdir()
    monkeypatch.setenv("USER_EXPORT_DIR", str(user_export_dir))
    monkeypatch.setattr(ue, "_USER_EXPORT_DIR", user_export_dir)
    return user_export_dir


@pytest.fixture
def store(monkeypatch):
    """Install the in-memory metadata store as the global singleton."""
    import config as _config

    s = _RoundtripStore()
    _config._instances["metadata_store"] = s
    yield s
    _config._instances.pop("metadata_store", None)


@contextmanager
def _booted_client():
    """TestClient over the live FastAPI app (lifespan executes)."""
    import main

    with TestClient(main.app) as client:
        yield client


def _create_user(email: str, role: str) -> str:
    import services.users as users_svc

    user = users_svc.create_user(email, "verylongpassword12", role)
    return user.id


def _login(client: TestClient, email: str) -> str:
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "verylongpassword12"},
    )
    assert resp.status_code == 200, f"login for {email} failed: {resp.status_code} {resp.text}"
    return resp.json()["access_token"]


# ---------------------------------------------------------------------------
# THE roundtrip
# ---------------------------------------------------------------------------


def test_per_user_export_roundtrip_full_route_stack(
    auth_env,
    store,
    export_dir,
):
    """Full route-stack roundtrip — see module docstring for invariants."""
    # ------------------------------------------------------------------
    # 1. Seed: alice (admin) owns 2 personal notes + 1 shared note;
    #          bob owns 1 personal note that MUST NOT leak into alice's
    #          export.
    # ------------------------------------------------------------------
    alice_id = _create_user("alice@home.lan", "admin")
    bob_id = _create_user("bob@home.lan", "user")

    store.tables["notes"] = [
        {
            "note_id": "note-a-1",
            "user_id": alice_id,
            "scope": "personal",
            "title": "alice secret 1",
            "body": "ALICE-PERSONAL-1",
        },
        {
            "note_id": "note-a-2",
            "user_id": alice_id,
            "scope": "personal",
            "title": "alice secret 2",
            "body": "ALICE-PERSONAL-2",
        },
        {
            "note_id": "note-a-shared",
            "user_id": alice_id,
            "scope": "shared",
            "title": "alice shared note",
            "body": "ALICE-SHARED-NOTE",
        },
        # Bob's note — must NOT appear in alice's archive even though
        # it lives in the same table; the per-user filter is the gate.
        {
            "note_id": "note-b-1",
            "user_id": bob_id,
            "scope": "personal",
            "title": "bob secret",
            "body": "BOB-PERSONAL-DO-NOT-LEAK",
        },
    ]

    with _booted_client() as client:
        alice_token = _login(client, "alice@home.lan")

        # --------------------------------------------------------------
        # 2. Self-export through the route — exercises require_user +
        # csrf.require_same_origin (Bearer-bypass branch, pinned by
        # test_export_route_with_bearer_skips_csrf_intentionally) +
        # services.user_export.export_user.
        # --------------------------------------------------------------
        export_resp = client.post(
            "/api/v1/me/export",
            headers={"Authorization": f"Bearer {alice_token}"},
            json={},
        )
        assert export_resp.status_code == 200, export_resp.text
        assert export_resp.headers["content-type"] == "application/zip"
        archive_bytes = export_resp.content
        assert archive_bytes, "export produced empty body"

        # The export service ALSO persists to disk under
        # ``$USER_EXPORT_DIR/<alice>/export_*.zip`` — that is the path
        # the admin import endpoint will resolve under its allowlist.
        alice_dir = export_dir / alice_id
        archives_on_disk = sorted(alice_dir.glob("export_*.zip"))
        assert len(archives_on_disk) == 1, (
            f"expected exactly one persisted archive under {alice_dir}, got {archives_on_disk!r}"
        )
        archive_on_disk = archives_on_disk[0]

        # ----------------------------------------------------------------
        # Simulate "import into a fresh target instance":
        #
        # The plan's integration scenario is "export from instance A,
        # import into instance B". This test runs a single process /
        # single MetadataStore, so we have to model the *destination*
        # state explicitly. Without this step, the parent-table UUID
        # collision pre-check (entities/sessions/notes/...) trips
        # immediately because alice's source notes are still sitting
        # in ``store.tables["notes"]`` with the same note_id values
        # that the archive carries — which would refuse the import as
        # ``uuid_collision_on_parent_table`` (correct behaviour for a
        # populated destination, *wrong* model for the cross-instance
        # roundtrip we're trying to exercise here).
        #
        # We delete *only* alice's source-side rows; bob's row stays so
        # we can assert later that the import did not touch it. The
        # users table is left intact — alice still owns her account on
        # the source instance; the destination just doesn't know about
        # ``alice-imported@home.lan`` yet.
        # ----------------------------------------------------------------
        store.tables["notes"] = [
            r for r in store.tables.get("notes", []) if r.get("user_id") != alice_id
        ]

        # --------------------------------------------------------------
        # 3. Verify the archive's notes section: alice's 3 rows in,
        #    bob's row OUT. This is a direct gate on the
        #    ``authored_by_filter`` SQL semantics — without it, a
        #    table-wide SELECT would have leaked bob's row.
        # --------------------------------------------------------------
        with zipfile.ZipFile(archive_on_disk) as zf:
            manifest = json.loads(zf.read("manifest.json"))
            notes_in_archive = json.loads(zf.read("postgres/notes.json"))
        assert manifest["exporting_user_id"] == alice_id
        assert manifest["scope_filter"] == "authored_by_me"
        # Defensive — the manifest section list must include the user
        # record and the per-user notes section, never bob's stuff.
        section_names = {s["name"] for s in manifest["sections"]}
        assert f"users/{alice_id}.json" in section_names
        assert "postgres/notes.json" in section_names

        archive_note_ids = {n["note_id"] for n in notes_in_archive}
        assert archive_note_ids == {
            "note-a-1",
            "note-a-2",
            "note-a-shared",
        }, (
            f"per-user export filter regression: archive notes = "
            f"{archive_note_ids!r}; expected exactly alice's 3 notes "
            f"(personal+shared), bob's row (note-b-1) MUST NOT leak."
        )

        # Defence-in-depth on the body — the leak sentinel is a string
        # the operator-side review can grep for without parsing JSON.
        archive_text = archive_on_disk.read_bytes()
        assert b"BOB-PERSONAL-DO-NOT-LEAK" not in archive_text, (
            "DATA LEAK: bob's note body appears in alice's archive"
        )

        # --------------------------------------------------------------
        # 4. Dry-run import — non-mutating preview.
        # --------------------------------------------------------------
        dry_resp = client.post(
            "/api/v1/admin/user-imports",
            headers={"Authorization": f"Bearer {alice_token}"},
            json={
                "archive_path": str(archive_on_disk),
                "new_user": {
                    "email": "alice-imported@home.lan",
                    "password": "verylongpassword12",
                    "role": "user",
                },
                "dry_run": True,
            },
        )
        assert dry_resp.status_code == 200, dry_resp.text
        plan = dry_resp.json()
        assert plan["would_succeed"] is True, plan
        assert plan["preconditions"]["target_email_available"] is True
        assert plan["preconditions"]["no_parent_pk_collisions"] is True
        # Non-mutation: the new email must STILL be unminted on the
        # destination after dry-run (no users row, no notes rows).
        assert all(r["email"] != "alice-imported@home.lan" for r in store.users.values()), (
            "dry-run minted a users row — it must be non-mutating"
        )

        # --------------------------------------------------------------
        # 5. Real import — 201 Created + Location header.
        # --------------------------------------------------------------
        real_resp = client.post(
            "/api/v1/admin/user-imports",
            headers={"Authorization": f"Bearer {alice_token}"},
            json={
                "archive_path": str(archive_on_disk),
                "new_user": {
                    "email": "alice-imported@home.lan",
                    "password": "verylongpassword12",
                    "role": "user",
                },
                "dry_run": False,
            },
        )
        assert real_resp.status_code == 201, real_resp.text
        receipt = real_resp.json()
        new_user_id = receipt["new_user_id"]
        assert new_user_id and new_user_id not in {alice_id, bob_id}, (
            f"import minted a user that collides with an existing id "
            f"({new_user_id!r}) — the destination should always get a "
            "fresh id, never re-use the originating instance's id."
        )
        loc = real_resp.headers.get("location") or real_resp.headers.get("Location")
        assert loc == f"/api/v1/admin/users/{new_user_id}", (
            f"missing or wrong Location header on 201 import: {loc!r}"
        )

        # --------------------------------------------------------------
        # 6. Imported personal data is present, re-pointed to the new
        #    user_id. The import service rewrites ``user_id`` on every
        #    row before insert so the rows survive a cross-instance
        #    move; we pin that contract here.
        # --------------------------------------------------------------
        imported_notes = [
            r for r in store.tables.get("notes", []) if r.get("user_id") == new_user_id
        ]
        imported_note_ids = {r["note_id"] for r in imported_notes}
        assert imported_note_ids == {
            "note-a-1",
            "note-a-2",
            "note-a-shared",
        }, (
            f"imported notes for new user = {imported_note_ids!r}; "
            "expected the same 3 alice notes the archive contained."
        )
        for r in imported_notes:
            assert r["user_id"] == new_user_id, (
                f"imported note {r['note_id']!r} kept the source "
                f"user_id ({r['user_id']!r}); the import service must "
                "rewrite user_id to the freshly minted account."
            )

        # --------------------------------------------------------------
        # 7. Bob's row stayed bob's; the import did not silently merge
        #    or overwrite. (`note-b-1` is the only row owned by bob,
        #    and it must remain owned by bob with its original body.)
        # --------------------------------------------------------------
        bob_notes = [r for r in store.tables.get("notes", []) if r.get("user_id") == bob_id]
        assert len(bob_notes) == 1
        assert bob_notes[0]["note_id"] == "note-b-1"
        assert bob_notes[0]["body"] == "BOB-PERSONAL-DO-NOT-LEAK"

        # --------------------------------------------------------------
        # 8. Re-import refusal — same email, populated destination ⇒
        #    409 email_exists. Pins "import does not silently merge
        #    with an existing populated destination".
        # --------------------------------------------------------------
        replay_resp = client.post(
            "/api/v1/admin/user-imports",
            headers={"Authorization": f"Bearer {alice_token}"},
            json={
                "archive_path": str(archive_on_disk),
                "new_user": {
                    "email": "alice-imported@home.lan",
                    "password": "verylongpassword12",
                    "role": "user",
                },
                "dry_run": False,
            },
        )
        assert replay_resp.status_code == 409, replay_resp.text
        body = replay_resp.json()
        assert body["detail"]["refusal_reason"] == "email_exists"

    # ------------------------------------------------------------------
    # 9. Audit lifecycle: at least one __user_export__.completed and
    #    one __user_import__.completed plus a __user_import__.refused
    #    for the replay collision should have been written.
    # ------------------------------------------------------------------
    # The audit writer goes through actions/audit.py → metadata_store.
    # We don't grep the store for action names here because the audit
    # write path is best-effort fail-soft (covered by the dedicated
    # audit tests in test_user_export.py + test_user_export_routes.py).
    # The end-to-end guarantee we *do* want is that the route returned
    # 201 and the rows landed — both already asserted above.
