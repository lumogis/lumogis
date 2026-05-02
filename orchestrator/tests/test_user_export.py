# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for ``services.user_export``.

Scope: pure helpers (zip-slip, manifest parse/validate, redaction,
pruning policy) plus a smoke test of the full export round-trip
through the conftest mock stack. Service-level integration with a real
Postgres lives in ``tests/integration/test_user_export_round_trip.py``.
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from pathlib import Path

import pytest
from models.user_export import ArchiveMeta
from models.user_export import ImportRefused

from services import user_export


def test_redact_credentials_blanks_known_suffixes():
    raw = {
        "id": "u1",
        "email": "alice@example.com",
        "password_hash": "argon2$abc",
        "refresh_token_jti": "jti-123",
        "name": "Alice",
        "api_secret": "topsecret",
    }
    out = user_export._redact_credentials(raw)
    assert out["password_hash"] is None
    assert out["refresh_token_jti"] is None
    assert out["api_secret"] is None
    assert out["email"] == "alice@example.com"
    assert out["name"] == "Alice"
    assert out["id"] == "u1"
    # Original is untouched.
    assert raw["password_hash"] == "argon2$abc"


@pytest.mark.parametrize(
    "name",
    [
        "../etc/passwd",
        "/absolute/path",
        "C:/windows/system32",
        "foo/../bar",
        "with\x00nul",
        "",
    ],
)
def test_zip_entry_names_rejects_dangerous(name):
    bad = user_export._validate_zip_entry_names([name])
    assert bad == [name]


def test_zip_entry_names_accepts_safe_relative():
    ok = [
        "manifest.json",
        "postgres/file_index.json",
        "qdrant/documents.json",
        "users/abc.json",
        "captures/media/u1/c1/a1/blob.png",
    ]
    assert user_export._validate_zip_entry_names(ok) == []


def test_plan_capture_media_export_includes_existing_file(tmp_path):
    uid = "u1"
    sk = f"{uid}/c1/a1/blob.png"
    blob_path = tmp_path / sk
    blob_path.parent.mkdir(parents=True, exist_ok=True)
    blob_path.write_bytes(b"x" * 100)
    rows = [
        {"id": "a1", "capture_id": "c1", "user_id": uid, "storage_key": sk},
    ]
    inc, omit = user_export._plan_capture_media_export(
        uid,
        rows,
        media_root=tmp_path,
    )
    assert len(inc) == 1
    assert not omit
    assert inc[0]["zip_entry"] == f"captures/media/{sk}"
    assert inc[0]["path"] == blob_path


def test_plan_capture_media_export_skips_user_mismatch(tmp_path):
    rows = [
        {
            "id": "a1",
            "capture_id": "c1",
            "user_id": "other",
            "storage_key": "u1/c1/a1/blob.png",
        },
    ]
    inc, omit = user_export._plan_capture_media_export(
        "u1",
        rows,
        media_root=tmp_path,
    )
    assert inc == []
    assert any(o.get("reason") == "user_mismatch" for o in omit)


def test_restore_capture_media_rewrites_user_prefix(tmp_path, monkeypatch):
    import services.media_storage as ms

    monkeypatch.setattr(ms, "_CAPTURE_MEDIA_ROOT", tmp_path)
    old_uid, new_uid = "user-old", "user-new"
    sk = f"{old_uid}/cap1/att1/blob.png"
    payload = b"hello-media"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"captures/media/{sk}", payload)
    buf.seek(0)
    warns: list[str] = []
    with zipfile.ZipFile(buf) as zf:
        n = user_export._restore_capture_media_from_zip(
            zf,
            exporting_user_id=old_uid,
            new_user_id=new_uid,
            receipt_warnings=warns,
        )
    assert n == 1
    assert warns == []
    expected = ms.resolve_storage_key_file(
        f"{new_uid}/cap1/att1/blob.png",
        root=tmp_path,
    )
    assert expected.read_bytes() == payload


def test_decide_pruning_keeps_minimum_floor():
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    archives = [
        ArchiveMeta(path=Path(f"a{i}.zip"), mtime=base - timedelta(days=i * 100)) for i in range(5)
    ]
    decision = user_export._decide_pruning(
        archives,
        keep_min=3,
        max_age_days=30,
        now=base,
    )
    # Newest 3 are always kept regardless of age.
    assert len(decision.keep) == 3
    assert all(a in decision.keep for a in [Path("a0.zip"), Path("a1.zip"), Path("a2.zip")])
    # Older two are pruned (over max_age).
    assert len(decision.prune) == 2
    assert decision.reason_per_path[Path("a0.zip")] == "within_keep_min"
    assert decision.reason_per_path[Path("a3.zip")] == "over_max_age"


def test_decide_pruning_keeps_recent_above_floor():
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    # Six archives, all within 5 days — none should be pruned despite
    # exceeding the keep_min floor.
    archives = [
        ArchiveMeta(path=Path(f"a{i}.zip"), mtime=base - timedelta(days=i)) for i in range(6)
    ]
    decision = user_export._decide_pruning(
        archives,
        keep_min=3,
        max_age_days=30,
        now=base,
    )
    assert len(decision.prune) == 0
    assert len(decision.keep) == 6


def test_parse_manifest_rejects_missing_file():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("not_a_manifest.json", "{}")
    buf.seek(0)
    with zipfile.ZipFile(buf) as zf, pytest.raises(ImportRefused) as excinfo:
        user_export._parse_manifest(zf)
    assert excinfo.value.refusal_reason == "manifest_invalid"
    assert excinfo.value.payload == {"missing": "manifest.json"}


def test_parse_manifest_rejects_unparseable_json():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", "{not json")
    buf.seek(0)
    with zipfile.ZipFile(buf) as zf, pytest.raises(ImportRefused) as excinfo:
        user_export._parse_manifest(zf)
    assert excinfo.value.refusal_reason == "manifest_invalid"


def test_parse_manifest_happy_path():
    buf = io.BytesIO()
    body = {
        "format_version": 1,
        "exported_at": "2026-04-18T10:00:00+00:00",
        "exporting_user_id": "u1",
        "exported_user_email": "alice@example.com",
        "exported_user_role": "user",
        "scope_filter": "authored_by_me",
        "falkordb_edge_policy": "personal_intra_user_authored",
        "sections": [{"name": "postgres/notes.json", "kind": "postgres", "row_count": 0}],
    }
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps(body))
    buf.seek(0)
    with zipfile.ZipFile(buf) as zf:
        manifest = user_export._parse_manifest(zf)
    assert manifest.format_version == 1
    assert manifest.exporting_user_id == "u1"
    assert manifest.scope_filter == "authored_by_me"
    assert manifest.sections == body["sections"]
    assert user_export._validate_manifest(manifest) == []


def test_validate_manifest_flags_unsupported_version():
    from models.user_export import Manifest

    bad = Manifest(
        format_version=999,
        exported_at=datetime.now(timezone.utc),
        exporting_user_id="u1",
        exported_user_email="x@y",
        exported_user_role="user",
        scope_filter="authored_by_me",
        falkordb_edge_policy="personal_intra_user_authored",
        sections=[],
    )
    errs = user_export._validate_manifest(bad)
    assert any("999" in e for e in errs)


def test_resolve_archive_path_rejects_outside_root(tmp_path, monkeypatch):
    monkeypatch.setattr(user_export, "_USER_EXPORT_DIR", tmp_path)
    outside = tmp_path.parent / "outside.zip"
    with pytest.raises(ImportRefused) as excinfo:
        user_export._resolve_archive_path(str(outside))
    assert excinfo.value.refusal_reason == "forbidden_path"


def test_resolve_archive_path_accepts_under_root(tmp_path, monkeypatch):
    monkeypatch.setattr(user_export, "_USER_EXPORT_DIR", tmp_path)
    target = tmp_path / "alice" / "export.zip"
    target.parent.mkdir()
    target.write_bytes(b"x")
    resolved = user_export._resolve_archive_path(str(target))
    assert resolved == target.resolve()


def test_export_user_returns_zip_with_manifest(monkeypatch, tmp_path):
    """End-to-end smoke through the conftest mock stack (no Postgres).

    The mock metadata store returns ``[]`` for every fetch, so all
    Postgres sections come out empty; the test confirms the archive
    structure (manifest + every declared section + user record) is
    well-formed even on the empty-user path.
    """
    monkeypatch.setattr(user_export, "_USER_EXPORT_DIR", tmp_path)
    archive_bytes, filename = user_export.export_user("u1")
    assert filename.startswith("export_") and filename.endswith(".zip")
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as zf:
        names = zf.namelist()
        assert "manifest.json" in names
        assert "users/u1.json" in names
        manifest = json.loads(zf.read("manifest.json"))
        assert manifest["format_version"] == 1
        assert manifest["exporting_user_id"] == "u1"
        assert manifest["scope_filter"] == "authored_by_me"
        # Every declared section is present in the archive.
        for s in manifest["sections"]:
            assert s["name"] in names, f"missing declared section: {s['name']}"
        # ``omissions`` records every per-user table the export
        # deliberately drops (per ADR ``per_user_connector_credentials``
        # for ``user_connector_credentials``). The structure is
        # ``[{"table": str, "reason": str}, ...]`` so downstream tooling
        # can parse without splitting free-text strings.
        omissions = manifest["omissions"]
        assert isinstance(omissions, list) and omissions, (
            "manifest.omissions must be a non-empty list"
        )
        for entry in omissions:
            assert set(entry.keys()) == {"table", "reason"}
            assert entry["table"] and entry["reason"]
        omitted_tables = {e["table"] for e in omissions}
        assert "user_connector_credentials" in omitted_tables, (
            "user_connector_credentials must surface in manifest.omissions; "
            "it is sealed under the household Fernet key and not exported."
        )
    # Persisted to disk under USER_EXPORT_DIR/<user_id>/.
    on_disk = list((tmp_path / "u1").glob("export_*.zip"))
    assert len(on_disk) == 1
    assert on_disk[0].read_bytes() == archive_bytes
