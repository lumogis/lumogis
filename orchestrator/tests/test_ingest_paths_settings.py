# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-397 C1 — ingest_paths settings API and config helpers."""

from __future__ import annotations

import json
import pathlib

import pytest


class _PathWithoutHostMount(pathlib.Path):
    """Path helper for tests where /host is not mounted in the container."""

    def exists(self, *args, **kwargs):
        if str(self) == "/host":
            return False
        return super().exists(*args, **kwargs)


from tests.test_admin_settings_legacy_keys_disabled import _client  # noqa: E402
from tests.test_admin_settings_legacy_keys_disabled import _mint_admin_jwt  # noqa: E402
from tests.test_admin_settings_legacy_keys_disabled import _seed_admin  # noqa: E402

pytest_plugins = ("tests.test_admin_settings_legacy_keys_disabled",)


@pytest.fixture
def ingest_env(monkeypatch, tmp_path):
    monkeypatch.setenv("FILESYSTEM_ROOT", "/data")
    monkeypatch.setenv("FILESYSTEM_ROOT_HOST", "/host/lumogis-data")
    monkeypatch.setenv("INGEST_PATHS_ALLOWED_PREFIX", "/data,/host")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(
        "config.host_ingest_path_to_container_index0",
        lambda _host: "/data",
    )
    yield data_dir


def test_migrate_filesystem_root_to_ingest_paths(store):
    store.app_settings["filesystem_root"] = "/old/root"
    import config as _config

    _config.migrate_filesystem_root_to_ingest_paths(store)
    assert store.app_settings.get("ingest_paths") == json.dumps(["/old/root"])
    assert store.app_settings.get("filesystem_root") == ""


def test_get_settings_ingest_paths_shape(store, auth_on_env, ingest_env):
    admin = _seed_admin(store)
    hdr = {"Authorization": f"Bearer {_mint_admin_jwt(admin)}"}
    with _client() as client:
        resp = client.get("/settings", headers=hdr)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "filesystem_root" not in body
    assert "pending_filesystem_root" not in body
    assert "ingest_paths" in body
    assert isinstance(body["ingest_paths"], list)
    assert "pending_ingest_paths" in body
    assert "restart_required" in body
    assert isinstance(body["restart_required"], bool)
    assert "paperless_configured" in body
    assert body["paperless_configured"] is False


def test_paperless_configured_true_when_row_exists(store, auth_on_env, ingest_env, monkeypatch):
    admin = _seed_admin(store)
    monkeypatch.setattr(
        "routes.admin.connector_credentials.get_payload",
        lambda user_id, connector: (
            {"url": "http://paperless"} if connector == "paperless" else None
        ),
    )
    hdr = {"Authorization": f"Bearer {_mint_admin_jwt(admin)}"}
    with _client() as client:
        resp = client.get("/settings", headers=hdr)
    assert resp.status_code == 200
    assert resp.json()["paperless_configured"] is True


def test_put_ingest_paths_rejects_spaces(store, auth_on_env, ingest_env):
    admin = _seed_admin(store)
    hdr = {"Authorization": f"Bearer {_mint_admin_jwt(admin)}"}
    with _client() as client:
        resp = client.put(
            "/settings",
            headers=hdr,
            json={"ingest_paths": ["/path with spaces"]},
        )
    assert resp.status_code == 400
    assert "spaces" in resp.json()["detail"].lower()


def test_put_first_path_writes_filesystem_root_env(
    store, auth_on_env, ingest_env, monkeypatch, tmp_path
):
    env_file = tmp_path / ".env"
    env_file.write_text("FILESYSTEM_ROOT=/data\n")
    monkeypatch.setattr("routes.admin._PROJECT_ENV_FILE", env_file)

    admin = _seed_admin(store)
    hdr = {"Authorization": f"Bearer {_mint_admin_jwt(admin)}"}
    with _client() as client:
        resp = client.put(
            "/settings",
            headers=hdr,
            json={"ingest_paths": ["/host/new-data"]},
        )
    assert resp.status_code == 200, resp.text
    content = env_file.read_text()
    assert "FILESYSTEM_ROOT=/host/new-data" in content
    assert "INGEST_PATHS_HOST=" in content
    assert "INGEST_PATHS=" in content
    assert store.app_settings.get("ingest_paths") == json.dumps(["/host/new-data"])


def test_restart_required_when_pending_differs_from_effective(
    store, auth_on_env, ingest_env, monkeypatch
):
    monkeypatch.setenv("INGEST_PATHS_HOST", json.dumps(["/host/current"]))
    store.app_settings["ingest_paths"] = json.dumps(["/host/pending"])
    admin = _seed_admin(store)
    hdr = {"Authorization": f"Bearer {_mint_admin_jwt(admin)}"}
    with _client() as client:
        resp = client.get("/settings", headers=hdr)
    assert resp.status_code == 200
    body = resp.json()
    assert body["restart_required"] is True
    assert body["pending_ingest_paths"] == ["/host/pending"]
    assert body["ingest_paths"] == ["/host/current"]


@pytest.fixture
def multi_ingest_dirs(tmp_path):
    primary = tmp_path / "ingest-primary"
    extra = tmp_path / "ingest-extra"
    primary.mkdir(parents=True, exist_ok=True)
    extra.mkdir(parents=True, exist_ok=True)
    return str(primary), str(extra)


@pytest.fixture
def project_writable(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("COMPOSE_FILE=docker-compose.yml\n")
    override = tmp_path / "docker-compose.override.yml"
    monkeypatch.setattr("routes.admin._PROJECT_ENV_FILE", env_file)
    monkeypatch.setattr("routes.admin.project_env_writable", lambda _f=None: True)
    monkeypatch.setattr("routes.admin.override_path", lambda project_dir=None: override)
    monkeypatch.setattr("compose_ingest_binds.override_path", lambda project_dir=None: override)
    return env_file, override


def test_put_two_host_paths_writes_ingest_paths_env_with_data_n(
    store, auth_on_env, ingest_env, monkeypatch, multi_ingest_dirs, project_writable
):
    primary, extra = multi_ingest_dirs
    env_file, _override = project_writable
    monkeypatch.setenv("INGEST_PATHS_ALLOWED_PREFIX", f"/data,{primary},{extra}")

    admin = _seed_admin(store)
    hdr = {"Authorization": f"Bearer {_mint_admin_jwt(admin)}"}
    with _client() as client:
        resp = client.put(
            "/settings",
            headers=hdr,
            json={"ingest_paths": [primary, extra]},
        )
    assert resp.status_code == 200, resp.text
    content = env_file.read_text()
    assert '"/data-1"' in content or "/data-1" in content
    body = resp.json()
    assert body["ingest_compose_snippet"]


def test_put_two_paths_returns_snippet_when_env_readonly(
    store, auth_on_env, ingest_env, monkeypatch, multi_ingest_dirs
):
    primary, extra = multi_ingest_dirs
    monkeypatch.setenv("INGEST_PATHS_ALLOWED_PREFIX", f"/data,{primary},{extra}")
    monkeypatch.setattr("routes.admin.project_env_writable", lambda _f=None: False)

    admin = _seed_admin(store)
    hdr = {"Authorization": f"Bearer {_mint_admin_jwt(admin)}"}
    with _client() as client:
        resp = client.put(
            "/settings",
            headers=hdr,
            json={"ingest_paths": [primary, extra]},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ingest_compose_snippet"]
    assert body["ingest_compose_override_written"] is False


def test_auto_write_chains_compose_file(
    store, auth_on_env, ingest_env, monkeypatch, multi_ingest_dirs, project_writable
):
    primary, extra = multi_ingest_dirs
    env_file, override = project_writable
    monkeypatch.setenv("INGEST_PATHS_ALLOWED_PREFIX", f"/data,{primary},{extra}")

    admin = _seed_admin(store)
    hdr = {"Authorization": f"Bearer {_mint_admin_jwt(admin)}"}
    with _client() as client:
        resp = client.put(
            "/settings",
            headers=hdr,
            json={"ingest_paths": [primary, extra]},
        )
    assert resp.status_code == 200, resp.text
    assert "docker-compose.override.yml" in env_file.read_text()
    assert override.is_file()
    assert resp.json()["ingest_compose_override_written"] is True
    assert resp.json()["ingest_compose_validation_ok"] is True


def test_validate_index1_requires_existing_dir(store, auth_on_env, ingest_env, multi_ingest_dirs):
    primary, _extra = multi_ingest_dirs
    admin = _seed_admin(store)
    hdr = {"Authorization": f"Bearer {_mint_admin_jwt(admin)}"}
    with _client() as client:
        resp = client.put(
            "/settings",
            headers=hdr,
            json={"ingest_paths": [primary, "/no/such/path"]},
        )
    assert resp.status_code == 400


def test_validate_index1_host_prefix_without_mount_message(
    store, auth_on_env, ingest_env, monkeypatch
):
    data_dir = ingest_env
    monkeypatch.setattr("config.Path", _PathWithoutHostMount)
    monkeypatch.setattr(
        "config.host_ingest_allowed_prefixes",
        lambda: ["/data"],
    )
    admin = _seed_admin(store)
    hdr = {"Authorization": f"Bearer {_mint_admin_jwt(admin)}"}
    with _client() as client:
        resp = client.put(
            "/settings",
            headers=hdr,
            json={"ingest_paths": [str(data_dir), "/host/foo"]},
        )
    assert resp.status_code == 400
    detail = resp.json()["detail"].lower()
    assert "override" in detail or "browse" in detail or "mount" in detail


def test_effective_ingest_paths_includes_data_1_after_put(
    store, auth_on_env, ingest_env, monkeypatch, multi_ingest_dirs, project_writable
):
    primary, extra = multi_ingest_dirs
    env_file, _ = project_writable
    monkeypatch.setenv("INGEST_PATHS_ALLOWED_PREFIX", f"/data,{primary},{extra}")

    admin = _seed_admin(store)
    hdr = {"Authorization": f"Bearer {_mint_admin_jwt(admin)}"}
    with _client() as client:
        resp = client.put(
            "/settings",
            headers=hdr,
            json={"ingest_paths": [primary, extra]},
        )
    assert resp.status_code == 200
    content = env_file.read_text()
    assert "/data-1" in content


def test_structural_validation_failure_surfaces_validation_ok_false(
    store, auth_on_env, ingest_env, monkeypatch, multi_ingest_dirs, project_writable
):
    primary, extra = multi_ingest_dirs
    monkeypatch.setenv("INGEST_PATHS_ALLOWED_PREFIX", f"/data,{primary},{extra}")
    monkeypatch.setattr(
        "routes.admin.validate_override_structure",
        lambda merged, expected_bind_count: (False, "bad structure"),
    )
    admin = _seed_admin(store)
    hdr = {"Authorization": f"Bearer {_mint_admin_jwt(admin)}"}
    with _client() as client:
        resp = client.put(
            "/settings",
            headers=hdr,
            json={"ingest_paths": [primary, extra]},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ingest_compose_snippet"]
    assert body["ingest_compose_override_written"] is False
    assert body["ingest_compose_validation_ok"] is False


def test_partial_write_healed_on_second_put(
    store, auth_on_env, ingest_env, monkeypatch, multi_ingest_dirs, project_writable
):
    primary, extra = multi_ingest_dirs
    env_file, override = project_writable
    monkeypatch.setenv("INGEST_PATHS_ALLOWED_PREFIX", f"/data,{primary},{extra}")
    chain_calls = {"n": 0}
    real_chain = __import__(
        "compose_ingest_binds", fromlist=["chain_compose_file_in_env"]
    ).chain_compose_file_in_env

    def chain_flaky(content: str):
        chain_calls["n"] += 1
        if chain_calls["n"] == 1:
            raise OSError("simulated chain failure")
        return real_chain(content)

    monkeypatch.setattr("routes.admin.chain_compose_file_in_env", chain_flaky)

    admin = _seed_admin(store)
    hdr = {"Authorization": f"Bearer {_mint_admin_jwt(admin)}"}
    with _client() as client:
        first = client.put(
            "/settings",
            headers=hdr,
            json={"ingest_paths": [primary, extra]},
        )
        assert first.status_code == 200, first.text
        assert override.is_file()
        assert "docker-compose.override.yml" not in env_file.read_text()

        second = client.put(
            "/settings",
            headers=hdr,
            json={"ingest_paths": [primary, extra]},
        )
    assert second.status_code == 200, second.text
    assert "docker-compose.override.yml" in env_file.read_text()
    assert second.json()["ingest_compose_override_written"] is True
