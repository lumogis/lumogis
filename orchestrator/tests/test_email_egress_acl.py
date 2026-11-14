# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-625 Layer 2 — email-IMAP Squid allow-file generation (cooperative audit).

Unit-level coverage of the security-relevant logic: which declared mailbox hosts
make it into the SNI allow file, which are skipped-and-logged (never a hard
failure), the household union, and the change-listener trigger. The live Squid
CONNECT proof is the Docker integration test (test_email_imap_egress.py).
"""

from __future__ import annotations

import pytest
from connectors.registry import IMAP

from services import email_egress_acl as acl


def test_compute_allow_keeps_valid_hosts():
    kept, skipped = acl.compute_allow_hosts([("imap.gmail.com", 993), ("mail.fastmail.com", 993)])
    assert kept == ["imap.gmail.com", "mail.fastmail.com"]
    assert skipped == []


def test_compute_allow_dedupes_order_preserving():
    kept, _ = acl.compute_allow_hosts(
        [("imap.gmail.com", 993), ("imap.gmail.com", 993), ("mail.x.com", 993)]
    )
    assert kept == ["imap.gmail.com", "mail.x.com"]


def test_compute_allow_skips_nonstandard_port():
    kept, skipped = acl.compute_allow_hosts([("imap.x.com", 1993)])
    assert kept == []
    assert len(skipped) == 1 and "port" in skipped[0][1]


def test_compute_allow_skips_idn_and_ipv6():
    kept, skipped = acl.compute_allow_hosts([("möt.example", 993), ("::1", 993)])
    assert kept == []
    skipped_hosts = {h for h, _ in skipped}
    assert skipped_hosts == {"möt.example", "::1"}


def test_write_allow_file_writes_sentinel_when_empty(tmp_path):
    path, kept, _ = acl.write_allow_file([], allow_dir=tmp_path)
    assert kept == []
    body = path.read_text(encoding="utf-8")
    assert acl._EMPTY_SENTINEL in body  # never an empty ACL (Squid FATAL guard)


def test_write_allow_file_is_atomic_no_temp_left(tmp_path):
    path, kept, _ = acl.write_allow_file([("imap.gmail.com", 993)], allow_dir=tmp_path)
    assert "imap.gmail.com" in path.read_text(encoding="utf-8")
    assert list(tmp_path.glob("*.tmp*")) == []  # temp swapped in atomically, cleaned up


def test_write_allow_file_contains_kept_only(tmp_path):
    path, kept, skipped = acl.write_allow_file(
        [("imap.gmail.com", 993), ("bad.host", 1993)], allow_dir=tmp_path
    )
    assert path == tmp_path / acl.ALLOW_FILE_NAME
    body = path.read_text(encoding="utf-8")
    assert "imap.gmail.com" in body
    assert "bad.host" not in body  # skipped (nonstandard port), Layer 1 still covers it
    assert kept == ["imap.gmail.com"] and len(skipped) == 1


def _imap_records(*instances):
    class _Rec:
        def __init__(self, connector, instance):
            self.connector = connector
            self.instance = instance

    return [_Rec(IMAP, inst) for inst in instances]


def test_declared_imap_hosts_url_survives_payload_decrypt_failure(monkeypatch):
    """Valid url path lists the host even when payload decrypt would fail."""
    from services import email_credentials as ec

    monkeypatch.setattr(
        "services.connector_credentials.list_records",
        lambda uid: _imap_records("m1"),
    )
    monkeypatch.setattr(
        ec,
        "_hosts_from_source_url",
        lambda uid, inst: ("imap.gmail.com", 993),
    )

    def _boom(*a, **k):
        raise RuntimeError("decrypt failed")

    monkeypatch.setattr("services.connector_credentials.get_payload", _boom)
    assert ec.declared_imap_hosts("u1") == [("imap.gmail.com", 993)]


def test_declared_imap_hosts_no_payload_decrypt_on_url_hit(monkeypatch):
    """Url-hit path never invokes IMAP payload decrypt."""
    from services import email_credentials as ec

    monkeypatch.setattr(
        "services.connector_credentials.list_records",
        lambda uid: _imap_records("m1", "m2"),
    )
    monkeypatch.setattr(
        ec,
        "_hosts_from_source_url",
        lambda uid, inst: (f"imap.{inst}.com", 993),
    )
    calls = []
    monkeypatch.setattr(
        "services.connector_credentials.get_payload",
        lambda *a, **k: calls.append((a, k)) or pytest.fail("must not decrypt"),
    )
    assert ec.declared_imap_hosts("u1") == [
        ("imap.m1.com", 993),
        ("imap.m2.com", 993),
    ]
    assert calls == []


def test_declared_imap_hosts_no_dns(monkeypatch):
    """Enumeration never calls load_connection (no live DNS)."""
    from services import email_credentials as ec

    monkeypatch.setattr(
        "services.connector_credentials.list_records",
        lambda uid: _imap_records("m1"),
    )
    monkeypatch.setattr(
        ec,
        "_hosts_from_source_url",
        lambda uid, inst: ("imap.gmail.com", 993),
    )
    monkeypatch.setattr(
        ec,
        "load_connection",
        lambda *a, **k: pytest.fail("enumeration must not resolve DNS"),
    )
    assert ec.declared_imap_hosts("u1") == [("imap.gmail.com", 993)]


def test_declared_imap_hosts_url_only_when_divergent(monkeypatch):
    """When url parses, payload is not read even if it would differ."""
    from services import email_credentials as ec

    monkeypatch.setattr(
        "services.connector_credentials.list_records",
        lambda uid: _imap_records("m1"),
    )
    monkeypatch.setattr(
        ec,
        "_hosts_from_source_url",
        lambda uid, inst: ("a.com", 993),
    )
    monkeypatch.setattr(
        "services.connector_credentials.get_payload",
        lambda *a, **k: pytest.fail("must not decrypt when url hit"),
    )
    assert ec.declared_imap_hosts("u1") == [("a.com", 993)]


def test_declared_imap_hosts_fallback_when_no_source(monkeypatch):
    """Url-miss falls back to payload decrypt."""
    from services import email_credentials as ec

    monkeypatch.setattr(
        "services.connector_credentials.list_records",
        lambda uid: _imap_records("m1"),
    )
    monkeypatch.setattr(ec, "_hosts_from_source_url", lambda uid, inst: None)
    monkeypatch.setattr(
        "services.connector_credentials.get_payload",
        lambda uid, connector, instance="": {"host": "fallback.example.com", "port": 993},
    )
    assert ec.declared_imap_hosts("u1") == [("fallback.example.com", 993)]


def test_declared_imap_hosts_db_error_skips_one_mailbox(monkeypatch):
    """fetch_one failure on one mailbox does not sink siblings."""
    from services import email_credentials as ec

    monkeypatch.setattr(
        "services.connector_credentials.list_records",
        lambda uid: _imap_records("m1", "m2"),
    )

    def _url(uid, inst):
        if inst == "m2":
            raise RuntimeError("db down")
        return ("imap.m1.com", 993)

    monkeypatch.setattr(ec, "_hosts_from_source_url", _url)
    assert ec.declared_imap_hosts("u1") == [("imap.m1.com", 993)]


def test_declared_imap_hosts_skips_empty_instance(monkeypatch):
    from services import email_credentials as ec

    class _Rec:
        def __init__(self, connector, instance):
            self.connector = connector
            self.instance = instance

    monkeypatch.setattr(
        "services.connector_credentials.list_records",
        lambda uid: [_Rec(IMAP, "m1"), _Rec("caldav", "c1"), _Rec(IMAP, "")],
    )
    monkeypatch.setattr(
        ec,
        "_hosts_from_source_url",
        lambda uid, inst: ("imap.gmail.com", 993),
    )
    assert ec.declared_imap_hosts("u1") == [("imap.gmail.com", 993)]


def test_household_union_across_users(monkeypatch):
    class _U:
        def __init__(self, uid):
            self.id = uid

    monkeypatch.setattr("services.users.list_users", lambda: [_U("a"), _U("b")])
    monkeypatch.setattr(
        "services.email_credentials.declared_imap_hosts",
        lambda uid: {"a": [("imap.a.com", 993)], "b": [("imap.b.com", 993)]}[uid],
    )
    assert acl.household_imap_hosts() == [("imap.a.com", 993), ("imap.b.com", 993)]


def test_default_allow_dir_honours_env_override(monkeypatch, tmp_path):
    # LUM-673: in a Docker deploy the repo-tree fallback resolves to a
    # container-local path Squid never reads; the env var pins the shared mount.
    monkeypatch.setenv(acl.ALLOW_DIR_ENV, str(tmp_path))
    assert acl._default_allow_dir() == tmp_path


def test_default_allow_dir_falls_back_without_env(monkeypatch):
    # Bare checkout / host-side CLI: repo-tree path (…/docker/egress-proxy/allow).
    monkeypatch.delenv(acl.ALLOW_DIR_ENV, raising=False)
    assert acl._default_allow_dir().parts[-3:] == ("docker", "egress-proxy", "allow")


def test_regenerate_writes_to_env_override_dir(monkeypatch, tmp_path):
    # End-to-end: with the env var set and no explicit allow_dir, the regen lands
    # in the shared (mounted) dir so Squid actually picks it up.
    monkeypatch.setenv(acl.ALLOW_DIR_ENV, str(tmp_path))
    monkeypatch.setattr(acl, "household_imap_hosts", lambda: [("imap.gmail.com", 993)])
    path = acl.regenerate()
    assert path == tmp_path / acl.ALLOW_FILE_NAME
    assert "imap.gmail.com" in path.read_text(encoding="utf-8")


def test_listener_triggers_regen_on_imap_put_and_delete(monkeypatch):
    # Regen runs off-thread (background); assert the trigger fires for IMAP put +
    # delete (removed mailbox shrinks the file) and is skipped for other connectors.
    calls = []
    monkeypatch.setattr(
        acl,
        "_regenerate_background",
        lambda reason, *, actor_user_id=None: calls.append((reason, actor_user_id)),
    )
    acl._on_credential_change(user_id="u1", connector=IMAP, action="put", instance="m1")
    acl._on_credential_change(user_id="u1", connector=IMAP, action="delete", instance="m1")
    acl._on_credential_change(user_id="u1", connector="caldav", action="put", instance="c1")
    assert calls == [("credential_change:put", "u1"), ("credential_change:delete", "u1")]
    # LUM-758: the actor's id must survive the hop. Before this it was received here
    # and dropped, which made "don't notify the person who did it" unimplementable.
