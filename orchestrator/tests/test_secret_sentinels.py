# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Pin the secret-sentinel convention to ``change-me-in-production``.

Why this test file exists
-------------------------
The original family-LAN plan (``.cursor/plans/family_lan_multi_user.plan.md``)
referred to ``__GENERATE_ME__`` as the sentinel for unset secrets, but the
actual implementation in ``.env.example`` and the orchestrator entrypoint
shipped with ``change-me-in-production``. The post-/verify-plan hardening
pass picked one convention (``change-me-in-production`` — the one that
already works in real installs) and aligned everything else around it.

These tests prevent that drift from re-emerging:

1. ``.env.example`` declares ``JWT_SECRET`` and ``JWT_REFRESH_SECRET`` with
   the literal sentinel the entrypoint knows how to rotate.
2. ``orchestrator/docker-entrypoint.sh`` hunts for that exact sentinel
   when auto-rotating secrets — a typo here would silently leave installs
   shipping with the placeholder.
3. The entrypoint has an ``AUTH_SECRET`` refusal block (auto-rotation is
   intentionally NOT applied to ``AUTH_SECRET`` — see the .env.example
   comment and ``main._enforce_auth_consistency``).
4. The legacy ``__GENERATE_ME__`` sentinel is rejected wherever
   ``change-me-in-production`` is rejected, so .env files copied from an
   older draft of the plan still fail closed instead of quietly running
   with a known-guessable secret.
"""

from __future__ import annotations

import re
from pathlib import Path

def _find_repo_root() -> Path:
    """Locate the repo root by walking up looking for ``docker-compose.yml``.

    The tests below baked into the orchestrator image live at
    ``/app/tests/...`` where ``parents[2]`` resolves to ``/`` and the repo
    files are missing. The compose dev mount exposes the repo at
    ``/project``; fall back to that, then to a parents[]-walk so the file
    works both inside the container and when run from the host.
    """
    for candidate in (*Path(__file__).resolve().parents, Path("/project")):
        if (candidate / "docker-compose.yml").is_file() and (
            candidate / ".env.example"
        ).is_file():
            return candidate
    raise RuntimeError(
        "Unable to locate Lumogis repo root (looked for docker-compose.yml + "
        ".env.example walking up from this test file and at /project)."
    )


_REPO_ROOT = _find_repo_root()


def _env_example() -> str:
    return (_REPO_ROOT / ".env.example").read_text()


def _entrypoint() -> str:
    return (_REPO_ROOT / "orchestrator" / "docker-entrypoint.sh").read_text()


# ---------------------------------------------------------------------------
# .env.example sentinel pinning
# ---------------------------------------------------------------------------


def test_env_example_uses_change_me_in_production_for_jwt_secrets():
    """``JWT_SECRET`` and ``JWT_REFRESH_SECRET`` must use the rotation sentinel.

    The entrypoint matches on this exact literal — any drift here breaks
    the auto-rotation loop in ``orchestrator/docker-entrypoint.sh``.
    """
    text = _env_example()
    for var in ("JWT_SECRET", "JWT_REFRESH_SECRET"):
        m = re.search(rf"^\s*{var}\s*=\s*(.+?)\s*$", text, re.MULTILINE)
        assert m is not None, f"{var} not declared in .env.example"
        value = m.group(1).strip()
        assert value == "change-me-in-production", (
            f"{var} must default to 'change-me-in-production' so the "
            f"entrypoint auto-rotation loop can detect and replace it. "
            f"Found: {value!r}"
        )


def test_no_make_secrets_target_exists():
    """The Makefile must NOT define a ``make secrets`` target.

    Pinned reality: the plan briefly mentioned a ``make secrets`` target
    that never landed. The entrypoint's auto-rotation loop covers
    ``JWT_SECRET`` / ``JWT_REFRESH_SECRET`` / ``RESTART_SECRET``, and
    ``AUTH_SECRET`` is operator-managed by design (see
    ``main._enforce_auth_consistency``). Adding a ``make`` target later
    would create two parallel paths to the same artefact and is rejected
    here so the docs and the Makefile stay in sync.
    """
    makefile = (_REPO_ROOT / "Makefile").read_text()
    assert not re.search(r"^secrets\s*:", makefile, re.MULTILINE), (
        "Makefile defines a `secrets` target — but the supported path is "
        "entrypoint auto-rotation + manual AUTH_SECRET. Either delete the "
        "target or update this test and the .env.example comment block."
    )


def test_env_example_documents_auth_secret_is_not_auto_rotated():
    """AUTH_SECRET section must call out that it is NOT auto-rotated.

    Pins the documented operator contract: family-LAN flip-on requires a
    deliberate AUTH_SECRET, otherwise both the entrypoint and the lifespan
    gate refuse to boot (see test_auth_phase1.py).
    """
    text = _env_example()
    auth_block = text.split("AUTH_SECRET=", 1)[0]
    assert "NOT auto-rotated" in auth_block or "not auto-rotated" in auth_block, (
        ".env.example must explicitly state that AUTH_SECRET is not auto-rotated "
        "by the entrypoint — that is the contract operators rely on."
    )


# ---------------------------------------------------------------------------
# docker-entrypoint.sh sentinel pinning
# ---------------------------------------------------------------------------


def test_entrypoint_rotates_change_me_in_production_only():
    """The entrypoint's auto-rotation loop must look for the canonical sentinel.

    A typo or terminology drift would silently leave secrets unrotated —
    a fresh install would then ship with the literal ``change-me-in-production``
    string as its real signing secret.
    """
    text = _entrypoint()
    assert 'JWT_SECRET=change-me-in-production' in text, (
        "Entrypoint must look for 'JWT_SECRET=change-me-in-production' — "
        "match the .env.example default exactly or auto-rotation breaks."
    )
    assert 'JWT_REFRESH_SECRET=change-me-in-production' in text, (
        "Entrypoint must look for 'JWT_REFRESH_SECRET=change-me-in-production' — "
        "match the .env.example default exactly or auto-rotation breaks."
    )


def test_entrypoint_refuses_placeholder_auth_secret_when_auth_enabled():
    """Entrypoint must hard-fail before uvicorn when AUTH_SECRET is a placeholder.

    Mirrors the Python-side guard in :func:`main._enforce_auth_consistency`
    (covered separately in ``test_auth_phase1.py``). Belt + braces: catch
    the misconfiguration *before* uvicorn starts so the failure is loud
    and operator-readable, not buried in a Python traceback.
    """
    text = _entrypoint()
    assert "AUTH_ENABLED" in text, "entrypoint must check AUTH_ENABLED"
    assert "AUTH_SECRET" in text, "entrypoint must check AUTH_SECRET"
    # The refusal must actually exit non-zero — without this assertion an
    # operator could miss the warning in the log and uvicorn would still boot.
    refusal_section = text.split("AUTH_SECRET", 1)[1].split("Apply Postgres migrations")[0]
    assert "exit 1" in refusal_section, (
        "Entrypoint must `exit 1` on AUTH_SECRET placeholder, not just warn — "
        "otherwise uvicorn boots and family-LAN mode runs with a guessable secret."
    )


def test_entrypoint_treats_legacy_generate_me_as_placeholder():
    """``__GENERATE_ME__`` (legacy sentinel) must be rejected too.

    Operators copying from an older draft of the plan or from old docs
    might still have the legacy sentinel in their .env. The refusal must
    cover both strings so the failure mode is consistent.
    """
    text = _entrypoint()
    assert "__GENERATE_ME__" in text, (
        "Entrypoint must explicitly reject the legacy '__GENERATE_ME__' "
        "sentinel — old .env files copied from the original plan draft "
        "would otherwise quietly boot with a known-guessable secret."
    )
