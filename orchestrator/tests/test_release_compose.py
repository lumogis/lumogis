# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""The two files a household installs Lumogis from (LUM-980).

``scripts/release/build_release_compose.py`` turns the repo's docker-compose.yml
(which builds) into the release docker-compose.yml (which only pulls), and ships
``docker/release/example.env`` beside it. These tests pin that contract: nothing
built, nothing read from a checkout, stack-control told where the install folder
is, and an example.env that agrees with .env.example and the entrypoint.
"""

from __future__ import annotations

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
_ENV_EXAMPLE = _ROOT / ".env.example"
_ENTRYPOINT = _ROOT / "orchestrator" / "docker-entrypoint.sh"

_spec = importlib.util.spec_from_file_location("build_release_compose", _SCRIPT)
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)

_IMAGES = {
    "orchestrator": "lumogis-orchestrator",
    "lumogis-web": "lumogis-web",
    "stack-control": "lumogis-stack-control",
    "backup": "lumogis-backup",
    "caddy": "lumogis-caddy",
}
# LUM-989: the Release file also carries these two, under the ``graph`` profile
# (test_release_compose_graph.py). A checkout still builds them from the overlays.
_GRAPH_SERVICES = {"falkordb", "lumogis-graph"}
# LUM-995: and this one, under the ``stt`` profile (test_release_compose_stt.py).
_STT_SERVICES = {"lumogis-stt"}
# Release example.env values that differ from the first entry of the same key in
# .env.example (which is empty or commented). Each key must still appear there.
_RELEASE_ONLY_VALUES = {
    "LUMOGIS_VERSION": "release",
    "COMPOSE_PROFILES": "stt",
    "STT_BACKEND": "whisper_sidecar",
}
_BAKED = {"./postgres/init.sql", "./docker/caddy/Caddyfile", "./docker/caddy/preflight.sh"}


@pytest.fixture(autouse=True)
def _overlays_beside_compose(tmp_path):
    """The builder reads the graph overlays (LUM-989) and the speech-to-text overlay
    (LUM-995) next to ``--compose``."""
    for name in (*gen.GRAPH_OVERLAYS, gen.STT_OVERLAY):
        shutil.copyfile(_ROOT / name, tmp_path / name)


@pytest.fixture(scope="module")
def generated(tmp_path_factory) -> tuple[Path, dict, str]:
    out = tmp_path_factory.mktemp("release")
    assert gen.main(["--version", "9.9.9", "--check", "--out", str(out)]) == 0
    text = (out / "docker-compose.yml").read_text(encoding="utf-8")
    return out, yaml.safe_load(text), text


def _bind_sources(svc: dict) -> list[str]:
    return [gen._source_and_target(v)[0] for v in svc.get("volumes") or []]


def _repo_services() -> dict:
    return yaml.safe_load((_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))["services"]


def test_release_compose_has_no_build_or_repo_mounts(generated):
    _, doc, _ = generated
    for name, svc in doc["services"].items():
        assert "build" not in svc and "pull_policy" not in svc, name
        if name in _IMAGES:
            assert svc["image"] == f"ghcr.io/lumogis/{_IMAGES[name]}:${{LUMOGIS_VERSION:-release}}"
        for source in _bind_sources(svc):
            allowed = (
                source in (".", "/var/run/docker.sock", "${PWD}")
                or re.fullmatch(
                    r"\$\{(WORKSPACE_HOST_DIR|FILESYSTEM_ROOT|BACKUP_HOST_DIR)(:-[^}]*)?\}", source
                )
                or "/" not in source  # named volume
            )
            assert allowed, f"{name}: unexpected bind source {source!r}"


def test_release_compose_drops_only_baked_mounts(generated):
    _, doc, _ = generated
    for name, repo_svc in _repo_services().items():
        if repo_svc.get("profiles"):
            continue
        repo_sources = _bind_sources(repo_svc)
        expected = [s for s in repo_sources if s not in _BAKED]
        if name == "stack-control":
            expected = expected + ["${PWD}"]
        if name == "backup":
            expected = expected + ["falkordb_data"]  # LUM-989: the graph's data volume
        assert _bind_sources(doc["services"][name]) == expected, name


def test_release_compose_services_match_repo(generated):
    _, doc, _ = generated
    repo = _repo_services()
    kept = {n for n, s in repo.items() if not s.get("profiles")}
    assert set(doc["services"]) == kept | _GRAPH_SERVICES | _STT_SERVICES
    for name, svc in doc["services"].items():
        deps = svc.get("depends_on") or {}
        assert set(deps) <= kept | _GRAPH_SERVICES | _STT_SERVICES, (
            f"{name} depends on a dropped service"
        )


def test_release_compose_stack_control_gets_host_dir(generated):
    _, doc, _ = generated
    sc = doc["services"]["stack-control"]
    assert sc["environment"]["HOST_PROJECT_DIR"] == "${PWD}"
    assert sc["environment"]["COMPOSE_PROJECT_NAME"] == "${COMPOSE_PROJECT_NAME:-lumogis}"
    assert "${PWD}:${PWD}:ro" in sc["volumes"]


def test_release_compose_keeps_the_secure_address_volume(generated):
    """LUM-331: a two-file install gets the caddy-home volume and Core's settings."""
    _, doc, _ = generated
    assert "caddy-home" in doc["volumes"]
    orch = doc["services"]["orchestrator"]
    assert "caddy-home:/caddy-home" in orch["volumes"]
    for key in ("CADDY_HOME_DIR", "LUMOGIS_HOME_DNS_URL", "LUMOGIS_SECURE_ADDRESS_PORT"):
        assert key in orch["environment"]
    assert {
        "type": "volume",
        "source": "caddy-home",
        "target": "/etc/caddy/home.d",
        "read_only": True,
        "volume": {"nocopy": True},
    } in doc["services"]["caddy"]["volumes"]


def test_release_compose_is_deterministic_and_fingerprinted(generated, tmp_path, capsys):
    out, _, text = generated
    assert gen.main(["--version", "9.9.9", "--out", str(tmp_path)]) == 0
    assert (tmp_path / "docker-compose.yml").read_text(encoding="utf-8") == text
    assert "# lumogis-release: 9.9.9" in text
    capsys.readouterr()
    assert gen.main(["--fingerprint-only"]) == 0
    printed = capsys.readouterr().out.strip()
    assert f"# lumogis-compose: {printed}" in text
    assert (out / "example.env").read_bytes() == _EXAMPLE_ENV.read_bytes()


def test_fingerprint_ignores_version_but_tracks_services():
    doc = gen.transform(yaml.safe_load((_ROOT / "docker-compose.yml").read_text(encoding="utf-8")))
    body = gen.render_body(doc)
    assert gen.render(doc, "1.0.0").split("# lumogis-compose: ")[1][:16] == gen.fingerprint(body)
    assert gen.render(doc, "2.0.0").endswith(body)
    doc["services"]["caddy"]["restart"] = "always"
    assert gen.fingerprint(gen.render_body(doc)) != gen.fingerprint(body)


@pytest.mark.parametrize(
    "source, reason",
    [
        ("./config/x.yaml", "points into the source checkout"),
        ("../elsewhere", "points into the source checkout"),
        ("/home/me/lumogis/docs", "absolute host path"),
        ("${CONFIG_DIR:-./config}", "only the data-directory variables"),
    ],
)
def test_release_compose_rejects_repo_paths(tmp_path, capsys, source, reason):
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(
        yaml.safe_dump(
            {"services": {"orchestrator": {"build": ".", "volumes": [f"{source}:/x:ro"]}}}
        ),
        encoding="utf-8",
    )
    assert gen.main(["--compose", str(compose), "--out", str(tmp_path / "out")]) == 1
    assert reason in capsys.readouterr().err


@pytest.mark.parametrize(
    "doc, reason",
    [
        ({"services": {"a": {"image": "x", "env_file": "./config/a.env"}}}, "only .env"),
        ({"services": {"a": {"image": "x", "env_file": [{"path": "x.env"}]}}}, "only .env"),
        ({"services": {"a": {"extends": {"file": "b.yml", "service": "b"}}}}, "extends"),
        ({"services": {"a": {"image": "x"}}, "configs": {"c": {"file": "./c"}}}, "configs"),
        ({"services": {"a": {"image": "x"}}, "include": ["other.yml"]}, "include"),
    ],
)
def test_release_compose_rejects_other_checkout_files(tmp_path, capsys, doc, reason):
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(yaml.safe_dump(doc), encoding="utf-8")
    assert gen.main(["--compose", str(compose), "--out", str(tmp_path / "out")]) == 1
    assert reason in capsys.readouterr().err


def test_release_compose_rejects_built_service_without_image_name(tmp_path, capsys):
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(yaml.safe_dump({"services": {"newthing": {"build": "."}}}), encoding="utf-8")
    assert gen.main(["--compose", str(compose), "--out", str(tmp_path / "out")]) == 1
    assert "no published image name" in capsys.readouterr().err


def test_release_compose_rejects_anchors(tmp_path, capsys):
    compose = tmp_path / "docker-compose.yml"
    compose.write_text("x-common: &c {restart: always}\nservices:\n  a: *c\n", encoding="utf-8")
    assert gen.main(["--compose", str(compose), "--out", str(tmp_path / "out")]) == 1
    assert "AnchorToken" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# example.env
# ---------------------------------------------------------------------------


def _env_pairs(text: str, *, commented: bool) -> dict[str, str]:
    pairs: dict[str, str] = {}
    pattern = r"^#\s?([A-Z][A-Z0-9_]*)=(\S*)" if commented else r"^([A-Z][A-Z0-9_]*)=(\S*)"
    for line in text.splitlines():
        m = re.match(pattern, line)
        if m and m.group(1) not in pairs:
            pairs[m.group(1)] = m.group(2)
    return pairs


def test_example_env_contract():
    example = _env_pairs(_EXAMPLE_ENV.read_text(encoding="utf-8"), commented=False)
    reference_text = _ENV_EXAMPLE.read_text(encoding="utf-8")
    set_ = _env_pairs(reference_text, commented=False)
    commented = _env_pairs(reference_text, commented=True)
    for key, value in example.items():
        if key in _RELEASE_ONLY_VALUES:
            assert key in set_ or key in commented, f"{key} is not in .env.example"
            assert value == _RELEASE_ONLY_VALUES[key]
            continue
        if key in set_:
            assert value == set_[key], f"{key}: {value!r} != .env.example {set_[key]!r}"
        else:
            assert key in commented, f"{key} is not in .env.example (set or commented)"
            assert value == commented[key], f"{key}: {value!r} != .env.example # {commented[key]!r}"


def test_example_env_placeholders_match_entrypoint():
    """The entrypoint rotates exactly these placeholders; a rename would ship a shared secret."""
    entry = _ENTRYPOINT.read_text(encoding="utf-8")
    rotated = dict(re.findall(r'"([A-Z_]+_SECRET)=([^"]+)"', entry))
    example = _env_pairs(_EXAMPLE_ENV.read_text(encoding="utf-8"), commented=False)
    secrets = {k: v for k, v in example.items() if k.endswith("_SECRET")}
    assert secrets, "example.env carries no secret placeholders"
    assert secrets == {k: rotated[k] for k in secrets}, "placeholder not rotated by the entrypoint"


def test_every_unset_compose_variable_has_a_value(generated):
    """Variables the release compose uses without a default must come from example.env."""
    _, _, text = generated
    body = text.split("\nservices:", 1)[1]
    needed = set(re.findall(r"\$\{([A-Z][A-Z0-9_]*)\}", body)) - {"PWD"}
    example = _env_pairs(_EXAMPLE_ENV.read_text(encoding="utf-8"), commented=False)
    assert needed <= set(example), f"missing from example.env: {sorted(needed - set(example))}"


# ---------------------------------------------------------------------------
# The pair, through Compose itself
# ---------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker CLI not available")
def test_release_pair_resolves(generated, tmp_path):
    out, _, _ = generated
    (out / ".env").write_text(_EXAMPLE_ENV.read_text(encoding="utf-8"), encoding="utf-8")
    res = subprocess.run(
        ["docker", "compose", "config", "-q"], cwd=out, capture_output=True, text=True, check=False
    )
    if res.returncode != 0 and "unknown command" in (res.stderr or ""):
        pytest.skip("docker compose plugin not available")
    assert res.returncode == 0, res.stderr
    assert "not set" not in res.stderr, res.stderr
    policy = subprocess.run(
        [
            "python3",
            str(_ROOT / "scripts" / "check_compose_policy.py"),
            "--project-directory",
            str(out),
            "--core-file",
            str(out / "docker-compose.yml"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert policy.returncode == 0, policy.stdout + policy.stderr


def test_ghcr_overlay_covers_every_lumogis_image():
    """In a checkout the overlay pulls all five images (it uses !reset, so check the text)."""
    text = (_ROOT / "docker-compose.ghcr.yml").read_text(encoding="utf-8")
    for name, image in _IMAGES.items():
        block = (
            f"  {name}:\n"
            f"    image: ghcr.io/lumogis/{image}:${{LUMOGIS_VERSION:-${{IMAGE_TAG:-latest}}}}\n"
            "    build: !reset null\n"
            "    pull_policy: !reset null\n"
        )
        assert block in text, name
