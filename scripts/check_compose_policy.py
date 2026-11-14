#!/usr/bin/env python3
"""
LUM-43: enforce that capability-tier compose services do not receive Core DB / Qdrant creds.

Pass A — raw YAML on each -f file (+ included paths): ban env_file on non-allowlisted services;
  detect forbidden env keys in environment: blocks.
  LUM-620 name-squat: a service whose name is in the allowlist may only be defined by a
  --core-file (or a file a core file includes). A non-core overlay that defines e.g.
  `backup` would otherwise inherit the allowlist's Core-credential exemption and, via
  compose merge, could override the core service's image. With no --core-file at all,
  every file is a non-core overlay (fail-closed default).
Pass B — docker compose … config --format json: forbidden keys in rendered environment.

Exit codes: 0 = clean, 1 = policy violation, 2 = tool error.
Diagnostics: service name + key name only (never values).
"""
from __future__ import annotations

import argparse
import json
import os
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
    # Parse the whole file up front: a YAML error must be a tool error (exit 2), never a
    # traceback (exit 1), which the adversarial Make targets would read as "violation caught".
    try:
        docs = list(yaml.safe_load_all(raw))
    except yaml.YAMLError as e:
        _die_tool(f"check_compose_policy: cannot parse YAML in {path}: {e}")

    for doc in docs:
        if doc is None:
            continue
        if not isinstance(doc, dict):
            _die_tool(f"check_compose_policy: expected mapping at top level in {path}")
        yield path, doc
        for inc in _collect_include_paths(doc, base_dir):
            yield from _iter_compose_file_docs(inc, seen_files)


def core_closure(core_files: Iterable[Path]) -> frozenset[Path]:
    """LUM-620: resolved paths of the core compose set — each --core-file plus every file
    it includes (transitively). Provenance is by path: a file is core iff it is in here."""
    seen_files: set[Path] = set()
    for root in core_files:
        for _ in _iter_compose_file_docs(root, seen_files):
            pass
    return frozenset(seen_files)


def _display_path(path: Path) -> str:
    try:
        return os.path.relpath(path)
    except ValueError:
        return str(path)


def pass_a_scan(
    compose_files: list[Path],
    allowlist: frozenset[str],
    violations: list[str],
    core_paths: frozenset[Path] = frozenset(),
) -> None:
    """Pass A. ``core_paths`` (see core_closure) is the core set; it defaults to empty,
    so with no core set every file is a non-core overlay (fail-closed)."""
    seen_files: set[Path] = set()
    for root in compose_files:
        for src_path, doc in _iter_compose_file_docs(root, seen_files):
            services = doc.get("services")
            if services is None:
                continue
            if not isinstance(services, dict):
                _die_tool("check_compose_policy: 'services' must be a mapping when present")
            for svc_name, svc_body in services.items():
                svc_name = str(svc_name)
                if svc_name in allowlist:
                    # LUM-620 name-squat: checked before the body-shape check, so an empty
                    # (`backup:`) override in an overlay still counts as a definition.
                    if src_path not in core_paths:
                        violations.append(
                            f"Pass A: name-squat: service {svc_name!r} is allowlisted "
                            f"(Core-credential exemption) but is defined in non-core file "
                            f"{_display_path(src_path)!r}; only a --core-file may define it"
                        )
                    continue
                if not isinstance(svc_body, dict):
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


class _ComposeFileAction(argparse.Action):
    """-f and --core-file share one ordered list of (path, is_core), so the compose merge
    order is exactly the order on the command line."""

    def __call__(self, parser, namespace, values, option_string=None):  # type: ignore[override]
        entries = getattr(namespace, self.dest, None) or []
        entries.append((values, bool(self.const)))
        setattr(namespace, self.dest, entries)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="LUM-43 compose policy: Core DB/Qdrant credentials must not reach "
        "non-allowlisted services. LUM-620: allowlisted service names may only be "
        "defined by --core-file files."
    )
    parser.add_argument(
        "-f",
        "--file",
        dest="compose_entries",
        action=_ComposeFileAction,
        const=False,
        nargs=None,
        metavar="PATH",
        help="Compose overlay file (repeat; same order as docker compose -f). Treated as "
        "NON-core: it may not define a service named in the allowlist.",
    )
    parser.add_argument(
        "--core-file",
        dest="compose_entries",
        action=_ComposeFileAction,
        const=True,
        nargs=None,
        metavar="PATH",
        help="LUM-620: a first-party core compose file (repeat). Composed in command-line "
        "order like -f, and allowed to define allowlisted service names (so are the files "
        "it includes). With no --core-file, every file is a non-core overlay.",
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
    entries = args.compose_entries or []
    if not entries:
        parser.error("at least one -f/--file or --core-file is required")
    compose_files: list[Path] = []
    core_files: list[Path] = []
    for f, is_core in entries:
        p = Path(f).expanduser()
        resolved = p.resolve() if p.is_absolute() else (project_dir / p).resolve()
        # Same file named twice (e.g. `--core-file X -f X`): compose it once, at its first
        # position; it is core if any mention marks it core.
        if resolved not in compose_files:
            compose_files.append(resolved)
        if is_core and resolved not in core_files:
            core_files.append(resolved)

    violations: list[str] = []
    pass_a_scan(compose_files, allowlist, violations, core_closure(core_files))
    if violations:
        for line in violations:
            print(line, file=sys.stderr)
        if not core_files and any(v.startswith("Pass A: name-squat:") for v in violations):
            print(
                "check_compose_policy: no --core-file given, so every file is a non-core "
                "overlay; pass first-party compose files with --core-file (LUM-620)",
                file=sys.stderr,
            )
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
