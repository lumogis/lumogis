# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for the LUM-618 egress-ACL generator (scripts/)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_GEN_PATH = _ROOT / "scripts" / "gen_capability_egress_acl.py"


def _load_gen():
    spec = importlib.util.spec_from_file_location("gen_capability_egress_acl", _GEN_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gen = _load_gen()


def test_generate_writes_allow_file_lowercased_deduped(tmp_path) -> None:
    out = gen.generate("acme.echo", ["Example.COM", "example.com"], allow_dir=tmp_path)
    hosts = [ln for ln in out.read_text().splitlines() if ln and not ln.startswith("#")]
    assert hosts == ["example.com"]  # lowercased + deduped by the shared normaliser


@pytest.mark.parametrize(
    "bad_id",
    ["../evil", "a/b", "has space", "UPPERCASE", 'quote"here', "semi;colon", "back`tick"],
)
def test_generate_rejects_unsafe_id_writes_nothing(tmp_path, bad_id: str) -> None:
    with pytest.raises(gen.GeneratorError):
        gen.generate(bad_id, ["example.com"], allow_dir=tmp_path)
    assert list(tmp_path.iterdir()) == []  # fail-closed: no file / no ACL token derived


@pytest.mark.parametrize(
    "bad_endpoint",
    ["http://example.com", "example.com:443", "*.example.com", "::1", "exa mple.com"],
)
def test_generate_rejects_malformed_endpoints(tmp_path, bad_endpoint: str) -> None:
    with pytest.raises(gen.GeneratorError):
        gen.generate("acme.echo", [bad_endpoint], allow_dir=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_check_ok_and_divergence(tmp_path) -> None:
    gen.generate("acme.echo", ["example.com"], allow_dir=tmp_path)
    ok, _ = gen.check("acme.echo", ["example.com"], allow_dir=tmp_path)
    assert ok is True
    ok, msg = gen.check("acme.echo", ["other.com"], allow_dir=tmp_path)
    assert ok is False
    assert "only_in_manifest" in msg


def test_check_committed_mock_allow_file() -> None:
    ok, msg = gen.check("lumogis.mock.echo", ["example.com"])
    assert ok is True, msg


def test_check_cli_exit_codes(tmp_path) -> None:
    gen.generate("acme.echo", ["example.com"], allow_dir=tmp_path)
    assert (
        gen.main(
            [
                "--check",
                "--id",
                "acme.echo",
                "--endpoints",
                "example.com",
                "--allow-dir",
                str(tmp_path),
            ]
        )
        == 0
    )
    assert (
        gen.main(
            [
                "--check",
                "--id",
                "acme.echo",
                "--endpoints",
                "drift.com",
                "--allow-dir",
                str(tmp_path),
            ]
        )
        == 1
    )
