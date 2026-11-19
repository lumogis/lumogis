# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Fakes for scripts/release/smoke/release_smoke.py (LUM-997).

* ``FakeCore`` — a threaded stdlib ``HTTPServer`` answering the household API shapes.
* ``FAKE_TOOL`` — one Python script installed as ``docker``, ``sudo``, ``iptables``,
  ``openssl``, ``dig`` and ``ip`` on ``PATH``. It records every call as a JSON line and
  keeps a small amount of compose state (container generations, recreate-on-nth-up).
"""

from __future__ import annotations

import json
import stat
import threading
from http.server import BaseHTTPRequestHandler
from http.server import HTTPServer
from pathlib import Path
from typing import Any
from typing import Callable

FAKE_TOOL = r"""#!/usr/bin/env python3
import json, os, sys
name = os.path.basename(sys.argv[0])
args = sys.argv[1:]
if name == "sudo":
    args = [a for a in args if a != "-n"]
    os.execvp(args[0], args)
sc = json.load(open(os.environ["FAKE_SCENARIO"]))
state_path = os.environ["FAKE_STATE"]
_fresh = {"ups": 0, "gen": {}, "n": {}}
state = json.load(open(state_path)) if os.path.exists(state_path) else _fresh
def save():
    json.dump(state, open(state_path, "w"))
with open(os.environ["FAKE_LOG"], "a") as fh:
    fh.write(json.dumps({"cmd": name, "argv": args, "pwd": os.environ.get("PWD", ""),
                         "cwd": os.getcwd(),
                         "env": open(".env").read() if os.path.exists(".env") else ""}) + "\n")
def out(stdout="", rc=0, stderr=""):
    sys.stdout.write(stdout); sys.stderr.write(stderr); save(); sys.exit(rc)
def gen(svc): return state["gen"].get(svc, 0)
def cid(svc): return f"{svc}@{gen(svc)}"
services = sc.get("services", [])
if name == "docker":
    if args[:1] == ["compose"]:
        rest = args[1:]
        if rest[:1] == ["up"]:
            state["ups"] += 1
            n = str(state["ups"])
            for svc in sc.get("recreate_on_up", {}).get(n, []):
                state["gen"][svc] = gen(svc) + 1
            extra = sc.get("env_append_on_up", {}).get(n)
            if extra:
                open(os.path.join(os.getcwd(), ".env"), "a").write(extra + "\n")
            out(sc.get("up_stdout", ""), sc.get("up_rc", 0), sc.get("up_stderr", ""))
        if rest[:1] == ["ps"]:
            flag = os.environ.get("FAKE_RESTART_FLAG")
            if flag and os.path.exists(flag):
                os.remove(flag); state["gen"]["orchestrator"] = gen("orchestrator") + 1
            rows = []
            for svc in services:
                if svc in sc.get("not_running", []):
                    continue
                health = "unhealthy" if svc in sc.get("unhealthy", []) else "healthy"
                rows.append({"Service": svc, "ID": cid(svc), "State": "running",
                             "Health": health})
            if sc.get("ps_array", True):
                out(json.dumps(rows))
            out("\n".join(json.dumps(r) for r in rows))
        if rest[:2] == ["config", "--services"]:
            out("\n".join(services) + "\n")
        if rest[:2] == ["config", "--volumes"]:
            out("\n".join(sc.get("volumes", [])) + "\n")
    if args[:2] == ["volume", "rm"]:
        state["wiped"] = True
        out("", sc.get("volume_rm_rc", 0), sc.get("volume_rm_stderr", ""))
    if any("restore.sh" in a for a in args):
        state["wiped"] = False
    if args[:1] == ["inspect"]:
        svc = args[1].rsplit("@", 1)[0]
        info = sc.get("inspect", {}).get(svc) or {
            "Image": "sha256:img-" + svc,
            "Config": {"Env": ["LUMOGIS_IMAGE_VERSION=" + sc.get("image_version", "")]},
            "NetworkSettings": {"Networks": {"n": {"IPAddress": "172.20.0.5"},
                                             "m": {"IPAddress": "172.21.0.5"}}},
        }
        out(json.dumps([info]))
    if args[:2] == ["image", "inspect"]:
        svc = args[2].replace("sha256:img-", "")
        out(json.dumps([{"RepoDigests": sc.get("repo_digests", {}).get(svc, [])}]))
elif name == "iptables":
    if args[:2] == ["-S", "DOCKER-USER"]:
        out("", sc.get("docker_user_rc", 0))
    if args[:1] == ["-L"]:
        seq = sc.get("egress_counts", [[2], [0]])
        i = state["n"].get("L", 0); state["n"]["L"] = i + 1
        counts = seq[min(i, len(seq) - 1)]
        row = "   %s    0 REJECT     all  --  *  *  172.20.0.5  0.0.0.0/0\n"
        out("".join(row % c for c in counts))
elif name == "openssl":
    out(sc.get("openssl_out", ""))
elif name == "dig":
    seq = sc.get("dig_seq")
    if seq:
        i = state["n"].get("dig", 0); state["n"]["dig"] = i + 1
        out(seq[min(i, len(seq) - 1)])
    out(sc.get("dig_out", ""))
elif name == "ip":
    out(sc.get("ip_out", "1.1.1.1 via 10.0.0.1 dev eth0 src 10.0.0.7 uid 0\n"))
for r in sc.get("rules", []):
    if r["cmd"] == name and all(t in args for t in r.get("has", [])):
        for rel in r.get("touch", []):
            p = os.path.join(os.getcwd(), rel); os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, "w").write("x")
        if state.get("wiped") and "stdout_wiped" in r:
            out(r["stdout_wiped"], r.get("rc", 0), r.get("stderr", ""))
        out(r.get("stdout", ""), r.get("rc", 0), r.get("stderr", ""))
out("", 0)
"""


def install_fake_tools(bindir: Path) -> None:
    bindir.mkdir(parents=True, exist_ok=True)
    for tool in ("docker", "sudo", "iptables", "openssl", "dig", "ip"):
        p = bindir / tool
        p.write_text(FAKE_TOOL)
        p.chmod(p.stat().st_mode | stat.S_IEXEC)


def read_log(path: Path) -> list[dict]:
    if not Path(path).exists():
        return []
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


class FakeCore:
    """Routes map ``(METHOD, path)`` -> ``callable(req) -> (status, body)`` or ``"drop"``."""

    def __init__(self) -> None:
        self.routes: dict[tuple[str, str], Callable[[dict], Any]] = {}
        self.requests: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _serve(self) -> None:
                length = int(self.headers.get("Content-Length", "0") or 0)
                body = self.rfile.read(length) if length else b""
                path = self.path.split("?")[0]
                req = {
                    "method": self.command,
                    "path": path,
                    "full": self.path,
                    "auth": self.headers.get("Authorization", ""),
                    "ctype": self.headers.get("Content-Type", ""),
                    "body": body,
                }
                outer.requests.append(req)
                fn = outer.routes.get((self.command, path))
                res = fn(req) if fn else (404, {"detail": "not found"})
                if res == "drop":
                    self.connection.close()
                    return
                status, payload = res
                data = (
                    payload
                    if isinstance(payload, bytes)
                    else (
                        payload.encode()
                        if isinstance(payload, str)
                        else json.dumps(payload).encode()
                    )
                )
                self.send_response(status)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _serve  # noqa: N815

            def log_message(self, *a: Any) -> None:
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def paths(self) -> list[str]:
        return [r["path"] for r in self.requests]
