# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-621 — egress deny tailer unit tests."""

from __future__ import annotations
from pathlib import Path

import pytest

from services import egress_deny_tail as edt

_FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "egress_deny_access_log_sample.txt"
)


def test_parse_captured_https_and_http_deny_lines() -> None:
    lines = [
        ln
        for ln in _FIXTURE.read_text(encoding="utf-8").splitlines()
        if ln and not ln.startswith("#")
    ]
    assert len(lines) >= 2
    https = edt.parse_lumogis_egress_line(lines[0])
    assert https is not None
    assert https["src_ip"] == "172.31.0.10"
    assert https["squid_status"] == "TCP_DENIED"
    assert https["http_method"] == "CONNECT"
    assert https["dst_host"] == "example.org"
    http = edt.parse_lumogis_egress_line(lines[1])
    assert http is not None
    assert http["http_method"] == "GET"
    assert http["dst_host"] == "example.org"


def test_dst_host_from_ru_variants() -> None:
    assert edt.dst_host_from_ru("example.org:443") == "example.org"
    assert edt.dst_host_from_ru("http://Example.COM/path?q=1") == "example.com"
    assert edt.dst_host_from_ru("-") is None


def test_non_denied_line_ignored() -> None:
    line = "1.0 000001 172.31.0.10 TCP_MISS/200 GET http://example.com/ -"
    assert edt.parse_lumogis_egress_line(line) is None


def test_dedup_window(monkeypatch: pytest.MonkeyPatch) -> None:
    edt._dedup.clear()
    monkeypatch.setenv("LUMOGIS_EGRESS_DENY_DEDUP_SECONDS", "60")
    fields = {
        "src_ip": "172.31.0.10",
        "dst_host": "evil.test",
        "http_method": "CONNECT",
        "squid_status": "TCP_DENIED",
    }
    assert edt._dedup_ok(fields["src_ip"], fields["dst_host"], 60.0) is True
    assert edt._dedup_ok(fields["src_ip"], fields["dst_host"], 60.0) is False


def test_start_noop_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LUMOGIS_EGRESS_ACCESS_LOG", raising=False)
    edt.stop()
    edt.start()
    assert edt._thread is None


def test_src_map_resolves(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "map.txt"
    p.write_text("172.31.0.10 lumogis.mock.echo\n", encoding="utf-8")
    monkeypatch.setenv("LUMOGIS_EGRESS_SRC_MAP", str(p))
    mapping = edt.load_src_map(refresh=True)
    assert mapping["172.31.0.10"] == "lumogis.mock.echo"
