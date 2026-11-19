# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Behavioural tests of the entrypoint's refresh-secret section (LUM-885).

``test_secret_sentinels.py`` only pins the entrypoint's *text*. These tests run
the real secrets section (the text between ``# >>> secrets-section`` and
``# <<< secrets-section``) under ``bash`` against a temp ``.env`` and assert the
file and the exported ``JWT_REFRESH_SECRET``, then feed the result to the Python
boot gate to prove the two halves agree.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
from tests.test_secret_sentinels import _REPO_ROOT

pytest_plugins = ("tests.test_auth_phase1",)

PLACEHOLDER = "change-me-in-production"
REAL = "ab12" * 16  # 64 hex chars
_ENTRYPOINT = _REPO_ROOT / "orchestrator" / "docker-entrypoint.sh"


def _sed_is_gnu() -> bool:
    sed = shutil.which("sed")
    if not sed:
        return False
    return subprocess.run([sed, "--version"], capture_output=True).returncode == 0


pytestmark = pytest.mark.skipif(
    not (shutil.which("bash") and shutil.which("python3") and _sed_is_gnu()),
    reason="needs bash, python3 and GNU sed (BSD sed -i takes a suffix argument)",
)


def _section() -> str:
    text = _ENTRYPOINT.read_text()
    m = re.search(r"^# >>> secrets-section\n(.*?)^# <<< secrets-section$", text, re.S | re.M)
    assert m, "secrets-section markers missing from docker-entrypoint.sh"
    return m.group(1)


class Result:
    def __init__(self, proc, exported: str, env_file: Path) -> None:
        self.stdout = proc.stdout
        self.stderr = proc.stderr
        self.exported = exported
        self.env_file = env_file

    @property
    def text(self) -> str:
        return self.env_file.read_bytes().decode() if self.env_file.exists() else ""


def run_block(
    tmp_path: Path,
    env_text: str | None,
    process_env: dict[str, str] | None = None,
    *,
    path_prefix: Path | None = None,
) -> Result:
    """Run the secrets section; ``exported`` is JWT_REFRESH_SECRET afterwards ('' if unset)."""
    env_file = tmp_path / ".env"
    if env_text is not None:
        env_file.write_bytes(env_text.encode())
    out = tmp_path / "exported.txt"
    script = _section() + '\nprintf "%s" "${JWT_REFRESH_SECRET-}" > "$LUM_TEST_OUT"\n'
    path = os.environ["PATH"]
    if path_prefix is not None:
        path = f"{path_prefix}:{path}"
    env = {"PATH": path, "HOME": str(tmp_path), "HOST_ENV": str(env_file), "LUM_TEST_OUT": str(out)}
    # The section's secret generator runs `python3` from PATH. On a CI runner that is
    # actions/setup-python's toolcache build, which links libpython3.12.so and finds it
    # only through LD_LIBRARY_PATH; drop it and `python3` fails to start, so every
    # generated secret comes back empty.
    if "LD_LIBRARY_PATH" in os.environ:
        env["LD_LIBRARY_PATH"] = os.environ["LD_LIBRARY_PATH"]
    # LUM-981: the section also generates AUTH_SECRET and LUMOGIS_CREDENTIAL_KEY.
    # These LUM-885 tests are about the refresh secret only, so they run with both
    # already real in the process env (the new generation is covered by
    # test_entrypoint_generated_secrets.py).
    env["AUTH_SECRET"] = "cd34" * 16
    env["LUMOGIS_CREDENTIAL_KEY"] = "OlGLYckGIbBSt54y8XVmgb441LgKJWvvYoHnpQ_cv9A="
    env.update(process_env or {})
    # The extracted text starts after the HOST_ENV assignment, so ours is used.
    proc = subprocess.run(
        ["bash", "-c", script], env=env, capture_output=True, text=True, timeout=60
    )
    return Result(proc, out.read_text() if out.exists() else "", env_file)


def _is_hex64(s: str) -> bool:
    return re.fullmatch(r"[0-9a-f]{64}", s) is not None


def _file_value(text: str) -> str:
    lines = [ln for ln in text.splitlines() if ln.startswith("JWT_REFRESH_SECRET=")]
    assert len(lines) == 1, lines
    return lines[0].split("=", 1)[1]


# 28-29 ----------------------------------------------------------------------


@pytest.mark.parametrize("auth", ["true", "false"])
def test_first_boot_placeholder_exports_generated_value(tmp_path, auth):
    r = run_block(
        tmp_path,
        f"JWT_REFRESH_SECRET={PLACEHOLDER}\nOTHER=1\n",
        {"AUTH_ENABLED": auth, "JWT_REFRESH_SECRET": PLACEHOLDER},
    )
    value = _file_value(r.text)
    assert _is_hex64(value)
    assert r.exported == value
    assert value not in r.stdout and value not in r.stderr
    assert "OTHER=1" in r.text


# 30 -------------------------------------------------------------------------


def test_restart_with_stale_process_env_exports_file_value(tmp_path):
    r = run_block(
        tmp_path,
        f"JWT_REFRESH_SECRET={REAL}\n",
        {"AUTH_ENABLED": "true", "JWT_REFRESH_SECRET": PLACEHOLDER},
    )
    assert r.exported == REAL
    assert r.text == f"JWT_REFRESH_SECRET={REAL}\n"


# 31 -------------------------------------------------------------------------


@pytest.mark.parametrize("has_trailing_newline", [True, False])
def test_missing_key_auth_on_appends_and_exports(tmp_path, has_trailing_newline):
    body = "FOO=bar" + ("\n" if has_trailing_newline else "")
    r = run_block(tmp_path, body, {"AUTH_ENABLED": "true"})
    value = _file_value(r.text)
    assert _is_hex64(value)
    assert r.exported == value
    assert r.text.startswith("FOO=bar\n")  # the append never glued onto the last line


def test_missing_key_auth_off_leaves_file_untouched(tmp_path):
    r = run_block(tmp_path, "FOO=bar\n", {"AUTH_ENABLED": "false"})
    assert r.text == "FOO=bar\n"
    assert r.exported == ""


# 32 -------------------------------------------------------------------------


@pytest.mark.parametrize("lum_value", ["real-lumogis-secret", PLACEHOLDER])
def test_lumogis_refresh_secret_set_is_noop(tmp_path, lum_value):
    """Mirrors ``auth._refresh_secret()``: first non-empty wins, even a placeholder (D8)."""
    original = f"JWT_REFRESH_SECRET={REAL}\n"
    r = run_block(
        tmp_path,
        original,
        {
            "AUTH_ENABLED": "true",
            "LUMOGIS_JWT_REFRESH_SECRET": lum_value,
            "JWT_REFRESH_SECRET": PLACEHOLDER,
        },
    )
    assert r.text == original
    assert r.exported == PLACEHOLDER  # untouched: still what the process had


# 33 -------------------------------------------------------------------------


def test_no_host_env_is_noop(tmp_path):
    r = run_block(tmp_path, None, {"AUTH_ENABLED": "true"})
    assert not r.env_file.exists()
    assert r.exported == ""


# 34 -------------------------------------------------------------------------


def test_existing_sentinel_loop_unchanged(tmp_path):
    r = run_block(
        tmp_path,
        "JWT_SECRET=change-me-in-production\nRESTART_SECRET=change-me-to-a-random-secret\n"
        f"JWT_REFRESH_SECRET={REAL}\n",
        {"AUTH_ENABLED": "false"},
    )
    assert "JWT_SECRET=change-me-in-production" not in r.text
    assert "RESTART_SECRET=change-me-to-a-random-secret" not in r.text
    for key in ("JWT_SECRET", "RESTART_SECRET"):
        m = re.search(rf"^{key}=([0-9a-f]{{64}})$", r.text, re.M)
        assert m, key
        assert m.group(1) not in r.stdout


def test_generate_me_placeholder_line_is_replaced(tmp_path):
    """The old loop only matched ``change-me-in-production``; ``__GENERATE_ME__`` never healed."""
    r = run_block(
        tmp_path,
        "JWT_REFRESH_SECRET=__GENERATE_ME__\n",
        {"AUTH_ENABLED": "true", "JWT_REFRESH_SECRET": "__GENERATE_ME__"},
    )
    value = _file_value(r.text)
    assert _is_hex64(value)
    assert r.exported == value


@pytest.mark.parametrize("fmt", ['"{v}"', "'{v}'", "{v}\r"])
def test_quoted_and_crlf_values_export_unquoted(tmp_path, fmt):
    line = "JWT_REFRESH_SECRET=" + fmt.format(v=REAL) + "\n"
    r = run_block(tmp_path, line, {"AUTH_ENABLED": "true", "JWT_REFRESH_SECRET": PLACEHOLDER})
    assert r.exported == REAL
    assert r.text == line  # the file is never rewritten


def test_unwritable_env_exports_nothing(tmp_path):
    """A secret that could not be persisted is never exported (D3c).

    A ``sed`` stub that fails stands in for a read-only mount, so the test does
    not depend on the uid (the test image runs as root, where chmod is a no-op).
    """
    stub_dir = tmp_path / "stub"
    stub_dir.mkdir()
    stub = stub_dir / "sed"
    stub.write_text("#!/bin/sh\nexit 1\n")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    original = f"JWT_REFRESH_SECRET={PLACEHOLDER}\n"

    r = run_block(
        tmp_path,
        original,
        {"AUTH_ENABLED": "true", "JWT_REFRESH_SECRET": PLACEHOLDER},
        path_prefix=stub_dir,
    )
    assert "could not write JWT_REFRESH_SECRET" in r.stderr
    assert r.exported == PLACEHOLDER
    assert r.text == original


def test_export_form_line_is_left_alone(tmp_path):
    original = f"export JWT_REFRESH_SECRET={REAL}\n"
    r = run_block(tmp_path, original, {"AUTH_ENABLED": "true", "JWT_REFRESH_SECRET": PLACEHOLDER})
    assert r.text == original
    assert r.exported == PLACEHOLDER
    assert "does not parse" in r.stderr


# 34d: the shell half and the Python gate, composed ---------------------------


def _gate(monkeypatch, exported_or_process: str, lumogis_value: str | None):
    import main
    import services.users as users_svc

    monkeypatch.delenv("LUMOGIS_JWT_REFRESH_SECRET", raising=False)
    monkeypatch.delenv("JWT_REFRESH_SECRET", raising=False)
    if exported_or_process:
        monkeypatch.setenv("JWT_REFRESH_SECRET", exported_or_process)
    if lumogis_value is not None:
        monkeypatch.setenv("LUMOGIS_JWT_REFRESH_SECRET", lumogis_value)
    users_svc.create_user("a@home.lan", "verylongpassword12", "admin")
    main._enforce_auth_consistency()


def test_heal_then_gate_composed_placeholder_is_healed(
    tmp_path, users_store, auth_env, no_skip_consistency, monkeypatch
):
    r = run_block(
        tmp_path,
        f"JWT_REFRESH_SECRET={PLACEHOLDER}\n",
        {"AUTH_ENABLED": "true", "JWT_REFRESH_SECRET": PLACEHOLDER},
    )
    _gate(monkeypatch, r.exported, None)  # must not raise


def test_heal_then_gate_composed_lumogis_placeholder_is_judged_by_python(
    tmp_path, users_store, auth_env, no_skip_consistency, monkeypatch
):
    r = run_block(
        tmp_path,
        f"JWT_REFRESH_SECRET={REAL}\n",
        {
            "AUTH_ENABLED": "true",
            "LUMOGIS_JWT_REFRESH_SECRET": PLACEHOLDER,
            "JWT_REFRESH_SECRET": PLACEHOLDER,
        },
    )
    assert r.text == f"JWT_REFRESH_SECRET={REAL}\n"  # the shell did nothing
    with pytest.raises(RuntimeError, match="LUMOGIS_JWT_REFRESH_SECRET"):
        _gate(monkeypatch, r.exported, PLACEHOLDER)


@pytest.mark.parametrize("fmt", ['"{v}"', "'{v}'", "{v}\r"])
def test_heal_then_gate_composed_quoted_and_crlf(
    tmp_path, users_store, auth_env, no_skip_consistency, monkeypatch, fmt
):
    import auth

    r = run_block(
        tmp_path,
        "JWT_REFRESH_SECRET=" + fmt.format(v=REAL) + "\n",
        {"AUTH_ENABLED": "true", "JWT_REFRESH_SECRET": PLACEHOLDER},
    )
    _gate(monkeypatch, r.exported, None)
    assert auth._refresh_secret() == REAL


def test_heal_then_gate_composed_unparsed_inline_comment_is_refused(
    tmp_path, users_store, auth_env, no_skip_consistency, monkeypatch
):
    r = run_block(
        tmp_path,
        "JWT_REFRESH_SECRET=abc123 # rotated\n",
        {"AUTH_ENABLED": "true", "JWT_REFRESH_SECRET": PLACEHOLDER},
    )
    assert "does not parse" in r.stderr
    assert r.exported == PLACEHOLDER
    with pytest.raises(RuntimeError, match="refresh secret"):
        _gate(monkeypatch, r.exported, None)
