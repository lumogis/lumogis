#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Generate the two files a household installs Lumogis from (LUM-980).

A user downloads ``docker-compose.yml`` and ``example.env`` from a
GitHub Release and runs ``docker compose up -d``: nothing is built, nothing is
read from a source checkout. This script derives that compose file from the
repo's ``docker-compose.yml`` (which builds, for developers and CI):

* every Lumogis service loses ``build:`` / ``pull_policy:`` and pulls
  ``ghcr.io/lumogis/<image>:${LUMOGIS_VERSION:-release}``;
* profile-only services (LibreChat, MongoDB) are dropped, except the knowledge
  graph (LUM-989): FalkorDB and ``lumogis-graph``, merged in from the two
  overlays under a ``graph`` profile, so they do not run until Admin → Knowledge
  graph (or ``COMPOSE_PROFILES=graph``) switches them on;
* the speech-to-text sidecar (LUM-995): ``lumogis-stt``, read from
  ``docker-compose.stt.yml`` and placed under an ``stt`` profile that the Release
  ``example.env`` turns on (``COMPOSE_PROFILES=stt``);
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
    "lumogis-graph": "lumogis-graph",
}
# The optional components a Release file carries, each switched on with its own
# Compose profile: ``graph`` (LUM-989; off until Admin turns it on) and ``stt``
# (LUM-995; on in the Release ``example.env``). A future optional component joins
# this set deliberately, with its own rule in check().
RELEASE_PROFILES = frozenset({"graph", "stt"})
GRAPH_PROFILE = "graph"
GRAPH_SERVICES = ("falkordb", "lumogis-graph")
GRAPH_OVERLAYS = ("docker-compose.falkordb.yml", "docker-compose.premium.yml")
STT_PROFILE = "stt"
STT_SERVICE = "lumogis-stt"
STT_OVERLAY = "docker-compose.stt.yml"
STT_VOLUME = "lumogis_stt_models"
STT_IMAGE_PREFIX = "ghcr.io/speaches-ai/speaches"
# The Release pulls FalkorDB's engine-only image (no browser, no extras); a
# checkout keeps the full image. A literal pin: in the base file FALKORDB_IMAGE is
# the backup's build argument and defaults to the full image.
RELEASE_FALKORDB_IMAGE = "falkordb/falkordb-server:v4.18.1"
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


def load_graph_overlays(compose_dir: Path) -> dict:
    """Read the two graph overlays that sit next to the base compose file."""
    docs = []
    for name in GRAPH_OVERLAYS:
        path = compose_dir / name
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise Violation(f"cannot read {path}: {exc}") from exc
        _reject_yaml_features(text)
        docs.append(yaml.safe_load(text) or {})
    return {"falkordb": docs[0], "premium": docs[1]}


def load_stt_overlay(compose_dir: Path) -> dict:
    """Read the speech-to-text overlay that sits next to the base compose file."""
    path = compose_dir / STT_OVERLAY
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise Violation(f"cannot read {path}: {exc}") from exc
    _reject_yaml_features(text)
    return yaml.safe_load(text) or {}


def _stt_service(stt_overlay: dict) -> dict:
    """The ``lumogis-stt`` service for the Release: the overlay's, under its profile."""
    src = (stt_overlay.get("services") or {}).get(STT_SERVICE)
    if not src:
        raise Violation(f"{STT_OVERLAY} no longer defines {STT_SERVICE}")
    svc = dict(src)
    svc.pop("ports", None)  # no host port in a household install
    env = dict(svc.get("environment") or {})
    env.pop("DEFAULT_MODEL", None)  # Speaches 0.8 ignores it; Core installs the model
    if env:
        svc["environment"] = env
    else:
        svc.pop("environment", None)
    svc["profiles"] = [STT_PROFILE]
    return svc


def _graph_services(overlays: dict, base: dict) -> tuple[dict, dict, dict]:
    """Return (falkordb, lumogis-graph, backup volume additions) for the Release."""
    fk_src = (overlays["falkordb"].get("services") or {}).get("falkordb")
    lg_src = (overlays["premium"].get("services") or {}).get("lumogis-graph")
    if not fk_src or not lg_src:
        raise Violation("the graph overlays no longer define falkordb and lumogis-graph")
    falkordb = dict(fk_src)
    falkordb["image"] = RELEASE_FALKORDB_IMAGE
    falkordb.pop("ports", None)  # no host port in a household install
    falkordb["profiles"] = [GRAPH_PROFILE]

    graph = dict(lg_src)
    graph.pop("build", None)
    graph.pop("pull_policy", None)
    graph["image"] = f"ghcr.io/lumogis/{IMAGES['lumogis-graph']}:{TAG}"
    graph = {"image": graph.pop("image"), **graph}
    graph["profiles"] = [GRAPH_PROFILE]

    backup_volume = "falkordb_data:/var/lib/falkordb/data"
    return falkordb, graph, {"backup_volume": backup_volume}


def transform(base: dict, overlays: dict | None = None, stt_overlay: dict | None = None) -> dict:
    _reject_checkout_features(base)
    services = base.get("services") or {}
    dropped = {name for name, svc in services.items() if svc.get("profiles")}
    out_services: dict = {}
    graph_extra: dict = {}
    if overlays is not None:
        falkordb, graph, graph_extra = _graph_services(overlays, base)
    stt_svc = _stt_service(stt_overlay) if stt_overlay is not None else None
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
        if name == "backup" and graph_extra:
            # The overlay mounts the FalkorDB data volume into backup so it can
            # copy the dump. Not its depends_on: an unprofiled service cannot wait
            # for a profiled one.
            svc["volumes"] = list(svc.get("volumes") or []) + [graph_extra["backup_volume"]]
        if name == "stack-control":
            env = dict(svc.get("environment") or {})
            env["HOST_PROJECT_DIR"] = "${PWD}"
            env["COMPOSE_PROJECT_NAME"] = "${COMPOSE_PROJECT_NAME:-lumogis}"
            svc["environment"] = env
            svc["volumes"] = list(svc.get("volumes") or []) + ["${PWD}:${PWD}:ro"]
        out_services[name] = svc
    if overlays is not None:
        out_services["falkordb"] = falkordb
        out_services["lumogis-graph"] = graph
    if stt_svc is not None:
        out_services[STT_SERVICE] = stt_svc
    out = dict(base)
    out["services"] = out_services
    if overlays is not None:
        volumes = dict(base.get("volumes") or {})
        for overlay in (overlays["falkordb"], overlays["premium"]):
            for vol, spec in (overlay.get("volumes") or {}).items():
                volumes.setdefault(vol, spec)
        out["volumes"] = volumes
    if stt_overlay is not None:
        volumes = dict(out.get("volumes") or base.get("volumes") or {})
        for vol, spec in (stt_overlay.get("volumes") or {}).items():
            volumes.setdefault(vol, spec)
        out["volumes"] = volumes
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
        profiles = svc.get("profiles")
        if profiles and not (set(profiles) <= RELEASE_PROFILES):
            problems.append(
                f"service {name!r} is profile-only ({sorted(profiles)}); "
                f"a release file only carries {sorted(RELEASE_PROFILES)}"
            )
        if name in GRAPH_SERVICES and profiles != [GRAPH_PROFILE]:
            problems.append(f"service {name!r} must sit under profiles: [{GRAPH_PROFILE}]")
        if name == STT_SERVICE and profiles != [STT_PROFILE]:
            problems.append(f"service {name!r} must sit under profiles: [{STT_PROFILE}]")
        if name not in GRAPH_SERVICES + (STT_SERVICE,) and profiles:
            problems.append(f"service {name!r} must not be in a profile in a release file")
    missing = set(IMAGES) - set(doc["services"])
    if missing:
        problems.append(f"missing Lumogis services: {sorted(missing)}")
    missing_graph = set(GRAPH_SERVICES) - set(doc["services"])
    if missing_graph:
        problems.append(f"missing graph services: {sorted(missing_graph)}")
    falkordb = doc["services"].get("falkordb") or {}
    if falkordb:
        if falkordb.get("image") != RELEASE_FALKORDB_IMAGE:
            problems.append(f"falkordb image is {falkordb.get('image')!r}")
        if falkordb.get("ports"):
            problems.append("falkordb publishes a host port in a release file")
    problems.extend(_stt_problems(doc))
    problems.extend(_structural_problems(doc))
    return problems


def _stt_problems(doc: dict) -> list[str]:
    """The speech-to-text rules (LUM-995). The image equals the overlay's by construction
    (transform copies it, ``test_stt_image_is_the_overlay_digest`` pins it): check() sees
    only the generated document, so it checks the shape, not the equality."""
    problems: list[str] = []
    stt = doc["services"].get(STT_SERVICE)
    if not stt:
        return [f"missing speech-to-text service: {STT_SERVICE!r}"]
    if stt.get("ports"):
        problems.append(f"{STT_SERVICE} publishes a host port in a release file")
    image = str(stt.get("image") or "")
    if not image.startswith(STT_IMAGE_PREFIX):
        problems.append(f"{STT_SERVICE} image is {image!r}, not {STT_IMAGE_PREFIX}")
    if "@sha256:" not in image:
        problems.append(f"{STT_SERVICE} image is not pinned by digest: {image!r}")
    env = stt.get("environment") or {}
    if str(env.get("HF_HUB_DISABLE_TELEMETRY")) != "1":
        problems.append(f"{STT_SERVICE} must set HF_HUB_DISABLE_TELEMETRY to 1 (telemetry off)")
    for vol in (doc["services"].get("backup") or {}).get("volumes") or []:
        source, _t, _r = _source_and_target(vol)
        if source == STT_VOLUME:
            problems.append(f"the speech model volume {STT_VOLUME!r} must not be backed up")
    return problems


def _depends_on_targets(svc: dict) -> list[str]:
    deps = svc.get("depends_on")
    if isinstance(deps, dict):
        return list(deps)
    if isinstance(deps, list):
        return [str(d) for d in deps]
    return []


def _structural_problems(doc: dict) -> list[str]:
    """No-Docker checks Compose would otherwise make: dangling names fail only at ``up``."""
    problems: list[str] = []
    services = doc["services"]
    declared = set((doc.get("volumes") or {}).keys())
    for name, svc in services.items():
        for target in _depends_on_targets(svc):
            if target not in services:
                problems.append(f"service {name!r} depends_on {target!r}, which is not in the file")
                continue
            # An unprofiled service cannot wait for a profiled one: it would stop
            # `docker compose up` before the profile is on.
            if not svc.get("profiles") and services[target].get("profiles"):
                problems.append(
                    f"service {name!r} (no profile) depends_on {target!r}, which is profile-only"
                )
        for vol in svc.get("volumes") or []:
            source, _t, _r = _source_and_target(vol)
            try:
                kind = classify_source(source)
            except Violation:
                continue
            if kind == "named" and source not in declared:
                problems.append(f"service {name!r} uses volume {source!r}, which is not declared")
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
        doc = transform(
            yaml.safe_load(text),
            load_graph_overlays(args.compose.parent),
            load_stt_overlay(args.compose.parent),
        )
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
