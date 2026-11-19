# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""The knowledge graph in the Release file (LUM-989).

The Release ``docker-compose.yml`` carries FalkorDB and ``lumogis-graph`` under a
``graph`` Compose profile: they are in the file, not running, until Admin ->
Knowledge graph (or ``COMPOSE_PROFILES=graph``) switches them on. These tests pin
that shape, and that ``check()`` refuses a file that drifts from it. They need no
Docker; the last test renders the file with ``docker compose config`` when it is
available.
"""

from __future__ import annotations

import copy
import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPT = _ROOT / "scripts" / "release" / "build_release_compose.py"

_spec = importlib.util.spec_from_file_location("build_release_compose_graph", _SCRIPT)
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)


def _build_doc() -> dict:
    base_path = _ROOT / "docker-compose.yml"
    base = yaml.safe_load(base_path.read_text(encoding="utf-8"))
    return gen.transform(
        base,
        gen.load_graph_overlays(base_path.parent),
        gen.load_stt_overlay(base_path.parent),
    )


@pytest.fixture(scope="module")
def doc() -> dict:
    return _build_doc()


def test_both_graph_services_sit_under_the_graph_profile(doc):
    for name in ("falkordb", "lumogis-graph"):
        assert doc["services"][name]["profiles"] == ["graph"], name


def test_graph_image_is_published_and_not_built(doc):
    svc = doc["services"]["lumogis-graph"]
    assert svc["image"] == "ghcr.io/lumogis/lumogis-graph:${LUMOGIS_VERSION:-release}"
    assert "build" not in svc and "pull_policy" not in svc


def test_falkordb_is_the_engine_only_image_with_no_host_port(doc):
    svc = doc["services"]["falkordb"]
    assert svc["image"] == "falkordb/falkordb-server:v4.18.1"
    assert "ports" not in svc
    assert "falkordb_data:/var/lib/falkordb/data" in svc["volumes"]
    assert "falkordb_data" in doc["volumes"]


def test_backup_mounts_the_graph_data_but_does_not_wait_for_it(doc):
    backup = doc["services"]["backup"]
    assert "falkordb_data:/var/lib/falkordb/data" in backup["volumes"]
    deps = backup.get("depends_on") or {}
    assert "falkordb" not in deps


def test_core_does_not_depend_on_the_profiled_graph(doc):
    deps = doc["services"]["orchestrator"].get("depends_on") or {}
    assert "lumogis-graph" not in deps and "falkordb" not in deps


def test_other_optional_services_are_still_dropped(doc):
    assert "librechat" not in doc["services"]
    assert "mongodb" not in doc["services"]
    unprofiled_extra = [
        n
        for n, s in doc["services"].items()
        if s.get("profiles") and n not in ("falkordb", "lumogis-graph", "lumogis-stt")
    ]
    assert unprofiled_extra == []


def test_no_service_outside_the_graph_carries_a_profile(doc):
    for name, svc in doc["services"].items():
        if name not in (
            "falkordb",
            "lumogis-graph",
            "lumogis-stt",
        ):  # stt: test_release_compose_stt
            assert not svc.get("profiles"), name


def test_check_accepts_the_generated_doc(doc):
    assert gen.check(doc) == []


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (lambda d: d["services"].pop("lumogis-graph"), "missing"),
        (lambda d: d["services"].pop("falkordb"), "missing graph services"),
        (lambda d: d["services"]["caddy"].update(profiles=["debug"]), "profile-only"),
        (
            lambda d: d["services"]["lumogis-graph"].update(profiles=["graph", "debug"]),
            "profile-only",
        ),
        (lambda d: d["services"]["lumogis-graph"].pop("profiles"), "must sit under profiles"),
        (lambda d: d["services"]["falkordb"].update(ports=["6380:6379"]), "host port"),
        (
            lambda d: d["services"]["falkordb"].update(image="falkordb/falkordb:v4.18.1"),
            "falkordb image",
        ),
        (lambda d: d["services"]["lumogis-graph"].update(build="."), "build:"),
        (
            lambda d: d["services"]["lumogis-graph"]["depends_on"].update(
                nothere={"condition": "service_started"}
            ),
            "not in the file",
        ),
        (lambda d: d["volumes"].pop("falkordb_data"), "not declared"),
        (
            lambda d: (
                d["services"]["orchestrator"]
                .setdefault("depends_on", {})
                .update({"lumogis-graph": {"condition": "service_healthy"}})
            ),
            "profile-only",
        ),
    ],
)
def test_check_fails_when_the_file_drifts(doc, mutate, expected):
    broken = copy.deepcopy(doc)
    mutate(broken)
    problems = gen.check(broken)
    assert any(expected in p for p in problems), problems


def test_missing_overlay_is_a_violation_not_a_silent_omission(tmp_path):
    shutil.copyfile(_ROOT / "docker-compose.yml", tmp_path / "docker-compose.yml")
    with pytest.raises(gen.Violation):
        gen.load_graph_overlays(tmp_path)


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker not available")
def test_docker_compose_renders_with_and_without_the_profile(tmp_path):
    assert gen.main(["--check", "--out", str(tmp_path)]) == 0
    env_file = tmp_path / "example.env"
    base = [
        "docker",
        "compose",
        "-f",
        str(tmp_path / "docker-compose.yml"),
        "--env-file",
        str(env_file),
    ]

    def services(profile: str | None) -> set[str]:
        import os

        env = {k: v for k, v in os.environ.items() if k != "COMPOSE_PROFILES"}
        if profile:
            env["COMPOSE_PROFILES"] = profile
        out = subprocess.run(
            [*base, "config", "--services"], capture_output=True, text=True, env=env, cwd=tmp_path
        )
        if out.returncode != 0:
            pytest.skip(f"docker compose unavailable here: {out.stderr[:120]}")
        return set(out.stdout.split())

    off, on = services(None), services("graph")
    assert not {"falkordb", "lumogis-graph"} & off
    assert {"falkordb", "lumogis-graph"} <= on
