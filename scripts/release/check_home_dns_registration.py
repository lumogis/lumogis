#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Does the name service accept this release's registration value? (LUM-996)

Reads DEFAULT_REGISTRATION_SECRET from Core's source and sends it to the name
service's ``/register`` with nothing else. The service checks the value before any
key material, so a refused value answers ``403 registration_forbidden`` and an
accepted one answers ``400 bad_input`` (no key was sent): no household is created.
The request does use one of the service's hourly per-address ``/register`` slots.

Exit codes: 0 accepted, 1 refused (publish nothing), 2 could not tell (try again).
Prints only a one-line reason; never the value.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SOURCE = ROOT / "orchestrator" / "services" / "home_dns" / "worker_client.py"
DEFAULT_URL = "https://dns.homes.lumogis.app"
USER_AGENT = "lumogis-release-check/1"
TIMEOUT_SEC = 15


def release_value(source: Path = SOURCE) -> str:
    """The constant, read with ``ast`` so no orchestrator import is needed."""
    tree = ast.parse(source.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        target = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target, value = node.target.id, node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            t = node.targets[0]
            target, value = (t.id if isinstance(t, ast.Name) else None), node.value
        if target == "DEFAULT_REGISTRATION_SECRET" and isinstance(value, ast.Constant):
            if isinstance(value.value, str) and value.value:
                return value.value
    raise LookupError("DEFAULT_REGISTRATION_SECRET not found in Core's source")


def _error_code(body: bytes) -> str | None:
    try:
        data = json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        return None
    if isinstance(data, dict) and isinstance(data.get("error"), str):
        return data["error"]
    return None


def classify(status: int, body: bytes) -> tuple[int, str]:
    code = _error_code(body)
    # Not checked: the message. The name service answers 400 bad_input for a mangled
    # body before it looks at the value too; the script's own body cannot trigger that.
    if status == 400 and code == "bad_input":
        return 0, "the name service accepts this release's registration value"
    if status == 403 and code == "registration_forbidden":
        return 1, "publish nothing: the name service refuses this release's registration value"
    if status == 403 and code is None:
        return 2, "HTTP 403 blocked before reaching the name service, not refused by it"
    if status == 429:
        return 2, "HTTP 429 from the name service: try again later"
    if status >= 500:
        return 2, f"HTTP {status} from the name service: try again later"
    if code is None:
        return 2, f"HTTP {status} without an error code: cannot tell"
    return 2, f"HTTP {status} with an unexpected error code: cannot tell"


def check(url: str, value: str) -> tuple[int, str]:
    if urllib.parse.urlsplit(url).scheme not in ("https", "http"):
        return 2, "the name-service URL must start with https:// (or http:// for a local test)"
    request = urllib.request.Request(
        f"{url.rstrip('/')}/register",
        data=json.dumps({"registration_secret": value}).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )
    try:
        # Scheme checked above (http/https only), so bandit's B310 does not apply.
        with urllib.request.urlopen(request, timeout=TIMEOUT_SEC) as resp:  # noqa: S310  # nosec B310
            status, body = resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        status, body = exc.code, exc.read()
    except Exception as exc:
        return 2, f"cannot reach the name service ({type(exc).__name__}): try again later"
    return classify(status, body)


def main(argv: list[str] | None = None) -> int:
    try:
        return _main(argv)
    except SystemExit:
        raise
    except Exception as exc:  # a crash is "cannot tell", never "refused"
        print(f"could not run the check ({type(exc).__name__}): try again", file=sys.stderr)
        return 2


def _main(argv: list[str] | None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default=os.environ.get("LUMOGIS_HOME_DNS_URL") or DEFAULT_URL)
    args = parser.parse_args(argv)
    host = urllib.parse.urlsplit(args.url).netloc
    code, reason = check(args.url, release_value())
    print(f"{host}: {reason}", file=sys.stderr if code else sys.stdout)
    return code


if __name__ == "__main__":
    sys.exit(main())
