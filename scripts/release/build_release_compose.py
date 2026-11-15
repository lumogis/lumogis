#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Generate the two files a household installs Lumogis from (LUM-980).

Like Immich, a user downloads ``docker-compose.yml`` and ``example.env`` from a
GitHub Release and runs ``docker compose up -d``: nothing is built, nothing is
read from a source checkout. This script derives that compose file from the
repo's ``docker-compose.yml`` (which builds, for developers and CI):

* every Lumogis service loses ``build:`` / ``pull_policy:`` and pulls
  ``ghcr.io/lumogis/<image>:${LUMOGIS_VERSION:-release}``;
* profile-only services (LibreChat, MongoDB) are dropped;
* three development mounts whose files are baked into the images are dropped
  (``init.sql``, the ``Caddyfile`` and ``preflight.sh``), and any other mount
  that points into the checkout is an error;
* stack-control gets ``HOST_PROJECT_DIR=${PWD}`` plus the same-path mount it
  needs, so restarts from Settings recreate containers with the host folder;
* a header carries the install / update commands, the release and a fingerprint
  of the file (the orchestrator compares it at start-up and warns when the
  user's copy is out of date).

Usage:
  build_release_compose.py [--compose PATH] [--example-env PATH] [--out DIR]
                           [--version X.Y.Z] [--check] [--fingerprint-only]

Exit codes: 0 ok; 1 a contract violation (each printed as ``release-compose: …``);
2 usage or IO error. Output is deterministic (input order, no timestamps).
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]

# Service name in docker-compose.yml -> published image name.
IMAGES: dict[str, str] = {
    "orchestrator": "lumogis-orchestrator",
    "lumogis-web": "lumogis-web",
    "stack-control": "lumogis-stack-control",
    "backup": "lumogis-backup",
    "caddy": "lumogis-caddy",
}
TAG = "${LUMOGIS_VERSION:-release}"

# Development mounts whose contents ship inside the images.
BAKED_MOUNTS = frozenset(
    {"./postgres/init.sql", "./docker/caddy/Caddyfile", "./docker/caddy/preflight.sh"}
)
# Bind sources allowed in the release file: the install folder, the host data
# directories (their defaults are relative to the install folder) and the socket.
DATA_DIR_VARS = frozenset({"WORKSPACE_HOST_DIR", "FILESYSTEM_ROOT", "BACKUP_HOST_DIR"})
ALLOWED_ABSOLUTE = frozenset({"/var/run/docker.sock"})

HEADER_RELEASE = "# lumogis-release: "
HEADER_FINGERPRINT = "# lumogis-compose: "


class Violation(Exception):
    pass


def _reject_yaml_features(text: str) -> None:
    """Anchors, aliases and tags would not survive a load / dump round trip."""
    for token in yaml.scan(text):
        kind = type(token).__name__
        if kind in ("AnchorToken", "AliasToken", "TagToken"):
            raise Violation(
                f"the base compose file uses YAML {kind}; the generator does not support it"
            )


def _split_volume(spec: str) -> list[str]:
    """Split a short-syntax volume on ':' outside ``${...}`` (defaults contain ':-')."""
    parts, buf, depth, i = [], [], 0, 0
    while i < len(spec):
        if spec.startswith("${", i):
            depth += 1
            buf.append("${")
            i += 2
            continue
        ch = spec[i]
        if ch == "}" and depth:
            depth -= 1
        if ch == ":" and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
        i += 1
    parts.append("".join(buf))
    return parts


def _source_and_target(volume) -> tuple[str, str, str]:
    """Return (source, target, rest) for short or long volume syntax."""
    if isinstance(volume, dict):
        return str(volume.get("source", "")), str(volume.get("target", "")), ""
    parts = _split_volume(str(volume))
    if len(parts) == 1:
        return "", parts[0], ""
    return parts[0], parts[1], ":".join(parts[2:])


_VAR = re.compile(r"^\$\{([A-Z0-9_]+)(:?-([^}]*))?\}(.*)$")


def classify_source(source: str) -> str:
    """Return one of: named, install-folder, data-dir, socket, baked, or raise Violation."""
    if not source:
        return "anonymous"
    if source == ".":
        return "install-folder"
    if source in BAKED_MOUNTS:
        return "baked"
    m = _VAR.match(source)
    if m:
        name, default, rest = m.group(1), m.group(3) or "", m.group(4)
        if name in DATA_DIR_VARS and not rest:
            return "data-dir"
        if name == "PWD" and not rest:
            return "install-folder"
        raise Violation(
            f"bind source {source!r} uses ${{{name}}}"
            + (f" with default {default!r}" if default else "")
            + "; only the data-directory variables may appear in a release install"
        )
    if source.startswith(("./", "../")):
        raise Violation(f"bind source {source!r} points into the source checkout")
    if source.startswith("/"):
        if source in ALLOWED_ABSOLUTE:
            return "socket"
        raise Violation(f"bind source {source!r} is an absolute host path")
    if "/" in source or source.startswith("~"):
        raise Violation(f"bind source {source!r} is a host path")
    return "named"


# Top-level keys whose files would have to come from the checkout.
CHECKOUT_TOP_LEVEL = ("configs", "secrets", "include")


def _reject_checkout_features(base: dict) -> None:
    for key in CHECKOUT_TOP_LEVEL:
        if base.get(key):
            raise Violation(f"top-level {key!r} reads files from the checkout")
    for name, svc in (base.get("services") or {}).items():
        if "extends" in svc:
            raise Violation(f"service {name!r} uses extends:, which reads another file")
        env_file = svc.get("env_file")
        files = env_file if isinstance(env_file, list) else [env_file] if env_file else []
        for f in files:
            path = f.get("path") if isinstance(f, dict) else f
            if path != ".env":
                raise Violation(
                    f"service {name!r} env_file {path!r}: only .env sits next to the release file"
                )


def transform(base: dict) -> dict:
    _reject_checkout_features(base)
    services = base.get("services") or {}
    dropped = {name for name, svc in services.items() if svc.get("profiles")}
    out_services: dict = {}
    for name, svc in services.items():
        if name in dropped:
            continue
        svc = dict(svc)
        if "build" in svc:
            if name not in IMAGES:
                raise Violation(f"service {name!r} is built but has no published image name")
            svc.pop("build")
            svc.pop("pull_policy", None)
            svc["image"] = f"ghcr.io/lumogis/{IMAGES[name]}:{TAG}"
            # keep image first for readability
            svc = {"image": svc.pop("image"), **svc}
        deps = svc.get("depends_on")
        if isinstance(deps, dict):
            svc["depends_on"] = {k: v for k, v in deps.items() if k not in dropped}
        elif isinstance(deps, list):
            svc["depends_on"] = [d for d in deps if d not in dropped]
        if "volumes" in svc:
            kept = []
            for vol in svc["volumes"]:
                source, _target, _rest = _source_and_target(vol)
                if classify_source(source) == "baked":
                    continue
                kept.append(vol)
            svc["volumes"] = kept
        if name == "stack-control":
            env = dict(svc.get("environment") or {})
            env["HOST_PROJECT_DIR"] = "${PWD}"
            env["COMPOSE_PROJECT_NAME"] = "${COMPOSE_PROJECT_NAME:-lumogis}"
            svc["environment"] = env
            svc["volumes"] = list(svc.get("volumes") or []) + ["${PWD}:${PWD}:ro"]
        out_services[name] = svc
    out = dict(base)
    out["services"] = out_services
    return out


def render_body(doc: dict) -> str:
    return yaml.safe_dump(doc, sort_keys=False, default_flow_style=False, width=1000)


def fingerprint(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def render(doc: dict, version: str) -> str:
    body = render_body(doc)
    header = "\n".join(
        [
            "# Lumogis — docker-compose.yml for installing from published images.",
            "#",
            "# Install (in an empty folder, with example.env saved as .env):",
            "#   docker compose up -d",
            "# Update:",
            "#   docker compose pull && docker compose up -d",
            "#",
            "# Generated from the repository's docker-compose.yml by",
            "# scripts/release/build_release_compose.py. Do not edit it: put local changes",
            "# in docker-compose.override.yml, which Compose merges automatically.",
            "# Run Compose from this folder (stack-control is given it as ${PWD}).",
            "# Upgrading and backups: docs/deployment/upgrading.md on GitHub.",
            f"{HEADER_RELEASE}{version}",
            f"{HEADER_FINGERPRINT}{fingerprint(body)}",
            "",
        ]
    )
    return header + "\n" + body


def check(doc: dict) -> list[str]:
    """Post-generation contract; returns a list of problems."""
    problems: list[str] = []
    for name, svc in doc["services"].items():
        if "build" in svc or "pull_policy" in svc:
            problems.append(f"service {name!r} still has build: or pull_policy:")
        if name in IMAGES and svc.get("image") != f"ghcr.io/lumogis/{IMAGES[name]}:{TAG}":
            problems.append(f"service {name!r} image is {svc.get('image')!r}")
        for vol in svc.get("volumes") or []:
            source, _t, _r = _source_and_target(vol)
            try:
                if classify_source(source) == "baked":
                    problems.append(f"service {name!r} still mounts {source!r}")
            except Violation as exc:
                problems.append(str(exc))
        if svc.get("profiles"):
            problems.append(f"service {name!r} is profile-only")
    missing = set(IMAGES) - set(doc["services"])
    if missing:
        problems.append(f"missing Lumogis services: {sorted(missing)}")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--compose", type=Path, default=ROOT / "docker-compose.yml")
    ap.add_argument("--example-env", type=Path, default=ROOT / "docker" / "release" / "example.env")
    ap.add_argument("--out", type=Path, default=ROOT / "dist" / "release")
    ap.add_argument("--version", default="dev")
    ap.add_argument("--check", action="store_true", help="also verify the generated file")
    ap.add_argument(
        "--fingerprint-only", action="store_true", help="print the fingerprint, write nothing"
    )
    args = ap.parse_args(argv)

    try:
        text = args.compose.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"release-compose: cannot read {args.compose}: {exc}", file=sys.stderr)
        return 2
    try:
        _reject_yaml_features(text)
        doc = transform(yaml.safe_load(text))
    except Violation as exc:
        print(f"release-compose: {exc}", file=sys.stderr)
        return 1

    if args.fingerprint_only:
        print(fingerprint(render_body(doc)))
        return 0

    if args.check:
        problems = check(doc)
        if problems:
            for p in problems:
                print(f"release-compose: {p}", file=sys.stderr)
            return 1

    try:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "docker-compose.yml").write_text(render(doc, args.version), encoding="utf-8")
        shutil.copyfile(args.example_env, args.out / "example.env")
    except OSError as exc:
        print(f"release-compose: cannot write to {args.out}: {exc}", file=sys.stderr)
        return 2
    print(f"release-compose: wrote {args.out / 'docker-compose.yml'} and example.env")
    return 0


if __name__ == "__main__":
    sys.exit(main())
