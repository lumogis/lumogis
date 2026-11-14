# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-922 — the admin's stop-entities list lives in the workspace.

It used to be ``/app/config/stop_entities.txt`` inside the image, so every
container recreate lost the admin's edits and the DR backup never saw them.
Now it is ``{WORKSPACE_PATH}/config/stop_entities.txt`` (``STOP_ENTITIES_PATH``
overrides), the shipped list is the read-only seed, and boot copies the seed
across once.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest

import config

_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    seed = tmp_path / "image" / "stop_entities.txt"
    seed.parent.mkdir()
    seed.write_text("# shipped\nthe meeting\nthe call\n", encoding="utf-8")
    monkeypatch.setenv("WORKSPACE_PATH", str(ws))
    monkeypatch.delenv("STOP_ENTITIES_PATH", raising=False)
    monkeypatch.setenv("STOP_ENTITIES_SEED_PATH", str(seed))
    monkeypatch.setattr(config, "_stop_entity_set", set())
    monkeypatch.setattr(config, "_stop_entity_mtime", None)
    dest = ws.resolve() / "config" / "stop_entities.txt"
    return type("Store", (), {"ws": ws, "seed": seed, "dest": dest})


# --- paths -------------------------------------------------------------------


def test_default_path_is_in_the_workspace(store):
    assert config.get_stop_entities_path() == str(store.dest)


def test_explicit_path_wins_and_blank_is_ignored(store, monkeypatch, tmp_path):
    monkeypatch.setenv("STOP_ENTITIES_PATH", str(tmp_path / "mine.txt"))
    assert config.get_stop_entities_path() == str(tmp_path / "mine.txt")
    monkeypatch.setenv("STOP_ENTITIES_PATH", "   ")
    assert config.get_stop_entities_path() == str(store.dest)


def test_seed_defaults_to_the_shipped_list(monkeypatch):
    monkeypatch.delenv("STOP_ENTITIES_SEED_PATH", raising=False)
    seed = config.get_stop_entities_seed_path()
    assert seed.endswith(os.path.join("config", "stop_entities.txt"))
    assert Path(seed).is_file()


def test_read_path_falls_back_to_the_seed_until_the_list_exists(store):
    assert config.get_stop_entities_read_path() == str(store.seed)
    store.dest.parent.mkdir()
    store.dest.write_text("x\n", encoding="utf-8")
    assert config.get_stop_entities_read_path() == str(store.dest)


def test_stop_set_uses_the_seed_then_the_persisted_list(store):
    assert config.get_stop_entity_set() == {"the meeting", "the call"}
    store.dest.parent.mkdir()
    store.dest.write_text("project phoenix\n", encoding="utf-8")
    # Same mtime is possible across two files: the cache is keyed by path too.
    st = store.seed.stat()
    os.utime(store.dest, (st.st_atime, st.st_mtime))
    assert config.get_stop_entity_set() == {"project phoenix"}


# --- one-time copy -----------------------------------------------------------


def test_copy_creates_the_list_once(store):
    assert config.ensure_stop_entities_file() is True
    assert store.dest.read_text(encoding="utf-8") == store.seed.read_text(encoding="utf-8")
    assert config.ensure_stop_entities_file() is False
    leftovers = [p.name for p in store.dest.parent.iterdir() if p.name != "stop_entities.txt"]
    assert leftovers == []


def test_copy_never_overwrites_an_existing_list(store):
    store.dest.parent.mkdir()
    store.dest.write_text("my curated phrase\n", encoding="utf-8")
    store.seed.write_text("a newer shipped phrase\n", encoding="utf-8")
    assert config.ensure_stop_entities_file() is False
    assert store.dest.read_text(encoding="utf-8") == "my curated phrase\n"


def test_copy_keeps_an_emptied_list_empty(store):
    # An admin who removed every phrase must not get the shipped list back.
    store.dest.parent.mkdir()
    store.dest.write_text("# header only\n", encoding="utf-8")
    assert config.ensure_stop_entities_file() is False
    assert config.get_stop_entity_set() == set()


def test_copy_loses_the_race_to_a_concurrent_writer(store, monkeypatch):
    real_link = os.link

    def link_after_someone_else(src, dst):
        Path(dst).write_text("written meanwhile\n", encoding="utf-8")
        return real_link(src, dst)

    monkeypatch.setattr(os, "link", link_after_someone_else)
    assert config.ensure_stop_entities_file() is False
    assert store.dest.read_text(encoding="utf-8") == "written meanwhile\n"
    assert sorted(p.name for p in store.dest.parent.iterdir()) == ["stop_entities.txt"]


def test_copy_falls_back_to_rename_without_hard_links(store, monkeypatch):
    def no_links(src, dst):
        raise PermissionError("hard links not supported")

    monkeypatch.setattr(os, "link", no_links)
    assert config.ensure_stop_entities_file() is True
    assert store.dest.read_text(encoding="utf-8") == store.seed.read_text(encoding="utf-8")
    assert sorted(p.name for p in store.dest.parent.iterdir()) == ["stop_entities.txt"]


def test_copy_without_a_seed_does_nothing(store):
    store.seed.unlink()
    assert config.ensure_stop_entities_file() is False
    assert not store.dest.exists()


def test_copy_skips_when_seed_and_list_are_the_same_file(store, monkeypatch):
    monkeypatch.setenv("STOP_ENTITIES_PATH", str(store.seed))
    assert config.ensure_stop_entities_file() is False


def test_copy_into_an_unwritable_workspace_warns_and_keeps_the_seed(store, caplog):
    store.ws.chmod(0o500)
    try:
        if os.access(store.ws, os.W_OK):
            pytest.skip("running as root: the workspace stays writable")
        with caplog.at_level(logging.WARNING, logger=config._log.name):
            assert config.ensure_stop_entities_file() is False
        assert "Could not create the stop entity list" in caplog.text
        assert config.get_stop_entity_set() == {"the meeting", "the call"}
    finally:
        store.ws.chmod(0o700)


def test_lifespan_runs_the_copy():
    text = (_ROOT / "orchestrator" / "main.py").read_text(encoding="utf-8")
    assert "config.ensure_stop_entities_file()" in text


# --- the admin routes read and write the new location --------------------------


def test_admin_routes_read_the_seed_and_write_the_workspace(store):
    from routes.admin import StopEntityRequest
    from routes.admin import kg_stop_entities_get
    from routes.admin import kg_stop_entities_post

    seed_before = store.seed.read_text(encoding="utf-8")
    got = kg_stop_entities_get()
    assert got["source_path"] == str(store.seed)
    assert got["phrases"] == ["the call", "the meeting"]

    out = kg_stop_entities_post(StopEntityRequest(action="add", phrase="Project Phoenix"))
    assert out == {"status": "ok", "count": 3}
    assert store.seed.read_text(encoding="utf-8") == seed_before  # the shipped list is untouched
    written = store.dest.read_text(encoding="utf-8")
    assert "Project Phoenix" in written and "the meeting" in written
    # readable like the shipped list: the DR snapshot keeps the mode
    assert store.dest.stat().st_mode & 0o777 == 0o644

    got = kg_stop_entities_get()
    assert got["source_path"] == str(store.dest)
    assert got["phrases"] == ["Project Phoenix", "the call", "the meeting"]
    assert "project phoenix" in config.get_stop_entity_set()

    kg_stop_entities_post(StopEntityRequest(action="remove", phrase="the call"))
    assert kg_stop_entities_get()["phrases"] == ["Project Phoenix", "the meeting"]
    assert store.seed.read_text(encoding="utf-8") == seed_before
