# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Speech-to-text in the Release file (LUM-995).

The Release ``docker-compose.yml`` carries the Speaches sidecar ``lumogis-stt`` under an
``stt`` Compose profile, and the Release ``example.env`` switches that profile on, so a
household that installs from the two files can dictate. These tests pin the shape and
that ``check()`` refuses a file that drifts from it. They need no Docker; the last test
renders the file with ``docker compose config`` when it is available.
"""

from __future__ import annotations

import copy
import importlib.util
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPT = _ROOT / "scripts" / "release" / "build_release_compose.py"
_EXAMPLE_ENV = _ROOT / "docker" / "release" / "example.env"

_spec = importlib.util.spec_from_file_location("build_release_compose_stt", _SCRIPT)
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


def _env_pairs() -> dict[str, str]:
    pairs: dict[str, str] = {}
    for line in _EXAMPLE_ENV.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Z][A-Z0-9_]*)=(\S*)", line)
        if m:
            pairs[m.group(1)] = m.group(2)
    return pairs


def test_stt_service_sits_under_the_stt_profile(doc):
    assert doc["services"]["lumogis-stt"]["profiles"] == ["stt"]


def test_stt_image_is_the_overlay_digest(doc):
    overlay = yaml.safe_load((_ROOT / "docker-compose.stt.yml").read_text(encoding="utf-8"))
    expected = overlay["services"]["lumogis-stt"]["image"]
    assert doc["services"]["lumogis-stt"]["image"] == expected
    assert "@sha256:" in expected


def test_stt_has_no_host_port_and_no_default_model_env(doc):
    svc = doc["services"]["lumogis-stt"]
    assert "ports" not in svc
    assert "DEFAULT_MODEL" not in (svc.get("environment") or {})
    assert svc["environment"]["HF_HUB_DISABLE_TELEMETRY"] == "1"


def test_stt_model_volume_declared_and_not_backed_up(doc):
    assert "lumogis_stt_models" in doc["volumes"]
    for vol in doc["services"]["backup"]["volumes"]:
        assert gen._source_and_target(vol)[0] != "lumogis_stt_models"


def test_core_does_not_depend_on_stt(doc):
    for name, svc in doc["services"].items():
        if svc.get("profiles"):
            continue
        assert "lumogis-stt" not in gen._depends_on_targets(svc), name


def test_check_accepts_the_generated_doc(doc):
    assert gen.check(doc) == []


def _backup_mounts_models(d):
    d["services"]["backup"]["volumes"].append("lumogis_stt_models:/x")


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (lambda d: d["services"].pop("lumogis-stt"), "missing"),
        (
            lambda d: d["services"]["lumogis-stt"].update(profiles=["graph"]),
            "must sit under profiles: [stt]",
        ),
        (lambda d: d["services"]["lumogis-stt"].pop("profiles"), "must sit under profiles"),
        (lambda d: d["services"]["lumogis-stt"].update(ports=["8000:8000"]), "host port"),
        (
            lambda d: d["services"]["lumogis-stt"].update(
                image="ghcr.io/speaches-ai/speaches:0.8.3-cpu"
            ),
            "digest",
        ),
        (
            lambda d: d["services"]["lumogis-stt"].update(
                image="docker.io/other/speaches@sha256:abc"
            ),
            "speaches",
        ),
        (_backup_mounts_models, "backed up"),
        (lambda d: d["services"]["lumogis-stt"].pop("environment"), "telemetry"),
    ],
)
def test_check_fails_when_stt_drifts(doc, mutate, expected):
    broken = copy.deepcopy(doc)
    mutate(broken)
    problems = gen.check(broken)
    assert any(expected in p for p in problems), problems


def test_missing_stt_overlay_is_a_violation(tmp_path):
    with pytest.raises(gen.Violation):
        gen.load_stt_overlay(tmp_path)


def test_overlay_without_the_service_is_a_violation():
    with pytest.raises(gen.Violation):
        gen.transform(
            yaml.safe_load((_ROOT / "docker-compose.yml").read_text(encoding="utf-8")),
            None,
            {"services": {}},
        )


def test_example_env_turns_stt_on():
    pairs = _env_pairs()
    assert pairs["COMPOSE_PROFILES"] == "stt"
    assert pairs["STT_BACKEND"] == "whisper_sidecar"
    assert pairs["STT_SIDECAR_URL"] == "http://lumogis-stt:8000"
    assert pairs["STT_MODEL"] == "Systran/faster-whisper-base.en"
    lines = [
        ln
        for ln in _EXAMPLE_ENV.read_text(encoding="utf-8").splitlines()
        if re.match(r"^#?\s?COMPOSE_PROFILES=", ln)
    ]
    assert len(lines) == 1, f"a second COMPOSE_PROFILES line overrides the first: {lines}"


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker not available")
def test_docker_compose_renders_stt_on_by_default_and_off(tmp_path):
    import os

    assert gen.main(["--check", "--out", str(tmp_path)]) == 0
    base = [
        "docker",
        "compose",
        "-f",
        str(tmp_path / "docker-compose.yml"),
        "--env-file",
        str(tmp_path / "example.env"),
    ]

    def services(profiles: str | None) -> set[str]:
        env = {k: v for k, v in os.environ.items() if k != "COMPOSE_PROFILES"}
        if profiles is not None:
            env["COMPOSE_PROFILES"] = profiles
        out = subprocess.run(
            [*base, "config", "--services"], capture_output=True, text=True, env=env, cwd=tmp_path
        )
        if out.returncode != 0:
            pytest.skip(f"docker compose unavailable here: {out.stderr[:120]}")
        return set(out.stdout.split())

    assert "lumogis-stt" in services(None)  # the release .env turns it on
    off = services("")  # an upgrader's old .env: no stt profile
    assert "lumogis-stt" not in off
    both = services("stt,graph")
    assert {"lumogis-stt", "falkordb", "lumogis-graph"} <= both
