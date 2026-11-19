# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""scripts/release/check_home_dns_registration.py against the Worker's real bodies (LUM-996).

The fake answers what the name service answers: ``{"error": <code>, "message": …}``
(there is no ``code`` field). A fake with the wrong key would let the check pass
for the wrong reason, so one case sends ``{"code": …}`` and expects "cannot tell".
"""

from __future__ import annotations

import importlib.util
import json
import re
import threading
from http.server import BaseHTTPRequestHandler
from http.server import HTTPServer
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPT = _ROOT / "scripts" / "release" / "check_home_dns_registration.py"

_spec = importlib.util.spec_from_file_location("check_home_dns_registration", _SCRIPT)
check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check)


class _Fake:
    def __init__(self, status: int, body: bytes, content_type: str = "application/json"):
        outer = self
        self.requests: list[dict] = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length", "0"))
                outer.requests.append(
                    {
                        "path": self.path,
                        "body": self.rfile.read(length),
                        "ua": self.headers.get("User-Agent", ""),
                        "ct": self.headers.get("Content-Type", ""),
                    }
                )
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):  # silence
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def _run(status, body, capsys, content_type="application/json"):
    fake = _Fake(status, body, content_type)
    try:
        rc = check.main(["--url", fake.url])
    finally:
        fake.close()
    return rc, capsys.readouterr(), fake


def _err(code: str) -> bytes:
    return json.dumps({"error": code, "message": "x"}).encode()


def test_accepted_value_is_exit_0(capsys):
    rc, out, _ = _run(400, _err("bad_input"), capsys)
    assert rc == 0 and "accepts" in out.out


def test_refused_value_is_exit_1(capsys):
    rc, out, _ = _run(403, _err("registration_forbidden"), capsys)
    assert rc == 1 and "publish nothing" in out.err


def test_rate_limited_is_exit_2(capsys):
    rc, out, _ = _run(429, _err("rate_limited"), capsys)
    assert rc == 2 and "429" in out.err


def test_server_error_is_exit_2(capsys):
    rc, out, _ = _run(503, _err("service_full"), capsys)
    assert rc == 2 and "503" in out.err


def test_a_code_key_instead_of_error_cannot_tell(capsys):
    """The Worker's key is ``error``; ``code`` must never produce exit 0 or 1."""
    rc, _, _ = _run(400, json.dumps({"code": "bad_input"}).encode(), capsys)
    assert rc == 2
    rc, _, _ = _run(403, json.dumps({"code": "registration_forbidden"}).encode(), capsys)
    assert rc == 2


def test_html_403_is_blocked_before_the_service(capsys):
    rc, out, _ = _run(403, b"<html>blocked</html>", capsys, "text/html")
    assert rc == 2 and "blocked before" in out.err


def test_unreachable_is_exit_2(capsys):
    rc = check.main(["--url", "http://127.0.0.1:1"])
    assert rc == 2 and "cannot reach" in capsys.readouterr().err


def test_request_is_value_only_with_an_explicit_agent(capsys):
    _, _, fake = _run(400, _err("bad_input"), capsys)
    (req,) = fake.requests
    assert req["path"] == "/register"
    assert json.loads(req["body"]) == {"registration_secret": check.release_value()}
    assert req["ct"] == "application/json"
    assert req["ua"] and not req["ua"].startswith("Python-urllib")


def test_value_is_never_printed(capsys):
    value = check.release_value()
    for status, body in (
        (400, _err("bad_input")),
        (403, _err("registration_forbidden")),
        (429, b"{}"),
    ):
        _, out, _ = _run(status, body, capsys)
        assert value not in out.out + out.err


def test_value_matches_core_default():
    from services.home_dns.worker_client import DEFAULT_REGISTRATION_SECRET

    assert check.release_value() == DEFAULT_REGISTRATION_SECRET


def test_release_assets_runs_the_check_before_every_publish_step():
    wf = (_ROOT / ".github" / "workflows" / "release-assets.yml").read_text(encoding="utf-8")
    names = re.findall(r"^\s+- name: (.+)$", wf, flags=re.M)
    check_step = next(i for i, n in enumerate(names) if "registration value" in n)
    assert "check_home_dns_registration.py" in wf
    for later in ("Require a draft Release", "Attest the install files", "Publish the Release"):
        idx = next(i for i, n in enumerate(names) if later in n)
        assert check_step < idx, later


def test_script_and_workflow_name_no_private_repository():
    for path in (_SCRIPT, _ROOT / ".github" / "workflows" / "release-assets.yml"):
        text = path.read_text(encoding="utf-8").lower()
        assert "lumogis-cloud" not in text and "runbook" not in text


def test_script_is_executable():
    assert _SCRIPT.stat().st_mode & 0o111


def test_a_crash_is_cannot_tell_never_refused(monkeypatch, capsys):
    def boom():
        raise LookupError("no constant")

    monkeypatch.setattr(check, "release_value", boom)
    assert check.main(["--url", "http://127.0.0.1:1"]) == 2
    assert "could not run" in capsys.readouterr().err


def test_a_non_http_url_is_cannot_tell_and_sends_nothing(capsys):
    """VERIFY-PLAN: bandit B310 fix. ``file:`` or a custom scheme never reaches urlopen."""
    for url in ("file:///etc/passwd", "ftp://127.0.0.1/"):
        assert check.main(["--url", url]) == 2
        assert "must start with https://" in capsys.readouterr().err
