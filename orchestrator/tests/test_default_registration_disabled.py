# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Pin LibreChat ``ALLOW_REGISTRATION`` to ``false`` by default.

Why this test exists
--------------------
Family-LAN plan §2 binds the decision in two places:

* **D6** — "Default ``ALLOW_REGISTRATION=false`` for LibreChat" as part
  of the clean break.
* **D15** — "**``ALLOW_REGISTRATION=false``** in the LibreChat env
  defaults; family operators provision LibreChat accounts manually
  during the transition."

The MULTI-USER audit ranks open registration as **A8 (P0, high impact /
trivial exploit)** in ``docs/private/MULTI-USER-AUDIT.md`` — anyone on
the LAN can sign up for a LibreChat account and inherit shared
orchestrator state because LibreChat is not (and is no longer planned
to be) bridged into Core's per-user auth surface (plan §23).

The plan's §17 test list names this exact assertion
(``test_env_example_default_allow_registration_false``); we extend it
to also pin the ``docker-compose.yml`` fallback default. Both files
shipped with ``true`` defaults until 2026-04-18 even though the plan
had bound them to ``false`` — these tests prevent that drift from
recurring.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml


def _find_repo_root() -> Path:
    """Locate the repo root by walking up looking for ``docker-compose.yml``.

    The tests below baked into the orchestrator image live at
    ``/app/tests/...`` where ``parents[2]`` resolves to ``/`` and the repo
    files are missing. The compose dev mount exposes the repo at
    ``/project``; fall back to that, then to a parents[]-walk so the file
    works both inside the container and when run from the host.
    """
    for candidate in (*Path(__file__).resolve().parents, Path("/project")):
        if (candidate / "docker-compose.yml").is_file() and (candidate / ".env.example").is_file():
            return candidate
    raise RuntimeError(
        "Unable to locate Lumogis repo root (looked for docker-compose.yml + "
        ".env.example walking up from this test file and at /project)."
    )


_REPO_ROOT = _find_repo_root()


def test_env_example_default_allow_registration_false():
    """``.env.example`` must ship with ``ALLOW_REGISTRATION=false``.

    Pinned by family-LAN plan D6 + D15 + §17 test list. Asserts on the
    last assignment seen so a comment-out + override pattern still
    counts the binding line.
    """
    text = (_REPO_ROOT / ".env.example").read_text()
    matches = re.findall(r"^\s*ALLOW_REGISTRATION\s*=\s*(\S+)", text, re.MULTILINE)
    assert matches, "ALLOW_REGISTRATION not declared in .env.example at all"
    last = matches[-1].strip().strip("\"'").lower()
    assert last == "false", (
        f".env.example must default ALLOW_REGISTRATION to 'false' "
        f"(plan D6 + D15, audit A8). Found: {last!r}"
    )


def test_docker_compose_default_allow_registration_false():
    """``docker-compose.yml`` must fall back to ``false``, not ``true``.

    LibreChat's environment block uses ``${ALLOW_REGISTRATION:-X}``;
    when an operator hasn't set the variable, the fallback ``X`` is the
    effective default a fresh clone boots with. Audit finding A8 is
    about exactly this fallback being ``true``, so the assertion here
    protects against a silent revert.
    """
    compose_text = (_REPO_ROOT / "docker-compose.yml").read_text()
    spec = yaml.safe_load(compose_text)
    librechat_env = spec["services"]["librechat"]["environment"]

    # Compose YAML can render env as either a list of "K=V" strings or a
    # mapping — handle both rather than depend on the current shape.
    if isinstance(librechat_env, dict):
        raw = librechat_env.get("ALLOW_REGISTRATION")
    else:
        raw = next(
            (e.split("=", 1)[1] for e in librechat_env if e.startswith("ALLOW_REGISTRATION=")),
            None,
        )
    assert raw is not None, "ALLOW_REGISTRATION not declared on the librechat service"

    # Match `${VAR:-default}` and pick `default` out. If the operator's
    # shell variable is set we don't care — only the fallback is what a
    # fresh clone uses on first boot.
    m = re.fullmatch(r"\$\{ALLOW_REGISTRATION:-(?P<default>[^}]*)\}", str(raw).strip())
    assert m is not None, (
        f"Expected ALLOW_REGISTRATION declared as ${{ALLOW_REGISTRATION:-<default>}} "
        f"so the test can pin the fallback. Got: {raw!r}"
    )
    default = m.group("default").strip().lower()
    assert default == "false", (
        f"docker-compose.yml must fall back to 'false' for ALLOW_REGISTRATION "
        f"(plan D6 + D15, audit A8 P0). Found: {default!r}"
    )
