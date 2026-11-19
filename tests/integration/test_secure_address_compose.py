# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-331 Pass 2 — the secure-address wiring on a real compose stack.

What it proves (the plan's compose steps, plus recovery):

1. Core writes ``home.caddy.pending`` through its own code path, stack-control
   validates it inside Caddy, and the site is placed and loaded.
2. Caddy sees ``home.caddy`` in ``/etc/caddy/home.d`` and the full Caddyfile
   validates with it.
3. A household site that stops validating does not stop Caddy: after a restart
   the front door still answers, and stack-control reports
   ``rejected_at_start``. Fixing the site and reloading recovers it, and the
   marker is cleared.
4. A Caddy without the ``caddy-home`` mount (an older compose file) makes
   ``apply`` report ``caddy_volume_missing``.

It drives a running stack (the RC stack: ``scripts/integration-public-rc.sh
gate-start``) through ``docker compose`` with the caller's ``COMPOSE_FILE`` /
``COMPOSE_PROJECT_NAME``. It restarts and recreates Caddy, so it is opt-in:
``LUMOGIS_SECURE_ADDRESS_COMPOSE=1``. No certificate is issued: the test site
uses Let's Encrypt **staging** and a name service that does not exist yet.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import textwrap
import time
from pathlib import Path

import httpx
import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("LUMOGIS_SECURE_ADDRESS_COMPOSE") != "1" or shutil.which("docker") is None,
        reason="opt-in (restarts Caddy): set LUMOGIS_SECURE_ADDRESS_COMPOSE=1 with a stack up",
    ),
]

ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = os.environ.get("INTEGRATION_ENV_FILE", "config/test.env.example")
WEB_BASE = os.environ.get("LUMOGIS_WEB_BASE_URL", "http://127.0.0.1").rstrip("/")
HID = "kq7f3r2abcdefghijklmnopqrs"
BEARER = "T" * 43  # never valid anywhere: the name service is not deployed


def compose(*args: str, files: list[str] | None = None, check: bool = True) -> str:
    cmd = ["docker", "compose", "--env-file", ENV_FILE]
    for f in files or []:
        cmd += ["-f", f]
    proc = subprocess.run(
        [*cmd, *args], cwd=ROOT, capture_output=True, text=True, check=False, timeout=600
    )
    if check and proc.returncode != 0:
        raise AssertionError(f"{' '.join(args)} failed:\n{proc.stdout}\n{proc.stderr}")
    return proc.stdout


def core(code: str) -> str:
    """Run Python inside the orchestrator container (Core's own code paths)."""
    return compose("exec", "-T", "orchestrator", "python", "-c", textwrap.dedent(code)).strip()


def apply_site() -> str:
    """Enable a test household in Core's state and run its apply step; returns the result."""
    return core(
        f"""
        import os
        os.environ["LUMOGIS_HOME_DNS_ACME_STAGING"] = "true"
        from services.home_dns import service, state as st
        s = st.load()
        s.enabled = True
        s.generation += 1
        s.household_id, s.bearer, s.lan_address = "{HID}", "{BEARER}", "192.168.1.20"
        st.save(s)
        print(service._apply_snippet(s.generation, "{HID}", "{BEARER}"))
        """
    )


def home_status() -> dict:
    out = core(
        """
        import json
        from services.home_dns import service
        print(json.dumps(service._sidecar("GET", "/caddy/home-status", 45)))
        """
    )
    return json.loads(out.splitlines()[-1])


def wait_for_front_door(timeout: float = 90) -> int:
    deadline = time.monotonic() + timeout
    last = 0
    while time.monotonic() < deadline:
        try:
            last = httpx.get(f"{WEB_BASE}/", timeout=5).status_code
            if last == 200:
                return last
        except httpx.HTTPError:
            pass
        time.sleep(2)
    return last


@pytest.fixture(scope="module", autouse=True)
def cleanup():
    yield
    # Leave the stack as found: no household site, state off, Caddy reloaded.
    core(
        """
        from services.home_dns import service, state as st
        service._remove_snippet()
        st.save(st.HomeDnsState())
        print(service._reload_caddy())
        """
    )


def test_1_core_places_a_validated_site():
    assert apply_site() == "None"  # _apply_snippet: None means applied and reloaded


def test_2_caddy_sees_and_validates_it():
    listing = compose("exec", "-T", "caddy", "ls", "/etc/caddy/home.d")
    assert "home.caddy" in listing.split()
    assert "home.caddy.pending" not in listing.split()
    compose(
        "exec", "-T", "caddy",
        "caddy", "validate", "--adapter", "caddyfile", "--config", "/etc/caddy/Caddyfile",
    )  # fmt: skip
    assert home_status() == {"present": True, "rejected_at_start": False, "reachable": True}


def test_3_broken_site_does_not_stop_caddy_and_reload_recovers():
    # Core's mount is writable; Caddy's is read-only.
    core(
        """
        from pathlib import Path
        from services.home_dns import service
        Path(service.caddy_home_dir(), "home.caddy").write_text("broken {\\n")
        """
    )
    compose("restart", "caddy")
    assert wait_for_front_door() == 200  # the LAN address still serves
    assert home_status() == {"present": True, "rejected_at_start": True, "reachable": True}

    # Recovery (Pass 8 l): put a valid site back and reload, as refresh_job does.
    assert apply_site() == "None"
    assert home_status()["rejected_at_start"] is False  # stack-control cleared the marker
    assert wait_for_front_door() == 200


def test_4_caddy_without_the_volume_reports_volume_missing(tmp_path):
    files = os.environ.get("COMPOSE_FILE", "docker-compose.yml").split(":")
    rendered = json.loads(compose("config", "--format", "json", files=files))
    volumes = [
        v for v in rendered["services"]["caddy"]["volumes"] if v.get("target") != "/etc/caddy/home.d"
    ]
    override = tmp_path / "no-caddy-home.json"  # JSON is YAML; !reset needs YAML tags
    override.write_text(
        "services:\n  caddy:\n    volumes: !override\n"
        + "".join(f"      - {json.dumps(v)}\n" for v in volumes)
    )
    try:
        compose("up", "-d", "--no-deps", "caddy", files=[*files, str(override)])
        wait_for_front_door()
        assert apply_site() == "caddy_volume_missing"
    finally:
        compose("up", "-d", "--no-deps", "--force-recreate", "caddy", files=files)
        wait_for_front_door()
