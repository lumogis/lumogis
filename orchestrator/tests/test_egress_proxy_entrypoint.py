# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-758 — the egress-proxy watcher's pure shell functions.

These run the real `docker/egress-proxy/entrypoint.sh` under bash with a fake
`/proc` and a fake `squid` on PATH. No Docker, so they run in `make test`.

Each test here pins a property that was wrong in an earlier draft of this work and
would have failed *silently* in production:

* the digest ignoring Core's in-flight temp file (else every byte-identical
  rewrite reloads Squid, churning its SSL context for nothing),
* the PID lookup refusing to assume PID 1 is Squid (else adding `init: true` to the
  compose service would send the reload to tini and report success),
* the parse gate checking output **and** exit status — neither alone suffices: a
  missing allow file exits 0 and only prints `ERROR:`, while a malformed directive
  exits 1 and never prints `ERROR` at all.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "docker" / "egress-proxy" / "entrypoint.sh"


def run_fn(fn: str, *, env: dict[str, str], path_prepend: str | None = None) -> tuple[int, str]:
    """Source the entrypoint (without running main) and call one function."""
    full_env = {
        "PATH": (f"{path_prepend}:" if path_prepend else "") + "/usr/bin:/bin",
        "LUMOGIS_ENTRYPOINT_SOURCE_ONLY": "1",
        **env,
    }
    proc = subprocess.run(
        ["bash", "-c", f'source "{SCRIPT}"; {fn}'],
        capture_output=True,
        text=True,
        env=full_env,
    )
    return proc.returncode, proc.stdout.strip()


@pytest.fixture
def fake_squid(tmp_path):
    """A stub `squid` whose `-k parse` output *and exit code* we control."""

    def _make(parse_output: str, exit_code: int = 0) -> str:
        bindir = tmp_path / "bin"
        bindir.mkdir(exist_ok=True)
        stub = bindir / "squid"
        stub.write_text(
            f'#!/bin/bash\nprintf %s "{parse_output}"\nexit {exit_code}\n', encoding="utf-8"
        )
        stub.chmod(0o755)
        return str(bindir)

    return _make


# ── squid_master_pid ─────────────────────────────────────────────────────────


def _proc(tmp_path: Path, entries: dict[int, str]) -> str:
    root = tmp_path / "proc"
    root.mkdir(exist_ok=True)
    for pid, comm in entries.items():
        d = root / str(pid)
        d.mkdir(exist_ok=True)
        (d / "comm").write_text(comm + "\n", encoding="utf-8")
    return str(root)


def test_finds_squid_when_it_is_pid_1(tmp_path):
    proc = _proc(tmp_path, {1: "squid", 7: "bash"})
    rc, out = run_fn("squid_master_pid", env={"PROC_ROOT": proc})
    assert (rc, out) == (0, "1")


def test_does_not_assume_pid_1_is_squid(tmp_path):
    """If someone adds `init: true`, PID 1 becomes tini.

    A hardcoded `kill -HUP 1` would signal the init shim, succeed, and reload
    nothing — the reload equivalent of a check that reports nothing wrong while
    checking nothing.
    """
    proc = _proc(tmp_path, {1: "tini", 9: "squid"})
    rc, out = run_fn("squid_master_pid", env={"PROC_ROOT": proc})
    assert (rc, out) == (0, "9")


def test_prefers_the_master_over_the_forked_kid(tmp_path):
    # A daemonised Squid forks a kid with the same comm; signal the lower PID.
    # Numeric sort matters: a plain glob would order 10 before 9.
    proc = _proc(tmp_path, {9: "squid", 10: "squid"})
    _, out = run_fn("squid_master_pid", env={"PROC_ROOT": proc})
    assert out == "9"


def test_reports_failure_when_squid_is_absent(tmp_path):
    proc = _proc(tmp_path, {1: "tini"})
    rc, _ = run_fn("squid_master_pid", env={"PROC_ROOT": proc})
    assert rc != 0


# ── allow_digest ─────────────────────────────────────────────────────────────


def test_digest_ignores_the_in_flight_temp_file(tmp_path):
    """The finding both reviewers raised, and the one a naive test would miss.

    `write_allow_file` creates `<name>.txt.tmp.<pid>` *inside* the watched directory
    before renaming it into place. A `*` glob hashes it, so a single write produces
    two digest changes — temp appears, temp vanishes — and reloads Squid even when
    the resulting bytes are identical. A test that doesn't race the write would
    never see it, so the temp file is placed explicitly here.
    """
    allow = tmp_path / "allow"
    allow.mkdir()
    (allow / "lumogis-email-imap.txt").write_text("imap.a.com\n", encoding="utf-8")
    _, before = run_fn("allow_digest", env={"ALLOW_DIR": str(allow)})

    (allow / "lumogis-email-imap.txt.tmp.4242").write_text("imap.b.com\n", encoding="utf-8")
    _, during = run_fn("allow_digest", env={"ALLOW_DIR": str(allow)})

    assert before == during, "a temp file mid-write must not look like a content change"


def test_digest_changes_when_content_changes(tmp_path):
    allow = tmp_path / "allow"
    allow.mkdir()
    target = allow / "lumogis-email-imap.txt"
    target.write_text("imap.a.com\n", encoding="utf-8")
    _, before = run_fn("allow_digest", env={"ALLOW_DIR": str(allow)})
    target.write_text("imap.b.com\n", encoding="utf-8")
    _, after = run_fn("allow_digest", env={"ALLOW_DIR": str(allow)})
    assert before != after


def test_digest_is_stable_for_a_byte_identical_rewrite(tmp_path):
    allow = tmp_path / "allow"
    allow.mkdir()
    target = allow / "lumogis-email-imap.txt"
    target.write_text("imap.a.com\n", encoding="utf-8")
    _, before = run_fn("allow_digest", env={"ALLOW_DIR": str(allow)})
    target.write_text("imap.a.com\n", encoding="utf-8")  # same bytes, new mtime
    _, after = run_fn("allow_digest", env={"ALLOW_DIR": str(allow)})
    assert before == after, "mtime must not drive reloads — content must"


# ── config_readable ──────────────────────────────────────────────────────────


def test_gate_rejects_an_unreadable_allow_file(tmp_path, fake_squid):
    bindir = fake_squid("2026/07/31 10:00:00| ERROR: Can not open file /etc/squid/allow/a.txt")
    rc, _ = run_fn("config_readable", env={}, path_prepend=bindir)
    assert rc != 0, "an ERROR line must block the reload even though parse exits 0"


def test_gate_passes_on_a_clean_parse(tmp_path, fake_squid):
    bindir = fake_squid("2026/07/31 10:00:00| Processing Configuration File")
    rc, _ = run_fn("config_readable", env={}, path_prepend=bindir)
    assert rc == 0


def test_gate_is_not_fooled_by_a_warning(tmp_path, fake_squid):
    # The real config emits benign warnings (e.g. an unwritable cache.log when
    # parse runs as root). Treating those as failures would wedge reloads forever.
    bindir = fake_squid("2026/07/31 10:00:00| WARNING: Cannot write log file")
    rc, _ = run_fn("config_readable", env={}, path_prepend=bindir)
    assert rc == 0


def test_gate_rejects_a_nonzero_exit_with_no_error_line(tmp_path, fake_squid):
    """The failure mode the first implementation missed entirely.

    A malformed `squid.conf` directive makes `squid -k parse` exit 1 while printing
    `unrecognized: '...'` — and never the word ERROR. Gating on output alone let
    that through. Because a mailbox add now reloads automatically, applying a
    config Squid cannot load would take the whole shared proxy down (community
    containment included) on the next routine member action.
    """
    bindir = fake_squid("2026/07/31 10:00:00| /etc/squid/squid.conf:2 unrecognized: 'nope'", 1)
    rc, _ = run_fn("config_readable", env={}, path_prepend=bindir)
    assert rc != 0, "a nonzero parse exit must block the reload even with no ERROR line"


def test_gate_needs_both_signals_to_pass(tmp_path, fake_squid):
    # Exit 0 AND no ERROR line — the only combination that may reload.
    bindir = fake_squid("2026/07/31 10:00:00| Processing Configuration File", 0)
    rc, _ = run_fn("config_readable", env={}, path_prepend=bindir)
    assert rc == 0
