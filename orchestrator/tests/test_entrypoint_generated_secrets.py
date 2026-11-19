# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Behavioural tests of the entrypoint's generated secrets (LUM-981, E1-E14).

Like ``test_entrypoint_refresh_secret.py``, these run the real secrets section
(the text between ``# >>> secrets-section`` and ``# <<< secrets-section``) under
``bash`` against a temp ``.env``. The FATAL backstops that follow it sit between
``# >>> secrets-backstop`` and ``# <<< secrets-backstop`` and are run the same way.

Contract under test: ``AUTH_SECRET`` (sign-in on), the refresh secret (sign-in
on) and ``LUMOGIS_CREDENTIAL_KEY`` (both modes, unless ``LUMOGIS_CREDENTIAL_KEYS``
is set) are generated into ``.env`` when missing or a placeholder, exported for
the same run, never replaced once real, and never printed.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from tests.test_secret_sentinels import _REPO_ROOT

PLACEHOLDER = "change-me-in-production"
REAL_AUTH = "ab12" * 16
REAL_REFRESH = "cd34" * 16
REAL_KEY = "OlGLYckGIbBSt54y8XVmgb441LgKJWvvYoHnpQ_cv9A="
_ENTRYPOINT = _REPO_ROOT / "orchestrator" / "docker-entrypoint.sh"
_NAMES = ("AUTH_SECRET", "JWT_REFRESH_SECRET", "LUMOGIS_CREDENTIAL_KEY")


def _sed_is_gnu() -> bool:
    sed = shutil.which("sed")
    return bool(sed) and subprocess.run([sed, "--version"], capture_output=True).returncode == 0


def _python3_has_cryptography() -> bool:
    py = shutil.which("python3")
    if not py:
        return False
    return (
        subprocess.run([py, "-c", "import cryptography.fernet"], capture_output=True).returncode
        == 0
    )


pytestmark = pytest.mark.skipif(
    not (shutil.which("bash") and _python3_has_cryptography() and _sed_is_gnu()),
    reason="needs bash, a python3 with cryptography, and GNU sed",
)


def _between(start: str, end: str) -> str:
    text = _ENTRYPOINT.read_text()
    m = re.search(rf"^# >>> {start}\n(.*?)^# <<< {end}$", text, re.S | re.M)
    assert m, f"{start} markers missing from docker-entrypoint.sh"
    return m.group(1)


class Result:
    def __init__(self, proc, exported: dict[str, str], env_file: Path) -> None:
        self.returncode = proc.returncode
        self.stdout = proc.stdout
        self.stderr = proc.stderr
        self.exported = exported
        self.env_file = env_file

    @property
    def text(self) -> str:
        return self.env_file.read_bytes().decode() if self.env_file.exists() else ""

    def file_value(self, key: str) -> str:
        lines = [ln for ln in self.text.splitlines() if ln.startswith(f"{key}=")]
        assert len(lines) == 1, (key, lines)
        return lines[0].split("=", 1)[1]


def run(
    tmp_path: Path,
    env_text: str | None,
    process_env: dict[str, str] | None = None,
    *,
    backstop: bool = False,
    path_prefix: Path | None = None,
) -> Result:
    env_file = tmp_path / ".env"
    if env_text is not None:
        env_file.write_bytes(env_text.encode())
    script = _between("secrets-section", "secrets-section")
    if backstop:
        script += "\n" + _between("secrets-backstop", "secrets-backstop")
    for i, name in enumerate(_NAMES):
        script += f'\nprintf "%s" "${{{name}-}}" > "$LUM_TEST_OUT_{i}"'
    path = os.environ["PATH"]
    if path_prefix is not None:
        path = f"{path_prefix}:{path}"
    env = {"PATH": path, "HOME": str(tmp_path), "HOST_ENV": str(env_file)}
    for i in range(len(_NAMES)):
        env[f"LUM_TEST_OUT_{i}"] = str(tmp_path / f"out{i}.txt")
    if "LD_LIBRARY_PATH" in os.environ:
        env["LD_LIBRARY_PATH"] = os.environ["LD_LIBRARY_PATH"]
    env.update(process_env or {})
    proc = subprocess.run(
        ["bash", "-c", script], env=env, capture_output=True, text=True, timeout=60
    )
    exported = {}
    for i, name in enumerate(_NAMES):
        out = tmp_path / f"out{i}.txt"
        exported[name] = out.read_text() if out.exists() else ""
    return Result(proc, exported, env_file)


def _is_hex64(s: str) -> bool:
    return re.fullmatch(r"[0-9a-f]{64}", s) is not None


def _is_fernet(s: str) -> bool:
    try:
        Fernet(s.encode())
    except Exception:  # noqa: BLE001
        return False
    return True


# E1 - E4 ---------------------------------------------------------------------


def test_e1_auth_on_without_auth_secret_appends_and_exports(tmp_path):
    r = run(tmp_path, "FOO=bar\n", {"AUTH_ENABLED": "true"})
    value = r.file_value("AUTH_SECRET")
    assert _is_hex64(value)
    assert r.exported["AUTH_SECRET"] == value
    assert r.text.startswith("FOO=bar\n")


def test_e2_placeholder_auth_secret_is_replaced(tmp_path):
    r = run(
        tmp_path,
        f"AUTH_SECRET={PLACEHOLDER}\nFOO=bar\n",
        {"AUTH_ENABLED": "true", "AUTH_SECRET": PLACEHOLDER},
    )
    value = r.file_value("AUTH_SECRET")
    assert _is_hex64(value) and value != PLACEHOLDER
    assert r.exported["AUTH_SECRET"] == value
    assert "FOO=bar" in r.text


def test_e3_real_auth_secret_is_untouched_and_not_regenerated(tmp_path):
    original = f"AUTH_SECRET={REAL_AUTH}\n"
    r = run(tmp_path, original, {"AUTH_ENABLED": "true", "AUTH_SECRET": REAL_AUTH})
    assert r.text.startswith(original)
    assert r.file_value("AUTH_SECRET") == REAL_AUTH
    assert r.exported["AUTH_SECRET"] == REAL_AUTH


def test_e4_auth_off_leaves_auth_secret_alone(tmp_path):
    r = run(tmp_path, "FOO=bar\n", {"AUTH_ENABLED": "false"})
    assert "AUTH_SECRET" not in r.text
    assert r.exported["AUTH_SECRET"] == ""


# E5 - E6, E13 -----------------------------------------------------------------


def test_e5_credential_key_is_generated_even_with_auth_off(tmp_path):
    r = run(tmp_path, "FOO=bar\n", {"AUTH_ENABLED": "false"})
    value = r.file_value("LUMOGIS_CREDENTIAL_KEY")
    assert _is_fernet(value)
    assert r.exported["LUMOGIS_CREDENTIAL_KEY"] == value


def test_e6_credential_keys_in_process_env_suppresses_single_key(tmp_path):
    r = run(
        tmp_path,
        "FOO=bar\n",
        {"AUTH_ENABLED": "true", "LUMOGIS_CREDENTIAL_KEYS": f"{REAL_KEY},{REAL_KEY}"},
    )
    assert "LUMOGIS_CREDENTIAL_KEY=" not in r.text
    assert r.exported["LUMOGIS_CREDENTIAL_KEY"] == ""


def test_e13_credential_keys_only_in_dotenv_suppresses_single_key(tmp_path):
    r = run(tmp_path, f"LUMOGIS_CREDENTIAL_KEYS={REAL_KEY}\n", {"AUTH_ENABLED": "true"})
    assert not re.search(r"^LUMOGIS_CREDENTIAL_KEY=", r.text, re.M)
    assert r.exported["LUMOGIS_CREDENTIAL_KEY"] == ""


def test_real_credential_key_is_never_replaced(tmp_path):
    """It protects every saved password; a new one would orphan them."""
    original = f"LUMOGIS_CREDENTIAL_KEY={REAL_KEY}\n"
    r = run(tmp_path, original, {"AUTH_ENABLED": "true", "LUMOGIS_CREDENTIAL_KEY": REAL_KEY})
    assert r.file_value("LUMOGIS_CREDENTIAL_KEY") == REAL_KEY


# E7, E14: the backstops ------------------------------------------------------


def test_e7_unwritable_env_exports_nothing_and_backstop_exits_1(tmp_path):
    stub_dir = tmp_path / "stub"
    stub_dir.mkdir()
    stub = stub_dir / "sed"
    stub.write_text("#!/bin/sh\nexit 1\n")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    original = (
        f"AUTH_SECRET={PLACEHOLDER}\nJWT_REFRESH_SECRET={REAL_REFRESH}\n"
        f"LUMOGIS_CREDENTIAL_KEY={REAL_KEY}\n"
    )
    proc_env = {
        "AUTH_ENABLED": "true",
        "AUTH_SECRET": PLACEHOLDER,
        "JWT_REFRESH_SECRET": REAL_REFRESH,
        "LUMOGIS_CREDENTIAL_KEY": REAL_KEY,
    }
    # The section alone: the value that could not be saved is not exported.
    r = run(tmp_path, original, proc_env, path_prefix=stub_dir)
    assert "could not write AUTH_SECRET" in r.stderr
    assert r.exported["AUTH_SECRET"] == PLACEHOLDER
    assert r.text == original
    # With the backstop: refuse to boot, naming the file and the fix.
    r = run(tmp_path, original, proc_env, backstop=True, path_prefix=stub_dir)
    assert r.returncode == 1
    assert "Lumogis could not save a generated AUTH_SECRET" in r.stderr
    assert "openssl rand -hex 32" in r.stderr
    assert "NOT auto-rotated" not in r.stderr


def test_e14_no_env_file_and_auth_on_exits_1_with_the_install_folder_message(tmp_path):
    r = run(tmp_path, None, {"AUTH_ENABLED": "true"}, backstop=True)
    assert r.returncode == 1
    assert "No .env was found" in r.stderr
    assert "AUTH_SECRET" in r.stderr
    assert not r.env_file.exists()


def test_backstop_is_quiet_when_secrets_were_generated(tmp_path):
    r = run(tmp_path, "FOO=bar\n", {"AUTH_ENABLED": "true"}, backstop=True)
    assert r.returncode == 0, r.stderr
    assert "FATAL" not in r.stderr


# E8 - E12 --------------------------------------------------------------------


@pytest.mark.parametrize("fmt", ['"{v}"', "'{v}'", "{v}\r"])
def test_e8_quoted_and_crlf_values_export_unquoted(tmp_path, fmt):
    lines = (
        "AUTH_SECRET=" + fmt.format(v=REAL_AUTH) + "\n"
        "LUMOGIS_CREDENTIAL_KEY=" + fmt.format(v=REAL_KEY) + "\n"
    )
    r = run(
        tmp_path,
        lines,
        {
            "AUTH_ENABLED": "true",
            "AUTH_SECRET": PLACEHOLDER,
            "LUMOGIS_CREDENTIAL_KEY": PLACEHOLDER,
            "JWT_REFRESH_SECRET": REAL_REFRESH,
        },
    )
    assert r.exported["AUTH_SECRET"] == REAL_AUTH
    assert r.exported["LUMOGIS_CREDENTIAL_KEY"] == REAL_KEY
    assert r.text.startswith(lines)  # the existing lines are never rewritten


def test_e9_two_runs_in_a_row_change_nothing_the_second_time(tmp_path):
    first = run(tmp_path, "FOO=bar\n", {"AUTH_ENABLED": "true"})
    after_first = first.text
    # A restart: the container env still predates the generated values.
    second = run(tmp_path, None, {"AUTH_ENABLED": "true"})
    assert second.text == after_first
    for name in _NAMES:
        assert second.exported[name] == first.exported[name]


def test_e10_no_output_line_contains_a_generated_value(tmp_path):
    r = run(tmp_path, "FOO=bar\n", {"AUTH_ENABLED": "true"})
    for name in _NAMES:
        value = r.exported[name]
        assert value
        assert value not in r.stdout and value not in r.stderr
    assert "Generated AUTH_SECRET" in r.stdout


def test_e11_restart_exports_values_from_env_file_and_changes_nothing(tmp_path):
    original = (
        f"AUTH_SECRET={REAL_AUTH}\nJWT_REFRESH_SECRET={REAL_REFRESH}\n"
        f"LUMOGIS_CREDENTIAL_KEY={REAL_KEY}\n"
    )
    r = run(
        tmp_path,
        original,
        {
            "AUTH_ENABLED": "true",
            "AUTH_SECRET": PLACEHOLDER,
            "JWT_REFRESH_SECRET": PLACEHOLDER,
            "LUMOGIS_CREDENTIAL_KEY": "",
        },
    )
    assert r.exported == {
        "AUTH_SECRET": REAL_AUTH,
        "JWT_REFRESH_SECRET": REAL_REFRESH,
        "LUMOGIS_CREDENTIAL_KEY": REAL_KEY,
    }
    assert r.text == original


def test_e12_one_first_run_generates_all_three_distinct_secrets(tmp_path):
    r = run(tmp_path, "FOO=bar\n", {"AUTH_ENABLED": "true"})
    auth = r.file_value("AUTH_SECRET")
    refresh = r.file_value("JWT_REFRESH_SECRET")
    key = r.file_value("LUMOGIS_CREDENTIAL_KEY")
    assert _is_hex64(auth) and _is_hex64(refresh) and _is_fernet(key)
    assert len({auth, refresh, key}) == 3
    assert refresh != auth
    assert r.exported == {
        "AUTH_SECRET": auth,
        "JWT_REFRESH_SECRET": refresh,
        "LUMOGIS_CREDENTIAL_KEY": key,
    }
