"""LUM-161: SPA pages whose path is also a legacy orchestrator route.

Lumogis Web serves "What Lumogis knows" at /entities and /entities/<id>, but the
orchestrator still answers JSON at /entities (GET), /entities/merge,
/entities/<id>/publish and others. Caddy (production) and the Vite dev proxy
must send browser navigations (Accept: text/html) to the SPA and everything
else to the orchestrator; otherwise opening or refreshing the page shows the
API's {"error":"missing token"}. Static guards, like test_deployment_hardening_config.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CADDYFILE = ROOT / "docker" / "caddy" / "Caddyfile"
VITE_CONFIG = ROOT / "clients" / "lumogis-web" / "vite.config.ts"

# SPA routes (App.tsx) that share a prefix with an orchestrator route.
SHARED = ["/entities", "/search", "/audit", "/settings"]


def _caddy_text() -> str:
    return CADDYFILE.read_text(encoding="utf-8")


def test_no_plain_orchestrator_handle_for_shared_spa_paths():
    text = _caddy_text()
    for path in SHARED:
        plain = re.compile(rf"^\s*handle {re.escape(path)}\*? \{{", re.M)
        assert not plain.search(text), (
            f"{path}: a plain handle would send browser navigations to the orchestrator"
        )


def test_shared_spa_paths_use_an_accept_html_matcher():
    text = _caddy_text()
    for path in SHARED:
        block = re.search(
            rf"@(\w+) \{{\s*path {re.escape(path)}\*?\s*not header Accept \*text/html\*\s*\}}",
            text,
        )
        assert block, f"{path}: no matcher excluding Accept text/html"
        name = block.group(1)
        assert re.search(rf"handle @{name} \{{\s*import to_orchestrator\s*\}}", text), (
            f"{path}: matcher not routed"
        )


def test_vite_proxy_keeps_entities_navigations_in_the_spa():
    text = VITE_CONFIG.read_text(encoding="utf-8")
    m = re.search(r'"/entities": \{(.*?)\n        \},', text, re.S)
    assert m, "vite proxy has no /entities entry with options"
    assert "text/html" in m.group(1) and '"/index.html"' in m.group(1)


def test_vite_proxy_keeps_settings_navigations_in_the_spa():
    # LUM-993 Pass 3: /settings/... is Lumogis Web's Settings in dev too.
    text = VITE_CONFIG.read_text(encoding="utf-8")
    m = re.search(r'"/settings": \{(.*?)\n        \},', text, re.S)
    assert m, "vite proxy has no /settings entry with options"
    assert "text/html" in m.group(1) and '"/index.html"' in m.group(1)
