#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Lumogis doctor — client-IP trust behind Caddy (LUM-901, follow-up of LUM-887).
# Prints TSV rows in the network category; always exits 0.
#
# Rows:
#   caddy-gateway          the compose gateway, read from Caddy's default route
#   caddy-trusted-proxies  whether LUMOGIS_CADDY_TRUSTED_PROXIES is a value Caddy
#                          accepts, and whether it includes that gateway
#
# Read-only: only `docker compose exec` into a running Caddy (`ip route`,
# `caddy validate` with the configured value passed as -e). Never `run`, never
# a restart. See docs/deployment/remote-access.md and ADR 246 / ADR 253.
set -euo pipefail

ROOT="${LUMOGIS_REPO_ROOT:?}"
export ROOT
export PS_CACHE="${DOCTOR_PS_CACHE:?}"
export CFG_CACHE="${DOCTOR_CONFIG_CACHE:?}"
export PS_EC="${DOCTOR_PS_EC:-1}"
export CFG_EC="${DOCTOR_CONFIG_EC:-1}"

exec python3 - <<'PY'
import ipaddress
import json
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(os.environ["ROOT"])
PS_CACHE = Path(os.environ["PS_CACHE"])
CFG_CACHE = Path(os.environ["CFG_CACHE"])
PS_EC = int(os.environ.get("PS_EC", "1") or "1")
CFG_EC = int(os.environ.get("CFG_EC", "1") or "1")

KNOB = "LUMOGIS_CADDY_TRUSTED_PROXIES"
# Same default as docker-compose.yml and docker/caddy/Caddyfile.
DEFAULT_VALUE = "127.0.0.1/32"
# Caddy's `private_ranges` shorthand (caddyhttp.PrivateRangesCIDR).
PRIVATE_RANGES = (
    "192.168.0.0/16",
    "172.16.0.0/12",
    "10.0.0.0/8",
    "127.0.0.1/8",
    "fd00::/8",
    "::1",
)
VALIDATE_CMD = "docker compose run --no-deps --rm caddy caddy validate --config /etc/caddy/Caddyfile"
APPLY_CMD = "docker compose up -d caddy"


def row(name: str, status: str, message: str, remediation: str = "") -> None:
    def clean(s: str) -> str:
        return s.replace("\t", " ").replace("\n", " ").replace("\r", " ")

    print(f"network\t{name}\t{status}\t{clean(message)}\t{clean(remediation)}")


def caddy_running() -> bool | None:
    """True/False from the cached `docker compose ps`; None when unknown."""
    if PS_EC != 0:
        return None
    raw = PS_CACHE.read_text(encoding="utf-8", errors="replace").strip()
    if not raw:
        return False
    try:
        if raw.startswith("["):
            ents = json.loads(raw)
        else:
            ents = [json.loads(ln) for ln in raw.splitlines() if ln.strip()]
    except ValueError:
        return None
    for ent in ents:
        if not isinstance(ent, dict):
            continue
        if (ent.get("Service") or ent.get("service")) == "caddy":
            return str(ent.get("State") or ent.get("state") or "").lower() == "running"
    return False


def configured_value() -> tuple[str | None, str]:
    """(value, source). value None means compose config has no caddy service."""
    if CFG_EC == 0:
        try:
            cfg = json.loads(CFG_CACHE.read_text(encoding="utf-8", errors="replace"))
        except ValueError:
            cfg = None
        if isinstance(cfg, dict):
            services = cfg.get("services") or {}
            caddy = services.get("caddy") if isinstance(services, dict) else None
            if not isinstance(caddy, dict):
                return None, "compose config"
            env = caddy.get("environment") or {}
            if isinstance(env, list):  # ["KEY=value", ...]
                env = dict(e.split("=", 1) for e in env if isinstance(e, str) and "=" in e)
            val = env.get(KNOB) if isinstance(env, dict) else None
            if val is None or not str(val).strip():
                return DEFAULT_VALUE, "default"
            return str(val), "compose config"
    val = os.environ.get(KNOB, "")
    if val.strip():
        return val, "environment"
    return DEFAULT_VALUE, "default"


_BITS = re.compile(r"^(0|[1-9][0-9]{0,2})$")


def parse_token(tok: str) -> list:
    """Parse one trusted_proxies token the way Caddy's static source does
    (netip.ParsePrefix / ParseAddr, plus `private_ranges`). Raises ValueError."""
    if tok == "private_ranges":
        return [ipaddress.ip_network(r, strict=False) for r in PRIVATE_RANGES]
    if "%" in tok:
        raise ValueError("zoned addresses are not accepted")
    if "/" in tok:
        addr_s, bits_s = tok.split("/", 1)
        addr = ipaddress.ip_address(addr_s)
        if not _BITS.match(bits_s) or int(bits_s) > addr.max_prefixlen:
            raise ValueError("bad prefix length")
        return [ipaddress.ip_network(f"{addr}/{bits_s}", strict=False)]
    addr = ipaddress.ip_address(tok)
    return [ipaddress.ip_network(f"{addr}/{addr.max_prefixlen}")]


def parse_value(value: str) -> tuple[list, str | None]:
    """(networks, error). Caddy splits the substituted value on whitespace."""
    nets: list = []
    for tok in value.split():
        try:
            nets.extend(parse_token(tok))
        except ValueError:
            hint = " (commas are not separators; use spaces)" if "," in tok else ""
            return [], f"'{tok}' is not an IP address or CIDR range{hint}"
    return nets, None


def compose_exec(*argv: str, timeout: int = 20) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            ["timeout", f"{timeout}s", "docker", "compose", "exec", "-T", *argv],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=timeout + 5,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except (subprocess.TimeoutExpired, OSError):
        return 124, "", ""


def parse_gateway(route_output: str):
    """The address after `via` on the `default` line. BusyBox `ip route show
    default` also prints the connected subnet line, which is not the gateway."""
    for line in route_output.splitlines():
        parts = line.split()
        if not parts or parts[0] != "default" or "via" not in parts:
            continue
        i = parts.index("via")
        if i + 1 < len(parts):
            try:
                return ipaddress.ip_address(parts[i + 1])
            except ValueError:
                continue
    return None


def is_docker_desktop() -> bool:
    try:
        proc = subprocess.run(
            ["timeout", "10s", "docker", "info", "--format", "{{.OperatingSystem}}"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return proc.returncode == 0 and "docker desktop" in proc.stdout.lower()


def last_line(text: str, limit: int = 200) -> str:
    """Caddy logs JSON lines; prefer the `msg` (or `error`) of the last one."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return ""
    last = lines[-1]
    try:
        obj = json.loads(last)
    except ValueError:
        obj = None
    if isinstance(obj, dict):
        last = str(obj.get("error") or obj.get("msg") or last)
    return last[-limit:]


def main() -> int:
    value, source = configured_value()
    if value is None:
        row("caddy-gateway", "skipped", "no caddy service in the compose config")
        row("caddy-trusted-proxies", "skipped", "no caddy service in the compose config")
        return 0

    running = caddy_running()
    shown = value.strip()

    # 1. Gateway (needs a running Caddy).
    gateway = None
    if running:
        ec, out, _err = compose_exec("caddy", "ip", "route", "show", "default")
        gateway = parse_gateway(out) if ec == 0 else None
        if gateway is not None:
            row(
                "caddy-gateway",
                "ok",
                f"compose gateway is {gateway} (default route inside the caddy container; "
                "it can change when the compose network is recreated)",
            )
        else:
            row(
                "caddy-gateway",
                "skipped",
                f"could not read the default route inside the caddy container (exit {ec})",
                "Run: docker compose exec caddy ip route show default (the gateway is the address after `via`).",
            )
    else:
        row("caddy-gateway", "skipped", "caddy is not running; the gateway is read from inside it")

    # 2. Does Caddy accept the value? A refused value keeps Caddy (and the web UI) down.
    nets, err = parse_value(value)
    fix_syntax = (
        f'Use space-separated CIDRs, quoted, e.g. {KNOB}="172.18.0.1/32" in .env; '
        f"check with: {VALIDATE_CMD}"
    )
    if err:
        row(
            "caddy-trusted-proxies",
            "error",
            f"{KNOB}={shown!r}: {err}. Caddy refuses this value and does not start, so the web UI is down",
            fix_syntax,
        )
        return 0
    validated = "parses"
    if running:
        ec, _out, verr = compose_exec(
            "-e", f"{KNOB}={value}", "caddy", "caddy", "validate", "--config", "/etc/caddy/Caddyfile",
            timeout=30,
        )
        if ec == 0:
            validated = "passes caddy validate"
        elif ec == 1:
            row(
                "caddy-trusted-proxies",
                "error",
                f"caddy validate refuses the config with {KNOB}={shown!r}: {last_line(verr)}. "
                "Caddy will not start with it",
                fix_syntax,
            )
            return 0
        else:
            validated = f"parses (caddy validate not run, exit {ec})"

    # 3. Does it include the current gateway?
    if gateway is None:
        why = "caddy is not running" if not running else "the gateway is unknown"
        row(
            "caddy-trusted-proxies",
            "ok",
            f"{KNOB}={shown!r} ({source}) {validated}; gateway match not checked ({why})",
        )
        return 0

    desktop = is_docker_desktop()
    covering = [n for n in nets if gateway.version == n.version and gateway in n]
    upstream = [n for n in nets if not n.subnet_of(ipaddress.ip_network("127.0.0.0/8" if n.version == 4 else "::1/128"))]

    if covering and desktop:
        row(
            "caddy-trusted-proxies",
            "warn",
            f"{KNOB}={shown!r} trusts the compose gateway {gateway} on Docker Desktop, where every "
            "LAN client arrives through the gateway: any device can choose the IP its login "
            "rate limit keys on",
            f"Remove {KNOB} from .env (the default trusts no upstream), then {APPLY_CMD}.",
        )
    elif covering:
        wide = [str(n) for n in covering if n.prefixlen < n.max_prefixlen]
        extra = f" (via {', '.join(wide)}, wider than the gateway alone)" if wide else ""
        row(
            "caddy-trusted-proxies",
            "ok",
            f"{KNOB}={shown!r} {validated} and includes the compose gateway {gateway}{extra}: "
            "devices behind Tailscale Serve or a proxy on this host keep their own rate-limit buckets",
        )
    elif not upstream:
        hint = (
            "On Docker Desktop do not set it to the gateway: every LAN client arrives through it."
            if desktop
            else f"Behind Tailscale Serve, a tunnel on this host or a host proxy, every device shares "
            f"one login rate-limit bucket until you set {KNOB}={gateway}/32"
        )
        row(
            "caddy-trusted-proxies",
            "ok",
            f"{KNOB}={shown!r} ({source}) {validated}: Caddy trusts no upstream proxy, right for a "
            f"LAN install. {hint}",
        )
    else:
        row(
            "caddy-trusted-proxies",
            "warn",
            f"{KNOB}={shown!r} does not include the current compose gateway {gateway}. If it was set "
            "for Tailscale Serve, a tunnel or a proxy on this host, it is stale (the gateway changes "
            "when the compose network is recreated) and every device shares one login rate-limit bucket",
            f'Set {KNOB}="{gateway}/32" in .env, then {APPLY_CMD}. Ignore this if the value names a '
            "proxy on another machine.",
        )
    # Not checked here (LUM-901 scope): whether the running Caddy still has an
    # older value than .env (edited without `up -d caddy`); rootless Docker,
    # whose port driver may also hide client addresses behind one peer.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
PY
