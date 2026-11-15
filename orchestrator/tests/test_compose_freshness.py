# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Compose freshness check (LUM-980): is the install's docker-compose.yml as new as its images?"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from services import compose_freshness as cf

_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPT = _ROOT / "scripts" / "release" / "build_release_compose.py"


@pytest.fixture
def gen():
    spec = importlib.util.spec_from_file_location("build_release_compose", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def _clear_cache():
    cached = cf.startup_result  # held here: some tests monkeypatch the name
    cached.cache_clear()
    yield
    cached.cache_clear()


def _compose(tmp_path: Path, fingerprint: str | None) -> Path:
    path = tmp_path / "docker-compose.yml"
    header = f"# lumogis-release: 1.2.3\n# lumogis-compose: {fingerprint}\n" if fingerprint else ""
    path.write_text(header + "\nservices:\n  orchestrator: {}\n", encoding="utf-8")
    return path


def test_ok_when_fingerprints_match(tmp_path):
    result = cf.check(_compose(tmp_path, "0123456789abcdef"), image_fingerprint="0123456789abcdef")
    assert result.status == "ok"


def test_stale_when_fingerprints_differ(tmp_path):
    result = cf.check(_compose(tmp_path, "0123456789abcdef"), image_fingerprint="fedcba9876543210")
    assert result.status == "stale"
    assert result.file_fingerprint == "0123456789abcdef"
    assert result.image_fingerprint == "fedcba9876543210"


@pytest.mark.parametrize(
    "fingerprint, image_fp, why",
    [
        (None, "fedcba9876543210", "a git checkout: no header line"),
        ("0123456789abcdef", "dev", "a local build"),
        ("0123456789abcdef", "", "no fingerprint baked"),
    ],
)
def test_not_applicable(tmp_path, fingerprint, image_fp, why):
    assert cf.check(_compose(tmp_path, fingerprint), image_fingerprint=image_fp).status == (
        "not_applicable"
    ), why


def test_missing_or_unreadable_file_is_not_applicable(tmp_path):
    assert cf.check(tmp_path / "nope.yml", image_fingerprint="0123456789abcdef").status == (
        "not_applicable"
    )
    assert cf.check(tmp_path, image_fingerprint="0123456789abcdef").status == "not_applicable"


def test_reads_the_image_fingerprint_from_the_environment(tmp_path, monkeypatch):
    path = _compose(tmp_path, "0123456789abcdef")
    monkeypatch.setenv("LUMOGIS_COMPOSE_FINGERPRINT", "0123456789abcdef")
    assert cf.check(path).status == "ok"
    monkeypatch.delenv("LUMOGIS_COMPOSE_FINGERPRINT")
    assert cf.check(path).status == "not_applicable"  # unset means a local build


def test_generated_release_file_matches_the_baked_fingerprint(tmp_path, gen, capsys):
    """The header the generator writes is what publish-image.yml bakes (--fingerprint-only)."""
    assert gen.main(["--version", "1.2.3", "--out", str(tmp_path)]) == 0
    capsys.readouterr()
    assert gen.main(["--fingerprint-only"]) == 0
    baked = capsys.readouterr().out.strip()
    assert cf.check(tmp_path / "docker-compose.yml", image_fingerprint=baked).status == "ok"
    assert cf.check(_ROOT / "docker-compose.yml", image_fingerprint=baked).status == (
        "not_applicable"
    ), "the repo compose has no header, so a checkout is never told it is stale"


def test_startup_logs_stale_once(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(cf, "COMPOSE_PATH", _compose(tmp_path, "0123456789abcdef"))
    monkeypatch.setenv("LUMOGIS_COMPOSE_FINGERPRINT", "fedcba9876543210")
    with caplog.at_level("WARNING", logger=cf.__name__):
        assert cf.startup_result().status == "stale"
        assert cf.startup_result().status == "stale"
    stale = [r for r in caplog.records if "compose_file_stale" in r.getMessage()]
    assert len(stale) == 1


def test_diagnostics_warns_only_when_stale(monkeypatch):
    from services import admin_diagnostics as ad

    monkeypatch.setattr(cf, "startup_result", lambda: cf.ComposeFreshness("stale", "a", "b"))
    warnings = ad._compose_freshness_warnings()
    assert [w.code for w in warnings] == ["compose_file_stale"]
    assert "(keep your .env)" in warnings[0].message

    for status in ("ok", "not_applicable"):
        monkeypatch.setattr(cf, "startup_result", lambda s=status: cf.ComposeFreshness(s))
        assert ad._compose_freshness_warnings() == []


def test_diagnostics_survives_a_failing_check(monkeypatch):
    from services import admin_diagnostics as ad

    def boom():
        raise RuntimeError("x")

    monkeypatch.setattr(cf, "startup_result", boom)
    assert ad._compose_freshness_warnings() == []
