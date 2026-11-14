"""LUM-880: the LiteLLM overlay must publish its port on loopback only.

``docker-compose.litellm.yml`` passes ``ANTHROPIC_API_KEY`` into the proxy
and sets no master key, so a port published on every interface lets anyone
on the network spend the operator's key without logging in. Core reaches the
proxy over the compose network (``http://litellm:4000``) and needs no
published port; the loopback publish exists only for the dashboard on the
host. These tests stop a silent revert to ``"4000:4000"``.
"""

from __future__ import annotations

import yaml
from tests._compose_helpers import find_repo_root
from tests._compose_helpers import host_ip as _host_ip

_OVERLAY = find_repo_root("docker-compose.litellm.yml") / "docker-compose.litellm.yml"


def _litellm_service() -> dict:
    spec = yaml.safe_load(_OVERLAY.read_text())
    return spec["services"]["litellm"]


def test_litellm_ports_bind_loopback_only():
    ports = _litellm_service().get("ports") or []
    for entry in ports:
        assert _host_ip(entry) == "127.0.0.1", (
            f"docker-compose.litellm.yml publishes {entry!r} beyond loopback. "
            "The proxy holds ANTHROPIC_API_KEY with no master key (LUM-880); "
            "publish on 127.0.0.1 only."
        )


def test_host_ip_parser_flags_unbound_publish():
    assert _host_ip("4000:4000") is None
    assert _host_ip("0.0.0.0:4000:4000") == "0.0.0.0"
    assert _host_ip("127.0.0.1:4000:4000") == "127.0.0.1"
    assert _host_ip({"target": 4000, "published": 4000}) is None
    assert _host_ip({"target": 4000, "host_ip": "127.0.0.1"}) == "127.0.0.1"
