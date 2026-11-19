# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-331 Pass 2 — the household secure-address site in Caddy.

* ``preflight.sh``: for ``caddy run`` only, a placed household site that no
  longer validates must not stop Caddy (start without it, leave a marker).
* ``validate-home.sh``: checks a pending site with the real Caddyfile, prints
  nothing (the site holds the acme-dns password).
* The Caddyfile shares one ``(lumogis_routes)`` between the main and household
  sites, keeps HSTS on the main site only, and imports ``home.d``.
* Compose wires the ``caddy-home`` volume (Core rw, Caddy ro + nocopy).

A stub ``caddy`` on ``PATH`` stands in for the binary; the real ``caddy
validate`` runs in the image (see tests/integration/test_secure_address_compose.py).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
CADDY_DIR = REPO / "docker" / "caddy"
PREFLIGHT = CADDY_DIR / "preflight.sh"
VALIDATE = CADDY_DIR / "validate-home.sh"
CADDYFILE = CADDY_DIR / "Caddyfile"
COMPOSE = REPO / "docker-compose.yml"
IMPORT_LINE = "import /etc/caddy/home.d/*.caddy"

pytestmark = pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX sh")


@pytest.fixture
def box(tmp_path):
    """A temp layout plus a stub `caddy` that records calls and answers validate."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls"
    stub = bin_dir / "caddy"
    stub.write_text(
        "#!/bin/sh\n"
        f'echo "$*" >> "{calls}"\n'
        'if [ "$1" = validate ]; then\n'
        '  echo "password supersecret-should-never-show" >&2\n'
        '  exit "${STUB_VALIDATE_RC:-0}"\n'
        "fi\n"
        'echo "RAN $*"\n'
    )
    stub.chmod(0o755)
    cfg = tmp_path / "Caddyfile"
    cfg.write_text(CADDYFILE.read_text())
    home = tmp_path / "home.d"
    home.mkdir()
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "LUMOGIS_HOME_SITE": str(home / "home.caddy"),
        "LUMOGIS_HOME_REJECTED": str(tmp_path / "rejected"),
        "LUMOGIS_NOHOME_CONFIG": str(tmp_path / "Caddyfile.nohome"),
        "LUMOGIS_HOME_PENDING": str(home / "home.caddy.pending"),
        "LUMOGIS_CADDYFILE": str(cfg),
        "LUMOGIS_CADDYFILE_CHECK": str(tmp_path / "Caddyfile.check"),
        "AUTH_ENABLED": "true",
    }

    class Box:
        root = tmp_path

        def preflight(self, *argv: str, validate_rc: int = 0) -> subprocess.CompletedProcess:
            return subprocess.run(
                ["sh", str(PREFLIGHT), *argv],
                env={**env, "STUB_VALIDATE_RC": str(validate_rc)},
                capture_output=True,
                text=True,
                cwd=tmp_path,
            )

        def validate(self, validate_rc: int = 0) -> subprocess.CompletedProcess:
            return subprocess.run(
                ["sh", str(VALIDATE)],
                env={**env, "STUB_VALIDATE_RC": str(validate_rc)},
                capture_output=True,
                text=True,
                cwd=tmp_path,
            )

        def calls(self) -> list[str]:
            return calls.read_text().splitlines() if calls.exists() else []

    b = Box()
    b.cfg, b.home = cfg, home
    return b


RUN = ("caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile")


# --------------------------------------------------------------------------- preflight


def test_no_household_site_runs_caddy_unchanged_without_validating(box):
    r = box.preflight(*RUN, validate_rc=1)
    assert r.returncode == 0
    assert r.stdout.strip() == "RAN " + " ".join(RUN[1:])
    assert not any(c.startswith("validate") for c in box.calls())  # no extra start-up cost
    assert not (box.root / "rejected").exists()


def test_valid_household_site_runs_caddy_unchanged(box):
    (box.home / "home.caddy").write_text("x.homes.lumogis.app {\n}\n")
    (box.root / "rejected").write_text("")  # left from an earlier start
    r = box.preflight(*RUN, validate_rc=0)
    assert r.stdout.strip() == "RAN " + " ".join(RUN[1:])
    assert r.stderr == ""
    assert not (box.root / "rejected").exists()  # a clean start clears it


@pytest.mark.parametrize("form", ["separate", "equals", "short"])
def test_rejected_household_site_starts_without_it(box, form):
    (box.home / "home.caddy").write_text("broken {\n")
    cfg = str(box.cfg)
    flag = {"separate": ["--config", cfg], "equals": [f"--config={cfg}"], "short": ["-c", cfg]}[
        form
    ]
    r = box.preflight("caddy", "run", *flag, "--adapter", "caddyfile", validate_rc=1)
    assert r.returncode == 0
    nohome = str(box.root / "Caddyfile.nohome")
    expected_flag = {
        "separate": f"--config {nohome}",
        "equals": f"--config={nohome}",
        "short": f"-c {nohome}",
    }
    assert r.stdout.strip() == f"RAN run {expected_flag[form]} --adapter caddyfile"
    assert "household secure-address config rejected; starting without it" in r.stderr
    assert "supersecret" not in r.stdout + r.stderr  # caddy's own output is not echoed
    assert (box.root / "rejected").exists()


def test_without_a_readable_caddyfile_caddy_starts_as_usual(box):
    """No copy can be made, so Caddy runs the command it was given (and reports)."""
    (box.home / "home.caddy").write_text("broken {\n")
    r = box.preflight(*RUN, validate_rc=1)
    assert r.stdout.strip() == "RAN " + " ".join(RUN[1:])
    assert not (box.root / "rejected").exists()


def test_fallback_copy_drops_only_the_household_import(tmp_path, box):
    # The copy is made from the --config file, so point --config at the test copy.
    (box.home / "home.caddy").write_text("broken {\n")
    box.preflight("caddy", "run", "--config", str(box.cfg), validate_rc=1)
    original = box.cfg.read_text().splitlines()
    copy = (box.root / "Caddyfile.nohome").read_text().splitlines()
    assert IMPORT_LINE in original and IMPORT_LINE not in copy
    assert [line for line in original if line != IMPORT_LINE] == copy


def test_other_commands_are_never_touched(box):
    (box.home / "home.caddy").write_text("broken {\n")
    r = box.preflight("caddy", "version", validate_rc=1)
    assert r.stdout.strip() == "RAN version"
    assert box.calls() == ["version"]
    assert r.stderr == ""
    assert not (box.root / "rejected").exists()


# --------------------------------------------------------------------------- validate-home.sh


def test_validate_home_without_a_pending_site_fails(box):
    assert box.validate().returncode == 1
    assert box.calls() == []


def test_validate_home_checks_the_pending_site_with_the_real_caddyfile(box):
    (box.home / "home.caddy.pending").write_text("x.homes.lumogis.app {\n}\n")
    r = box.validate(validate_rc=0)
    assert r.returncode == 0
    check = (box.root / "Caddyfile.check").read_text().splitlines()
    assert IMPORT_LINE not in check
    assert f"import {box.home / 'home.caddy.pending'}" in check
    assert box.calls() == [f"validate --adapter caddyfile --config {box.root / 'Caddyfile.check'}"]


def test_validate_home_reports_rejection_and_prints_nothing(box):
    (box.home / "home.caddy.pending").write_text("broken {\n")
    r = box.validate(validate_rc=1)
    assert r.returncode == 1
    assert r.stdout == "" and r.stderr == ""  # never the password Caddy may echo


def test_validate_home_fails_on_a_caddyfile_without_the_import(box):
    (box.home / "home.caddy.pending").write_text("x.homes.lumogis.app {\n}\n")
    box.cfg.write_text("\n".join(x for x in CADDYFILE.read_text().splitlines() if x != IMPORT_LINE))
    assert box.validate(validate_rc=0).returncode == 1
    assert box.calls() == []


# --------------------------------------------------------------------------- Caddyfile


def _top_level_blocks(text: str) -> dict[str, str]:
    """Top-level `name {` … `}` blocks by their opening line (tabs are nesting)."""
    blocks, name, buf = {}, None, []
    for line in text.splitlines():
        if name is None and line.endswith("{") and not line.startswith(("\t", "#", " ")):
            name, buf = line[:-1].strip(), []
        elif name is not None and line == "}":
            blocks[name] = "\n".join(buf)
            name = None
        elif name is not None:
            buf.append(line)
    return blocks


def test_caddyfile_shares_routes_and_keeps_hsts_on_the_main_site():
    text = CADDYFILE.read_text()
    blocks = _top_level_blocks(text)
    routes, main = blocks["(lumogis_routes)"], blocks["{$CADDY_DOMAIN}"]
    assert "Strict-Transport-Security" not in routes
    assert "Strict-Transport-Security" in main and "import lumogis_routes" in main
    for needle in (
        "reverse_proxy lumogis-web:80",
        "handle /api/*",
        "Content-Security-Policy",
        "log {",
    ):
        assert needle in routes
    # The household import comes after the snippet it uses, at top level.
    assert re.search(r"^import /etc/caddy/home\.d/\*\.caddy$", text, re.M)
    assert text.index("(lumogis_routes) {") < text.index(IMPORT_LINE)


def test_household_site_snippet_imports_the_shared_routes():
    from services.home_dns.snippet import render

    site = render("kq7f3r2abcdefghijklmnopqrs", "b" * 43, "https://dns.homes.lumogis.app")
    assert "\timport lumogis_routes" in site.splitlines()
    assert "Strict-Transport-Security" not in site


# --------------------------------------------------------------------------- compose and images


def test_compose_wires_the_caddy_home_volume():
    doc = yaml.safe_load(COMPOSE.read_text())
    assert "caddy-home" in doc["volumes"]
    orch = doc["services"]["orchestrator"]
    assert "caddy-home:/caddy-home" in orch["volumes"]
    env = orch["environment"]
    assert env["CADDY_HOME_DIR"] == "/caddy-home"
    assert env["LUMOGIS_SECURE_ADDRESS_PORT"] == "${CADDY_HTTPS_HOST_PORT:-443}"
    assert env["LUMOGIS_HOME_DNS_URL"] == "${LUMOGIS_HOME_DNS_URL:-https://dns.homes.lumogis.app}"
    mounts = [v for v in doc["services"]["caddy"]["volumes"] if isinstance(v, dict)]
    assert {
        "type": "volume",
        "source": "caddy-home",
        "target": "/etc/caddy/home.d",
        "read_only": True,
        "volume": {"nocopy": True},
    } in mounts


def test_caddy_image_bakes_the_validator_and_pins_the_dns_module():
    dockerfile = (CADDY_DIR / "Dockerfile").read_text()
    assert "COPY validate-home.sh /etc/caddy/validate-home.sh" in dockerfile
    assert re.search(r"github\.com/caddy-dns/acmedns@v\d+\.\d+\.\d+\b", dockerfile)
    assert "@latest" not in dockerfile
    # No RUN in the final stage: the arm64 image builds without emulation.
    final = dockerfile.split("FROM caddy:2.11.9-alpine", 1)[1]
    assert not re.search(r"^RUN ", final, re.M)
    assert os.access(VALIDATE, os.X_OK)


def test_orchestrator_owns_caddy_home():
    entry = (REPO / "orchestrator" / "docker-entrypoint.sh").read_text()
    assert '_lum_own_tree owned "$_LUM_DEFAULT_CADDY_HOME"' in entry
    assert "/caddy-home" in (REPO / "orchestrator" / "Dockerfile").read_text()
