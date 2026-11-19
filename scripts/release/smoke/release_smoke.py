#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Release smoke: install a release from its two Release files and check it (LUM-997).

One suite = one install in an empty folder, driven through ``docker compose`` and
Core's public HTTP API, with results written as JSON after every check. ``summary``
renders many result files into one Markdown table and a verdict.

Standard library only: the runner needs no pip. No orchestrator code is imported;
this script is a client of the household API (those routes are now release-gating,
see the plan's Interoperability notes).

Secrets come only from the environment (SMOKE_HOME_DNS_CI_VALUE, SMOKE_CLOUD_LLM_KEY)
and never reach a result, a log or an artifact: every detail string and every compose
log line passes through ``_redact``.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import hashlib
import json
import os
import re
import secrets as _secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

SCHEMA = 1
HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"
ADMIN_EMAIL = "smoke@example.com"
PHRASE = "marmalade lighthouse"
# docs/deployment/upgrading.md § Going back, step 2 (keep the two in step).
RESET_SCHEMA_SH = ('psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" '
                   '-c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"')
FIXTURE_DOC = "smoke-document.txt"
VOICE_SENTENCE = "the quick brown fox jumps over the lazy dog"
EGRESS_CHAIN = "LUMOGIS_SMOKE_EGRESS"
PRIVATE_RANGES = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8")
ENV_READERS = {"orchestrator", "stack-control", "backup"}
STAMP_RE = re.compile(r"^\d{8}-\d{6}$")
# Prereleases are -rc.N only (the publish skill's rc flow); the workflows accept any suffix, so another one fails closed here (exit 2).
VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(-rc\.[0-9]+)?$")
IMAGE_SERVICE = {
    "lumogis-orchestrator": "orchestrator",
    "lumogis-web": "lumogis-web",
    "lumogis-stack-control": "stack-control",
    "lumogis-backup": "backup",
    "lumogis-caddy": "caddy",
    "lumogis-graph": "lumogis-graph",
}
SUITES = ("install", "graph", "upgrade", "secure-address", "cloud-llm")
FEATURES = ("stt", "signin", "graph-switch")

# Planned check ids per suite, in run order. Every one of them is written as
# ``fail: not run`` before the first check, so a killed job still lists what did not run.
PLANNED: dict[str, list[str]] = {
    "install": [
        "FILES-001", "MS-001", "SETUP-CLAIM", "MS-001-DIGESTS", "MS-001-STEADY", "MS-003",
        "MS-004", "MS-008", "VOICE-PROFILE", "VOICE", "MS-005", "MS-011", "MS-010",
        "RUNNER-FOOTPRINT",
    ],
    "graph": [
        "FILES-001", "MS-001", "SETUP-CLAIM", "GRAPH-ON", "GRAPH-ENTITIES", "GRAPH-BACKUP",
        "GRAPH-RESTORE", "GRAPH-OFF", "RUNNER-FOOTPRINT",
    ],
    "upgrade": [
        "FILES-001", "UPGRADE-PREV", "UPGRADE-PULL", "UPGRADE-SIGNIN", "UPGRADE-BACK",
    ],
    "secure-address": ["FILES-001", "SECURE-ADDRESS"],
    "cloud-llm": ["FILES-001", "MS-001", "SETUP-CLAIM", "MS-006"],
}
NOT_REQUIRED = {"RUNNER-FOOTPRINT", "MS-006"}
CHECK_IDS = frozenset(
    {i for ids in PLANNED.values() for i in ids}
    | {"MS-009", "RUNNER-DISK", "SUITE-MISSING", "RESULTS-INVALID", "RESULTS-DUPLICATE"}
)


# --------------------------------------------------------------------------- plumbing

# Module-level so unit tests can drive time.
_clock: Callable[[], float] = time.monotonic
_sleep: Callable[[float], None] = time.sleep


class CheckFailed(Exception):
    """The check ran and the release did not meet it."""


class NotApplicable(Exception):
    """The check does not apply to this release or setup."""


class DeadlineReached(CheckFailed):
    """The suite's own deadline passed."""


def core_url() -> str:
    return os.environ.get("LUMOGIS_SMOKE_CORE_URL", "http://127.0.0.1:8000").rstrip("/")


def web_url() -> str:
    return os.environ.get("LUMOGIS_SMOKE_WEB_URL", "http://127.0.0.1").rstrip("/")


def host_ports() -> list[int]:
    raw = os.environ.get("LUMOGIS_SMOKE_PORTS", "80,443,8000,6334")
    return [int(p) for p in raw.split(",") if p.strip()]


def version_base(version: str) -> str:
    return version.split("-", 1)[0]


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _redact(text: str, secret_values: set[str]) -> str:
    """Replace every known secret value with ``***`` (longest first)."""
    for value in sorted((v for v in secret_values if v), key=len, reverse=True):
        text = text.replace(value, "***")
    return text


@dataclasses.dataclass
class Check:
    id: str
    suite: str
    arch: str
    result: str  # pass | fail | na
    required: bool
    seconds: float
    detail: str


@dataclasses.dataclass
class Results:
    version: str
    arch: str
    suite: str
    started: str
    checks: list[Check]

    def to_json(self) -> str:
        return json.dumps(
            {
                "schema": SCHEMA,
                "version": self.version,
                "arch": self.arch,
                "suite": self.suite,
                "started": self.started,
                "checks": [dataclasses.asdict(c) for c in self.checks],
            },
            indent=2,
        )

    def write(self, out: Path) -> None:
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_name(out.name + ".tmp")
        tmp.write_text(self.to_json(), encoding="utf-8")
        os.replace(tmp, out)


def poll(
    predicate: Callable[[], Any],
    timeout_s: float,
    interval_s: float,
    desc: str,
    deadline: Optional[float] = None,
) -> tuple[bool, float, Any]:
    """Call ``predicate`` until it returns something truthy. Never waits past ``deadline``."""
    start = _clock()
    last: Any = None
    while True:
        last = predicate()
        if last:
            return True, _clock() - start, last
        now = _clock()
        if deadline is not None and now >= deadline:
            raise DeadlineReached(f"suite deadline reached while waiting for {desc}")
        if now - start >= timeout_s:
            return False, now - start, last
        _sleep(interval_s)


def encode_multipart(
    fields: dict[str, str], files: dict[str, tuple[str, str, bytes]]
) -> tuple[bytes, str]:
    boundary = "lumogis" + uuid.uuid4().hex
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        )
    for name, (filename, ctype, data) in files.items():
        head = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; '
            f'filename="{filename}"\r\nContent-Type: {ctype}\r\n\r\n'
        ).encode()
        parts.append(head + data + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


class Api:
    """stdlib client for Core. Returns ``(status, json_or_text)``; never raises on a status."""

    def __init__(self, base: Optional[str] = None, auth: str = "none"):
        self.base = (base or core_url()).rstrip("/")
        self.auth = auth  # "none" (sign-in off: Core answers as admin) | "token"
        self.email: Optional[str] = None
        self.password: Optional[str] = None
        self.token: Optional[str] = None
        self.token_at: float = 0.0
        self.calls: list[tuple[str, str]] = []

    def set_session(self, email: str, password: str, token: str) -> None:
        self.auth = "token"
        self.email, self.password, self.token = email, password, token
        self.token_at = _clock()

    def token_age(self) -> float:
        return _clock() - self.token_at

    def _once(
        self, method: str, path: str, body: Optional[bytes], headers: dict[str, str],
        use_auth: bool, timeout: float,
    ) -> tuple[int, Any]:
        hdrs = dict(headers)
        if use_auth and self.auth == "token" and self.token:
            hdrs["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(self.base + path, data=body, method=method, headers=hdrs)
        self.calls.append((method, path))
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status, raw = resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            status, raw = exc.code, exc.read()
        except (urllib.error.URLError, OSError, ConnectionError) as exc:
            return 0, f"{type(exc).__name__}: {exc}"
        text = raw.decode("utf-8", "replace")
        try:
            return status, json.loads(text)
        except ValueError:
            return status, text

    def request(
        self, method: str, path: str, *, json_body: Any = None, raw_body: Optional[bytes] = None,
        content_type: Optional[str] = None, auth: bool = True, timeout: float = 30,
    ) -> tuple[int, Any]:
        headers: dict[str, str] = {}
        body = raw_body
        if json_body is not None:
            body = json.dumps(json_body).encode()
            headers["Content-Type"] = "application/json"
        if content_type:
            headers["Content-Type"] = content_type
        status, data = self._once(method, path, body, headers, auth, timeout)
        if status == 401 and auth and self.auth == "token" and self.password:
            if self.relogin():
                status, data = self._once(method, path, body, headers, auth, timeout)
        return status, data

    def get(self, path: str, **kw: Any) -> tuple[int, Any]:
        return self.request("GET", path, **kw)

    def post_json(self, path: str, body: Any, **kw: Any) -> tuple[int, Any]:
        return self.request("POST", path, json_body=body, **kw)

    def post_multipart(
        self, path: str, fields: dict[str, str], files: dict[str, tuple[str, str, bytes]], **kw: Any
    ) -> tuple[int, Any]:
        body, ctype = encode_multipart(fields, files)
        return self.request("POST", path, raw_body=body, content_type=ctype, **kw)

    def relogin(self) -> bool:
        """Tokens last 900 s and a suite runs longer: sign in again with the password we hold."""
        status, data = self._once(
            "POST", "/api/v1/auth/login",
            json.dumps({"email": self.email, "password": self.password}).encode(),
            {"Content-Type": "application/json"}, False, 30,
        )
        if status == 200 and isinstance(data, dict) and data.get("access_token"):
            self.token = data["access_token"]
            self.token_at = _clock()
            return True
        return False


# --------------------------------------------------------------------------- .env helpers

def read_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not Path(path).exists():
        return out
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def write_env_key(path: Path, key: str, value: str) -> None:
    path = Path(path)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    new = f"{key}={value}"
    for i, line in enumerate(lines):
        if re.match(rf"^\s*{re.escape(key)}\s*=", line):
            lines[i] = new
            break
    else:
        lines.append(new)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


_SECRET_KEY_RE = re.compile(r"SECRET|KEY|PASSWORD|TOKEN", re.I)


class Compose:
    """``docker compose`` in the household's folder.

    ``PWD`` is set explicitly: ``cwd=`` does not change it, and the release compose hands
    stack-control ``${PWD}`` as the install folder. Without it, restart and the graph
    switch would act on the runner's checkout.
    """

    def __init__(self, work_dir: Path, redact: Callable[[str], str] = lambda s: s):
        self.work = Path(work_dir)
        self.redact = redact

    @property
    def env_file(self) -> Path:
        return self.work / ".env"

    def _env(self) -> dict[str, str]:
        return {**os.environ, "PWD": str(self.work)}

    def docker(self, *argv: str, timeout: Optional[float] = None) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(
                ["docker", *argv], cwd=self.work, env=self._env(), text=True,
                capture_output=True, check=False, timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            return subprocess.CompletedProcess(exc.cmd, 124, "", f"timed out after {timeout}s")
        except FileNotFoundError as exc:
            return subprocess.CompletedProcess(["docker", *argv], 127, "", str(exc))

    def compose(self, *argv: str, timeout: Optional[float] = None) -> subprocess.CompletedProcess:
        return self.docker("compose", *argv, timeout=timeout)

    def up(self) -> subprocess.CompletedProcess:
        return self.compose("up", "-d", timeout=1800)

    def pull(self) -> subprocess.CompletedProcess:
        return self.compose("pull", timeout=1800)

    def down(self, volumes: bool = False) -> subprocess.CompletedProcess:
        return self.compose(*(["down", "-v"] if volumes else ["down"]), timeout=600)

    def ps_json(self) -> list[dict]:
        """Both shapes: one JSON array (older Compose) or one object per line (newer).

        Keep in step with ``parse_compose_ps`` in scripts/doctor/checks/services.sh; a
        third copy moves both into a shared module.
        """
        proc = self.compose("ps", "--format", "json")
        if proc.returncode != 0:
            raise CheckFailed("docker compose ps failed: " + tail(proc.stderr or proc.stdout))
        raw = (proc.stdout or "").strip()
        if not raw:
            return []
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                return [x for x in data if isinstance(x, dict)]
            if isinstance(data, dict):
                return [data]
        except ValueError:
            pass
        out = []
        for line in raw.splitlines():
            line = line.strip()
            if line:
                try:
                    obj = json.loads(line)
                except ValueError:
                    raise CheckFailed("docker compose ps printed unparseable JSON: " + line[:80])
                if isinstance(obj, dict):
                    out.append(obj)
        return out

    def services(self) -> list[str]:
        proc = self.compose("config", "--services")
        return [s.strip() for s in (proc.stdout or "").splitlines() if s.strip()]

    def container_map(self) -> dict[str, str]:
        """service -> container id of what is running now."""
        out = {}
        for ent in self.ps_json():
            svc = ent.get("Service") or ent.get("service")
            cid = ent.get("ID") or ent.get("Id") or ent.get("id")
            if isinstance(svc, str) and cid:
                out[svc] = str(cid)
        return out

    def container_ids(self) -> list[str]:
        return sorted(self.container_map().values())

    def exec(self, service: str, *argv: str, timeout: Optional[float] = 120
             ) -> subprocess.CompletedProcess:
        return self.compose("exec", "-T", service, *argv, timeout=timeout)

    def run_t(self, service: str, *argv: str, env: Optional[dict[str, str]] = None,
              no_deps: bool = False, timeout: Optional[float] = 900) -> subprocess.CompletedProcess:
        # Always ``-T``: CI has no TTY and the household doc's ``-it`` would fail there
        # (docs/deployment/upgrading.md keeps ``-it`` for people at a terminal).
        args = ["run", "--rm", "-T"]
        if no_deps:
            args.append("--no-deps")
        for k, v in (env or {}).items():
            args += ["-e", f"{k}={v}"]
        return self.compose(*args, service, *argv, timeout=timeout)

    def set_env(self, key: str, value: str) -> None:
        write_env_key(self.env_file, key, value)

    def read_env(self) -> dict[str, str]:
        return read_env(self.env_file)

    def write_logs(self, path: Path) -> None:
        proc = self.compose("logs", "--no-color", "--timestamps", timeout=300)
        text = (proc.stdout or "") + (proc.stderr or "")
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(self.redact(line) for line in text.splitlines()) + "\n",
                        encoding="utf-8")


# --------------------------------------------------------------------------- suite context

@dataclasses.dataclass
class Ctx:
    suite: str
    arch: str
    version: str
    out: Path
    files_dir: Path
    work: Path
    expect: set[str]
    deadline_min: float = 80
    loopback: bool = False
    required_secure: bool = False
    previous_dir: Optional[Path] = None
    previous_version: str = ""
    secret_values: set[str] = dataclasses.field(default_factory=set)
    results: Optional[Results] = None
    api: Api = dataclasses.field(default_factory=Api)
    compose: Optional[Compose] = None
    deadline: float = 0.0
    status: dict[str, str] = dataclasses.field(default_factory=dict)
    facts: dict[str, Any] = dataclasses.field(default_factory=dict)

    def redact(self, text: str) -> str:
        return _redact(text, self.secret_values)

    def refresh_env_secrets(self) -> None:
        """Re-read generated secrets after every ``up`` (the entrypoint writes them)."""
        if self.compose is None:
            return
        for key, value in self.compose.read_env().items():
            if _SECRET_KEY_RE.search(key) and len(value) >= 8:
                self.secret_values.add(value)

    def planned(self) -> list[str]:
        return PLANNED[self.suite]

    def start(self) -> None:
        self.deadline = _clock() + self.deadline_min * 60
        self.results = Results(self.version, self.arch, self.suite, now_iso(), [
            Check(i, self.suite, self.arch, "fail", i not in NOT_REQUIRED, 0.0, "not run")
            for i in self.planned()
        ])
        self.flush()

    def flush(self) -> None:
        assert self.results is not None
        self.results.write(self.out)

    def record(self, cid: str, result: str, detail: str, seconds: float, required: bool) -> None:
        assert self.results is not None
        row = Check(cid, self.suite, self.arch, result, required, round(seconds, 1),
                    self.redact(detail)[:300])
        for i, existing in enumerate(self.results.checks):
            if existing.id == cid:
                self.results.checks[i] = row
                break
        else:
            self.results.checks.append(row)
        self.status[cid] = result
        self.flush()

    def run(self, cid: str, fn: Callable[[], Any], required: Optional[bool] = None,
            after: tuple[str, ...] = ()) -> None:
        """Run one check. A failed check never aborts the suite; dependants are marked."""
        if required is None:
            required = cid not in NOT_REQUIRED
        for dep in after:
            if self.status.get(dep) == "fail":
                self.record(cid, "fail", f"skipped because {dep} failed", 0, required)
                return
        if _clock() >= self.deadline:
            self.record(cid, "fail", f"suite deadline ({self.deadline_min:g} min) reached", 0,
                        required)
            return
        start = _clock()
        try:
            detail = fn()
            self.record(cid, "pass", str(detail or ""), _clock() - start, required)
        except NotApplicable as exc:
            self.record(cid, "na", str(exc), _clock() - start, required)
        except DeadlineReached as exc:
            self.record(cid, "fail", f"suite deadline ({self.deadline_min:g} min) reached: {exc}",
                        _clock() - start, required)
        except CheckFailed as exc:
            self.record(cid, "fail", str(exc), _clock() - start, required)
        except Exception as exc:  # noqa: BLE001 — any bug in a check is a failed check
            self.record(cid, "fail", f"{type(exc).__name__}: {exc}", _clock() - start, required)

    def na_all(self, reason: str) -> None:
        for cid in self.planned():
            self.record(cid, "na", reason, 0, cid not in NOT_REQUIRED)

    def finish_unrun(self) -> None:
        """After the suite: rows still ``not run`` stay failed; stamp the deadline reason."""
        assert self.results is not None
        if _clock() >= self.deadline:
            for c in self.results.checks:
                if c.detail == "not run":
                    c.detail = f"suite deadline ({self.deadline_min:g} min) reached"
        self.flush()

    def wait(self, predicate: Callable[[], Any], timeout_s: float, interval_s: float,
             desc: str) -> Any:
        ok, secs, last = poll(predicate, timeout_s, interval_s, desc, self.deadline)
        if not ok:
            raise CheckFailed(f"timed out after {int(secs)}s waiting for {desc}; last={last!r}"[:300])
        return last


def tail(text: str, n: int = 300) -> str:
    text = (text or "").strip().replace("\n", " | ")
    return text[-n:]


# --------------------------------------------------------------------------- shared steps

def verify_files(files_dir: Path) -> str:
    sums = Path(files_dir) / "SHA256SUMS"
    if not sums.exists():
        raise CheckFailed("SHA256SUMS missing")
    bad = []
    n = 0
    for line in sums.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, _, name = line.partition("  ")
        name = name.strip().lstrip("*")
        n += 1
        target = Path(files_dir) / name
        if not target.exists():
            bad.append(f"{name} missing")
        elif hashlib.sha256(target.read_bytes()).hexdigest() != digest.strip():
            bad.append(f"{name} differs")
    listed = {ln.partition("  ")[2].strip().lstrip("*") for ln in
              sums.read_text(encoding="utf-8").splitlines() if ln.strip()}
    bad += [f"{name} not listed" for name in ("docker-compose.yml", "example.env", "image-digests.txt")
            if name not in listed]
    if bad:
        raise CheckFailed("checksum mismatch: " + ", ".join(bad))
    return f"{n} files match SHA256SUMS"


def prepare_install(ctx: Ctx, files_dir: Path, version: str, *, extra_env: Optional[dict] = None
                    ) -> Compose:
    """Copy the two files into the empty work dir and pin the version (Install contract)."""
    work = ctx.work
    work.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(Path(files_dir) / "docker-compose.yml", work / "docker-compose.yml")
    shutil.copyfile(Path(files_dir) / "example.env", work / ".env")
    compose = Compose(work, ctx.redact)
    # A stable example.env says ``release``, which moves only after the smoke is green.
    compose.set_env("LUMOGIS_VERSION", version)
    if ctx.loopback:
        compose.set_env("CADDY_HTTP_HOST_PORT", "127.0.0.1:80")
        compose.set_env("CADDY_HTTPS_HOST_PORT", "127.0.0.1:443")
    if ctx.suite in ("graph", "upgrade", "secure-address"):
        compose.set_env("OLLAMA_EXTRA_MODELS", "")
    for k, v in (extra_env or {}).items():
        compose.set_env(k, v)
    ctx.compose = compose
    return compose


def check_ports_free() -> None:
    busy = []
    for port in host_ports():
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                busy.append(port)
    if busy:
        raise CheckFailed("port " + ", ".join(str(p) for p in busy) + " busy")


def services_healthy(compose: Compose) -> tuple[bool, str]:
    want = compose.services()
    if not want:
        return False, "no services"
    have = {}
    for ent in compose.ps_json():
        svc = ent.get("Service") or ent.get("service")
        if isinstance(svc, str):
            have[svc] = ent
    bad = []
    for svc in want:
        ent = have.get(svc)
        if ent is None:
            bad.append(f"{svc}: not running")
            continue
        state = str(ent.get("State", "")).lower()
        health = str(ent.get("Health", "")).lower()
        if state != "running" or health not in ("", "healthy"):
            bad.append(f"{svc}: {state}/{health or '-'}")
    return (not bad), ", ".join(bad)


def wait_healthy(ctx: Ctx, minutes: float, desc: str = "all services healthy") -> float:
    assert ctx.compose is not None
    last = {"d": ""}

    def pred() -> bool:
        ok, detail = services_healthy(ctx.compose)  # type: ignore[arg-type]
        last["d"] = detail
        return ok

    ok, secs, _ = poll(pred, minutes * 60, 5, desc, ctx.deadline)
    if not ok:
        raise CheckFailed(f"{desc} not reached in {minutes:g} min: {last['d']}"[:300])
    return secs


def fresh_install(ctx: Ctx) -> str:
    """MS-001: ports free, first ``up -d`` builds nothing, everything healthy, one recreate."""
    compose = ctx.compose
    assert compose is not None
    check_ports_free()
    proc = compose.up()
    ctx.refresh_env_secrets()
    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        raise CheckFailed("docker compose up failed: " + tail(out))
    if any(re.search(r"\bBuilding\b", ln) or re.search(r"\bBuilt$", ln.strip())
           for ln in out.splitlines()):
        raise CheckFailed("compose built an image on first up: " + tail(out))
    secs = wait_healthy(ctx, 15)
    # The entrypoint wrote generated secrets into .env, so a second `up -d` recreates the
    # services that read it, once (and only those).
    before = compose.container_map()
    proc2 = compose.up()
    ctx.refresh_env_secrets()
    if proc2.returncode != 0:
        raise CheckFailed("second docker compose up failed: " + tail(proc2.stderr))
    after = compose.container_map()
    changed = {s for s in after if s in before and after[s] != before[s]}
    extra = changed - ENV_READERS
    if extra:
        raise CheckFailed("second up recreated " + ", ".join(sorted(extra)))
    wait_healthy(ctx, 10)
    n = len(compose.services())
    return (f"{n} services healthy in {int(secs)}s; no Building; second up recreated "
            f"{', '.join(sorted(changed)) or 'nothing'}; LUMOGIS_VERSION pinned to {ctx.version} "
            "(deliberate: release moves only after the smoke)")


def claim_admin(ctx: Ctx, expect_signin: Optional[bool] = None) -> str:
    """SETUP-CLAIM: the first API call after healthy (keeps the open-claim window short)."""
    assert ctx.compose is not None
    env = ctx.compose.read_env()
    auth_on = env.get("AUTH_ENABLED", "").lower() == "true"
    ctx.api = Api(core_url(), "token" if auth_on else "none")
    if expect_signin is None:
        expect_signin = "signin" in ctx.expect
    if not auth_on:
        if expect_signin:
            raise CheckFailed("release ships sign-in off")
        raise NotApplicable(f"sign-in off in {ctx.version}")
    status, data = ctx.api.get("/api/v1/setup/status", auth=False)
    if status == 404:
        raise CheckFailed("sign-in is on but /api/v1/setup/status is 404")
    if status != 200 or not isinstance(data, dict):
        raise CheckFailed(f"setup/status answered {status}")
    if not data.get("needs_setup"):
        raise CheckFailed("setup is not open (needs_setup false) on a fresh install")
    password = _secrets.token_urlsafe(24)
    ctx.secret_values.add(password)
    status, data = ctx.api.post_json(
        "/api/v1/setup/claim", {"email": ADMIN_EMAIL, "password": password}, auth=False)
    if status != 200 or not isinstance(data, dict) or not data.get("access_token"):
        raise CheckFailed(f"claim answered {status}")
    ctx.secret_values.add(data["access_token"])
    ctx.api.set_session(ADMIN_EMAIL, password, data["access_token"])
    status, data = ctx.api.get("/api/v1/setup/status", auth=False)
    if status != 200 or data.get("needs_setup"):
        raise CheckFailed("setup still open after the claim")
    return "admin claimed; setup closed"


def parse_digests(path: Path) -> list[tuple[str, str, str]]:
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) == 3:
            rows.append((parts[0], parts[1], parts[2]))
    return rows


def docker_info_store(compose: Compose) -> str:
    proc = compose.docker("info", "--format", "{{.Driver}} {{json .DriverStatus}}", timeout=60)
    return tail(proc.stdout, 120)


def check_digests(ctx: Ctx, digests_file: Path, version: str) -> str:
    """Every running Lumogis service runs exactly the verified image digest."""
    compose = ctx.compose
    assert compose is not None
    cmap = compose.container_map()
    checked = []
    for repo, _v, digest in parse_digests(digests_file):
        image = repo.rsplit("/", 1)[-1]
        service = IMAGE_SERVICE.get(image)
        if service is None or service not in cmap:
            continue  # profile service that is not running in this suite
        proc = compose.docker("inspect", cmap[service], timeout=60)
        try:
            info = json.loads(proc.stdout)[0]
        except (ValueError, IndexError):
            raise CheckFailed(f"cannot inspect {service}: {tail(proc.stderr)}")
        iproc = compose.docker("image", "inspect", info.get("Image", ""), timeout=60)
        try:
            repo_digests = json.loads(iproc.stdout)[0].get("RepoDigests", [])
        except (ValueError, IndexError):
            repo_digests = []
        if f"{repo}@{digest}" not in repo_digests:
            raise CheckFailed(
                f"{image}: want {digest[:19]}, found {repo_digests}; image store: "
                f"{docker_info_store(compose)}"[:300])
        env = info.get("Config", {}).get("Env", []) or []
        wanted = {f"LUMOGIS_IMAGE_VERSION={version}", f"LUMOGIS_IMAGE_VERSION={version_base(version)}"}
        if not wanted.intersection(env):
            raise CheckFailed(f"{image}: LUMOGIS_IMAGE_VERSION is not {version}")
        checked.append(service)
    if not checked:
        raise CheckFailed("no running service matched image-digests.txt")
    return "digests match for " + ", ".join(sorted(checked))


def steady_state(ctx: Ctx) -> str:
    compose = ctx.compose
    assert compose is not None
    before = compose.container_map()
    if not before:
        raise CheckFailed("no containers listed before pull && up; nothing to compare")
    p = compose.pull()
    if p.returncode != 0:
        raise CheckFailed("pull failed: " + tail(p.stderr))
    u = compose.up()
    if u.returncode != 0:
        raise CheckFailed("up failed: " + tail(u.stderr))
    after = compose.container_map()
    changed = sorted(s for s in before if after.get(s) != before[s])
    if changed:
        raise CheckFailed("pull && up recreated " + ", ".join(changed))
    return f"{len(before)} containers unchanged by pull && up -d"


def reachability(ctx: Ctx) -> str:
    # /healthz: runs before the claim, and /health needs a token with sign-in on.
    status, data = ctx.api.get("/healthz", auth=False)
    if status != 200:
        raise CheckFailed(f"/healthz answered {status}")
    req = urllib.request.Request(web_url() + "/")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read(2048).decode("utf-8", "replace").lower()
            code = resp.status
    except Exception as exc:  # noqa: BLE001
        raise CheckFailed(f"Caddy GET / failed: {exc}")
    if code != 200 or "<html" not in body:
        raise CheckFailed(f"Caddy GET / gave {code} without HTML")
    return "/healthz 200; Caddy serves HTML"


def ingest_fixture(ctx: Ctx) -> str:
    data = (FIXTURES / FIXTURE_DOC).read_bytes()
    status, body = ctx.api.post_multipart(
        "/api/v1/ingest/upload", {}, {"file": (FIXTURE_DOC, "text/plain", data)})
    if status != 202 or not isinstance(body, dict) or "job_id" not in body:
        raise CheckFailed(f"upload answered {status}: {tail(str(body), 120)}")
    job = body["job_id"]
    last: dict = {}

    def done() -> bool:
        st, b = ctx.api.get(f"/api/v1/ingest/jobs/{job}")
        if isinstance(b, dict):
            last.update(b)
            if b.get("stage") == "failed" or b.get("status") == "failed":
                raise CheckFailed(f"ingest failed: {b.get('error') or b.get('status_message')}")
            return b.get("stage") == "done"
        return False

    ctx.wait(done, 600, 10, "ingest job done")
    return f"job {job} done"


def is_fixture_path(file_path: str) -> bool:
    """True when a search hit's file_path is the uploaded fixture.

    Core stores an upload as ``<file_id>_<basename>`` (routes/api_v1/ingest.py),
    so the stored name is never the bare fixture name.
    """
    name = Path(file_path).name
    return name == FIXTURE_DOC or name.endswith("_" + FIXTURE_DOC)


def search_hits(ctx: Ctx) -> str:
    def hit() -> bool:
        status, data = ctx.api.get("/search?q=" + PHRASE.replace(" ", "%20"))
        if status != 200 or not isinstance(data, list):
            return False
        return any(is_fixture_path(str(r.get("file_path", "")))
                   for r in data if isinstance(r, dict))

    ctx.wait(hit, 120, 10, f"search hit for {FIXTURE_DOC}")
    return f"search finds {FIXTURE_DOC}"


def health_counts(ctx: Ctx) -> dict:
    # /health needs a token when sign-in is on; Api sends one only if it has one.
    status, data = ctx.api.get("/health")
    if status != 200 or not isinstance(data, dict):
        raise CheckFailed(f"/health answered {status}")
    return data


def diagnostics_ok(ctx: Ctx) -> str:
    h = health_counts(ctx)
    if not (h.get("qdrant_doc_count", 0) > 0 and h.get("total_chunks_indexed", 0) > 0):
        raise CheckFailed(f"nothing indexed: qdrant_doc_count={h.get('qdrant_doc_count')}, "
                          f"total_chunks_indexed={h.get('total_chunks_indexed')}")
    status, diag = ctx.api.get("/api/v1/admin/diagnostics")
    if status != 200:
        raise CheckFailed(f"/admin/diagnostics answered {status}")
    if isinstance(diag, dict) and diag.get("error"):
        raise CheckFailed(f"diagnostics reports error: {diag.get('error')}")
    for sub in ("stack-status", "backup-status"):
        st, _ = ctx.api.get(f"/api/v1/admin/diagnostics/{sub}")
        if st != 200:
            raise CheckFailed(f"{sub} answered {st}")
    return (f"qdrant_doc_count={h.get('qdrant_doc_count')}, "
            f"total_chunks_indexed={h.get('total_chunks_indexed')}")


# ---- voice

def voice_profile(ctx: Ctx) -> str:
    assert ctx.compose is not None
    profiles = ctx.compose.read_env().get("COMPOSE_PROFILES", "").split(",")
    has = "stt" in [p.strip() for p in profiles]
    if not has:
        if "stt" in ctx.expect:
            raise CheckFailed("speech-to-text expected but 'stt' not in COMPOSE_PROFILES")
        raise NotApplicable(f"no speech-to-text in {ctx.version}")
    running = ctx.compose.container_map()
    if not any("stt" in s for s in running):
        raise CheckFailed("'stt' profile on but the Speaches service is not running")
    return "stt profile on; Speaches running"


def normalise_words(text: str) -> list[str]:
    return re.sub(r"[^a-z ]", " ", text.lower()).split()


def voice_transcribe(ctx: Ctx) -> str:
    if ctx.status.get("VOICE-PROFILE") == "na":
        raise NotApplicable("no speech-to-text profile")

    def ready() -> bool:
        st, d = ctx.api.get("/api/v1/admin/diagnostics")
        stt = d.get("speech_to_text", {}) if isinstance(d, dict) else {}
        return stt.get("model_state") == "ready"

    ctx.wait(ready, 900, 10, "speech_to_text.model_state ready")
    wav = (FIXTURES / "smoke-voice.wav").read_bytes()
    status, data = ctx.api.post_multipart(
        "/api/v1/voice/transcribe", {}, {"file": ("smoke-voice.wav", "audio/wav", wav)}, timeout=180)
    if status != 200 or not isinstance(data, dict):
        raise CheckFailed(f"transcribe answered {status}: {tail(str(data), 120)}")
    text = str(data.get("text", ""))
    want = VOICE_SENTENCE.split()
    got = set(normalise_words(text))
    hits = sum(1 for w in want if w in got)
    if hits < 7:
        raise CheckFailed(f"{hits}/{len(want)} words; text={text!r}")
    return f"{hits}/{len(want)} words; text={text!r}; provider={data.get('provider')}; model={data.get('model')}"


# ---- egress (MS-005)

def egress_rules(ips: list[str]) -> list[list[str]]:
    """Rules appended to the chain, per orchestrator IP: private RETURNs, LOG, REJECT."""
    rules: list[list[str]] = []
    for ip in ips:
        for net in PRIVATE_RANGES:
            rules.append(["-s", ip, "-d", net, "-j", "RETURN"])
        rules.append(["-s", ip, "-m", "conntrack", "--ctstate", "NEW", "-j", "LOG",
                      "--log-prefix", "lumogis-smoke-egress "])
        rules.append(["-s", ip, "-m", "conntrack", "--ctstate", "NEW", "-j", "REJECT"])
    return rules


def _ipt(*argv: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["sudo", "-n", "iptables", *argv], text=True, capture_output=True,
                              check=False, timeout=60)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(argv, 127, "", str(exc))


def egress_rules_remove() -> None:
    for _ in range(5):
        if _ipt("-D", "DOCKER-USER", "-j", EGRESS_CHAIN).returncode != 0:
            break
    _ipt("-F", EGRESS_CHAIN)
    _ipt("-X", EGRESS_CHAIN)


def egress_rules_install(ips: list[str]) -> None:
    if _ipt("-S", "DOCKER-USER").returncode != 0:
        raise CheckFailed("DOCKER-USER chain missing (or iptables unusable)")
    for argv in ([["-N", EGRESS_CHAIN]]
                 + [["-A", EGRESS_CHAIN, *r] for r in egress_rules(ips)]
                 + [["-I", "DOCKER-USER", "1", "-j", EGRESS_CHAIN]]):
        proc = _ipt(*argv)
        if proc.returncode != 0:
            raise CheckFailed("iptables " + " ".join(argv[:3]) + " failed: " + tail(proc.stderr))


def egress_rules_count() -> int:
    """Sum of the ``pkts`` column over every REJECT rule in the chain."""
    proc = _ipt("-L", EGRESS_CHAIN, "-v", "-n", "-x")
    total = 0
    for line in (proc.stdout or "").splitlines():
        cols = line.split()
        if len(cols) > 3 and "REJECT" in cols and cols[0].isdigit():
            total += int(cols[0])
    return total


def egress_rules_zero() -> None:
    _ipt("-Z", EGRESS_CHAIN)


def orchestrator_ips(compose: Compose) -> list[str]:
    cid = compose.container_map().get("orchestrator")
    if not cid:
        raise CheckFailed("orchestrator is not running")
    proc = compose.docker("inspect", cid, timeout=60)
    try:
        nets = json.loads(proc.stdout)[0]["NetworkSettings"]["Networks"]
    except (ValueError, IndexError, KeyError):
        raise CheckFailed("cannot read the orchestrator's networks")
    ips = [n.get("IPAddress") for n in nets.values() if n.get("IPAddress")]
    if not ips:
        raise CheckFailed("orchestrator has no container IP")
    return ips


def rejected_destinations() -> str:
    for cmd in (["journalctl", "-k", "--grep", "lumogis-smoke-egress", "--no-pager", "-n", "20"],
                ["dmesg"]):
        try:
            proc = subprocess.run(cmd, text=True, capture_output=True, check=False, timeout=30)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
        dests = re.findall(r"DST=([0-9.]+).*?DPT=(\d+)", proc.stdout or "")
        if dests:
            return ", ".join(sorted({f"{d}:{p}" for d, p in dests}))[:150]
    return "destinations unavailable"


def egress_guarded_chat(ctx: Ctx) -> str:
    compose = ctx.compose
    assert compose is not None
    status, pm = ctx.api.get("/api/v1/me/privacy-mode")
    if status != 200 or not isinstance(pm, dict):
        raise CheckFailed(f"privacy-mode answered {status}")
    if pm.get("privacy_effective") != "local_only":
        raise CheckFailed(f"privacy mode is not local_only: {pm.get('privacy_effective')}")

    def model_present() -> bool:
        proc = compose.exec("ollama", "ollama", "list")
        return "llama3.2" in (proc.stdout or "")

    ctx.wait(model_present, 900, 15, "chat model llama3.2:3b pulled")
    # Sign in again BEFORE the window, never during it: the window makes no extra calls.
    if ctx.api.auth == "token" and ctx.api.token_age() > 600:
        if not ctx.api.relogin():
            raise CheckFailed("could not sign in again before the egress window")
    ips = orchestrator_ips(compose)
    egress_rules_remove()
    try:
        egress_rules_install(ips)
        # Negative control: a public destination must be rejected AND counted.
        ctrl = compose.exec(
            "orchestrator", "python", "-c",
            "import urllib.request; urllib.request.urlopen('https://api.anthropic.com', timeout=5)",
            timeout=60)
        if ctrl.returncode == 0:
            raise CheckFailed("egress rule proves nothing: the control connection succeeded")
        if egress_rules_count() < 1:
            raise CheckFailed("egress rule proves nothing: control failed but nothing was counted")
        egress_rules_zero()
        start = _clock()
        status, data = ctx.api.post_json(
            "/api/v1/chat/completions",
            {"model": "llama", "messages": [{"role": "user",
                                              "content": "Reply with the single word: ready"}],
             "stream": False}, timeout=300)
        elapsed = _clock() - start
        if status == 0:
            raise CheckFailed(f"chat did not answer in 300 s (elapsed {int(elapsed)}s): "
                              f"{tail(str(data), 100)}")
        hits = egress_rules_count()
        if hits:
            raise CheckFailed(f"{hits} outbound connection(s) during local chat: "
                              f"{rejected_destinations()}")
        if status != 200 or not isinstance(data, dict):
            raise CheckFailed(f"chat answered {status}: {tail(str(data), 120)}")
        content = (data.get("message") or {}).get("content", "")
        if not str(content).strip():
            raise CheckFailed("chat answered with empty content")
        model = str(data.get("model", ""))
        if "llama" not in model.lower():
            raise CheckFailed(f"chat was answered by {model!r}, not the local model")
        return f"local model {model} answered in {int(elapsed)}s; 0 outbound connections"
    finally:
        egress_rules_remove()


# ---- restart / stop-start

def restart_check(ctx: Ctx) -> str:
    compose = ctx.compose
    assert compose is not None
    ver = compose.exec("stack-control", "docker", "version", "--format", "{{.Client.APIVersion}}")
    if ver.returncode != 0 or not (ver.stdout or "").strip():
        raise CheckFailed("docker client in stack-control: " + tail(ver.stderr or ver.stdout))
    srv = compose.exec("stack-control", "docker", "version", "--format", "{{.Server.Version}}")
    if srv.returncode != 0 or not (srv.stdout or "").strip():
        raise CheckFailed("stack-control cannot reach the host engine: " + tail(srv.stderr))
    cv = compose.exec("stack-control", "docker", "compose", "version", "--short")
    major = (cv.stdout or "").strip().lstrip("v").split(".")[0]
    if cv.returncode != 0 or not major.isdigit() or int(major) < 2:
        raise CheckFailed("compose plugin in stack-control: " + tail(cv.stdout + cv.stderr))
    before = compose.container_map().get("orchestrator")
    if not before:
        raise CheckFailed("orchestrator is not in docker compose ps before the restart")
    ctx.api.request("POST", "/settings/restart", timeout=10)  # the answer may never arrive

    def recreated() -> bool:
        now = compose.container_map().get("orchestrator")
        if not now or now == before:
            return False
        st, _ = ctx.api.get("/healthz", auth=False)
        return st == 200

    ctx.wait(recreated, 300, 5, "orchestrator recreated and healthy")
    return f"stack-control: Compose {cv.stdout.strip()}; orchestrator recreated, /healthz 200"


def stop_start(ctx: Ctx) -> str:
    compose = ctx.compose
    assert compose is not None
    d = compose.down()
    if d.returncode != 0:
        raise CheckFailed("down failed: " + tail(d.stderr))
    u = compose.up()
    ctx.refresh_env_secrets()
    if u.returncode != 0:
        raise CheckFailed("up failed: " + tail(u.stderr))
    wait_healthy(ctx, 10)
    search_hits(ctx)
    status, _ = ctx.api.get("/api/v1/auth/me")
    if status != 200:
        raise CheckFailed(f"/auth/me answered {status} after restart")
    return "data and sign-in survived down && up -d"


def footprint(ctx: Ctx) -> str:
    compose = ctx.compose
    assert compose is not None
    disk = shutil.disk_usage("/")
    stats = compose.docker("stats", "--no-stream", "--format", "{{.Name}} {{.MemUsage}}", timeout=60)
    sysdf = compose.docker("system", "df", timeout=60)
    extra = ""
    if ctx.suite == "graph":
        ol = compose.exec("ollama", "ollama", "list")
        extra = "; chat model " + ("pulled" if "llama3.2" in (ol.stdout or "") else "absent")
    return (f"disk used {disk.used // 2**30} GiB of {disk.total // 2**30} GiB; mem: "
            f"{tail(stats.stdout, 120)}; docker: {tail(sysdf.stdout, 80)}{extra}")


# --------------------------------------------------------------------------- suites

def _start(ctx: Ctx) -> None:
    ctx.start()
    ctx.refresh_env_secrets()


def suite_install(ctx: Ctx) -> None:
    _start(ctx)
    egress_rules_remove()  # a stale chain from a killed run must not poison MS-005
    try:
        ctx.run("FILES-001", lambda: verify_files(ctx.files_dir))
        if ctx.status.get("FILES-001") == "fail":
            return
        prepare_install(ctx, ctx.files_dir, ctx.version)
        ctx.run("MS-001", lambda: fresh_install(ctx), after=("FILES-001",))
        ctx.run("SETUP-CLAIM", lambda: claim_admin(ctx), after=("MS-001",))
        digests = ctx.files_dir / "image-digests.txt"
        ctx.run("MS-001-DIGESTS", lambda: check_digests(ctx, digests, ctx.version), after=("MS-001",))
        ctx.run("MS-001-STEADY", lambda: steady_state(ctx), after=("MS-001",))
        ctx.run("MS-003", lambda: reachability(ctx), after=("MS-001",))
        both = ("MS-001", "SETUP-CLAIM")
        ctx.run("MS-004", lambda: (ingest_fixture(ctx), search_hits(ctx))[1], after=both)
        ctx.run("MS-008", lambda: diagnostics_ok(ctx), after=("MS-004",))
        ctx.run("VOICE-PROFILE", lambda: voice_profile(ctx), after=("MS-001",))
        ctx.run("VOICE", lambda: voice_transcribe(ctx), after=("VOICE-PROFILE",))
        ctx.run("MS-005", lambda: egress_guarded_chat(ctx), after=both)
        ctx.run("MS-011", lambda: restart_check(ctx), after=both)
        ctx.run("MS-010", lambda: stop_start(ctx), after=both + ("MS-004",))
        ctx.run("RUNNER-FOOTPRINT", lambda: footprint(ctx), required=False)
    finally:
        egress_rules_remove()
        _finish(ctx)


def _finish(ctx: Ctx) -> None:
    ctx.finish_unrun()
    if ctx.compose is not None:
        with contextlib.suppress(Exception):
            ctx.compose.write_logs(Path(ctx.out).parent / "logs" / f"{ctx.suite}-{ctx.arch}.log")


def graph_status(ctx: Ctx) -> tuple[int, Any]:
    return ctx.api.get("/api/v1/admin/knowledge-graph")


def profiles_of(compose: Compose) -> list[str]:
    return [p.strip() for p in compose.read_env().get("COMPOSE_PROFILES", "").split(",") if p.strip()]


def graph_on(ctx: Ctx) -> str:
    compose = ctx.compose
    assert compose is not None
    status, _ = graph_status(ctx)
    if status == 404:
        if "graph-switch" in ctx.expect:
            raise CheckFailed("graph switch expected but the route is 404")
        raise NotApplicable(f"no graph switch in {ctx.version}")
    status, data = ctx.api.post_json("/api/v1/admin/knowledge-graph/enable",
                                     {"confirm_low_memory": False})
    if status == 409:
        raise CheckFailed(f"enable refused: {data.get('detail') if isinstance(data, dict) else data}")
    if status != 202:
        raise CheckFailed(f"enable answered {status}")

    def on() -> bool:
        st, d = graph_status(ctx)
        state = d.get("state") if isinstance(d, dict) else None
        if state in ("start_failed", "service_wiring_failed", "unsupported_mode"):
            raise CheckFailed(f"graph state {state}")
        return state == "on"

    ctx.wait(on, 35 * 60, 30, "graph state on")
    profiles = profiles_of(compose)
    if "graph" not in profiles:
        raise CheckFailed(f"COMPOSE_PROFILES lacks graph: {profiles}")
    if "stt" in ctx.expect and "stt" not in profiles:
        raise CheckFailed(f"graph on dropped stt from COMPOSE_PROFILES: {profiles}")
    digests = ctx.files_dir / "image-digests.txt"
    return check_digests(ctx, digests, ctx.version)


def graph_entities(ctx: Ctx) -> str:
    ingest_fixture(ctx)
    ctx.wait(lambda: health_counts(ctx).get("entity_count", 0) > 0, 600, 10, "entity_count > 0")
    return f"entity_count={health_counts(ctx).get('entity_count')}"


def falkor_node_count(compose: Compose) -> int:
    proc = compose.exec("falkordb", "redis-cli", "GRAPH.QUERY", "lumogis",
                        "MATCH (n) RETURN count(n)")
    nums = re.findall(r"^\s*(\d+)\s*$", proc.stdout or "", re.M)
    if proc.returncode != 0 or not nums:
        raise CheckFailed("cannot count FalkorDB nodes: " + tail(proc.stderr or proc.stdout))
    return int(nums[0])


def step_backup(ctx: Ctx) -> str:
    """Run the documented backup; return the new snapshot stamp."""
    compose = ctx.compose
    assert compose is not None
    snaps = compose.work / "ai-workspace" / "backups" / "snapshots"
    before = {p.name for p in snaps.iterdir()} if snaps.exists() else set()
    proc = compose.run_t("backup", "/scripts/backup/backup.sh", "run")
    if proc.returncode != 0:
        raise CheckFailed("backup.sh run failed: " + tail(proc.stderr or proc.stdout))
    after = {p.name for p in snaps.iterdir()} if snaps.exists() else set()
    new = sorted(n for n in after - before if STAMP_RE.match(n))
    if not new:
        raise CheckFailed("backup made no new snapshot directory")
    ctx.facts["stamp"] = new[-1]
    return new[-1]


def graph_backup(ctx: Ctx) -> str:
    compose = ctx.compose
    assert compose is not None
    stamp = step_backup(ctx)
    dump = compose.work / "ai-workspace" / "backups" / "snapshots" / stamp / "falkordb" / "dump.rdb"
    if not dump.exists():
        raise CheckFailed(f"snapshot {stamp} has no falkordb/dump.rdb")
    ctx.facts["entities"] = health_counts(ctx).get("entity_count")
    ctx.facts["nodes"] = falkor_node_count(compose)
    return f"snapshot {stamp}; entity_count={ctx.facts['entities']}; graph nodes={ctx.facts['nodes']}"


def restore_stamp(compose: Compose, stamp: str, *, stop: Optional[list[str]] = None) -> None:
    if not STAMP_RE.match(stamp or ""):
        raise CheckFailed(f"refusing snapshot stamp {stamp!r}")
    proc = compose.run_t("backup", "/scripts/backup/restore.sh", stamp, "--yes",
                         env={"RESTORE_CONFIRM": "1", "RESTORE_SNAPSHOT": stamp}, no_deps=True)
    if proc.returncode != 0:
        raise CheckFailed("restore.sh failed: " + tail(proc.stderr or proc.stdout))


def graph_restore(ctx: Ctx) -> str:
    """Wipe Postgres, Qdrant and FalkorDB, restore, and prove the data came back.

    Follows scripts/integration-backup-roundtrip.sh:318-371 step for step.
    """
    compose = ctx.compose
    assert compose is not None
    stamp = ctx.facts.get("stamp", "")
    if not STAMP_RE.match(stamp):
        raise CheckFailed(f"refusing snapshot stamp {stamp!r}")
    # `down` (no -v), not `stop`: Docker refuses `volume rm` while a stopped container
    # still references the volume, which would turn the wipe into a silent no-op.
    compose.compose("down", timeout=600)
    project = compose.read_env().get("COMPOSE_PROJECT_NAME", "lumogis")
    vols = compose.compose("config", "--volumes").stdout.split()
    wipe = [v for v in vols if re.search(r"postgres|qdrant|falkordb", v)]
    if len(wipe) < 3:
        raise CheckFailed(f"expected postgres, qdrant and falkordb volumes, found {wipe}")
    for v in wipe:
        rm = compose.docker("volume", "rm", f"{project}_{v}", timeout=120)
        if rm.returncode != 0:
            raise CheckFailed(f"could not wipe volume {project}_{v}: " + tail(rm.stderr or rm.stdout))
    compose.compose("up", "-d", "--wait", "postgres", "qdrant", "falkordb", timeout=900)
    wiped = falkor_node_count(compose)
    if wiped != 0:
        raise CheckFailed(f"wipe did not empty FalkorDB ({wiped} nodes); restore would prove nothing")
    compose.compose("stop", "falkordb", timeout=120)
    restore_stamp(compose, stamp)
    compose.up()
    wait_healthy(ctx, 15)
    entities = health_counts(ctx).get("entity_count")
    nodes = falkor_node_count(compose)
    if entities != ctx.facts.get("entities") or nodes != ctx.facts.get("nodes"):
        raise CheckFailed(f"counts differ after restore: entity_count {entities} vs "
                          f"{ctx.facts.get('entities')}, graph nodes {nodes} vs {ctx.facts.get('nodes')}")
    search_hits(ctx)
    return f"restored {stamp}: entity_count={entities}, graph nodes={nodes}"


def graph_off(ctx: Ctx) -> str:
    compose = ctx.compose
    assert compose is not None
    status, _ = ctx.api.post_json("/api/v1/admin/knowledge-graph/disable", {})
    if status != 202:
        raise CheckFailed(f"disable answered {status}")

    def off() -> bool:
        st, d = graph_status(ctx)
        state = d.get("state") if isinstance(d, dict) else None
        if state == "stop_failed":
            raise CheckFailed("graph state stop_failed")
        return state in ("off", "off_low_memory")

    ctx.wait(off, 15 * 60, 15, "graph state off")
    wait_healthy(ctx, 10)
    if "stt" in ctx.expect and "stt" not in profiles_of(compose):
        raise CheckFailed("graph off dropped stt from COMPOSE_PROFILES")
    search_hits(ctx)
    return "graph off; services healthy; search still hits"


def suite_graph(ctx: Ctx) -> None:
    _start(ctx)
    try:
        ctx.run("FILES-001", lambda: verify_files(ctx.files_dir))
        if ctx.status.get("FILES-001") == "fail":
            return
        prepare_install(ctx, ctx.files_dir, ctx.version)
        ctx.run("MS-001", lambda: fresh_install(ctx), after=("FILES-001",))
        ctx.run("SETUP-CLAIM", lambda: claim_admin(ctx), after=("MS-001",))
        base = ("MS-001", "SETUP-CLAIM")
        ctx.run("GRAPH-ON", lambda: graph_on(ctx), after=base)
        if ctx.status.get("GRAPH-ON") == "na":
            for cid in ("GRAPH-ENTITIES", "GRAPH-BACKUP", "GRAPH-RESTORE", "GRAPH-OFF"):
                ctx.record(cid, "na", f"no graph switch in {ctx.version}", 0, True)
        else:
            ctx.run("GRAPH-ENTITIES", lambda: graph_entities(ctx), after=("GRAPH-ON",))
            ctx.run("GRAPH-BACKUP", lambda: graph_backup(ctx), after=("GRAPH-ENTITIES",))
            ctx.run("GRAPH-RESTORE", lambda: graph_restore(ctx), after=("GRAPH-BACKUP",))
            ctx.run("GRAPH-OFF", lambda: graph_off(ctx), after=("GRAPH-ON",))
        ctx.run("RUNNER-FOOTPRINT", lambda: footprint(ctx), required=False)
    finally:
        _finish(ctx)


# ---- upgrade

def update_status_check(ctx: Ctx) -> str:
    st, d = ctx.api.get("/api/v1/admin/diagnostics/update-status")
    if st != 200 or not isinstance(d, dict):
        raise CheckFailed(f"update-status answered {st}")
    if d.get("current_version") != version_base(ctx.version):
        raise CheckFailed(f"current_version {d.get('current_version')!r} != {version_base(ctx.version)!r}")
    return f"current_version {d.get('current_version')}"


def suite_upgrade(ctx: Ctx) -> None:
    _start(ctx)
    try:
        ctx.run("FILES-001", lambda: verify_files(ctx.files_dir))
        if ctx.status.get("FILES-001") == "fail":
            return
        if not ctx.previous_version or ctx.previous_dir is None:
            for cid in ("UPGRADE-PREV", "UPGRADE-PULL", "UPGRADE-SIGNIN", "UPGRADE-BACK"):
                ctx.record(cid, "na", "no previous stable release with the two install files", 0, True)
            return
        prev_dir = Path(ctx.previous_dir)
        prev_auth_on = {"v": False}

        def prev() -> str:
            verify_files(prev_dir)
            compose = prepare_install(ctx, prev_dir, ctx.previous_version)
            check_ports_free()
            p = compose.up()
            ctx.refresh_env_secrets()
            if p.returncode != 0:
                raise CheckFailed("previous up failed: " + tail(p.stderr))
            wait_healthy(ctx, 15)
            compose.up()  # the same one recreate a household sees
            ctx.refresh_env_secrets()
            wait_healthy(ctx, 10)
            prev_auth_on["v"] = compose.read_env().get("AUTH_ENABLED", "").lower() == "true"
            try:
                claim_admin(ctx, expect_signin=False)
            except NotApplicable:
                pass
            ctx.facts["prev_api"] = ctx.api
            ingest_fixture(ctx)
            search_hits(ctx)
            shutil.copyfile(compose.env_file, ctx.work / ".env.before-update")
            ctx.facts["stamp"] = ""
            step_backup(ctx)
            return f"{ctx.previous_version} installed, ingested, backed up as {ctx.facts['stamp']}"

        ctx.run("UPGRADE-PREV", prev, after=("FILES-001",))

        def pull() -> str:
            compose = ctx.compose
            assert compose is not None
            before_env = compose.read_env()
            shutil.copyfile(Path(ctx.files_dir) / "docker-compose.yml", ctx.work / "docker-compose.yml")
            compose.set_env("LUMOGIS_VERSION", ctx.version)
            p = compose.pull()
            if p.returncode != 0:
                raise CheckFailed("pull failed: " + tail(p.stderr))
            u = compose.up()
            ctx.refresh_env_secrets()
            if u.returncode != 0:
                raise CheckFailed("up failed: " + tail(u.stderr))
            wait_healthy(ctx, 15)
            now_env = compose.read_env()
            for key in ("AUTH_ENABLED", "COMPOSE_PROFILES"):
                if now_env.get(key) != before_env.get(key):
                    raise CheckFailed(f"{key} changed by the update")
            prof_before = [p.strip() for p in before_env.get("COMPOSE_PROFILES", "").split(",")]
            if "stt" not in prof_before and any("stt" in s for s in compose.container_map()):
                raise CheckFailed("the update switched speech-to-text on")
            if now_env.get("AUTH_ENABLED", "").lower() == "true":
                st, _ = ctx.api.post_json("/api/v1/auth/login",
                                          {"email": ADMIN_EMAIL, "password": ctx.api.password or ""},
                                          auth=False)
                if st != 200:
                    raise CheckFailed(f"previous admin cannot sign in after the update ({st})")
            else:
                st, d = ctx.api.get("/api/v1/setup/status", auth=False)
                if st == 200 and isinstance(d, dict) and d.get("needs_setup"):
                    raise CheckFailed("sign-in off but setup is open after the update")
                me, _ = ctx.api.get("/api/v1/auth/me")
                if me != 200:
                    raise CheckFailed(f"/auth/me answered {me} after the update")
            search_hits(ctx)
            return update_status_check(ctx)

        ctx.run("UPGRADE-PULL", pull, after=("UPGRADE-PREV",))

        def signin() -> str:
            compose = ctx.compose
            assert compose is not None
            if prev_auth_on["v"] or "signin" not in ctx.expect:
                raise NotApplicable("previous release already had sign-in on, or not expected")
            compose.set_env("AUTH_ENABLED", "true")
            compose.up()
            ctx.refresh_env_secrets()
            wait_healthy(ctx, 10)
            if not compose.read_env().get("AUTH_SECRET"):
                raise CheckFailed("entrypoint did not write AUTH_SECRET")
            claim_admin(ctx)
            search_hits(ctx)
            return "sign-in turned on, claimed, old data found under the new admin"

        ctx.run("UPGRADE-SIGNIN", signin, after=("UPGRADE-PULL",))

        def back() -> str:
            compose = ctx.compose
            assert compose is not None
            stamp = ctx.facts.get("stamp", "")
            if not STAMP_RE.match(stamp):
                raise CheckFailed(f"refusing snapshot stamp {stamp!r}")
            shutil.copyfile(prev_dir / "docker-compose.yml", ctx.work / "docker-compose.yml")
            shutil.copyfile(ctx.work / ".env.before-update", compose.env_file)
            s = compose.compose("stop", "orchestrator", "lumogis-web", "falkordb", timeout=300)
            if s.returncode != 0:
                raise CheckFailed("documented stop failed (doc bug): " + tail(s.stderr))
            # upgrading.md § Going back step 2: the newer release's tables would
            # otherwise stop the older restore (found by the 0.14.0-rc.1 smoke).
            e = compose.compose("exec", "-T", "postgres", "sh", "-c", RESET_SCHEMA_SH, timeout=120)
            if e.returncode != 0:
                raise CheckFailed("documented schema reset failed (doc bug): " + tail(e.stderr))
            restore_stamp(compose, stamp)
            u = compose.up()
            ctx.refresh_env_secrets()
            if u.returncode != 0:
                raise CheckFailed("up failed: " + tail(u.stderr))
            wait_healthy(ctx, 15)
            ctx.api = ctx.facts["prev_api"]  # the previous release's own session (or none)
            search_hits(ctx)
            digests = prev_dir / "image-digests.txt"
            check_digests(ctx, digests, ctx.previous_version)
            return f"back on {ctx.previous_version} using .env.before-update; previous digests match"

        ctx.run("UPGRADE-BACK", back, after=("UPGRADE-PULL",))
    finally:
        _finish(ctx)


# ---- secure address

def lan_address() -> str:
    proc = subprocess.run(["ip", "-4", "route", "get", "1.1.1.1"], text=True, capture_output=True,
                          check=False, timeout=30)
    m = re.search(r"\bsrc\s+(\d+\.\d+\.\d+\.\d+)", proc.stdout or "")
    if not m:
        raise CheckFailed("runner has no private address")
    ip = m.group(1)
    a, b = int(ip.split(".")[0]), int(ip.split(".")[1])
    if not (a == 10 or (a == 172 and 16 <= b <= 31) or (a == 192 and b == 168)):
        raise CheckFailed(f"runner has no private address ({ip} is public)")
    return ip


def secure_address_run(ctx: Ctx) -> str:
    compose = ctx.compose
    assert compose is not None
    ip = lan_address()
    registered = {"v": False}
    try:
        status, _ = ctx.api.post_json("/api/v1/admin/secure-address/enable", {"lan_address": ip})
        if not 200 <= status < 300:
            raise CheckFailed(f"enable answered {status}")
        registered["v"] = True
        last = {}

        def ready() -> bool:
            st, d = ctx.api.get("/api/v1/admin/secure-address")
            if isinstance(d, dict):
                last.update(d)
                if d.get("state") in ("error", "router_blocked"):
                    raise CheckFailed(f"state {d.get('state')}: {d.get('error_code')}")
                return d.get("state") == "ready"
            return False

        ctx.wait(ready, 20 * 60, 10, "secure address ready")
        name = str(last.get("name") or "")
        sc = subprocess.run(
            ["openssl", "s_client", "-connect", "127.0.0.1:443", "-servername", name],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False, timeout=60)
        proc = subprocess.run(
            ["openssl", "x509", "-noout", "-issuer", "-ext", "subjectAltName"],
            input=sc.stdout, capture_output=True, text=True, check=False, timeout=60)
        cert = proc.stdout or ""
        if "(STAGING)" not in cert:
            raise CheckFailed("CI value got a production certificate" if cert else "no certificate presented")
        if name not in cert:
            raise CheckFailed(f"certificate SAN does not name {name}")
        st, _ = ctx.api.post_json("/api/v1/admin/secure-address/disable", {})
        registered["v"] = False
        if not 200 <= st < 300:
            raise CheckFailed(f"disable answered {st}")

        def gone() -> bool:
            _, d = ctx.api.get("/api/v1/admin/secure-address")
            return isinstance(d, dict) and d.get("state") == "off"

        ctx.wait(gone, 300, 10, "secure address off")
        # The state goes to off at once; the name service deletes the record in the
        # background (after_disable), so poll up to 5 min for it to stop resolving.
        answers: list[str] = []

        def unresolved() -> bool:
            dig = subprocess.run(["dig", "+short", name, "A", "@1.1.1.1"], text=True,
                                 capture_output=True, check=False, timeout=30)
            answers[:] = [(dig.stdout or "").strip()]
            return not answers[0]

        ok, _, _ = poll(unresolved, 300, 10, "name stops resolving", ctx.deadline)
        if not ok:
            raise CheckFailed(f"{name} still resolves to {answers[0][:40]} after 5 min")
        return f"staging certificate for {name}; disabled; name no longer resolves"
    finally:
        if registered["v"]:
            with contextlib.suppress(Exception):
                ctx.api.post_json("/api/v1/admin/secure-address/disable", {})


def suite_secure_address(ctx: Ctx) -> None:
    _start(ctx)
    try:
        ctx.run("FILES-001", lambda: verify_files(ctx.files_dir))
        if ctx.status.get("FILES-001") == "fail":
            return
        value = os.environ.get("SMOKE_HOME_DNS_CI_VALUE", "")
        ctx.secret_values.add(value)
        if not ctx.required_secure:
            ctx.record("SECURE-ADDRESS", "na", "CI scope not declared deployed", 0, False)
            return
        if not value:
            ctx.record("SECURE-ADDRESS", "fail", "CI registration value not configured", 0, True)
            return

        def full() -> str:
            compose = prepare_install(ctx, ctx.files_dir, ctx.version, extra_env={
                "LUMOGIS_HOME_DNS_REGISTRATION_SECRET": value,
                "LUMOGIS_HOME_DNS_ACME_STAGING": "true",
            })
            fresh_install(ctx)
            claim_admin(ctx)
            return secure_address_run(ctx)

        ctx.run("SECURE-ADDRESS", full, required=True)
    finally:
        _finish(ctx)


# ---- cloud LLM

def suite_cloud_llm(ctx: Ctx) -> None:
    _start(ctx)
    try:
        key = os.environ.get("SMOKE_CLOUD_LLM_KEY", "")
        ctx.secret_values.add(key)
        if not key:
            ctx.na_all("no SMOKE_CLOUD_LLM_KEY")
            return
        ctx.run("FILES-001", lambda: verify_files(ctx.files_dir))
        if ctx.status.get("FILES-001") == "fail":
            return
        prepare_install(ctx, ctx.files_dir, ctx.version, extra_env={"OLLAMA_EXTRA_MODELS": ""})
        ctx.run("MS-001", lambda: fresh_install(ctx), after=("FILES-001",))
        ctx.run("SETUP-CLAIM", lambda: claim_admin(ctx), after=("MS-001",))

        def cloud() -> str:
            # Per-user key route: PUT /settings api_keys answers 422 under AUTH_ENABLED=true.
            st, _ = ctx.api.request("PUT", "/api/v1/me/connector-credentials/llm_anthropic",
                                    json_body={"payload": {"api_key": key}})
            if st != 200:
                raise CheckFailed(f"setting the key answered {st}")
            try:
                st, _ = ctx.api.request("PUT", "/settings", json_body={"privacy_mode": "allow_cloud"})
                if st != 200:
                    raise CheckFailed(f"allow_cloud answered {st}")
                st, models = ctx.api.get("/api/v1/models")
                if st != 200:
                    raise CheckFailed(f"/models answered {st}")
                st, data = ctx.api.post_json(
                    "/api/v1/chat/completions",
                    {"model": "claude", "messages": [{"role": "user", "content": "Reply: ready"}],
                     "stream": False}, timeout=120)
                if st != 200:
                    raise CheckFailed(f"cloud completion answered {st}")
                return f"cloud model {data.get('model') if isinstance(data, dict) else '?'} answered"
            finally:
                ctx.api.request("PUT", "/settings", json_body={"privacy_mode": "local_only"})

        ctx.run("MS-006", cloud, required=False, after=("SETUP-CLAIM",))
    finally:
        _finish(ctx)


# --------------------------------------------------------------------------- previous / summary

def previous_version(version: str, releases: list[dict]) -> str:
    """Newest non-draft, non-prerelease vX.Y.Z strictly below ``version``'s base."""
    def key(v: str) -> tuple[int, ...]:
        return tuple(int(x) for x in v.split("."))

    base = key(version_base(version))
    best: Optional[str] = None
    for rel in releases:
        tag = str(rel.get("tagName", ""))
        if rel.get("isDraft") or rel.get("isPrerelease"):
            continue
        m = re.match(r"^v(\d+\.\d+\.\d+)$", tag)
        if not m:
            continue
        if key(m.group(1)) < base and (best is None or key(m.group(1)) > key(best)):
            best = m.group(1)
    return best or ""


def summary(results_dir: Path, markdown: Path, expect_suites: list[str], expect_arches: list[str],
            skipped_reason: str = "", outputs: Optional[Path] = None) -> int:
    def out_verdict(v: str) -> None:
        if outputs:
            with open(outputs, "a", encoding="utf-8") as fh:
                fh.write(f"verdict={v}\n")

    if skipped_reason:
        Path(markdown).write_text(f"Release smoke SKIPPED: {skipped_reason}\n", encoding="utf-8")
        out_verdict("skipped")
        return 0

    found: dict[tuple[str, str], list[dict]] = {}
    rows: list[tuple[str, str, str, bool, str, str]] = []  # id, arch, result, required, detail, suite
    evidence: list[tuple[str, str, str]] = []
    for f in sorted(Path(results_dir).glob("*.json")):
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
            if doc.get("schema") != SCHEMA:
                raise ValueError("schema")
            for c in doc["checks"]:
                if c["result"] not in ("pass", "fail", "na"):
                    raise ValueError("result")
            found.setdefault((doc["suite"], doc["arch"]), []).append(doc)
        except (ValueError, KeyError, TypeError):
            rows.append(("RESULTS-INVALID", "-", "fail", True, f.name, "-"))
    if not expect_suites:
        rows.append(("SUITE-MISSING", "-", "fail", True, "no suites expected; nothing was checked", "-"))
    for entry in expect_suites:
        # ``suite@arch`` restricts a suite to one architecture (cloud-llm runs on amd64 only).
        suite, _, only = entry.partition("@")
        for arch in ([only] if only else expect_arches):
            docs = found.get((suite, arch), [])
            if not docs:
                rows.append(("SUITE-MISSING", arch, "fail", True, f"{suite} on {arch}", suite))
            elif len(docs) > 1:
                rows.append(("RESULTS-DUPLICATE", arch, "fail", True, f"{suite} on {arch}", suite))
    for docs in found.values():
        for doc in docs[:1]:
            for c in doc["checks"]:
                if c["id"] == "RUNNER-FOOTPRINT":
                    evidence.append((f"{doc['suite']}", doc["arch"], c["detail"]))
                else:
                    rows.append((c["id"], doc["arch"], c["result"], bool(c["required"]), c["detail"], doc["suite"]))
    failed = [r for r in rows if r[2] == "fail" and r[3]]
    # One row per (check, suite): MS-001, SETUP-CLAIM and FILES-001 run in several suites,
    # and a table keyed on the id alone would show the last suite's result for all of them.
    ids = sorted({(r[0], r[5]) for r in rows})
    lines = ["## Release smoke", "", f"Commit: `{os.environ.get('GITHUB_SHA', 'local')}`", "",
             "| Check | Suite | amd64 | arm64 | Required | Detail |", "|---|---|---|---|---|---|"]
    for cid, suite_name in ids:
        by_arch = {r[1]: r for r in rows if r[0] == cid and r[5] == suite_name}
        def cell(a: str) -> str:
            return by_arch[a][2] if a in by_arch else "-"

        any_row = next(iter(by_arch.values()))
        details = "; ".join(f"{a}: {r[4]}" for a, r in by_arch.items() if r[2] != "pass")[:200]
        lines.append(f"| {cid} | {suite_name} | {cell('amd64')} | {cell('arm64')} | "
                     f"{'yes' if any_row[3] else 'no'} | {details} |")
    if evidence:
        lines += ["", "### Evidence", "", "| Suite | Arch | Footprint |", "|---|---|---|"]
        lines += [f"| {s} | {a} | {d} |" for s, a, d in evidence]
    verdict = "failed" if failed else "passed"
    lines += ["", f"**Verdict: {verdict}**" + (f" ({len(failed)} required check(s) failed)" if failed else "")]
    Path(markdown).write_text("\n".join(lines) + "\n", encoding="utf-8")
    out_verdict(verdict)
    return 1 if failed else 0


# --------------------------------------------------------------------------- CLI

def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--files-dir", required=True)
    p.add_argument("--version", required=True)
    p.add_argument("--arch", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--expect", default="stt,signin,graph-switch")
    p.add_argument("--deadline-min", type=float, default=80)
    p.add_argument("--work-dir")
    p.add_argument("--loopback", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("install", "graph", "cloud-llm"):
        _common(sub.add_parser(name))
    up = sub.add_parser("upgrade")
    _common(up)
    up.add_argument("--previous-dir")
    up.add_argument("--previous-version", default="")
    sa = sub.add_parser("secure-address")
    _common(sa)
    sa.add_argument("--required", action="store_true")
    pv = sub.add_parser("previous")
    pv.add_argument("--version", required=True)
    pv.add_argument("--releases-json", required=True)
    sm = sub.add_parser("summary")
    sm.add_argument("--results-dir", required=True)
    sm.add_argument("--markdown", required=True)
    sm.add_argument("--expect-suites", default="")
    sm.add_argument("--expect-arches", default="amd64,arm64")
    sm.add_argument("--skipped-reason", default="")
    sm.add_argument("--outputs")
    return ap


SUITE_FUNCS = {
    "install": suite_install, "graph": suite_graph, "upgrade": suite_upgrade,
    "secure-address": suite_secure_address, "cloud-llm": suite_cloud_llm,
}


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "previous":
        releases = json.loads(Path(args.releases_json).read_text(encoding="utf-8") or "[]")
        print(previous_version(args.version, releases))
        return 0
    if args.cmd == "summary":
        suites = [s for s in args.expect_suites.split(",") if s]
        arches = [a for a in args.expect_arches.split(",") if a]
        return summary(Path(args.results_dir), Path(args.markdown), suites, arches,
                       args.skipped_reason, Path(args.outputs) if args.outputs else None)
    if not VERSION_RE.match(args.version):
        print(f"bad --version {args.version!r}", file=sys.stderr)
        return 2
    expect = {f for f in args.expect.split(",") if f}
    unknown = expect - set(FEATURES)
    if unknown:
        print(f"unknown --expect features: {sorted(unknown)}", file=sys.stderr)
        return 2
    base = os.environ.get("RUNNER_TEMP") or os.getcwd()
    work = Path(args.work_dir) if args.work_dir else Path(tempfile.mkdtemp(prefix="lumogis-smoke-", dir=base))
    ctx = Ctx(
        suite=args.cmd, arch=args.arch, version=args.version, out=Path(args.out),
        files_dir=Path(args.files_dir), work=work, expect=expect,
        deadline_min=args.deadline_min, loopback=args.loopback,
        required_secure=getattr(args, "required", False),
        previous_dir=Path(args.previous_dir) if getattr(args, "previous_dir", None) else None,
        previous_version=getattr(args, "previous_version", ""),
    )
    for name in ("SMOKE_HOME_DNS_CI_VALUE", "SMOKE_CLOUD_LLM_KEY"):
        if os.environ.get(name):
            ctx.secret_values.add(os.environ[name])
    SUITE_FUNCS[args.cmd](ctx)
    assert ctx.results is not None
    return 1 if any(c.result == "fail" and c.required for c in ctx.results.checks) else 0


if __name__ == "__main__":
    sys.exit(main())
