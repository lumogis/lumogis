# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""``services/host_env.py`` — the one writer of the install folder's ``.env`` (LUM-989)."""

from __future__ import annotations

import os
import re
import stat
import threading
from pathlib import Path

import pytest

from services import host_env


def _old_rewrite(content: str, key: str, value: str) -> str:
    """The helper as it was in routes/admin.py before it moved (verbatim)."""
    pattern = re.compile(rf"^[ \t]*{re.escape(key)}[ \t]*=.*(?:\r?\n)?", re.MULTILINE)
    content = pattern.sub("", content).rstrip()
    if content:
        content += "\n"
    content += f"{key}={value}\n"
    return content


@pytest.fixture
def env_file(tmp_path, monkeypatch) -> Path:
    path = tmp_path / ".env"
    path.write_text("A=1\nGRAPH_MODE=disabled\n", encoding="utf-8")
    monkeypatch.setattr(host_env, "ENV_PATH", path)
    return path


# ------------------------------------------------------------------- rewrite / remove


@pytest.mark.parametrize(
    "content",
    [
        "",
        "A=1\n",
        "A=1\nK=old\nB=2\n",
        "K = spaced\nK=dup\nA=1",
        "  K=indented\n",
        "# K=commented\nA=1\n",
        "A=1\n\n\n",
    ],
)
def test_rewrite_key_is_byte_identical_to_the_old_helper_for_lf_files(content):
    assert host_env.rewrite_key(content, "K", "v") == _old_rewrite(content, "K", "v")


def test_remove_key_strips_every_assignment_and_appends_nothing():
    out = host_env.remove_key("A=1\nK=1\nexport K=2\n  K = 3\nB=2\n", "K")
    assert out == "A=1\nB=2\n"
    assert host_env.remove_key("A=1\n", "K") == "A=1\n"


def test_remove_key_leaves_a_commented_line():
    assert host_env.remove_key("# K=1\nK=2\n", "K") == "# K=1\n"


def test_write_keys_none_removes(env_file):
    host_env.write_keys({"GRAPH_MODE": None, "NEW": "x"})
    assert env_file.read_text() == "A=1\nNEW=x\n"


def test_a_commented_example_line_plus_a_real_write_leave_exactly_one_real_line(env_file):
    """The example.env form: ``# GRAPH_MODE=service`` is documentation, not an assignment."""
    env_file.write_text("# GRAPH_MODE=service\n# COMPOSE_PROFILES=graph\nA=1\n")
    host_env.write_keys({"GRAPH_MODE": "service", "COMPOSE_PROFILES": "graph"})
    text = env_file.read_text()
    real = [ln for ln in text.splitlines() if ln and not ln.startswith("#")]
    assert real.count("GRAPH_MODE=service") == 1 and real.count("COMPOSE_PROFILES=graph") == 1
    assert "# GRAPH_MODE=service" in text


def test_a_hand_set_line_and_a_later_write_leave_one_line(env_file):
    env_file.write_text("GRAPH_MODE=service\nexport GRAPH_MODE=service\n")
    host_env.write_keys({"GRAPH_MODE": "service"})
    assert env_file.read_text().count("GRAPH_MODE=") == 1


# ------------------------------------------------------------------------ parse rule


@pytest.mark.parametrize(
    "line",
    ["K=v", "export K=v", 'K="v"', "K='v'", "K=v # note", "K=v\r", "  K =  v  ", 'K="v" # note'],
)
def test_parse_rule_reads_v(env_file, line):
    env_file.write_text(line + "\n")
    assert host_env.read_key("K") == "v"


def test_last_assignment_wins_and_missing_is_none(env_file):
    env_file.write_text("K=1\nK=2\n")
    assert host_env.read_key("K") == "2"
    assert host_env.read_key("NOPE") is None
    assert host_env.read_key("K", path=env_file.parent / "absent") is None


def test_hash_without_space_is_part_of_an_unquoted_value(env_file):
    env_file.write_text('K=abc#def\nQ="a # b"\nE=\n')
    assert host_env.read_key("K") == "abc#def"
    assert host_env.read_key("Q") == "a # b"
    assert host_env.read_key("E") == ""


def test_a_commented_key_is_not_read(env_file):
    env_file.write_text("# K=1\n")
    assert host_env.read_key("K") is None


# --------------------------------------------------------------------------- lists


@pytest.mark.parametrize(
    "current, expected",
    [
        (None, "graph"),
        ("", "graph"),
        ("librechat", "librechat,graph"),
        ("a, b", "a,b,graph"),
        ("graph", "graph"),
        ("a,,b,a", "a,b,graph"),
        ("*", "*"),
    ],
)
def test_list_with(current, expected):
    assert host_env.list_with(current, "graph") == expected


def test_a_second_add_creates_no_duplicate():
    once = host_env.list_with("librechat", "graph")
    assert host_env.list_with(once, "graph") == once


@pytest.mark.parametrize(
    "current, expected",
    [("graph", ""), ("a,graph,b", "a,b"), ("a", "a"), ("*", "*"), (None, "")],
)
def test_list_without(current, expected):
    assert host_env.list_without(current, "graph") == expected


# --------------------------------------------------------------------------- writing


def test_a_crlf_file_stays_crlf(env_file):
    env_file.write_bytes(b"A=1\r\nGRAPH_MODE=disabled\r\n")
    host_env.write_keys({"GRAPH_MODE": "service", "NEW": "x"})
    raw = env_file.read_bytes()
    assert raw == b"A=1\r\nGRAPH_MODE=service\r\nNEW=x\r\n"
    assert b"\n" not in raw.replace(b"\r\n", b"")


def test_update_returns_false_and_does_not_write_when_nothing_changes(env_file, monkeypatch):
    calls = []
    monkeypatch.setattr(host_env, "_write_locked", lambda *a: calls.append(a))
    assert host_env.update(lambda c: c) is False
    assert calls == []


def test_a_failed_write_leaves_the_old_file(env_file, monkeypatch):
    before = env_file.read_bytes()

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(host_env, "atomic_write_text", boom)
    with pytest.raises(host_env.EnvUnwritable):
        host_env.write_keys({"X": "1"})
    assert env_file.read_bytes() == before


def test_the_replace_is_atomic_no_temp_file_is_left(env_file):
    host_env.write_keys({"X": "1"})
    assert [p.name for p in env_file.parent.iterdir()] == [".env"]


@pytest.mark.parametrize("mode", [0o640, 0o600])
def test_the_files_mode_is_kept(env_file, mode):
    os.chmod(env_file, mode)
    host_env.write_keys({"X": "1"})
    assert stat.S_IMODE(env_file.stat().st_mode) == mode


def test_the_owner_is_restored_when_the_new_file_differs(env_file, monkeypatch):
    real_stat = os.stat
    calls = {"stat": 0}

    def fake_stat(path, *a, **k):
        st = real_stat(path, *a, **k)
        calls["stat"] += 1
        values = list(st)
        if calls["stat"] == 1:  # before the write: the file belongs to uid 1000
            values[4], values[5] = 1000, 1001
        else:  # after: the new file belongs to root
            values[4], values[5] = 0, 0
        return os.stat_result(values)

    chowned = []
    monkeypatch.setattr(host_env.os, "stat", fake_stat)
    monkeypatch.setattr(host_env.os, "chown", lambda p, u, g: chowned.append((Path(p), u, g)))
    host_env.write_keys({"X": "1"})
    assert chowned == [(env_file, 1000, 1001)]


def test_a_chown_failure_is_logged_not_raised(env_file, monkeypatch, caplog):
    real_stat = os.stat
    state = {"n": 0}

    def fake_stat(path, *a, **k):
        st = real_stat(path, *a, **k)
        state["n"] += 1
        values = list(st)
        values[4] = 1000 if state["n"] == 1 else 0
        return os.stat_result(values)

    def deny(*_a):
        raise PermissionError("not root")

    monkeypatch.setattr(host_env.os, "stat", fake_stat)
    monkeypatch.setattr(host_env.os, "chown", deny)
    with caplog.at_level("WARNING"):
        host_env.write_keys({"X": "1"})
    assert "X=1" in env_file.read_text()
    assert any("owner" in r.getMessage() for r in caplog.records)


def test_two_threads_writing_different_keys_both_land(env_file):
    barrier = threading.Barrier(8)

    def worker(i: int):
        barrier.wait()
        host_env.write_keys({f"K{i}": str(i)})

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    text = env_file.read_text()
    for i in range(8):
        assert f"K{i}={i}\n" in text


def test_a_missing_file_is_env_unwritable(tmp_path):
    with pytest.raises(host_env.EnvUnwritable):
        host_env.write_keys({"X": "1"}, path=tmp_path / "absent.env")
    assert host_env.writable(tmp_path / "absent.env") is False


def test_writable_is_false_for_a_read_only_folder(tmp_path):
    folder = tmp_path / "ro"
    folder.mkdir()
    (folder / ".env").write_text("A=1\n")
    os.chmod(folder, 0o555)
    try:
        if os.access(folder, os.W_OK):
            pytest.skip("running as a user that ignores directory permissions")
        assert host_env.writable(folder / ".env") is False
    finally:
        os.chmod(folder, 0o755)


def test_writable_is_true_for_a_normal_file(env_file):
    assert host_env.writable() is True


def test_placeholders_include_the_entrypoint_set_and_the_lum937_sample():
    assert {"", "change-me-in-production", "__GENERATE_ME__", "<a long random value>"} == set(
        host_env.PLACEHOLDERS
    )


@pytest.mark.parametrize("value", ["a\nB=2", "a\rB=2"])
def test_set_key_refuses_a_line_break(value):
    with pytest.raises(ValueError):
        host_env.set_key("A=1\n", "K", value)
