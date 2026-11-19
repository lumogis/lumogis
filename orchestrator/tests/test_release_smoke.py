# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""scripts/release/smoke/release_smoke.py against fakes (LUM-997).

The script is a client of Core's household API and of ``docker compose``. Core is a
threaded stdlib server answering the real response shapes; ``docker``, ``sudo``,
``iptables``, ``openssl``, ``dig`` and ``ip`` are one recording fake on ``PATH``.
A fake never fails the way we imagine: each failure below is the shape the real
collaborator produces (a 404 route, an empty list, a dropped connection).
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import socket
import sys
from email.parser import BytesParser
from email.policy import HTTP
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPT = _ROOT / "scripts" / "release" / "smoke" / "release_smoke.py"
_FAKES = Path(__file__).with_name("_release_smoke_fakes.py")


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


smoke = _load("release_smoke", _SCRIPT)
fakes = _load("_release_smoke_fakes", _FAKES)

VERSION = "0.14.0"
DIGEST = "sha256:" + "a" * 64
SERVICES = [
    "orchestrator",
    "qdrant",
    "postgres",
    "stack-control",
    "lumogis-web",
    "caddy",
    "ollama",
    "backup",
    "lumogis-stt",
]
IMAGES = {
    "lumogis-orchestrator": "orchestrator",
    "lumogis-web": "lumogis-web",
    "lumogis-stack-control": "stack-control",
    "lumogis-backup": "backup",
    "lumogis-caddy": "caddy",
    "lumogis-graph": "lumogis-graph",
}


class FakeTime:
    def __init__(self) -> None:
        self.now = 1000.0

    def clock(self) -> float:
        return self.now

    def sleep(self, s: float) -> None:
        self.now += s


@pytest.fixture(autouse=True)
def fake_time(monkeypatch):
    t = FakeTime()
    monkeypatch.setattr(smoke, "_clock", t.clock)
    monkeypatch.setattr(smoke, "_sleep", t.sleep)
    return t


class World:
    """One install: files on disk, a fake Core, and the fake tools' scenario."""

    def __init__(
        self,
        tmp_path: Path,
        monkeypatch,
        *,
        auth_on: bool = True,
        version: str = VERSION,
        example_version: str = "release",
        profiles: str = "stt",
    ):
        self.tmp = tmp_path
        self.monkeypatch = monkeypatch
        self.version = version
        self.files = tmp_path / "files"
        self.files.mkdir()
        self.work = tmp_path / "work"
        self.out = tmp_path / "out" / "install-amd64.json"
        self.core = fakes.FakeCore()
        self.claimed = False
        self.password = ""
        self.entities = 3
        self.search_empty = False
        self.voice_text = "The quick brown fox jumps over the lazy dog."
        self.jobs = ["queued", "embedding", "done"]
        self.job_stage_failed = False
        self.chat_model = "llama3.2:3b"
        self.expired: set[str] = set()
        self.scenario: dict = {
            "services": list(SERVICES),
            "image_version": version,
            "volumes": [],
            "recreate_on_up": {"2": ["orchestrator", "stack-control", "backup"]},
            "env_append_on_up": {
                "1": "AUTH_SECRET=generatedsecret123\nLUMOGIS_CREDENTIAL_KEY=credentialkey456"
            },
            "repo_digests": {},
            "rules": [
                {
                    "cmd": "docker",
                    "has": ["exec", "ollama", "list"],
                    "stdout": "llama3.2:3b  abc\n",
                },
                {
                    "cmd": "docker",
                    "has": ["exec", "stack-control", "{{.Client.APIVersion}}"],
                    "stdout": "1.45\n",
                },
                {
                    "cmd": "docker",
                    "has": ["exec", "stack-control", "{{.Server.Version}}"],
                    "stdout": "27.0\n",
                },
                {
                    "cmd": "docker",
                    "has": ["exec", "stack-control", "compose", "--short"],
                    "stdout": "2.29.0\n",
                },
                {
                    "cmd": "docker",
                    "has": ["exec", "orchestrator", "python"],
                    "rc": 1,
                    "stderr": "blocked",
                },
                {
                    "cmd": "docker",
                    "has": ["run", "backup", "/scripts/backup/backup.sh"],
                    "touch": ["ai-workspace/backups/snapshots/20261120-100000/falkordb/dump.rdb"],
                },
            ],
        }
        digest_lines = []
        for image, svc in IMAGES.items():
            repo = f"ghcr.io/lumogis/{image}"
            digest_lines.append(f"{repo} {version} {DIGEST}")
            self.scenario["repo_digests"][svc] = [f"{repo}@{DIGEST}"]
        (self.files / "docker-compose.yml").write_text("services: {}\n")
        env = [
            f"LUMOGIS_VERSION={example_version}",
            "COMPOSE_PROJECT_NAME=lumogis",
            f"AUTH_ENABLED={'true' if auth_on else 'false'}",
            f"COMPOSE_PROFILES={profiles}",
        ]
        (self.files / "example.env").write_text("\n".join(env) + "\n")
        (self.files / "image-digests.txt").write_text("\n".join(digest_lines) + "\n")
        self.write_sums()
        self.bin = tmp_path / "bin"
        fakes.install_fake_tools(self.bin)
        self.log = tmp_path / "calls.jsonl"
        self.flag = tmp_path / "restart.flag"
        monkeypatch.setenv("PATH", f"{self.bin}:{__import__('os').environ['PATH']}")
        monkeypatch.setenv("FAKE_LOG", str(self.log))
        monkeypatch.setenv("FAKE_STATE", str(tmp_path / "state.json"))
        monkeypatch.setenv("FAKE_RESTART_FLAG", str(self.flag))
        monkeypatch.setenv("LUMOGIS_SMOKE_CORE_URL", self.core.url)
        monkeypatch.setenv("LUMOGIS_SMOKE_WEB_URL", self.core.url)
        monkeypatch.setenv("LUMOGIS_SMOKE_PORTS", "1")
        self.monkeypatch = monkeypatch
        self.write_scenario()
        self.install_routes()

    def write_sums(self) -> None:
        lines = []
        for name in ("docker-compose.yml", "example.env", "image-digests.txt"):
            lines.append(f"{hashlib.sha256((self.files / name).read_bytes()).hexdigest()}  {name}")
        (self.files / "SHA256SUMS").write_text("\n".join(lines) + "\n")

    def write_scenario(self) -> None:
        path = self.tmp / "scenario.json"
        path.write_text(json.dumps(self.scenario))
        self.monkeypatch.setenv("FAKE_SCENARIO", str(path))

    def calls(self) -> list[dict]:
        return fakes.read_log(self.log)

    def argvs(self, cmd: str = "docker") -> list[list[str]]:
        return [c["argv"] for c in self.calls() if c["cmd"] == cmd]

    def auth_ok(self, req) -> bool:
        tok = req["auth"].replace("Bearer ", "")
        return tok not in self.expired

    def install_routes(self) -> None:
        r = self.core.routes

        def authed(fn):
            def wrapped(req):
                if not self.auth_ok(req):
                    return 401, {"detail": "expired"}
                return fn(req)

            return wrapped

        r[("GET", "/api/v1/setup/status")] = lambda q: (200, {"needs_setup": not self.claimed})

        def claim(q):
            body = json.loads(q["body"])
            self.password = body["password"]
            self.claimed = True
            return 200, {
                "access_token": "tok1",
                "token_type": "bearer",
                "expires_in": 900,
                "user": {},
            }

        r[("POST", "/api/v1/setup/claim")] = claim

        def login(q):
            return 200, {"access_token": "tok2", "token_type": "bearer", "expires_in": 900}

        r[("POST", "/api/v1/auth/login")] = login
        r[("GET", "/health")] = lambda q: (
            200,
            {
                "status": "ok",
                "qdrant_doc_count": 1,
                "total_chunks_indexed": 3,
                "entity_count": self.entities,
            },
        )
        r[("GET", "/")] = lambda q: (200, "<html><body>Lumogis</body></html>")
        r[("POST", "/api/v1/ingest/upload")] = authed(
            lambda q: (202, {"status": "queued", "file_id": "f", "job_id": 7})
        )

        def job(q):
            stage = self.jobs.pop(0) if len(self.jobs) > 1 else self.jobs[0]
            return 200, {
                "job_id": 7,
                "status": "failed" if stage == "failed" else "running",
                "stage": stage,
                "error": "boom" if stage == "failed" else None,
            }

        r[("GET", "/api/v1/ingest/jobs/7")] = authed(job)
        r[("GET", "/search")] = authed(
            lambda q: (
                200,
                []
                if self.search_empty
                else [
                    {
                        # Core's real stored name: <file_id>_<basename> (LUM-997 rehearsal).
                        "file_path": "/workspace/uploads/default/45ef52b4_smoke-document.txt",
                        "score": 0.9,
                        "chunk_text": "x",
                        "metadata": {},
                    }
                ],
            )
        )
        r[("GET", "/api/v1/admin/diagnostics")] = authed(
            lambda q: (
                200,
                {
                    "status": "ok",
                    "speech_to_text": {"model_state": "ready", "backend": "whisper_sidecar"},
                },
            )
        )
        r[("GET", "/api/v1/admin/diagnostics/stack-status")] = authed(lambda q: (200, {}))
        r[("GET", "/api/v1/admin/diagnostics/backup-status")] = authed(lambda q: (200, {}))
        r[("GET", "/api/v1/admin/diagnostics/update-status")] = authed(
            lambda q: (200, {"current_version": smoke.version_base(self.version)})
        )
        r[("GET", "/api/v1/me/privacy-mode")] = authed(
            lambda q: (200, {"privacy_effective": "local_only"})
        )
        r[("POST", "/api/v1/chat/completions")] = authed(
            lambda q: (
                200,
                {
                    "id": "c",
                    "model": self.chat_model,
                    "message": {"role": "assistant", "content": "ready"},
                },
            )
        )
        r[("POST", "/api/v1/voice/transcribe")] = authed(
            lambda q: (200, {"text": self.voice_text, "provider": "speaches", "model": "base.en"})
        )
        r[("GET", "/api/v1/auth/me")] = authed(lambda q: (200, {"email": "smoke@example.com"}))

        def restart(q):
            self.flag.write_text("1")
            return "drop"

        r[("POST", "/settings/restart")] = authed(restart)

    def ctx(self, suite: str = "install", expect: str = "stt,signin,graph-switch", **kw):
        return smoke.Ctx(
            suite=suite,
            arch="amd64",
            version=self.version,
            out=self.out,
            files_dir=self.files,
            work=self.work,
            expect={e for e in expect.split(",") if e},
            **kw,
        )

    def close(self) -> None:
        self.core.close()


@pytest.fixture
def world(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    yield w
    w.close()


def results(w: World) -> dict[str, dict]:
    doc = json.loads(w.out.read_text())
    return {c["id"]: c for c in doc["checks"]}


# ---------------------------------------------------------------- scaffolding


def test_encode_multipart_round_trips():
    body, ctype = smoke.encode_multipart({"k": "v"}, {"file": ("a.txt", "text/plain", b"hello\n")})
    msg = BytesParser(policy=HTTP).parsebytes(
        b"Content-Type: " + ctype.encode() + b"\r\n\r\n" + body
    )
    parts = {p.get_param("name", header="content-disposition"): p for p in msg.iter_parts()}
    assert parts["k"].get_content().strip() == "v"
    f = parts["file"]
    assert f.get_filename() == "a.txt" and f.get_content_type() == "text/plain"
    assert f.get_payload(decode=True) == b"hello\r\n".replace(b"\r\n", b"\n") or f.get_payload(
        decode=True
    ).startswith(b"hello")


def test_files_check_passes_on_matching_sums(world):
    assert "3 files" in smoke.verify_files(world.files)


def test_files_check_fails_on_checksum_mismatch(world):
    p = world.files / "example.env"
    p.write_bytes(p.read_bytes() + b"x")
    with pytest.raises(smoke.CheckFailed, match="example.env"):
        smoke.verify_files(world.files)


def test_script_is_stdlib_only():
    tree = ast.parse(_SCRIPT.read_text())
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            roots.add((node.module or "").split(".")[0])
    assert roots <= set(sys.stdlib_module_names), roots - set(sys.stdlib_module_names)


def test_redact_removes_secret_values_from_detail():
    assert (
        smoke._redact("token abcdefgh and zzzzzzzz", {"abcdefgh", "zzzzzzzz", ""})
        == "token *** and ***"
    )


def test_compose_sets_pwd_to_work_dir(world, monkeypatch):
    monkeypatch.setenv("PWD", "/somewhere/else")
    world.work.mkdir()
    smoke.Compose(world.work).ps_json()
    smoke.Compose(world.work).up()
    pwds = {c["pwd"] for c in world.calls()}
    assert pwds == {str(world.work)}


def test_ps_json_reads_both_shapes(world):
    world.work.mkdir()
    a = smoke.Compose(world.work).ps_json()
    world.scenario["ps_array"] = False
    world.write_scenario()
    b = smoke.Compose(world.work).ps_json()
    assert len(a) == len(b) == len(SERVICES)


def test_stable_example_env_is_pinned_before_first_up(world):
    ctx = world.ctx()
    smoke.prepare_install(ctx, world.files, VERSION)
    assert smoke.read_env(world.work / ".env")["LUMOGIS_VERSION"] == VERSION
    # An rc example.env already says the version and is left as it is.
    (world.files / "example.env").write_text("LUMOGIS_VERSION=0.14.0-rc.1\n")
    smoke.prepare_install(world.ctx(), world.files, "0.14.0-rc.1")
    assert smoke.read_env(world.work / ".env")["LUMOGIS_VERSION"] == "0.14.0-rc.1"


def test_loopback_sets_caddy_host_ports(world):
    smoke.prepare_install(world.ctx(loopback=True), world.files, VERSION)
    env = smoke.read_env(world.work / ".env")
    assert env["CADDY_HTTP_HOST_PORT"] == "127.0.0.1:80"
    assert env["CADDY_HTTPS_HOST_PORT"] == "127.0.0.1:443"


def test_busy_port_fails_before_up(world, monkeypatch):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    monkeypatch.setenv("LUMOGIS_SMOKE_PORTS", str(s.getsockname()[1]))
    ctx = world.ctx()
    smoke.prepare_install(ctx, world.files, VERSION)
    ctx.start()
    ctx.run("MS-001", lambda: smoke.fresh_install(ctx))
    s.close()
    assert (
        results(world)["MS-001"]["result"] == "fail"
        and "busy" in results(world)["MS-001"]["detail"]
    )
    assert not any(a[:2] == ["compose", "up"] for a in world.argvs())


# ---------------------------------------------------------------- the install suite end to end


def run_install(world: World) -> dict[str, dict]:
    smoke.suite_install(world.ctx())
    return results(world)


def test_install_suite_passes_every_check_against_fakes(world):
    res = run_install(world)
    bad = {k: v["detail"] for k, v in res.items() if v["result"] != "pass"}
    assert not bad, bad
    assert res["MS-001"]["detail"].count("pinned") == 1


def test_claim_flow_against_fake_core(world):
    res = run_install(world)
    assert res["SETUP-CLAIM"]["result"] == "pass"
    claim = next(r for r in world.core.requests if r["path"] == "/api/v1/setup/claim")
    body = json.loads(claim["body"])
    assert body["email"] == "smoke@example.com" and len(body["password"]) >= 12
    assert claim["auth"] == ""
    later = [r for r in world.core.requests if r["path"] == "/search"]
    assert later and all(r["auth"] == "Bearer tok1" for r in later)
    assert body["password"] not in world.out.read_text()


def test_claim_is_first_api_call_after_healthy(world):
    run_install(world)
    api_paths = [p for p in world.core.paths() if p.startswith("/api/")]
    assert api_paths[:2] == ["/api/v1/setup/status", "/api/v1/setup/claim"]


def test_dependent_check_marked_failed_not_passed(world):
    world.claimed = True  # setup is not open: the claim cannot work
    res = run_install(world)
    assert res["SETUP-CLAIM"]["result"] == "fail"
    for cid in ("MS-004", "MS-005", "MS-011", "MS-010"):
        assert res[cid]["result"] == "fail" and "skipped because" in res[cid]["detail"], cid


def test_results_written_after_each_check(world):
    ctx = world.ctx()
    ctx.start()
    on_disk = json.loads(world.out.read_text())["checks"]
    assert all(c["result"] == "fail" and c["detail"] == "not run" for c in on_disk)
    ctx.run("FILES-001", lambda: "ok")
    mid = {c["id"]: c for c in json.loads(world.out.read_text())["checks"]}
    assert mid["FILES-001"]["result"] == "pass" and mid["MS-001"]["detail"] == "not run"
    ctx.run("MS-001", lambda: "ok")
    assert not list(world.out.parent.glob("*.tmp"))


def test_suite_deadline_marks_unrun_checks(world, fake_time):
    ctx = world.ctx(deadline_min=1)
    ctx.start()
    ctx.run("FILES-001", lambda: fake_time.sleep(120))
    ctx.run("MS-001", lambda: "never")
    ctx.finish_unrun()
    res = results(world)
    assert res["MS-001"]["result"] == "fail" and "deadline" in res["MS-001"]["detail"]
    assert "deadline" in res["MS-003"]["detail"]


def test_second_up_may_recreate_only_the_three_env_readers(world):
    ok = run_install(world)
    assert ok["MS-001"]["result"] == "pass"
    world.scenario["recreate_on_up"] = {"2": ["qdrant"]}
    world.write_scenario()
    world.work.rename(world.tmp / "w1")
    (world.tmp / "state.json").unlink()
    ctx = world.ctx()
    ctx.start()
    smoke.prepare_install(ctx, world.files, VERSION)
    ctx.run("MS-001", lambda: smoke.fresh_install(ctx))
    assert results(world)["MS-001"]["result"] == "fail"
    assert "qdrant" in results(world)["MS-001"]["detail"]


def test_building_in_up_output_fails_ms001(world):
    world.scenario["up_stdout"] = " Container x  Building\n"
    world.write_scenario()
    res = run_install(world)
    assert res["MS-001"]["result"] == "fail" and "built an image" in res["MS-001"]["detail"]


def test_buildx_warning_is_not_a_build(world):
    world.scenario["up_stderr"] = "WARN buildx is not installed, build is skipped\n"
    world.write_scenario()
    assert run_install(world)["MS-001"]["result"] == "pass"


def test_steady_state_detects_recreated_container(world):
    world.scenario["recreate_on_up"] = {"2": list(smoke.ENV_READERS), "3": ["caddy"]}
    world.write_scenario()
    res = run_install(world)
    assert res["MS-001-STEADY"]["result"] == "fail" and "caddy" in res["MS-001-STEADY"]["detail"]


def test_digest_check_matches_repo_digests(world):
    world.scenario["repo_digests"]["caddy"] = ["ghcr.io/lumogis/lumogis-caddy@sha256:" + "b" * 64]
    world.write_scenario()
    res = run_install(world)
    assert (
        res["MS-001-DIGESTS"]["result"] == "fail"
        and "lumogis-caddy" in res["MS-001-DIGESTS"]["detail"]
    )


def test_digest_check_skips_profile_services_not_running(world):
    # image-digests.txt lists lumogis-graph, but no graph container runs in `install`.
    res = run_install(world)
    assert res["MS-001-DIGESTS"]["result"] == "pass"
    assert "lumogis-graph" not in res["MS-001-DIGESTS"]["detail"]


def test_search_without_fixture_hit_fails(world):
    world.search_empty = True
    assert run_install(world)["MS-004"]["result"] == "fail"


def test_ingest_failed_stage_fails_with_error(world):
    world.jobs = ["queued", "failed"]
    res = run_install(world)
    assert res["MS-004"]["result"] == "fail" and "boom" in res["MS-004"]["detail"]


def test_ingest_polls_until_done_then_searches(world):
    res = run_install(world)
    assert res["MS-004"]["result"] == "pass"
    paths = world.core.paths()
    assert (
        paths.index("/api/v1/ingest/upload")
        < paths.index("/api/v1/ingest/jobs/7")
        < paths.index("/search")
    )


def test_voice_waits_for_model_ready_and_checks_words(world):
    world.voice_text = "the quick brown fox jumps over a lazy dog"
    assert run_install(world)["VOICE"]["result"] == "pass"


def test_voice_wrong_text_fails_with_text_in_detail(world):
    world.voice_text = "hello world"
    res = run_install(world)
    assert res["VOICE"]["result"] == "fail" and "hello world" in res["VOICE"]["detail"]


def test_voice_na_without_stt_when_not_expected(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch, profiles="")
    try:
        smoke.suite_install(w.ctx(expect="signin"))
        res = results(w)
        assert res["VOICE-PROFILE"]["result"] == "na" and res["VOICE"]["result"] == "na"
    finally:
        w.close()


def test_voice_profile_fails_when_expected_but_missing(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch, profiles="")
    try:
        smoke.suite_install(w.ctx(expect="stt,signin"))
        assert results(w)["VOICE-PROFILE"]["result"] == "fail"
    finally:
        w.close()


def test_restart_tolerates_dropped_response(world):
    res = run_install(world)
    assert res["MS-011"]["result"] == "pass"
    assert ["exec", "-T", "stack-control", "docker", "compose", "version", "--short"] in [
        a[1:] for a in world.argvs()
    ]


def test_auth_off_install_runs_without_token(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch, auth_on=False)
    try:
        smoke.suite_install(w.ctx(expect=""))
        res = results(w)
        assert res["SETUP-CLAIM"]["result"] == "na"
        assert res["MS-004"]["result"] == "pass"
        assert all(r["auth"] == "" for r in w.core.requests)
    finally:
        w.close()


def test_auth_off_install_fails_when_signin_expected(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch, auth_on=False)
    try:
        smoke.suite_install(w.ctx(expect="signin"))
        assert results(w)["SETUP-CLAIM"]["result"] == "fail"
    finally:
        w.close()


# ---------------------------------------------------------------- egress (MS-005)


def test_egress_check_requires_negative_control(world):
    world.scenario["egress_counts"] = [[0], [0]]
    world.write_scenario()
    res = run_install(world)
    assert res["MS-005"]["result"] == "fail" and "proves nothing" in res["MS-005"]["detail"]


def test_egress_control_that_connects_proves_nothing(world):
    world.scenario["rules"] = [
        r for r in world.scenario["rules"] if "python" not in r.get("has", [])
    ]
    world.write_scenario()
    assert "proves nothing" in run_install(world)["MS-005"]["detail"]


def test_egress_check_fails_on_counted_connection_during_chat(world):
    world.scenario["egress_counts"] = [[2], [1]]
    world.write_scenario()
    res = run_install(world)
    assert res["MS-005"]["result"] == "fail" and "outbound" in res["MS-005"]["detail"]


def test_egress_check_passes_with_local_model_and_zero_counter(world):
    res = run_install(world)
    assert res["MS-005"]["result"] == "pass"
    paths = world.core.paths()
    i = paths.index("/api/v1/chat/completions")
    # The window holds the chat call and nothing else (no update-check trigger).
    iptables_calls = [c for c in world.calls() if c["cmd"] == "iptables"]
    assert iptables_calls
    assert paths[i - 1] != "/api/v1/admin/diagnostics/update-status"


def test_egress_counter_sums_reject_rules_over_networks(world):
    world.scenario["egress_counts"] = [[0, 2]]
    world.write_scenario()
    assert smoke.egress_rules_count() == 2


def test_egress_rule_list_returns_private_before_reject():
    rules = smoke.egress_rules(["172.20.0.5", "172.21.0.5"])
    per_ip = len(rules) // 2
    assert per_ip == 6
    first = rules[:per_ip]
    assert [r[r.index("-j") + 1] for r in first] == ["RETURN"] * 4 + ["LOG", "REJECT"]
    assert [r[3] for r in first[:4]] == list(smoke.PRIVATE_RANGES)


def test_egress_rules_removed_even_when_chat_raises(world):
    world.core.routes[("POST", "/api/v1/chat/completions")] = lambda q: "drop"
    res = run_install(world)
    assert res["MS-005"]["result"] == "fail"
    ipt = [c["argv"] for c in world.calls() if c["cmd"] == "iptables"]
    assert (
        ipt[-3:]
        == [
            ["-D", "DOCKER-USER", "-j", "LUMOGIS_SMOKE_EGRESS"],
            ["-F", "LUMOGIS_SMOKE_EGRESS"],
            ["-X", "LUMOGIS_SMOKE_EGRESS"],
        ]
        or ["-X", "LUMOGIS_SMOKE_EGRESS"] in ipt[-2:]
    )


def test_egress_chat_timeout_fails_with_elapsed(world):
    world.core.routes[("POST", "/api/v1/chat/completions")] = lambda q: "drop"
    detail = run_install(world)["MS-005"]["detail"]
    assert "did not answer" in detail and "elapsed" in detail


def test_missing_docker_user_chain_is_fail_not_na(world):
    world.scenario["docker_user_rc"] = 1
    world.write_scenario()
    res = run_install(world)
    assert res["MS-005"]["result"] == "fail" and "DOCKER-USER" in res["MS-005"]["detail"]


def test_stale_egress_chain_removed_at_start(world):
    run_install(world)
    argv = [(c["cmd"], c["argv"]) for c in world.calls()]
    first_up = next(
        i for i, (c, a) in enumerate(argv) if c == "docker" and a[:2] == ["compose", "up"]
    )
    sweep = [a for c, a in argv[:first_up] if c == "iptables"]
    assert ["-D", "DOCKER-USER", "-j", "LUMOGIS_SMOKE_EGRESS"] in sweep
    assert ["-F", "LUMOGIS_SMOKE_EGRESS"] in sweep and ["-X", "LUMOGIS_SMOKE_EGRESS"] in sweep


def test_egress_window_relogs_before_not_during(world, fake_time):
    ctx = world.ctx()
    smoke.prepare_install(ctx, world.files, VERSION)
    ctx.start()
    ctx.run("MS-001", lambda: smoke.fresh_install(ctx))
    ctx.run("SETUP-CLAIM", lambda: smoke.claim_admin(ctx))
    fake_time.sleep(660)  # the token is now older than 10 minutes
    ctx.run("MS-005", lambda: smoke.egress_guarded_chat(ctx))
    assert results(world)["MS-005"]["result"] == "pass"
    paths = world.core.paths()
    assert paths.index("/api/v1/auth/login") < paths.index("/api/v1/chat/completions")
    assert paths.count("/api/v1/auth/login") == 1
    # Nothing else is called between the sign-in and the chat: the window holds the chat only.
    assert paths[paths.index("/api/v1/chat/completions") - 1] == "/api/v1/auth/login"


def test_api_relogs_once_on_401(world):
    api = smoke.Api(world.core.url, "token")
    api.set_session("smoke@example.com", "pw-pw-pw-pw-pw", "old")
    world.expired.add("old")
    status, _ = api.get("/search?q=x")
    assert status == 200 and api.token == "tok2"
    assert world.core.paths().count("/api/v1/auth/login") == 1
    # A second 401 is returned to the check, and login is not called a third time.
    world.expired.add("tok2")
    status, _ = api.get("/search?q=x")
    assert status == 401
    assert world.core.paths().count("/api/v1/auth/login") == 2


# ---------------------------------------------------------------- redaction


def test_redaction_covers_generated_env_secrets(world):
    world.scenario["rules"].append(
        {
            "cmd": "docker",
            "has": ["logs"],
            "stdout": "boot AUTH_SECRET=generatedsecret123 key credentialkey456\n",
        }
    )
    world.write_scenario()
    smoke.suite_install(world.ctx())
    text = world.out.read_text() + (world.out.parent / "logs" / "install-amd64.log").read_text()
    assert "generatedsecret123" not in text and "credentialkey456" not in text
    assert "***" in (world.out.parent / "logs" / "install-amd64.log").read_text()


def test_written_compose_logs_are_redacted(world, monkeypatch):
    monkeypatch.setenv("SMOKE_HOME_DNS_CI_VALUE", "ci-value-ci-value")
    world.scenario["rules"].append(
        {"cmd": "docker", "has": ["logs"], "stdout": "got ci-value-ci-value\n"}
    )
    world.write_scenario()
    ctx = world.ctx()
    ctx.secret_values.add("ci-value-ci-value")
    world.work.mkdir()
    smoke.Compose(world.work, ctx.redact).write_logs(world.tmp / "l.log")
    assert (world.tmp / "l.log").read_text().strip().endswith("got ***")


def test_ms011_checks_compose_inside_stack_control(world):
    run_install(world)
    assert any(
        a[1:] == ["exec", "-T", "stack-control", "docker", "compose", "version", "--short"]
        for a in world.argvs()
    )


# ---------------------------------------------------------------- expect (version skew)


def test_expect_missing_feature_is_fail_and_unexpected_is_na(tmp_path, monkeypatch):
    # stt: not in COMPOSE_PROFILES
    w = World(tmp_path, monkeypatch, profiles="")
    try:
        ctx = w.ctx(expect="stt")
        smoke.prepare_install(ctx, w.files, VERSION)
        with pytest.raises(smoke.CheckFailed):
            smoke.voice_profile(ctx)
        ctx.expect = set()
        with pytest.raises(smoke.NotApplicable):
            smoke.voice_profile(ctx)
        # signin: sign-in off in .env
        (w.work / ".env").write_text("AUTH_ENABLED=false\n")
        ctx.expect = {"signin"}
        with pytest.raises(smoke.CheckFailed):
            smoke.claim_admin(ctx)
        ctx.expect = set()
        with pytest.raises(smoke.NotApplicable):
            smoke.claim_admin(ctx)
    finally:
        w.close()


# ---------------------------------------------------------------- summary / previous


def _result_file(d: Path, suite: str, arch: str, checks: list[dict], schema: int = 1) -> None:
    rows = [
        {
            "id": c[0],
            "suite": suite,
            "arch": arch,
            "result": c[1],
            "required": c[2] if len(c) > 2 else True,
            "seconds": 1.0,
            "detail": c[3] if len(c) > 3 else "",
        }
        for c in checks
    ]
    (d / f"{suite}-{arch}.json").write_text(
        json.dumps(
            {
                "schema": schema,
                "version": "1",
                "arch": arch,
                "suite": suite,
                "started": "x",
                "checks": rows,
            }
        )
    )


def _summary(
    tmp_path, suites=("install",), arches=("amd64", "arm64"), reason=""
) -> tuple[int, str, str]:
    md = tmp_path / "s.md"
    out = tmp_path / "gh_out"
    rc = smoke.summary(tmp_path / "res", md, list(suites), list(arches), reason, out)
    return rc, md.read_text(), out.read_text() if out.exists() else ""


def test_summary_fails_on_required_failure_only(tmp_path):
    d = tmp_path / "res"
    d.mkdir()
    for arch in ("amd64", "arm64"):
        _result_file(
            d, "install", arch, [("MS-001", "pass"), ("MS-006", "fail", False), ("VOICE", "na")]
        )
    rc, md, out = _summary(tmp_path)
    assert rc == 0 and "verdict=passed" in out
    _result_file(d, "install", "arm64", [("MS-001", "fail")])
    rc, md, out = _summary(tmp_path)
    assert rc == 1 and "verdict=failed" in out


def test_summary_reports_missing_suite(tmp_path):
    d = tmp_path / "res"
    d.mkdir()
    _result_file(d, "install", "amd64", [("MS-001", "pass")])
    rc, md, _ = _summary(tmp_path)
    assert rc == 1 and "SUITE-MISSING" in md


def test_summary_rejects_unknown_schema_or_result(tmp_path):
    d = tmp_path / "res"
    d.mkdir()
    _result_file(d, "install", "amd64", [("MS-001", "pass")], schema=2)
    _result_file(d, "install", "arm64", [("MS-001", "maybe")])
    rc, md, _ = _summary(tmp_path)
    assert rc == 1 and md.count("RESULTS-INVALID") >= 1


def test_summary_markdown_has_one_row_per_check_with_both_arches(tmp_path):
    d = tmp_path / "res"
    d.mkdir()
    for arch in ("amd64", "arm64"):
        _result_file(d, "install", arch, [("MS-001", "pass"), ("MS-003", "pass")])
    _, md, _ = _summary(tmp_path)
    row = next(line for line in md.splitlines() if line.startswith("| MS-001"))
    assert row.count("pass") == 2
    assert sum(1 for line in md.splitlines() if line.startswith("| MS-00")) == 2


def test_summary_table_keeps_a_row_per_suite_for_a_shared_check_id(tmp_path):
    # MS-001 runs in several suites; a failure in graph must not be hidden by install's pass.
    d = tmp_path / "res"
    d.mkdir()
    _result_file(d, "graph", "amd64", [("MS-001", "fail", True, "port busy")])
    _result_file(d, "install", "amd64", [("MS-001", "pass")])
    rc, md, _ = _summary(tmp_path, suites=("graph", "install"), arches=("amd64",))
    rows = [line for line in md.splitlines() if line.startswith("| MS-001")]
    assert rc == 1 and len(rows) == 2
    assert any("graph" in r and "fail" in r for r in rows)
    assert any("install" in r and "pass" in r and "fail" not in r for r in rows)


def test_summary_with_no_expected_suites_is_a_failure(tmp_path):
    (tmp_path / "res").mkdir()
    rc, md, out = _summary(tmp_path, suites=())
    assert rc == 1 and "SUITE-MISSING" in md and "verdict=failed" in out


def test_files_check_fails_when_sums_omit_an_install_file(tmp_path):
    for n in ("docker-compose.yml", "example.env", "image-digests.txt"):
        (tmp_path / n).write_text(n)
    import hashlib

    (tmp_path / "SHA256SUMS").write_text(
        hashlib.sha256(b"example.env").hexdigest() + "  example.env\n"
    )
    with pytest.raises(smoke.CheckFailed, match="docker-compose.yml not listed"):
        smoke.verify_files(tmp_path)
    (tmp_path / "SHA256SUMS").write_text("")
    with pytest.raises(smoke.CheckFailed, match="not listed"):
        smoke.verify_files(tmp_path)


def test_summary_duplicate_results_fail(tmp_path):
    d = tmp_path / "res"
    d.mkdir()
    _result_file(d, "install", "amd64", [("MS-001", "pass")])
    (d / "again.json").write_text((d / "install-amd64.json").read_text())
    rc, md, _ = _summary(tmp_path, arches=("amd64",))
    assert rc == 1 and "RESULTS-DUPLICATE" in md


def test_summary_footprint_is_evidence_not_check(tmp_path):
    d = tmp_path / "res"
    d.mkdir()
    for arch in ("amd64", "arm64"):
        _result_file(
            d, "install", arch, [("MS-001", "pass"), ("RUNNER-FOOTPRINT", "fail", False, "9 GiB")]
        )
    rc, md, _ = _summary(tmp_path)
    assert rc == 0 and "### Evidence" in md and "9 GiB" in md
    assert not any(line.startswith("| RUNNER-FOOTPRINT") for line in md.splitlines())


def test_summary_skipped_reason_writes_verdict_skipped_and_exits_0(tmp_path):
    (tmp_path / "res").mkdir()
    rc, md, out = _summary(tmp_path, reason="LUM-123 registry outage")
    assert rc == 0 and "SKIPPED" in md and "verdict=skipped" in out


def test_previous_picks_newest_stable_below_base():
    rel = [
        {"tagName": "v0.13.0"},
        {"tagName": "v0.13.1"},
        {"tagName": "v0.14.0-rc.1", "isPrerelease": True},
        {"tagName": "v0.14.0", "isDraft": True},
    ]
    assert smoke.previous_version("0.14.0-rc.2", rel) == "0.13.1"
    assert smoke.previous_version("0.13.1", rel) == "0.13.0"
    assert smoke.previous_version("0.13.0", rel) == ""


def test_every_catalogue_id_is_in_the_checklist():
    text = (_ROOT / "docs" / "RELEASE-MANUAL-CHECKLIST.md").read_text()
    synthesised = {"RUNNER-DISK", "SUITE-MISSING", "RESULTS-INVALID", "RESULTS-DUPLICATE"}
    missing = [i for i in sorted(smoke.CHECK_IDS - synthesised) if i not in text]
    assert not missing, missing


# ---------------------------------------------------------------- graph


def graph_routes(w: World, *, enable=(202, {"state": "starting"}), states=("starting", "on")):
    seq = list(states)
    w.core.routes[("GET", "/api/v1/admin/knowledge-graph")] = lambda q: (
        200,
        {"state": seq.pop(0) if len(seq) > 1 else seq[0]},
    )
    w.core.routes[("POST", "/api/v1/admin/knowledge-graph/enable")] = lambda q: enable
    w.core.routes[("POST", "/api/v1/admin/knowledge-graph/disable")] = lambda q: (
        202,
        {"state": "stopping"},
    )


def graph_world(tmp_path, monkeypatch, **kw) -> World:
    w = World(tmp_path, monkeypatch, **kw)
    w.scenario["services"] += ["falkordb", "lumogis-graph"]
    w.scenario["volumes"] = ["postgres_data", "qdrant_data", "falkordb_data", "caddy_data"]
    w.scenario["rules"] += [
        {
            "cmd": "docker",
            "has": ["exec", "falkordb", "redis-cli"],
            "stdout": "1) 1\n2) 1\n3) 1\n\n7\n",
            "stdout_wiped": "1) 1\n2) 1\n3) 1\n\n0\n",
        },
    ]
    w.write_scenario()
    return w


def started_graph_ctx(w: World):
    ctx = w.ctx("graph")
    ctx.start()
    smoke.prepare_install(ctx, w.files, VERSION)
    smoke.fresh_install(ctx)
    smoke.claim_admin(ctx)
    return ctx


def test_graph_enable_without_confirmation_on_large_runner(tmp_path, monkeypatch):
    w = graph_world(tmp_path, monkeypatch)
    try:
        graph_routes(w)
        ctx = started_graph_ctx(w)
        (w.work / ".env").open("a").write(
            "COMPOSE_PROFILES=stt,graph\n"
        )  # what stack-control writes
        assert smoke.graph_on(ctx).startswith("digests match")
        enable = next(r for r in w.core.requests if r["path"].endswith("/enable"))
        assert json.loads(enable["body"]) == {"confirm_low_memory": False}
    finally:
        w.close()


def test_graph_enable_409_needs_confirmation_fails(tmp_path, monkeypatch):
    w = graph_world(tmp_path, monkeypatch)
    try:
        graph_routes(w, enable=(409, {"detail": "needs_confirmation"}))
        ctx = started_graph_ctx(w)
        with pytest.raises(smoke.CheckFailed, match="needs_confirmation"):
            smoke.graph_on(ctx)
    finally:
        w.close()


def test_graph_off_keeps_stt_profile(tmp_path, monkeypatch):
    w = graph_world(tmp_path, monkeypatch)
    try:
        graph_routes(w, states=("off",))
        ctx = started_graph_ctx(w)
        (w.work / ".env").open("a").write("COMPOSE_PROFILES=\n")
        with pytest.raises(smoke.CheckFailed, match="stt"):
            smoke.graph_off(ctx)
    finally:
        w.close()


def test_graph_suite_na_without_graph_switch_when_not_expected(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch, auth_on=False)
    try:
        smoke.suite_graph(w.ctx("graph", expect=""))
        res = results(w)
        assert res["GRAPH-ON"]["result"] == "na" and res["GRAPH-OFF"]["result"] == "na"
    finally:
        w.close()


def test_graph_suite_fails_when_switch_expected_but_missing(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch, auth_on=False)
    try:
        smoke.suite_graph(w.ctx("graph", expect="graph-switch"))
        assert results(w)["GRAPH-ON"]["result"] == "fail"
    finally:
        w.close()


def test_graph_restore_wipes_then_restores_in_roundtrip_order(tmp_path, monkeypatch):
    w = graph_world(tmp_path, monkeypatch)
    try:
        ctx = started_graph_ctx(w)
        smoke.graph_backup(ctx)
        smoke.graph_restore(ctx)
        argv = [a for a in w.argvs() if a]
        names = []
        for a in argv:
            if a[:2] == ["compose", "down"] and len(a) == 2:
                names.append("stop-all")
            elif a[:2] == ["volume", "rm"]:
                names.append("rm")
            elif a[:3] == ["compose", "up", "-d"] and "--wait" in a:
                names.append("up-wait")
            elif a[:3] == ["compose", "stop", "falkordb"]:
                names.append("stop-falkor")
            elif "/scripts/backup/restore.sh" in a:
                names.append("restore")
        tail = names[names.index("stop-all") :]
        assert tail[:7] == ["stop-all", "rm", "rm", "rm", "up-wait", "stop-falkor", "restore"], tail
        restore = next(a for a in argv if "/scripts/backup/restore.sh" in a)
        assert restore[:5] == ["compose", "run", "--rm", "-T", "-e"] or "-T" in restore
        assert "--no-deps" in restore and "-it" not in restore
        # `down`, not `stop`: Docker refuses `volume rm` while a stopped container uses it.
        assert not any(a[:2] == ["compose", "stop"] and len(a) == 2 for a in argv)
        # The restore must see the entity count come back; a lower count fails.
        w.entities = 1
        with pytest.raises(smoke.CheckFailed, match="counts differ"):
            smoke.graph_restore(ctx)
    finally:
        w.close()


def test_graph_restore_fails_when_a_volume_cannot_be_wiped(tmp_path, monkeypatch):
    # A silently kept volume would let restore "succeed" over the old data.
    w = graph_world(tmp_path, monkeypatch)
    try:
        w.scenario["volume_rm_rc"] = 1
        w.scenario["volume_rm_stderr"] = "volume is in use"
        w.write_scenario()
        ctx = started_graph_ctx(w)
        smoke.graph_backup(ctx)
        with pytest.raises(smoke.CheckFailed, match="could not wipe volume"):
            smoke.graph_restore(ctx)
    finally:
        w.close()


def test_graph_restore_fails_when_wipe_leaves_graph_nodes(tmp_path, monkeypatch):
    w = graph_world(tmp_path, monkeypatch)
    try:
        for r in w.scenario["rules"]:
            r.pop("stdout_wiped", None)  # the fake graph keeps its 7 nodes after the "wipe"
        w.write_scenario()
        ctx = started_graph_ctx(w)
        smoke.graph_backup(ctx)
        with pytest.raises(smoke.CheckFailed, match="wipe did not empty FalkorDB"):
            smoke.graph_restore(ctx)
    finally:
        w.close()


def test_restore_uses_tty_less_run(world):
    world.work.mkdir()
    smoke.restore_stamp(smoke.Compose(world.work), "20261120-100000")
    restore = next(a for a in world.argvs() if "/scripts/backup/restore.sh" in a)
    assert {"run", "--rm", "-T", "--no-deps"} <= set(restore) and "-it" not in restore


def test_snapshot_stamp_validated(world):
    world.work.mkdir()
    with pytest.raises(smoke.CheckFailed, match="stamp"):
        smoke.restore_stamp(smoke.Compose(world.work), "../x")
    assert world.argvs() == []


# ---------------------------------------------------------------- upgrade


def upgrade_world(tmp_path, monkeypatch) -> World:
    """Previous release 0.13.1 ships sign-in off and has no setup route; 0.14.0 has both."""
    w = World(tmp_path, monkeypatch, auth_on=True, version="0.14.0")
    prev = tmp_path / "prev"
    prev.mkdir()
    (prev / "docker-compose.yml").write_text("services: {}\n")
    (prev / "example.env").write_text(
        "LUMOGIS_VERSION=release\nCOMPOSE_PROJECT_NAME=lumogis\nAUTH_ENABLED=false\n"
    )
    (prev / "image-digests.txt").write_text(
        "".join(f"ghcr.io/lumogis/{i} 0.13.1 sha256:{'c' * 64}\n" for i in IMAGES)
    )
    lines = [
        f"{hashlib.sha256((prev / n).read_bytes()).hexdigest()}  {n}"
        for n in ("docker-compose.yml", "example.env", "image-digests.txt")
    ]
    (prev / "SHA256SUMS").write_text("\n".join(lines) + "\n")
    w.prev = prev
    w.scenario["image_version"] = "0.13.1"
    w.scenario["services"] = [s for s in SERVICES if s != "lumogis-stt"]
    w.scenario["repo_digests"] = {
        s: [f"ghcr.io/lumogis/{i}@sha256:{'c' * 64}"] for i, s in IMAGES.items()
    }
    w.scenario["env_append_on_up"] = {"1": "AUTH_SECRET=generatedsecret123"}
    w.scenario["recreate_on_up"] = {}
    w.write_scenario()

    def setup_status(q):
        env = smoke.read_env(w.work / ".env")
        if env.get("LUMOGIS_VERSION") == "0.13.1":
            return 404, {"detail": "not found"}  # 0.13.1 has no setup route
        if env.get("AUTH_ENABLED") != "true":
            return 200, {"needs_setup": False}
        return 200, {"needs_setup": not w.claimed}

    w.core.routes[("GET", "/api/v1/setup/status")] = setup_status
    return w


def run_upgrade(w: World, expect: str = "signin"):
    ctx = w.ctx("upgrade", expect=expect, previous_dir=w.prev, previous_version="0.13.1")
    smoke.suite_upgrade(ctx)
    return ctx, results(w)


def test_upgrade_na_without_previous(world):
    smoke.suite_upgrade(world.ctx("upgrade"))
    res = results(world)
    assert all(
        res[c]["result"] == "na"
        for c in ("UPGRADE-PREV", "UPGRADE-PULL", "UPGRADE-SIGNIN", "UPGRADE-BACK")
    )


def test_upgrade_from_auth_off_runs_the_whole_path(tmp_path, monkeypatch):
    w = upgrade_world(tmp_path, monkeypatch)
    try:
        ctx, res = run_upgrade(w)
        for cid in ("UPGRADE-PREV", "UPGRADE-PULL", "UPGRADE-SIGNIN", "UPGRADE-BACK"):
            assert res[cid]["result"] == "pass", (cid, res[cid]["detail"])
        argv = w.argvs()
        stop = next(a for a in argv if a[:2] == ["compose", "stop"] and "orchestrator" in a)
        assert stop[2:] == ["orchestrator", "lumogis-web", "falkordb"]
        restore = next(a for a in argv if "/scripts/backup/restore.sh" in a)
        assert {"--no-deps", "-T", "--rm"} <= set(restore) and "-it" not in restore
        # Going back restores the kept .env copy: previous version pinned, sign-in off.
        env = smoke.read_env(w.work / ".env")
        assert env["LUMOGIS_VERSION"] == "0.13.1" and env["AUTH_ENABLED"] == "false"
        assert "UPGRADE-BACK" in res and "previous digests" in res["UPGRADE-BACK"]["detail"]
    finally:
        w.close()


def test_upgrade_pull_keeps_sign_in_off_secrets_and_pin(tmp_path, monkeypatch):
    w = upgrade_world(tmp_path, monkeypatch)
    try:
        ctx = w.ctx("upgrade", expect="signin", previous_dir=w.prev, previous_version="0.13.1")
        ctx.start()
        ctx.run("UPGRADE-PREV", lambda: "x")  # placeholder so `after` has a status
        # Drive the real steps: previous install, then the update.
        smoke.suite_upgrade(
            w.ctx("upgrade", expect="signin", previous_dir=w.prev, previous_version="0.13.1")
        )
        res = results(w)
        assert res["UPGRADE-PULL"]["detail"].startswith("current_version 0.14.0")
        pulls = [a for a in w.argvs() if a[:2] == ["compose", "pull"]]
        assert pulls
    finally:
        w.close()


def test_upgrade_does_not_turn_stt_on(tmp_path, monkeypatch):
    w = upgrade_world(tmp_path, monkeypatch)
    try:
        # After the update a Speaches service is running although the previous .env had no stt.
        w.scenario["services"] = SERVICES
        w.write_scenario()
        _, res = run_upgrade(w)
        assert res["UPGRADE-PULL"]["result"] == "fail" and "speech" in res["UPGRADE-PULL"]["detail"]
    finally:
        w.close()


def test_upgrade_signin_na_when_previous_had_sign_in_on(tmp_path, monkeypatch):
    w = upgrade_world(tmp_path, monkeypatch)
    try:
        (w.prev / "example.env").write_text(
            "LUMOGIS_VERSION=release\nCOMPOSE_PROJECT_NAME=lumogis\nAUTH_ENABLED=true\n"
        )
        lines = [
            f"{hashlib.sha256((w.prev / n).read_bytes()).hexdigest()}  {n}"
            for n in ("docker-compose.yml", "example.env", "image-digests.txt")
        ]
        (w.prev / "SHA256SUMS").write_text("\n".join(lines) + "\n")
        w.core.routes[("GET", "/api/v1/setup/status")] = lambda q: (
            200,
            {"needs_setup": not w.claimed},
        )
        _, res = run_upgrade(w)
        assert res["UPGRADE-SIGNIN"]["result"] == "na"
    finally:
        w.close()


def test_upgrade_keeps_generated_secrets_in_the_kept_env_copy(tmp_path, monkeypatch):
    w = upgrade_world(tmp_path, monkeypatch)
    try:
        run_upgrade(w)
        kept = (w.work / ".env.before-update").read_text()
        assert "AUTH_SECRET=generatedsecret123" in kept and "LUMOGIS_VERSION=0.13.1" in kept
    finally:
        w.close()


def test_upgrade_current_version_is_base(world):
    world.version = "0.14.0-rc.1"
    world.core.routes[("GET", "/api/v1/admin/diagnostics/update-status")] = lambda q: (
        200,
        {"current_version": "0.14.0"},
    )
    ctx = world.ctx()
    assert "0.14.0" in smoke.update_status_check(ctx)
    world.core.routes[("GET", "/api/v1/admin/diagnostics/update-status")] = lambda q: (
        200,
        {"current_version": "0.14.0-rc.1"},
    )
    with pytest.raises(smoke.CheckFailed):
        smoke.update_status_check(ctx)


# ---------------------------------------------------------------- secure address (Pass 4)

STAGING_CERT = (
    "issuer=C=US, O=(STAGING) Let's Encrypt, CN=(STAGING) Ersatz\n"
    "X509v3 Subject Alternative Name:\n"
    "    DNS:abc.homes.lumogis.app\n"
)


def secure_routes(w: World, states=("registering", "ready")):
    seq = list(states)
    after = {"disabled": False}

    def state(q):
        if after["disabled"]:
            return 200, {"state": "off"}
        st = seq.pop(0) if len(seq) > 1 else seq[0]
        return 200, {"state": st, "name": "abc.homes.lumogis.app"}

    def disable(q):
        after["disabled"] = True
        return 200, {"state": "off"}

    w.core.routes[("GET", "/api/v1/admin/secure-address")] = state
    w.core.routes[("POST", "/api/v1/admin/secure-address/enable")] = lambda q: (202, {})
    w.core.routes[("POST", "/api/v1/admin/secure-address/disable")] = disable
    w.scenario["openssl_out"] = STAGING_CERT
    w.write_scenario()


def run_secure(w: World, monkeypatch, *, value="ci-registration-value", required=True):
    if value:
        monkeypatch.setenv("SMOKE_HOME_DNS_CI_VALUE", value)
    else:
        monkeypatch.delenv("SMOKE_HOME_DNS_CI_VALUE", raising=False)
    smoke.suite_secure_address(w.ctx("secure-address", required_secure=required))
    return results(w)["SECURE-ADDRESS"]


def test_secure_address_na_without_required_even_with_value(world, monkeypatch):
    secure_routes(world)
    row = run_secure(world, monkeypatch, required=False)
    assert row["result"] == "na" and row["required"] is False
    assert not any(a[:2] == ["compose", "up"] for a in world.argvs())
    assert not any("secure-address" in p for p in world.core.paths())


def test_secure_address_fail_without_value_when_required(world, monkeypatch):
    secure_routes(world)
    row = run_secure(world, monkeypatch, value="", required=True)
    assert row["result"] == "fail" and "not configured" in row["detail"]


def test_secure_address_sets_env_before_first_up(world, monkeypatch):
    secure_routes(world)
    assert run_secure(world, monkeypatch)["result"] == "pass"
    first_up = next(
        c for c in world.calls() if c["cmd"] == "docker" and c["argv"][:2] == ["compose", "up"]
    )
    assert "LUMOGIS_HOME_DNS_REGISTRATION_SECRET=ci-registration-value" in first_up["env"]
    assert "LUMOGIS_HOME_DNS_ACME_STAGING=true" in first_up["env"]
    assert "ci-registration-value" not in world.out.read_text()


def test_production_issuer_fails(world, monkeypatch):
    secure_routes(world)
    world.scenario["openssl_out"] = (
        "issuer=C=US, O=Let's Encrypt, CN=R11\nDNS:abc.homes.lumogis.app\n"
    )
    world.write_scenario()
    row = run_secure(world, monkeypatch)
    assert row["result"] == "fail" and "production certificate" in row["detail"]
    # finally: a failed run still turns the secure address off again
    assert ("POST", "/api/v1/admin/secure-address/disable") in [
        (r["method"], r["path"]) for r in world.core.requests
    ]


def test_secure_address_rejects_public_runner_address(world, monkeypatch):
    secure_routes(world)
    world.scenario["ip_out"] = "1.1.1.1 via 1.2.3.4 dev eth0 src 8.8.8.8 uid 0\n"
    world.write_scenario()
    row = run_secure(world, monkeypatch)
    assert row["result"] == "fail" and "private address" in row["detail"]


def test_secure_address_disables_in_finally_after_failure(world, monkeypatch):
    secure_routes(world, states=("registering", "error"))
    row = run_secure(world, monkeypatch)
    assert row["result"] == "fail"
    paths = [(r["method"], r["path"]) for r in world.core.requests]
    assert ("POST", "/api/v1/admin/secure-address/disable") in paths


def test_secure_address_waits_for_the_name_to_stop_resolving(world, monkeypatch):
    # The state flips to off at once; the Worker record goes later (after_disable).
    secure_routes(world)
    world.scenario["dig_seq"] = ["10.0.0.7\n", "10.0.0.7\n", ""]
    world.write_scenario()
    row = run_secure(world, monkeypatch)
    assert row["result"] == "pass", row
    assert len([c for c in world.calls() if c["cmd"] == "dig"]) == 3


def test_secure_address_fails_if_name_still_resolves(world, monkeypatch):
    secure_routes(world)
    world.scenario["dig_out"] = "10.0.0.7\n"
    world.write_scenario()
    row = run_secure(world, monkeypatch)
    assert row["result"] == "fail" and "still resolves" in row["detail"]
    assert len([c for c in world.calls() if c["cmd"] == "dig"]) > 5  # polled, not asked once


# ---------------------------------------------------------------- cloud LLM (MS-006)


def test_cloud_llm_na_without_key_and_not_required(world, monkeypatch):
    monkeypatch.delenv("SMOKE_CLOUD_LLM_KEY", raising=False)
    smoke.suite_cloud_llm(world.ctx("cloud-llm"))
    res = results(world)
    assert all(c["result"] == "na" for c in res.values())
    assert res["MS-006"]["required"] is False


def _cloud_world(world, monkeypatch, completion_status=200):
    monkeypatch.setenv("SMOKE_CLOUD_LLM_KEY", "sk-cloud-secret-key")
    puts = []
    keys = []

    def put(q):
        puts.append(json.loads(q["body"]))
        return 200, {}

    world.core.routes[("PUT", "/settings")] = put

    def put_key(q):
        keys.append(json.loads(q["body"]))
        return 200, {}

    world.core.routes[("PUT", "/api/v1/me/connector-credentials/llm_anthropic")] = put_key
    world.core.routes[("GET", "/api/v1/models")] = lambda q: (
        200,
        [{"id": "claude", "enabled": True}],
    )
    world.core.routes[("POST", "/api/v1/chat/completions")] = lambda q: (
        completion_status,
        {"model": "claude-x", "message": {"content": "ready"}},
    )
    world.cloud_keys = keys
    return puts


def test_cloud_llm_switches_privacy_and_restores(world, monkeypatch):
    puts = _cloud_world(world, monkeypatch)
    smoke.suite_cloud_llm(world.ctx("cloud-llm"))
    assert results(world)["MS-006"]["result"] == "pass"
    modes = [p["privacy_mode"] for p in puts if "privacy_mode" in p]
    assert modes == ["allow_cloud", "local_only"]
    # The key goes to the per-user route; PUT /settings api_keys is a 422 with sign-in on.
    assert world.cloud_keys == [{"payload": {"api_key": "sk-cloud-secret-key"}}]
    assert not any("api_keys" in p for p in puts)
    assert "sk-cloud-secret-key" not in world.out.read_text()


def test_cloud_llm_restores_local_only_when_completion_fails(world, monkeypatch):
    puts = _cloud_world(world, monkeypatch, completion_status=500)
    smoke.suite_cloud_llm(world.ctx("cloud-llm"))
    res = results(world)["MS-006"]
    assert res["result"] == "fail" and res["required"] is False
    assert [p["privacy_mode"] for p in puts if "privacy_mode" in p][-1] == "local_only"


def test_fixture_path_matches_core_upload_name():
    assert smoke.is_fixture_path("/workspace/uploads/default/45ef52b4_smoke-document.txt")
    assert smoke.is_fixture_path("/data/smoke-document.txt")
    assert not smoke.is_fixture_path("/workspace/uploads/default/45ef52b4_other.txt")
    assert not smoke.is_fixture_path("/workspace/uploads/default/xsmoke-document.txt")
