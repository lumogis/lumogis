# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for the process-wide egress ceiling (LUM-571).

In-process only — these tests NEVER call the real ``tethered.activate()`` (a
locked, permanent audit hook would poison the whole pytest interpreter). The
``activate_ceiling()`` kwargs test uses a fake ``tethered`` module that installs
no hook; the real locked hook is exercised only in
``test_egress_ceiling_subprocess.py`` via throwaway child interpreters.
"""

from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import features
import pytest
import yaml

import config
from services import egress_guard as eg


class _PrivacyFakeStore:
    """Minimal metadata-store stand-in driving ``effective_privacy_mode``."""

    def __init__(self) -> None:
        self.app_settings: dict[str, str] = {}
        self.privacy_user: dict[str, str] = {}

    def ping(self) -> bool:
        return True

    def execute(self, query: str, params: tuple | None = None) -> None:
        q = " ".join(query.lower().split())
        params = params or ()
        if "insert into app_settings" in q or "on conflict" in q:
            self.app_settings[params[0]] = params[1]

    def fetch_one(self, query: str, params: tuple | None = None) -> dict | None:
        q = " ".join(query.lower().split())
        params = params or ()
        if "from app_settings" in q:
            key = params[0] if params else None
            val = self.app_settings.get(key)
            return {"value": val} if val is not None else None
        if "from privacy_user_settings" in q:
            key = params[0] if params else None
            val = self.privacy_user.get(key)
            return {"restriction": val} if val is not None else None
        return None

    def fetch_all(self, query: str, params: tuple | None = None) -> list:
        return []

    def close(self) -> None:
        pass

    @contextmanager
    def transaction(self):
        yield


_FAKE_MODELS = {
    "claude": {"adapter": "anthropic", "api_key_env": "ANTHROPIC_API_KEY"},
    "chatgpt": {"adapter": "openai", "api_key_env": "OPENAI_API_KEY"},
    "llama": {"adapter": "openai", "base_url": "http://ollama:11434/v1"},
}


@pytest.fixture(autouse=True)
def _reset_ceiling_state():
    """Reset the module-global ceiling state after every test.

    ``test_ceiling_activate_passes_exact_kwargs`` runs the *real*
    ``activate_ceiling()`` body (with a fake spy ``tethered.activate``), which
    sets ``_ceiling_active = True``. Without this reset it would leak into the
    later fail-closed test and short-circuit it into a silent no-op (deterministic
    — this repo has no pytest-randomly, so tests run in file-declaration order).
    """
    yield
    eg._ceiling_active = False
    eg._CEILING_LOCK_TOKEN = object()


@pytest.fixture
def ceiling_env(monkeypatch):
    store = _PrivacyFakeStore()
    monkeypatch.setitem(config._instances, "metadata_store", store)
    monkeypatch.setattr(config, "get_all_models_config", lambda: dict(_FAKE_MODELS))
    monkeypatch.setattr(
        config,
        "is_local_model",
        lambda name: "ollama" in (_FAKE_MODELS.get(name, {}).get("base_url") or "").lower(),
    )
    monkeypatch.setattr(config, "_dynamic_ollama_models", lambda: {})
    monkeypatch.setattr("auth.auth_enabled", lambda: False)
    monkeypatch.setenv("POSTGRES_HOST", "postgres")
    monkeypatch.setenv("QDRANT_URL", "http://qdrant:6333")
    monkeypatch.setenv("OLLAMA_URL", "http://ollama:11434")
    # No mailboxes / proxy / infra hosts unless a test sets them.
    monkeypatch.setattr("services.email_egress_acl.household_imap_hosts", lambda: [])
    for var in (
        "LUMOGIS_IMAP_EGRESS_PROXY",
        "CAPABILITY_SERVICE_URLS",
        "STACK_CONTROL_URL",
        "STT_SIDECAR_URL",
        "CALENDAR_CALDAV_URL",
    ):
        monkeypatch.delenv(var, raising=False)
    eg._dynamic_ollama_cache = None
    eg._dynamic_ollama_cached_at = 0.0
    yield store


class _Conn:
    def __init__(self, host: str, use_ssl: bool = True, port: int = 993) -> None:
        self.host = host
        self.use_ssl = use_ssl
        self.port = port


# --- flag / enable ---------------------------------------------------------


def test_ceiling_disabled_by_default(monkeypatch):
    monkeypatch.delenv("LUMOGIS_FF_EGRESS_GUARD_CEILING", raising=False)
    assert eg.egress_ceiling_enabled() is False


def test_ceiling_enabled_when_flag_set(monkeypatch):
    monkeypatch.setenv("LUMOGIS_FF_EGRESS_GUARD_CEILING", "true")
    assert eg.egress_ceiling_enabled() is True


def test_flag_registered_in_features():
    flag = features.get_flag("EGRESS_GUARD_CEILING")
    assert flag.default is False
    assert features.is_enabled("EGRESS_GUARD_CEILING") is False


# --- superset builder ------------------------------------------------------


def test_ceiling_allowlist_superset_of_scope(ceiling_env):
    ceiling_env.app_settings["privacy_mode"] = "allow_cloud"
    scope = eg.build_allowlist(user_id=None, for_ceiling=False)
    ceiling = eg.build_allowlist(user_id=None, for_ceiling=True)
    assert scope <= ceiling


def test_ceiling_includes_household_imap_hosts(ceiling_env, monkeypatch):
    monkeypatch.setattr(
        "services.email_egress_acl.household_imap_hosts",
        lambda: [("imap.mail.test", 993)],
    )
    ceiling = eg.build_allowlist(user_id=None, for_ceiling=True)
    scope = eg.build_allowlist(user_id=None, for_ceiling=False)
    assert "imap.mail.test" in ceiling
    assert "imap.mail.test" not in scope


def test_ceiling_includes_oauth_token_host_when_client_id_set(ceiling_env, monkeypatch):
    # LUM-686: a configured OAuth client_id folds the provider's token host into
    # the ceiling so an on-demand refresh isn't self-blocked; it's ceiling-only.
    monkeypatch.setenv("LUMOGIS_OAUTH_GOOGLE_CLIENT_ID", "cid-123")
    monkeypatch.delenv("LUMOGIS_OAUTH_MICROSOFT_CLIENT_ID", raising=False)
    ceiling = eg.build_allowlist(user_id=None, for_ceiling=True)
    scope = eg.build_allowlist(user_id=None, for_ceiling=False)
    assert "oauth2.googleapis.com" in ceiling
    assert "oauth2.googleapis.com" not in scope
    assert "login.microsoftonline.com" not in ceiling  # no client_id → not folded


def test_ceiling_excludes_oauth_token_host_when_no_client_id(ceiling_env, monkeypatch):
    monkeypatch.delenv("LUMOGIS_OAUTH_GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("LUMOGIS_OAUTH_MICROSOFT_CLIENT_ID", raising=False)
    ceiling = eg.build_allowlist(user_id=None, for_ceiling=True)
    assert "oauth2.googleapis.com" not in ceiling


def test_ceiling_superset_of_imap_target_host_direct(ceiling_env, monkeypatch):
    """No proxy → the IMAP scope target is the mail host, and it's in the ceiling."""
    monkeypatch.setattr(
        "services.email_egress_acl.household_imap_hosts",
        lambda: [("imap.mail.test", 993)],
    )
    conn = _Conn("imap.mail.test", use_ssl=True)
    target = eg.imap_egress_target_host(conn)
    assert target == "imap.mail.test"
    assert target in eg.build_allowlist(user_id=None, for_ceiling=True)


def test_ceiling_superset_of_imap_target_host_proxy(ceiling_env, monkeypatch):
    """Proxy set (P0 regression) → the scope target is the PROXY host; ceiling covers it."""
    monkeypatch.setattr(
        "services.email_egress_acl.household_imap_hosts",
        lambda: [("imap.mail.test", 993)],
    )
    monkeypatch.setenv("LUMOGIS_IMAP_EGRESS_PROXY", "egress-proxy.test:3128")
    conn = _Conn("imap.mail.test", use_ssl=True)
    target = eg.imap_egress_target_host(conn)
    assert target == "egress-proxy.test"
    assert target in eg.build_allowlist(user_id=None, for_ceiling=True)


def test_ceiling_covers_real_target_for_proxy_ineligible_tls(ceiling_env, monkeypatch):
    """TLS mailbox on a nonstandard port + proxy: the real dialer takes the DIRECT
    path (mail host). The ceiling must cover the mail host regardless of what the
    imperfect ``imap_egress_target_host`` oracle predicts (Sonnet D2.2)."""
    monkeypatch.setattr(
        "services.email_egress_acl.household_imap_hosts",
        lambda: [("imap.mail.test", 1993)],
    )
    monkeypatch.setenv("LUMOGIS_IMAP_EGRESS_PROXY", "egress-proxy.test:3128")
    ceiling = eg.build_allowlist(user_id=None, for_ceiling=True)
    # The mail host (the actual direct target for a proxy-ineligible host) is folded.
    assert "imap.mail.test" in ceiling
    # LUM-638: imap_egress_target_host must ALSO predict the direct target (mail
    # host), not the proxy, for a proxy-ineligible (nonstandard-port) TLS conn.
    conn = _Conn("imap.mail.test", use_ssl=True, port=1993)
    assert eg.imap_egress_target_host(conn) == "imap.mail.test"


def test_ceiling_includes_infra_hosts(ceiling_env, monkeypatch):
    monkeypatch.setenv("CAPABILITY_SERVICE_URLS", "http://lumogis-graph:8100, http://cap2:9000")
    monkeypatch.setenv("STACK_CONTROL_URL", "http://stack-control:9000")
    monkeypatch.setenv("STT_SIDECAR_URL", "http://whisper-sidecar:9002")
    monkeypatch.setenv("CALENDAR_CALDAV_URL", "https://caldav.mail.test/dav")
    ceiling = eg.build_allowlist(user_id=None, for_ceiling=True)
    scope = eg.build_allowlist(user_id=None, for_ceiling=False)
    for host in ("lumogis-graph", "cap2", "stack-control", "whisper-sidecar", "caldav.mail.test"):
        assert host in ceiling, host
        assert host not in scope, host


def test_ceiling_excludes_cloud_when_local_only(ceiling_env):
    ceiling = eg.build_allowlist(user_id=None, for_ceiling=True)
    assert "api.anthropic.com" not in ceiling
    assert "api.openai.com" not in ceiling


def test_ceiling_includes_cloud_when_allow_cloud(ceiling_env):
    ceiling_env.app_settings["privacy_mode"] = "allow_cloud"
    ceiling = eg.build_allowlist(user_id=None, for_ceiling=True)
    assert "api.anthropic.com" in ceiling
    assert "api.openai.com" in ceiling


def test_ceiling_host_normalised(ceiling_env, monkeypatch):
    """Trailing dot + mixed case are normalised to the dialed form."""
    monkeypatch.setattr(
        "services.email_egress_acl.household_imap_hosts",
        lambda: [("IMAP.Mail.Test.", 993)],
    )
    ceiling = eg.build_allowlist(user_id=None, for_ceiling=True)
    assert "imap.mail.test" in ceiling


def test_ceiling_extra_hosts_swallows_enumeration_error(ceiling_env, monkeypatch):
    def _boom():
        raise RuntimeError("bad user store")

    monkeypatch.setattr("services.email_egress_acl.household_imap_hosts", _boom)
    monkeypatch.setenv("STACK_CONTROL_URL", "http://stack-control:9000")
    ceiling = eg.build_allowlist(user_id=None, for_ceiling=True)  # must not raise
    assert "stack-control" in ceiling


def test_ceiling_extra_hosts_swallows_bad_proxy_config(ceiling_env, monkeypatch):
    monkeypatch.setattr(
        "services.email_egress_acl.household_imap_hosts",
        lambda: [("imap.mail.test", 993)],
    )
    monkeypatch.setenv("LUMOGIS_IMAP_EGRESS_PROXY", "not a valid proxy value")
    ceiling = eg.build_allowlist(user_id=None, for_ceiling=True)  # must not raise
    assert "imap.mail.test" in ceiling


# --- activate_ceiling() (fake tethered — no real hook) ---------------------


def _install_fake_tethered(monkeypatch):
    calls: list[dict] = []

    def _fake_activate(**kwargs):
        calls.append(kwargs)

    fake = SimpleNamespace(activate=_fake_activate)
    monkeypatch.setitem(sys.modules, "tethered", fake)
    return calls


def test_ceiling_activate_passes_exact_kwargs(ceiling_env, monkeypatch):
    monkeypatch.setenv("LUMOGIS_FF_EGRESS_GUARD_CEILING", "true")
    calls = _install_fake_tethered(monkeypatch)

    eg.activate_ceiling()

    assert len(calls) == 1
    kw = calls[0]
    assert kw["locked"] is True
    assert kw["lock_token"] is eg._CEILING_LOCK_TOKEN
    assert kw["allow_localhost"] is True
    # LUM-637: block, not allow, when tethered's own policy check raises. The real
    # behaviour is proven on the locked hook in test_egress_ceiling_subprocess.py.
    assert kw["fail_closed"] is True
    assert kw["external_subprocess_policy"] == "warn"
    assert kw["allow"] == sorted(eg.build_allowlist(user_id=None, for_ceiling=True))
    assert eg._ceiling_active is True


def test_ceiling_activate_noop_when_disabled(ceiling_env, monkeypatch):
    monkeypatch.delenv("LUMOGIS_FF_EGRESS_GUARD_CEILING", raising=False)
    calls = _install_fake_tethered(monkeypatch)
    eg.activate_ceiling()
    assert calls == []
    assert eg._ceiling_active is False


def test_ceiling_activate_idempotent(ceiling_env, monkeypatch):
    monkeypatch.setenv("LUMOGIS_FF_EGRESS_GUARD_CEILING", "true")
    calls = _install_fake_tethered(monkeypatch)
    eg.activate_ceiling()
    eg.activate_ceiling()
    assert len(calls) == 1  # second call short-circuits on _ceiling_active


def test_ceiling_activate_fail_closed_without_tethered(ceiling_env, monkeypatch):
    monkeypatch.setenv("LUMOGIS_FF_EGRESS_GUARD_CEILING", "true")
    # Belt-and-braces: install a spy so an accidental real activation is impossible,
    # then force `import tethered` to raise ImportError via a None sys.modules entry.
    calls = _install_fake_tethered(monkeypatch)
    monkeypatch.setitem(sys.modules, "tethered", None)
    with pytest.raises(RuntimeError):
        eg.activate_ceiling()
    assert calls == []
    assert eg._ceiling_active is False


# --- launchers run uvicorn on the asyncio loop (LUM-903) ---------------------
#
# tethered>=1.0 refuses activate() with RuntimeError once uvloop is imported (a
# uvloop event loop raises no audit events). uvicorn[standard] installs uvloop and
# picks it by default, so a launcher without ``--loop asyncio`` makes the ceiling
# refuse boot.

_REPO = Path(__file__).resolve().parents[2]
_DOCKERFILE = _REPO / "orchestrator" / "Dockerfile"
_DEV_COMPOSE = _REPO / "docker-compose.dev.yml"


def _loop_from_argv(argv: list[str]) -> str:
    for i, arg in enumerate(argv):
        if arg == "--loop":
            return argv[i + 1]
        if arg.startswith("--loop="):
            return arg.split("=", 1)[1]
    return "auto"


def _dockerfile_cmd() -> list[str]:
    cmds = [ln for ln in _DOCKERFILE.read_text().splitlines() if ln.startswith("CMD ")]
    assert cmds, "orchestrator/Dockerfile has no CMD"
    return json.loads(cmds[-1][len("CMD ") :])


@pytest.mark.skipif(not _DOCKERFILE.is_file(), reason="orchestrator/Dockerfile not present")
def test_dockerfile_cmd_runs_asyncio_loop():
    argv = _dockerfile_cmd()
    assert argv[:2] == ["uvicorn", "main:app"]
    assert _loop_from_argv(argv) == "asyncio"


@pytest.mark.skipif(not _DEV_COMPOSE.is_file(), reason="repo tree not present")
def test_dev_overlay_runs_asyncio_loop():
    dev = yaml.safe_load(_DEV_COMPOSE.read_text())
    assert _loop_from_argv(dev["services"]["orchestrator"]["command"]) == "asyncio"


def test_loop_from_argv_parser():
    assert _loop_from_argv(["uvicorn", "main:app"]) == "auto"
    assert _loop_from_argv(["uvicorn", "--loop", "asyncio"]) == "asyncio"
    assert _loop_from_argv(["uvicorn", "--loop=uvloop"]) == "uvloop"
