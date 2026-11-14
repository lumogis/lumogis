# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Subprocess tests for scripts/npm_audit_allowlist.py (the fail-closed npm audit allowlist).

Fixtures are built inline in ``tmp_path``: a minimal package-lock.json, an ``npm audit --json``
report, and a ``src/`` tree. The script is stdlib-only, so no stack is needed.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "npm_audit_allowlist.py"

GHSA_DEEPMERGE = "GHSA-gcqx-w48m-w2h4"
URL_DEEPMERGE = f"https://github.com/advisories/{GHSA_DEEPMERGE}"
URL_OTHER = "https://github.com/advisories/GHSA-xxxx-yyyy-zzzz"
URL_GLOB = "https://github.com/advisories/GHSA-3g3m-2fjr-6pw4"
URL_JSYAML = "https://github.com/advisories/GHSA-2883-xcg3-v3hh"
URL_ROUTER = "https://github.com/advisories/GHSA-qwww-vcr4-c8h2"


def _advisory(url: str, name: str = "x") -> dict:
    return {
        "source": 1,
        "name": name,
        "dependency": name,
        "title": "t",
        "url": url,
        "severity": "low",
    }


def _deepmerge_vuln() -> dict:
    return {"deepmerge": {"name": "deepmerge", "via": [_advisory(URL_DEEPMERGE, "deepmerge")]}}


def _chain_vulns() -> dict:
    return {
        "@rollup/plugin-node-resolve": {
            "name": "@rollup/plugin-node-resolve",
            "via": ["deepmerge"],
        },
        "workbox-build": {"name": "workbox-build", "via": ["@rollup/plugin-node-resolve"]},
        "vite-plugin-pwa": {"name": "vite-plugin-pwa", "via": ["workbox-build"]},
    }


def _lock_ok() -> dict:
    return {
        "node_modules/deepmerge": {"version": "4.3.1", "dev": True},
        "node_modules/@rollup/plugin-node-resolve": {
            "version": "16.0.3",
            "dev": True,
            "dependencies": {"deepmerge": "^4.2.2"},
        },
    }


def _all_vulns() -> dict:
    return {**_deepmerge_vuln(), **_chain_vulns()}


def _run(
    tmp_path: Path,
    vulns: dict,
    lock_packages: dict,
    today: str | None = "2026-11-06",
    src_files: dict[str, str] | None = None,
    root_meta: dict | None = None,
    audit: dict | None = None,
) -> subprocess.CompletedProcess:
    lock = {
        "lockfileVersion": 3,
        "packages": {"": root_meta or {"name": "lumogis-web"}, **lock_packages},
    }
    (tmp_path / "package-lock.json").write_text(json.dumps(lock))
    for rel, text in (src_files or {"src/main.tsx": 'import React from "react";\n'}).items():
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
    audit_path = tmp_path / "audit.json"
    audit_path.write_text(json.dumps(audit if audit is not None else {"vulnerabilities": vulns}))
    cmd = [sys.executable, str(SCRIPT), str(tmp_path), str(audit_path)]
    if today is not None:
        cmd += ["--today", today]
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def _remaining(r: subprocess.CompletedProcess) -> list[str]:
    marker = "still reports vulnerabilities after allowlist: "
    for line in r.stderr.splitlines():
        if marker in line:
            return line.split(marker, 1)[1].split(", ")
    return []


# --- positive -------------------------------------------------------------------------------


def test_clean_report_exits_zero(tmp_path):
    assert _run(tmp_path, {}, {}).returncode == 0


def test_deepmerge_chain_allowlisted(tmp_path):
    r = _run(tmp_path, _all_vulns(), _lock_ok())
    assert r.returncode == 0, r.stderr
    assert "allowlisted GHSA-gcqx-w48m-w2h4 on deepmerge@4.3.1 (review by 2027-02-04)" in r.stderr


def test_review_by_boundary_inclusive(tmp_path):
    assert _run(tmp_path, _all_vulns(), _lock_ok(), today="2027-02-04").returncode == 0


# --- negative -------------------------------------------------------------------------------


def test_expired_entry_fails(tmp_path):
    r = _run(tmp_path, _all_vulns(), _lock_ok(), today="2027-02-05")
    assert r.returncode == 1
    assert "review_by 2027-02-04 passed" in r.stderr
    assert "deepmerge" in _remaining(r)


def test_newer_deepmerge_still_flagged_fails(tmp_path):
    lock = _lock_ok()
    lock["node_modules/deepmerge"]["version"] = "4.3.2"
    r = _run(tmp_path, _all_vulns(), lock)
    assert r.returncode == 1
    assert "NOT applied" in r.stderr
    assert "above the allowlisted maximum" in r.stderr


@pytest.mark.parametrize("dev_meta", [{}, {"dev": False}])
def test_non_dev_deepmerge_fails(tmp_path, dev_meta):
    lock = _lock_ok()
    lock["node_modules/deepmerge"] = {"version": "4.3.1", **dev_meta}
    r = _run(tmp_path, _all_vulns(), lock)
    assert r.returncode == 1
    assert "NOT applied" in r.stderr
    assert "not dev-only" in r.stderr


def test_unexpected_dependent_fails(tmp_path):
    lock = _lock_ok()
    lock["node_modules/some-runtime-lib"] = {
        "version": "1.0.0",
        "dependencies": {"deepmerge": "^4"},
    }
    r = _run(tmp_path, _all_vulns(), lock)
    assert r.returncode == 1
    assert "some-runtime-lib" in r.stderr


def test_extra_advisory_on_deepmerge_fails(tmp_path):
    vulns = _all_vulns()
    vulns["deepmerge"]["via"].append(_advisory(URL_OTHER, "deepmerge"))
    r = _run(tmp_path, vulns, _lock_ok())
    assert r.returncode == 1
    assert "NOT applied" in r.stderr
    assert "reported for something other than" in r.stderr


def test_chain_package_with_own_advisory_not_popped(tmp_path):
    vulns = _all_vulns()
    vulns["workbox-build"]["via"] = [
        "@rollup/plugin-node-resolve",
        _advisory(URL_OTHER, "workbox-build"),
    ]
    r = _run(tmp_path, vulns, _lock_ok())
    assert r.returncode == 1
    assert "workbox-build" in _remaining(r)


def test_unrelated_vuln_remains(tmp_path):
    vulns = _all_vulns()
    vulns["js-yaml"] = {"name": "js-yaml", "via": [_advisory(URL_JSYAML, "js-yaml")]}
    r = _run(tmp_path, vulns, _lock_ok())
    assert r.returncode == 1
    assert _remaining(r) == ["js-yaml"]


def test_react_router_no_longer_allowlisted(tmp_path):
    vulns = {
        "react-router": {"name": "react-router", "via": [_advisory(URL_ROUTER, "react-router")]}
    }
    lock = {"node_modules/react-router": {"version": "7.18.5"}}
    r = _run(tmp_path, vulns, lock)
    assert r.returncode == 1
    assert _remaining(r) == ["react-router"]


def test_missing_vulnerabilities_key_fails_closed(tmp_path):
    r = _run(tmp_path, {}, {}, audit={"error": {"code": "ENOAUDIT", "summary": "boom"}})
    assert r.returncode == 1


def test_nested_deepmerge_instance_checked(tmp_path):
    lock = _lock_ok()
    lock["node_modules/foo/node_modules/deepmerge"] = {"version": "4.3.2", "dev": True}
    r = _run(tmp_path, _all_vulns(), lock)
    assert r.returncode == 1
    assert "node_modules/foo/node_modules/deepmerge" in r.stderr


def test_chain_parent_with_other_path_kept(tmp_path):
    vulns = _all_vulns()
    vulns["workbox-build"]["via"] = ["@rollup/plugin-node-resolve", "glob"]
    vulns["glob"] = {"name": "glob", "via": [_advisory(URL_GLOB, "glob")]}
    r = _run(tmp_path, vulns, _lock_ok())
    assert r.returncode == 1
    assert _remaining(r) == ["glob", "vite-plugin-pwa", "workbox-build"]
    assert "allowlisted GHSA-gcqx-w48m-w2h4" in r.stderr


def test_non_chain_parent_of_package_is_not_popped(tmp_path):
    # A package outside entry.chain whose whole `via` is popped names must stay in the report.
    vulns = _all_vulns()
    vulns["other-consumer"] = {"name": "other-consumer", "via": ["deepmerge"]}
    r = _run(tmp_path, vulns, _lock_ok())
    assert r.returncode == 1
    assert _remaining(r) == ["other-consumer"]


def test_string_via_on_package_itself_fails(tmp_path):
    vulns = _all_vulns()
    vulns["deepmerge"]["via"] = [_advisory(URL_DEEPMERGE, "deepmerge"), "workbox-build"]
    r = _run(tmp_path, vulns, _lock_ok())
    assert r.returncode == 1
    assert "NOT applied" in r.stderr
    assert "reported for something other than" in r.stderr


def test_dev_optional_deepmerge_fails(tmp_path):
    lock = _lock_ok()
    lock["node_modules/deepmerge"] = {"version": "4.3.1", "devOptional": True}
    r = _run(tmp_path, _all_vulns(), lock)
    assert r.returncode == 1
    assert "require_dev" in r.stderr


@pytest.mark.parametrize(
    "line",
    [
        'import merge from "deepmerge";',
        "import merge from 'deepmerge';",
        'import "deepmerge/dist/cjs.js";',
        'const m = await import("deepmerge");',
        'const m = require("deepmerge");',
        'export { default } from "deepmerge";',
    ],
)
def test_src_import_of_package_fails(tmp_path, line):
    r = _run(tmp_path, _all_vulns(), _lock_ok(), src_files={"src/lib/merge.ts": line + "\n"})
    assert r.returncode == 1
    assert "imported from src/" in r.stderr


def test_src_import_of_lookalike_package_does_not_trip(tmp_path):
    r = _run(
        tmp_path,
        _all_vulns(),
        _lock_ok(),
        src_files={"src/lib/merge.ts": 'import x from "deepmerge-ts";\n'},
    )
    assert r.returncode == 0, r.stderr


def test_package_absent_from_lock_fails(tmp_path):
    lock = _lock_ok()
    del lock["node_modules/deepmerge"]
    r = _run(tmp_path, _all_vulns(), lock)
    assert r.returncode == 1
    assert "not found in package-lock.json" in r.stderr


def test_root_dev_dependency_on_package_fails(tmp_path):
    r = _run(
        tmp_path, _all_vulns(), _lock_ok(), root_meta={"devDependencies": {"deepmerge": "^4.3.1"}}
    )
    assert r.returncode == 1
    assert "<root>" in r.stderr


def test_missing_src_dir_fails_closed(tmp_path):
    # Build the fixture without a src/ tree: the entry must not pass vacuously.
    lock = {"lockfileVersion": 3, "packages": {"": {}, **_lock_ok()}}
    (tmp_path / "package-lock.json").write_text(json.dumps(lock))
    (tmp_path / "audit.json").write_text(json.dumps({"vulnerabilities": _all_vulns()}))
    r = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            str(tmp_path),
            str(tmp_path / "audit.json"),
            "--today",
            "2026-11-06",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode != 0


def test_today_override_prints_notice(tmp_path):
    r = _run(tmp_path, {}, {})
    assert "--today override in effect (tests only)" in r.stderr
    bad = _run(tmp_path, {}, {}, today="2026-13-01")
    assert bad.returncode == 2
    assert "invalid --today value" in bad.stderr


def test_without_today_flag_no_notice(tmp_path):
    # audit_local.sh never passes --today; the notice must not appear in that path.
    r = _run(tmp_path, {}, {}, today=None)
    assert r.returncode == 0
    assert "--today override" not in r.stderr
