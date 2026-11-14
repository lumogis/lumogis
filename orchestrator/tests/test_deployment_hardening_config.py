"""LUM-887: static guards for the deployment hardening (security audit #2).

Each test stops a silent revert of one hardening: the loopback publish of the
orchestrator (INFRA-001), the Caddy-resolved client IP and the trusted-proxy
default (ID-007), and the non-root containers (INFRA-003). They read the
compose files, the Caddyfile, the Dockerfiles, squid.conf and the Makefile as
text; the behaviour is proven in ``test_entrypoint_privdrop*.py``,
``test_api_v1_csrf_proxy.py`` and the live ``make caddy-client-ip-prove``.
"""

from __future__ import annotations

import re

import pytest
from tests._compose_helpers import caddy_block_after
from tests._compose_helpers import find_repo_root
from tests._compose_helpers import host_ip
from tests._compose_helpers import load_compose
from tests._compose_helpers import strip_vars

_ROOT = find_repo_root()
_COMPOSE = _ROOT / "docker-compose.yml"
_CADDYFILE = _ROOT / "docker" / "caddy" / "Caddyfile"
_ENTRYPOINT = _ROOT / "orchestrator" / "docker-entrypoint.sh"
_DOCKERFILE = _ROOT / "orchestrator" / "Dockerfile"
_WEB_DOCKERFILE = _ROOT / "clients" / "lumogis-web" / "Dockerfile"


def _services() -> dict:
    return load_compose(_COMPOSE)["services"]


def _non_comment(text: str) -> str:
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))


# --- INFRA-001: loopback publish ------------------------------------------


def test_orchestrator_publish_is_loopback_only():
    files = sorted(_ROOT.glob("docker-compose*.yml"))
    assert files, "no docker-compose*.yml found"
    base_ports = (_services().get("orchestrator") or {}).get("ports") or []
    assert base_ports, "docker-compose.yml must publish the orchestrator (no vacuous pass)"
    for f in files:
        svc = (load_compose(f).get("services") or {}).get("orchestrator") or {}
        for entry in svc.get("ports") or []:
            assert host_ip(entry) == "127.0.0.1", (
                f"{f.name} publishes the orchestrator as {entry!r} beyond loopback "
                "(LUM-887 / INFRA-001). Reach Core through Caddy; publish on 127.0.0.1 only."
            )


# --- ID-007: trust boundary ------------------------------------------------


def test_compose_trusted_proxies_default_is_caddy():
    value = _services()["orchestrator"]["environment"]["LUMOGIS_TRUSTED_PROXIES"]
    assert value == "${LUMOGIS_TRUSTED_PROXIES:-caddy}"
    assert not re.search(r"/(8|12|16)\b", value)


def test_caddy_receives_trusted_proxies_knob():
    env = _services()["caddy"]["environment"]
    assert env["LUMOGIS_CADDY_TRUSTED_PROXIES"] == "${LUMOGIS_CADDY_TRUSTED_PROXIES:-127.0.0.1/32}"


def test_caddyfile_has_no_private_ranges_and_is_strict():
    text = _non_comment(_CADDYFILE.read_text())
    assert "private_ranges" not in text
    assert "trusted_proxies_strict" in text
    assert "trusted_proxies static {$LUMOGIS_CADDY_TRUSTED_PROXIES:127.0.0.1/32}" in text


def test_every_orchestrator_proxy_sets_client_ip():
    text = _CADDYFILE.read_text()
    hits = [
        m.start()
        for m in re.finditer(r"reverse_proxy orchestrator:8000", text)
        if not text[text.rfind("\n", 0, m.start()) + 1 : m.start()].lstrip().startswith("#")
    ]
    assert len(hits) == 2, (
        f"expected 2 `reverse_proxy orchestrator:8000` (snippet + /events*), found {len(hits)}. "
        "A new special-case block must carry both header_up lines; ordinary routes use "
        "`import to_orchestrator`."
    )
    for at in hits:
        body = caddy_block_after(text, at)
        assert body, "a one-liner reverse_proxy to the orchestrator has no header_up"
        assert "header_up X-Forwarded-For {client_ip}" in body
        assert "header_up -X-Real-IP" in body


def test_orchestrator_handles_import_the_snippet():
    text = _non_comment(_CADDYFILE.read_text())
    assert len(re.findall(r"^\s*import to_orchestrator\s*$", text, re.M)) >= 25
    # Outside the snippet and /events*, no handle body proxies to the orchestrator itself.
    stripped = re.sub(r"\(to_orchestrator\) \{.*?\n\}\n", "", text, flags=re.S)
    stripped = re.sub(r"handle /events\*\s*\{.*?\n\t\}\n", "", stripped, flags=re.S)
    assert "reverse_proxy orchestrator:8000" not in stripped


# --- INFRA-003: non-root ---------------------------------------------------


def _privdrop_text() -> str:
    text = _ENTRYPOINT.read_text()
    m = re.search(r"^# >>> privdrop-section\n(.*?)^# <<< privdrop-section$", text, re.S | re.M)
    assert m, "privdrop-section markers missing from docker-entrypoint.sh"
    return m.group(1)


# Writable orchestrator mounts that the repair list deliberately does not own.
NOT_REPAIRED = {
    "/project": (
        "checkout; only .env, config/librechat.yaml and docker-compose.override.yml are repaired"
    ),
}


def test_every_writable_orchestrator_mount_is_repaired():
    section = _privdrop_text()
    mounts = _services()["orchestrator"]["volumes"]
    checked = 0
    for entry in mounts:
        parts = strip_vars(str(entry)).split(":")
        if len(parts) < 2 or (len(parts) >= 3 and parts[-1] == "ro"):
            continue
        target = parts[1]
        if target == "X":  # a `${VAR:-default}` target is not used here
            continue
        checked += 1
        if target in NOT_REPAIRED:
            continue
        assert target in section, (
            f"orchestrator mount {target} is writable but not in the privdrop repair list "
            "(orchestrator/docker-entrypoint.sh). Add it, or add it to NOT_REPAIRED with a reason."
        )
    assert checked >= 4


# Env vars that move a path the orchestrator writes. Each must reach the repair
# list, or a moved path is not writable after the privilege drop (LUM-887).
MOVABLE_WRITE_PATHS = (
    "HF_HOME",
    "EMAIL_ATTACHMENT_STAGING_ROOT",
    "LUMOGIS_DATA_DIR",
    "CAPTURE_MEDIA_ROOT",
    "LUMOGIS_IMAP_EGRESS_ALLOW_DIR",
    "USER_EXPORT_DIR",
    "STOP_ENTITIES_PATH",  # LUM-922
)


def test_movable_write_paths_are_repaired():
    section = _privdrop_text()
    for var in MOVABLE_WRITE_PATHS:
        assert "${" + var + ":-" in section, (
            f"{var} moves a path the orchestrator writes, but the privdrop repair list "
            "(orchestrator/docker-entrypoint.sh) does not read it"
        )
    # LUM-922: the default stop-entities location is named on the list itself.
    assert "$ws/config/stop_entities.txt" in section


def test_stop_entities_default_matches_the_repair_list(monkeypatch):
    """The path config.py writes by default is the one the entrypoint repairs."""
    import config

    monkeypatch.delenv("STOP_ENTITIES_PATH", raising=False)
    monkeypatch.setenv("WORKSPACE_PATH", "/workspace")
    assert config.get_stop_entities_path() == "/workspace/config/stop_entities.txt"


def test_orchestrator_and_web_set_no_new_privileges():
    services = _services()
    for name in ("orchestrator", "lumogis-web"):
        assert "no-new-privileges:true" in services[name].get("security_opt", []), name


def test_orchestrator_drops_all_capabilities_but_the_init_set():
    """LUM-916: cap_drop ALL; add back only what the root init uses.

    Each capability was shown necessary by booting the image without it:
    CHOWN (ownership repair), DAC_OVERRIDE (writing the checkout owner's .env
    and config/), FOWNER (``sed -i`` keeping .env's mode), SETUID and SETGID
    (``setpriv``). Adding one needs the same proof.
    """
    svc = _services()["orchestrator"]
    assert svc.get("cap_drop") == ["ALL"]
    assert sorted(svc.get("cap_add") or []) == [
        "CHOWN",
        "DAC_OVERRIDE",
        "FOWNER",
        "SETGID",
        "SETUID",
    ]


def test_librechat_publish_is_loopback_only():
    """LUM-916: LibreChat is not behind Caddy, so it is host-only like Core."""
    ports = _services()["librechat"].get("ports") or []
    assert ports, "librechat must still be published on the host (no vacuous pass)"
    for entry in ports:
        assert host_ip(entry) == "127.0.0.1", entry


def test_hf_cache_mount_matches_hf_home():
    df = _DOCKERFILE.read_text()
    m = re.search(r"^ENV HF_HOME=(\S+)$", df, re.M)
    assert m, "Dockerfile must set HF_HOME"
    mounts = _services()["orchestrator"]["volumes"]
    assert f"hf_cache:{m.group(1)}" in mounts
    assert "SENTENCE_TRANSFORMERS_HOME" not in _non_comment(df)


def test_orchestrator_dockerfile_creates_runtime_user_and_no_global_git_config():
    df = _DOCKERFILE.read_text()
    assert "--uid 10001" in df
    assert "git config --system" in df
    assert "git config --global" not in df


def test_web_final_stage_is_unprivileged_nginx():
    froms = [ln for ln in _WEB_DOCKERFILE.read_text().splitlines() if ln.startswith("FROM ")]
    assert froms[-1].startswith("FROM nginxinc/nginx-unprivileged:")
    assert re.search(
        r"\blisten 80\b", (_ROOT / "clients" / "lumogis-web" / "nginx.conf").read_text()
    )


def test_squid_umask_lets_core_read_access_log():
    conf = (_ROOT / "docker" / "egress-proxy" / "squid.conf").read_text()
    assert re.search(r"^umask 022$", conf, re.M)


def test_env_example_caddy_knob_is_space_separated():
    text = (_ROOT / ".env.example").read_text()
    values = re.findall(r"^#?\s*LUMOGIS_CADDY_TRUSTED_PROXIES=(.*)$", text, re.M)
    assert values, ".env.example must show LUMOGIS_CADDY_TRUSTED_PROXIES"
    for v in values:
        assert "," not in v, (
            f"comma in LUMOGIS_CADDY_TRUSTED_PROXIES example {v!r}: Caddy refuses it"
        )


def _make_recipes() -> dict[str, str]:
    """Target name -> continuation-joined recipe text."""
    text = (_ROOT / "Makefile").read_text().replace("\\\n", " ")
    out: dict[str, str] = {}
    current = None
    for line in text.splitlines():
        m = re.match(r"^([A-Za-z0-9_.-]+):(?!=)", line)
        if m:
            current = m.group(1)
            out[current] = ""
        elif current and line.startswith("\t"):
            out[current] += line + "\n"
    return out


def test_pip_installing_make_targets_keep_root():
    recipes = _make_recipes()
    for target in (
        "compose-test",
        "compose-test-stack-control",
        "compose-test-integration",
        "web-caddy-headers",
        "web-caddy-headers-prove",
        "caddy-client-ip-prove",
    ):
        body = recipes[target]
        assert "LUMOGIS_RUN_UID=0" in body or "--entrypoint" in body, target
    for target, body in recipes.items():
        for line in body.splitlines():
            if (
                re.search(r"docker compose .*\brun\b", line)
                and " orchestrator" in line
                and "pip install" in line
            ):
                assert "LUMOGIS_RUN_UID=0" in line or "--entrypoint" in line, (
                    f"Makefile target {target} pip installs through the entrypoint as non-root; "
                    "add -e LUMOGIS_RUN_UID=0 (LUM-887)"
                )


def test_ci_runs_the_root_privdrop_tests_on_the_orchestrator_base_image():
    # VERIFY-PLAN: added. The CI pytest runs as a non-root user, so
    # test_entrypoint_privdrop_root.py skips there; a dedicated CI step runs it
    # as root in a container. That container must use the orchestrator's base
    # image, or the real find / chown / setpriv it proves are not the shipped ones.
    ci = _ROOT / ".github" / "workflows" / "ci.yml"
    if not ci.is_file():
        pytest.skip("no .github/workflows/ci.yml in this tree")
    text = ci.read_text(encoding="utf-8")
    base = re.search(r"^FROM\s+(\S+)", _DOCKERFILE.read_text(encoding="utf-8"), re.M)
    assert base, "orchestrator/Dockerfile has no FROM line"
    step = re.search(r"docker run[^\n]*(?:\\\n[^\n]*)*test_entrypoint_privdrop_root\.py", text)
    assert step, "ci.yml no longer runs test_entrypoint_privdrop_root.py in a root container"
    assert base.group(1) in step.group(0), (
        f"the CI root-test container must use the orchestrator base image {base.group(1)}"
    )


# --- LUM-874: lint-and-test service containers take Docker-assigned host ports -

_PG_PORT_EXPR = "job.services.postgres.ports['5432']"
_QDRANT_PORT_EXPR = "job.services.qdrant.ports['6333']"
_FALKORDB_PORT_EXPR = "job.services.falkordb.ports['6379']"
_PORT_CONSUMERS = {
    "POSTGRES_PORT": _PG_PORT_EXPR,
    "PGPORT": _PG_PORT_EXPR,
    "LUMOGIS_INTEGRATION_POSTGRES_DSN": _PG_PORT_EXPR,
    "QDRANT_URL": _QDRANT_PORT_EXPR,
    "CI_QDRANT_PORT": _QDRANT_PORT_EXPR,
    "FALKORDB_URL": _FALKORDB_PORT_EXPR,  # LUM-720
    "LUM811_IMAP_PORT": "job.services.greenmail.ports['3143']",
    "LUM811_SMTP_PORT": "job.services.greenmail.ports['3025']",
}


def test_ci_lint_and_test_services_use_ephemeral_host_ports():
    # Both self-hosted runners share one Docker daemon with a live stack. A fixed
    # host port is held by a container a cancelled run left behind, or by the
    # other runner's lint-and-test, and the job dies with "port is already
    # allocated" before a single test runs. Steps must read the port Docker
    # assigned, and the guards that stop an unresolved port reaching psql must stay.
    ci = _ROOT / ".github" / "workflows" / "ci.yml"
    if not ci.is_file():
        pytest.skip("no .github/workflows/ci.yml in this tree")
    text = ci.read_text(encoding="utf-8")
    job = load_compose(ci)["jobs"]["lint-and-test"]

    services = job.get("services") or {}
    assert {"postgres", "qdrant", "falkordb", "greenmail"} <= set(services), (
        "lint-and-test lost a service container (the live integration modules need each one)"
    )
    for name, svc in services.items():
        ports = svc.get("ports") or []
        assert ports, f"service {name} publishes no port (no vacuous pass)"
        for entry in ports:
            fixed = "published" in entry if isinstance(entry, dict) else ":" in str(entry)
            assert not fixed, (
                f"service {name} maps a fixed host port ({entry!r}); publish the container "
                "port only (LUM-874: fixed host ports collide on the shared runner)"
            )

    old_port = re.search(r"\b(55432|56333)\b", text)
    assert old_port is None, f"the old fixed host port {old_port.group(0)} is back in ci.yml"

    steps = job.get("steps") or []
    seen: dict[str, list[str]] = {key: [] for key in _PORT_CONSUMERS}
    for step in steps:
        env = step.get("env")
        if isinstance(env, dict):
            for key in _PORT_CONSUMERS:
                if key in env:
                    seen[key].append(str(env[key]))
    for key, expr in _PORT_CONSUMERS.items():
        assert seen[key], f"no lint-and-test step sets {key} (no vacuous pass)"
        for value in seen[key]:
            # The whole expression, so a `|| '5433'` fallback cannot mask an unresolved port.
            assert f"${{{{ {expr} }}}}" in value, (
                f"{key}={value!r} must read the assigned port via ${{{{ {expr} }}}}"
            )

    runs = [str(step.get("run") or "") for step in steps]
    psql = [run for run in runs if "psql" in run]
    assert psql, "no lint-and-test step runs psql (no vacuous pass)"
    for run in psql:
        assert '-p "$PGPORT"' in run, "psql must take the assigned port from PGPORT"
        assert "!= 5432" in run, "the guard rejecting an unresolved or default PGPORT is gone"
        assert re.search(r"psql[^\n]*-p\s*\d", run) is None, "psql is given a literal port"
    readyz = [run for run in runs if "/readyz" in run]
    assert readyz, "no lint-and-test step waits for Qdrant (no vacuous pass)"
    for run in readyz:
        assert '-n "${CI_QDRANT_PORT}"' in run, "the guard on an unresolved Qdrant port is gone"


def test_ci_buildx_builders_stay_private_to_their_job():
    # Both self-hosted runners run as one OS user and share ~/.docker/buildx. A
    # setup-buildx-action left at its default `use: true` makes its throwaway
    # builder the current builder for every concurrent job, and its post-step
    # then removes it mid-build ("failed to receive status ... EOF").
    ci = _ROOT / ".github" / "workflows" / "ci.yml"
    if not ci.is_file():
        pytest.skip("no .github/workflows/ci.yml in this tree")
    jobs = load_compose(ci)["jobs"]
    seen = 0
    for name, job in jobs.items():
        steps = job.get("steps") or []
        setups = [
            s for s in steps if str(s.get("uses", "")).startswith("docker/setup-buildx-action")
        ]
        for setup in setups:
            seen += 1
            assert (setup.get("with") or {}).get("use") is False, (
                f"{name}: setup-buildx-action must set `use: false` (shared runner user)"
            )
            assert setup.get("id"), f"{name}: setup-buildx-action needs an id to name its builder"
            builder = f"steps.{setup['id']}.outputs.name"
            for step in steps:
                uses = str(step.get("uses", ""))
                if uses.startswith(("docker/bake-action", "docker/build-push-action")):
                    assert builder in str((step.get("with") or {}).get("builder", "")), (
                        f"{name}: {uses} must build on its own builder ({builder})"
                    )
    assert seen, "no setup-buildx-action left in ci.yml (no vacuous pass; delete this test with it)"


# --- LUM-892: Lumogis-owned files are in the workspace and in the DR snapshot -


def _workspace_source(service: dict) -> str | None:
    for entry in service.get("volumes") or []:
        parts = str(entry).rsplit(":", 2)
        # "<source>:/workspace" or "<source>:/workspace:ro"; skip /workspace/backups
        for i, part in enumerate(parts):
            if part == "/workspace":
                return ":".join(parts[:i]) if i else None
    return None


def test_backup_service_mounts_workspace_and_orchestrator_uses_same_source():
    services = _services()
    backup = services["backup"]
    orch_src = _workspace_source(services["orchestrator"])
    backup_src = _workspace_source(backup)
    assert orch_src, "orchestrator must mount the workspace at /workspace"
    assert backup_src, "backup service must mount the workspace at /workspace (LUM-892)"
    assert orch_src == backup_src, (
        f"orchestrator mounts {orch_src!r} but backup mounts {backup_src!r}: "
        "the DR files store would copy a different folder than the one Lumogis writes"
    )
    assert "WORKSPACE_HOST_DIR" in orch_src, "the workspace host dir must be configurable"
    assert backup["environment"]["WORKSPACE_PATH"] == "/workspace"


def _line_index(lines: list[str], needle: str) -> int:
    for i, ln in enumerate(lines):
        if not ln.lstrip().startswith("#") and needle in ln:
            return i
    raise AssertionError(f"{needle!r} not found")


def test_update_flow_rebuilds_backup_and_rescues_before_recreate():
    update = (_ROOT / "scripts" / "update" / "update.sh").read_text().splitlines()
    pull = _line_index(update, "docker compose pull")
    build = _line_index(update, "docker compose build --pull backup")
    rescue = _line_index(update, "rescue-captures.sh")
    rescue_stop = _line_index(update, "rescue-stop-entities.sh")  # LUM-922
    up = _line_index(update, "docker compose up -d")
    assert pull < build < rescue < rescue_stop < up
    # LUM-900: qdrant has `image: lumogis-qdrant:…` *and* `build:`, so a plain
    # `docker compose pull` asks a registry for it and exits non-zero
    # ("pull access denied"), aborting every default-install update.
    assert "--ignore-buildable" in update[pull], (
        "update.sh must pull with --ignore-buildable: locally built images have no registry"
    )
    assert "|| die" in update[rescue_stop + 1], "a failed stop-entities rescue must stop the update"
    # a failed backup-image build warns; it must not block an image update
    assert "|| warn" in update[build + 1], "the backup image build must be followed by `|| warn`"

    make = (_ROOT / "Makefile").read_text()
    build_recipe = re.search(r"^build:\n((?:\t.*\n)+)", make, re.M)
    assert build_recipe, "Makefile build: recipe not found"
    recipe = build_recipe.group(1).splitlines()
    assert (
        _line_index(recipe, "docker compose build --pull")
        < _line_index(recipe, "rescue-captures.sh")
        < _line_index(recipe, "rescue-stop-entities.sh")
        < _line_index(recipe, "docker compose up")
    )
    dev_recipe = re.search(r"^dev:\n((?:\t.*\n)+)", make, re.M)
    assert dev_recipe, "Makefile dev: recipe not found"
    dev = dev_recipe.group(1).splitlines()
    assert _line_index(dev, "rescue-captures.sh") < _line_index(dev, "up --build")
    assert _line_index(dev, "rescue-stop-entities.sh") < _line_index(dev, "up --build")


@pytest.mark.parametrize("script", ["rescue-captures.sh", "rescue-stop-entities.sh"])
def test_rescue_script_is_host_portable(script):
    """It runs on the host, which may be macOS: no GNU-only or behaviour-changing flags."""
    text = _non_comment((_ROOT / "scripts" / "update" / script).read_text())
    for banned in (
        "stat -c",
        "stat --",
        "cp -n",
        "cp --update",
        "readlink -f",
        "date -d",
        "sed -i",
        "-printf",
        "grep -P",
    ):
        assert banned not in text, f"{script} uses {banned!r}, which is not host-portable"


def test_backup_image_installs_rsync():
    assert re.search(r"^\s+rsync\b", (_ROOT / "docker" / "backup" / "Dockerfile").read_text(), re.M)


# LUM-900: update/rollback waited on a hard-coded :8000, so `make update` failed
# its health gate on every install with ORCHESTRATOR_HOST_PORT moved.
def test_update_scripts_do_not_hard_code_the_health_port():
    for name in ("update.sh", "rollback.sh"):
        text = (_ROOT / "scripts" / "update" / name).read_text()
        assert "127.0.0.1:8000/healthz" not in text, f"{name} hard-codes the health port"
        assert 'wait_for_stack_ready "$(orchestrator_health_url)"' in text, name


@pytest.mark.parametrize(
    ("published", "env", "expected"),
    [
        ("127.0.0.1:18000", {}, "http://127.0.0.1:18000/healthz"),
        ("0.0.0.0:8001", {}, "http://127.0.0.1:8001/healthz"),
        ("", {}, "http://127.0.0.1:8000/healthz"),  # not running: the documented default
        ("127.0.0.1:18000", {"LUMOGIS_HEALTH_URL": "http://h:1/healthz"}, "http://h:1/healthz"),
    ],
)
def test_orchestrator_health_url_uses_the_published_port(tmp_path, published, env, expected):
    import os
    import subprocess

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "docker"
    fake.write_text(
        f'#!/usr/bin/env bash\n[ "$1 $2" = "compose port" ] && echo "{published}"\nexit 0\n'
    )
    fake.chmod(0o755)
    common = _ROOT / "scripts" / "update" / "common.sh"
    full_env = {"PATH": f"{bin_dir}:{os.environ['PATH']}", **env}
    out = subprocess.run(
        ["bash", "-c", f'source "{common}"; orchestrator_health_url'],
        env=full_env,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    ).stdout.strip()
    assert out == expected


# LUM-310: a service with both `image:` and `build:` is built here and has no
# registry. Without `pull_policy: never`, `docker compose up` first asks a registry
# for it, and every new install prints "pull access denied".
def test_locally_built_images_are_never_pulled():
    built_here = {n: s for n, s in _services().items() if "image" in s and "build" in s}
    assert "qdrant" in built_here, "expected the locally built qdrant image"
    for name, svc in built_here.items():
        assert svc.get("pull_policy") == "never", f"{name}: set pull_policy: never"


# LUM-310: the orchestrator never gets a GPU (docker-compose.gpu.yml reserves one
# for Ollama only), so the CUDA torch from PyPI is several GB of dead weight. Torch
# comes from the CPU-only index, on its own, before the requirements.
def test_orchestrator_image_installs_cpu_only_torch_first():
    lines = _non_comment((_ROOT / "orchestrator" / "Dockerfile").read_text()).splitlines()
    cpu = _line_index(lines, "pip install --index-url https://download.pytorch.org/whl/cpu torch")
    reqs = _line_index(lines, "pip install -r requirements.txt")
    assert cpu < reqs, "CPU torch must be installed before the requirements resolve torch"
    assert "--extra-index-url" not in "\n".join(lines), "only torch may come from the PyTorch index"
    assert "endswith('+cpu')" in lines[reqs + 1], "the build must fail if a CUDA torch slips in"
    gpu = (_ROOT / "docker-compose.gpu.yml").read_text()
    assert "orchestrator" not in gpu, "the orchestrator got a GPU: revisit the CPU-only torch"
