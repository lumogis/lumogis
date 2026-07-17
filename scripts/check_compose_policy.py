#!/usr/bin/env python3
"""
LUM-43: enforce that capability-tier compose services do not receive Core DB / Qdrant creds.

Pass A — raw YAML on each -f file (+ included paths): ban env_file on non-allowlisted services;
  detect forbidden env keys in environment: blocks.
Pass B — docker compose … config --format json: forbidden keys in rendered environment.

Exit codes: 0 = clean, 1 = policy violation, 2 = tool error.
Diagnostics: service name + key name only (never values).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

import yaml


def _construct_compose_reset(loader: yaml.SafeLoader, node: Any) -> None:
    """Compose merge `!reset` tag (drops inherited mapping keys); swallow for Pass A scan."""
    if isinstance(node, yaml.nodes.MappingNode):
        loader.construct_mapping(node)
    elif isinstance(node, yaml.nodes.SequenceNode):
        loader.construct_sequence(node)
    else:
        loader.construct_scalar(node)
    return None


yaml.SafeLoader.add_constructor("!reset", _construct_compose_reset)

EXIT_OK = 0
EXIT_VIOLATION = 1
EXIT_TOOL = 2

FORBIDDEN_EXACT: frozenset[str] = frozenset(
    {
        "QDRANT_URL",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_DB",
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_URL",
        "DATABASE_URL",
    }
)


def _die_tool(msg: str) -> None:
    print(msg, file=sys.stderr)
    sys.exit(EXIT_TOOL)


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def load_allowlist(path: Path) -> frozenset[str]:
    if not path.is_file():
        _die_tool(f"check_compose_policy: allowlist not found: {path}")
    names: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.split("#", 1)[0].strip()
        if s:
            names.add(s)
    return frozenset(names)


def is_forbidden_key(key: str) -> bool:
    if key in FORBIDDEN_EXACT:
        return True
    if key.startswith("AP_POSTGRES_"):
        return True
    return False


def _normalize_raw_env_keys(env_block: Any) -> list[str]:
    """Extract env var names from a compose 'environment:' value (Pass A)."""
    if env_block is None:
        return []
    if isinstance(env_block, dict):
        return [str(k) for k in env_block.keys()]
    if isinstance(env_block, list):
        keys: list[str] = []
        for item in env_block:
            if not isinstance(item, str):
                continue
            if "=" in item:
                keys.append(item.split("=", 1)[0].strip())
            else:
                keys.append(item.strip())
        return keys
    _die_tool(f"check_compose_policy: unhandled environment shape (Pass A): {type(env_block)!r}")


def _collect_include_paths(doc: dict[str, Any], base_dir: Path) -> list[Path]:
    out: list[Path] = []
    inc = doc.get("include")
    if inc is None:
        return out
    if not isinstance(inc, list):
        _die_tool("check_compose_policy: 'include' must be a list when present")
    for entry in inc:
        if isinstance(entry, dict) and "path" in entry:
            p = entry["path"]
        elif isinstance(entry, str):
            p = entry
        else:
            _die_tool(f"check_compose_policy: unsupported include entry: {entry!r}")
        out.append((base_dir / str(p)).resolve())
    return out


def _iter_compose_file_docs(
    path: Path, seen_files: set[Path]
) -> Iterable[tuple[Path, dict[str, Any]]]:
    path = path.resolve()
    if path in seen_files:
        return
    if not path.is_file():
        _die_tool(f"check_compose_policy: compose file not found: {path}")
    seen_files.add(path)
    base_dir = path.parent
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        _die_tool(f"check_compose_policy: cannot read {path}: {e}")

    for doc in yaml.safe_load_all(raw):
        if doc is None:
            continue
        if not isinstance(doc, dict):
            _die_tool(f"check_compose_policy: expected mapping at top level in {path}")
        yield path, doc
        for inc in _collect_include_paths(doc, base_dir):
            yield from _iter_compose_file_docs(inc, seen_files)


def pass_a_scan(
    compose_files: list[Path],
    allowlist: frozenset[str],
    violations: list[str],
) -> None:
    seen_files: set[Path] = set()
    for root in compose_files:
        for _src_path, doc in _iter_compose_file_docs(root, seen_files):
            services = doc.get("services")
            if services is None:
                continue
            if not isinstance(services, dict):
                _die_tool("check_compose_policy: 'services' must be a mapping when present")
            for svc_name, svc_body in services.items():
                if not isinstance(svc_body, dict):
                    continue
                if svc_name in allowlist:
                    continue
                # env_file ban on non-allowlisted services
                if "env_file" in svc_body:
                    violations.append(
                        f"Pass A: service {svc_name!r}: env_file is not allowed on non-allowlisted services"
                    )
                for key in _normalize_raw_env_keys(svc_body.get("environment")):
                    if is_forbidden_key(key):
                        violations.append(f"Pass A: service {svc_name!r}: forbidden key {key!r}")


def _normalize_rendered_env_keys(env_val: Any) -> list[str]:
    if env_val is None:
        return []
    if isinstance(env_val, dict):
        return [str(k) for k in env_val.keys()]
    if isinstance(env_val, list):
        keys: list[str] = []
        for item in env_val:
            if isinstance(item, str) and "=" in item:
                keys.append(item.split("=", 1)[0].strip())
            elif isinstance(item, str):
                keys.append(item.strip())
            elif isinstance(item, dict):
                # rare compose JSON shapes
                for k in item:
                    keys.append(str(k))
            else:
                _die_tool(
                    "check_compose_policy: unhandled JSON environment list entry "
                    f"(Pass B): {type(item)!r}"
                )
        return keys
    _die_tool(f"check_compose_policy: unhandled JSON environment shape (Pass B): {type(env_val)!r}")


def pass_b_compose_config(
    compose_files: list[Path],
    project_dir: Path,
    allowlist: frozenset[str],
    violations: list[str],
) -> None:
    cmd: list[str] = ["docker", "compose", "--project-directory", str(project_dir)]
    for f in compose_files:
        cmd.extend(["-f", str(f.resolve())])
    cmd.extend(["config", "--format", "json"])
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        _die_tool("check_compose_policy: 'docker' not found (Pass B)")
    if proc.returncode != 0:
        _die_tool(
            "check_compose_policy: docker compose config failed (Pass B):\n"
            f"{proc.stderr or proc.stdout or '(no output)'}"
        )
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        _die_tool(f"check_compose_policy: invalid JSON from docker compose config: {e}")

    services = data.get("services")
    if services is None:
        return
    if not isinstance(services, dict):
        _die_tool("check_compose_policy: rendered 'services' is not an object (Pass B)")

    for svc_name, svc_body in services.items():
        if not isinstance(svc_body, dict):
            continue
        if svc_name in allowlist:
            continue
        for key in _normalize_rendered_env_keys(svc_body.get("environment")):
            if is_forbidden_key(key):
                violations.append(f"Pass B: service {svc_name!r}: forbidden key {key!r}")


def _render_compose_json(compose_files: list[Path], project_dir: Path) -> dict[str, Any]:
    """Render the merged compose graph as JSON (shared with Pass B mechanism)."""
    cmd: list[str] = ["docker", "compose", "--project-directory", str(project_dir)]
    for f in compose_files:
        cmd.extend(["-f", str(f.resolve())])
    cmd.extend(["config", "--format", "json"])
    try:
        proc = subprocess.run(
            cmd, cwd=str(project_dir), capture_output=True, text=True, check=False
        )
    except FileNotFoundError:
        _die_tool("check_compose_policy: 'docker' not found (Pass C)")
    if proc.returncode != 0:
        _die_tool(
            "check_compose_policy: docker compose config failed (Pass C):\n"
            f"{proc.stderr or proc.stdout or '(no output)'}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        _die_tool(f"check_compose_policy: invalid JSON from docker compose config (Pass C): {e}")
    return {}  # unreachable (_die_tool raises); keeps type-checkers happy


def _service_network_names(svc_body: dict[str, Any]) -> list[str]:
    """Network names a rendered service is attached to (dict or list form)."""
    nets = svc_body.get("networks")
    if isinstance(nets, dict):
        return list(nets.keys())
    if isinstance(nets, list):
        return [str(n) for n in nets]
    return []


def pass_c_network_membership(
    compose_files: list[Path],
    project_dir: Path,
    community_services: frozenset[str],
    egress_proxy_service: str,
    violations: list[str],
) -> None:
    """LUM-618 Pass C — a community capability container must be network-contained.

    For every ``--community-service``:
      * it MUST be a member of at least one ``internal: true`` network, and
      * it MUST NOT be a member of any internet-facing (non-``internal``) network.

    "Internet-facing" is keyed on the network's ``internal`` flag (NOT a hardcoded
    name), so a future multi-isolated-network topology still validates. The
    selector is the explicit operator-supplied list (NOT "is on the internal net"
    — that would be circular with the invariant being proven).

    Also asserts the declared egress path exists: ``egress_proxy_service`` must be
    dual-homed on both an internal and an external network. (Core/orchestrator is
    legitimately dual-homed too — default + the isolated net — so we do NOT forbid
    all internal<->external membership; the load-bearing guarantee is that the
    *community service itself* has no external leg, and Docker does not forward
    between a container's networks.)
    """
    if not community_services:
        return
    data = _render_compose_json(compose_files, project_dir)
    services = data.get("services")
    networks = data.get("networks") or {}
    if not isinstance(services, dict):
        _die_tool("check_compose_policy: rendered 'services' is not an object (Pass C)")

    def is_internal(net_name: str) -> bool:
        defn = networks.get(net_name) if isinstance(networks, dict) else None
        return bool(isinstance(defn, dict) and defn.get("internal"))

    for svc_name in sorted(community_services):
        body = services.get(svc_name)
        if not isinstance(body, dict):
            violations.append(
                f"Pass C: community service {svc_name!r} not found in rendered config "
                "(is the egress overlay composed?)"
            )
            continue
        net_names = _service_network_names(body)
        internal_nets = [n for n in net_names if is_internal(n)]
        external_nets = [n for n in net_names if not is_internal(n)]
        if not internal_nets:
            violations.append(
                f"Pass C: community service {svc_name!r} is on no internal:true "
                f"network (networks={net_names or 'default'}) — not contained"
            )
        if external_nets:
            violations.append(
                f"Pass C: community service {svc_name!r} is on internet-facing "
                f"network(s) {sorted(external_nets)} — must be isolated-network only"
            )

    proxy = services.get(egress_proxy_service)
    if not isinstance(proxy, dict):
        violations.append(
            f"Pass C: egress proxy service {egress_proxy_service!r} not found "
            "(no declared egress path for the isolated network — activate the "
            "'community-egress' profile?)"
        )
    else:
        proxy_nets = _service_network_names(proxy)
        if not any(is_internal(n) for n in proxy_nets) or not any(
            not is_internal(n) for n in proxy_nets
        ):
            violations.append(
                f"Pass C: egress proxy {egress_proxy_service!r} must be dual-homed "
                f"(one internal + one external network); got {sorted(proxy_nets)}"
            )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="LUM-43 compose policy: Core DB/Qdrant credentials must not reach "
        "non-allowlisted services."
    )
    parser.add_argument(
        "-f",
        "--file",
        dest="compose_files",
        action="append",
        required=True,
        metavar="PATH",
        help="Compose file (repeat; same order as docker compose -f).",
    )
    parser.add_argument(
        "--project-directory",
        default=None,
        help="Directory passed as docker compose --project-directory (default: repo root).",
    )
    parser.add_argument(
        "--allowlist",
        default=None,
        help="Path to compose_core_allowlist.txt (default: scripts/compose_core_allowlist.txt).",
    )
    parser.add_argument(
        "--community-service",
        dest="community_services",
        action="append",
        default=[],
        metavar="NAME",
        help="LUM-618 Pass C: a community capability service that MUST be network-"
        "contained (isolated internal net, no internet-facing net). Repeatable.",
    )
    parser.add_argument(
        "--egress-proxy-service",
        default="egress-proxy",
        help="LUM-618 Pass C: the dual-homed egress proxy service name "
        "(default: egress-proxy).",
    )
    args = parser.parse_args()

    repo = _repo_root()
    project_dir = Path(args.project_directory).resolve() if args.project_directory else repo
    allowlist_path = (
        Path(args.allowlist).resolve()
        if args.allowlist
        else repo / "scripts" / "compose_core_allowlist.txt"
    )
    allowlist = load_allowlist(allowlist_path)
    compose_files: list[Path] = []
    for f in args.compose_files:
        p = Path(f).expanduser()
        compose_files.append(p.resolve() if p.is_absolute() else (project_dir / p).resolve())

    violations: list[str] = []
    pass_a_scan(compose_files, allowlist, violations)
    if violations:
        for line in violations:
            print(line, file=sys.stderr)
        sys.exit(EXIT_VIOLATION)

    pass_b_compose_config(compose_files, project_dir, allowlist, violations)
    if violations:
        for line in violations:
            print(line, file=sys.stderr)
        sys.exit(EXIT_VIOLATION)

    pass_c_network_membership(
        compose_files,
        project_dir,
        frozenset(args.community_services),
        args.egress_proxy_service,
        violations,
    )
    if violations:
        for line in violations:
            print(line, file=sys.stderr)
        sys.exit(EXIT_VIOLATION)

    sys.exit(EXIT_OK)


if __name__ == "__main__":
    main()
