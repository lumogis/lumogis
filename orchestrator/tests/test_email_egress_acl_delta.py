# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-758 — allow-file delta, the emit gate, and who gets told.

The delta exists so the log can answer "what changed?" rather than "how many are
there now", and so the notification can fire on **widening only**. The gate exists
because ``register()`` is wired to the Layer-1 feature flag alone, which says
nothing about whether a proxy is deployed — without it, a flag-on/no-proxy install
would alert every admin about an egress surface nobody is enforcing, on every
container recreate.
"""

from __future__ import annotations

import pytest

from services import email_egress_acl as acl


@pytest.fixture
def allow_dir(monkeypatch, tmp_path):
    """Pin the canonical allow dir so `_should_notify` sees a proxied deployment."""
    monkeypatch.setenv(acl.ALLOW_DIR_ENV, str(tmp_path))
    return tmp_path


def _hosts(monkeypatch, pairs):
    monkeypatch.setattr(acl, "household_imap_hosts", lambda: pairs)


class _Emitted(list):
    def types(self):
        return [n.notification_type for n in self]

    def users(self):
        return [n.user_id for n in self]


@pytest.fixture
def emitted(monkeypatch):
    box = _Emitted()
    monkeypatch.setattr("services.notifications.dispatcher.emit", lambda n: box.append(n))
    return box


@pytest.fixture
def admins(monkeypatch):
    def _set(ids):
        monkeypatch.setattr("services.users.list_admin_ids", lambda: list(ids))

    return _set


# ── read_allow_hosts ─────────────────────────────────────────────────────────


def test_read_allow_hosts_absent_file_is_empty(tmp_path):
    assert acl.read_allow_hosts(tmp_path / "nope.txt") == []


def test_read_allow_hosts_skips_comments_and_sentinel(allow_dir, monkeypatch):
    _hosts(monkeypatch, [])
    path = acl.regenerate()
    # A household with no mailboxes writes the never-matching sentinel so the ACL
    # file is never empty — but that is not a host, and must not read back as one.
    assert acl._EMPTY_SENTINEL in path.read_text(encoding="utf-8")
    assert acl.read_allow_hosts(path) == []


# ── delta ────────────────────────────────────────────────────────────────────


def test_delta_is_computed_against_the_previous_file_not_memory(allow_dir, monkeypatch, caplog):
    """Restart-safety: the baseline lives on disk, so a fresh process sees no delta.

    Had the previous state been a module global, every Core restart would have
    reported every host as newly added and notified admins all over again.
    """
    _hosts(monkeypatch, [("imap.a.com", 993)])
    acl.regenerate()

    caplog.clear()
    with caplog.at_level("INFO"):
        acl.regenerate()  # same hosts, brand-new call — nothing changed
    line = [r.message for r in caplog.records if "regenerated" in r.message][-1]
    assert "added=-" in line and "removed=-" in line


def test_delta_reports_added_and_removed(allow_dir, monkeypatch, caplog, emitted, admins):
    admins(["admin"])
    _hosts(monkeypatch, [("imap.a.com", 993)])
    acl.regenerate()

    caplog.clear()
    _hosts(monkeypatch, [("imap.b.com", 993)])
    with caplog.at_level("INFO"):
        acl.regenerate()
    line = [r.message for r in caplog.records if "regenerated" in r.message][-1]
    assert "imap.b.com" in line  # added
    assert "imap.a.com" in line  # removed


# ── the emit gate ────────────────────────────────────────────────────────────


def test_widening_notifies_admins(allow_dir, monkeypatch, emitted, admins):
    admins(["admin1", "admin2"])
    _hosts(monkeypatch, [("imap.a.com", 993)])
    acl.regenerate()
    assert emitted.users() == ["admin1", "admin2"]
    assert all(t.value == "egress_allowlist_widened" for t in emitted.types())


def test_removal_alone_does_not_notify(allow_dir, monkeypatch, emitted, admins):
    admins(["admin1"])
    _hosts(monkeypatch, [("imap.a.com", 993), ("imap.b.com", 993)])
    acl.regenerate()
    emitted.clear()

    _hosts(monkeypatch, [("imap.a.com", 993)])
    acl.regenerate()
    # Narrowing the egress surface is not something to page a household about.
    assert emitted == []


def test_no_emit_without_allow_dir_env(monkeypatch, tmp_path, emitted, admins):
    """The notification-storm case: flag on, no proxy deployed.

    Without the env var the allow file lands somewhere nothing mounts and does not
    survive a container recreate, so "everything is new" would be reported forever.
    """
    monkeypatch.delenv(acl.ALLOW_DIR_ENV, raising=False)
    admins(["admin1"])
    _hosts(monkeypatch, [("imap.a.com", 993)])
    acl.regenerate(allow_dir=tmp_path)
    assert emitted == []


def test_no_emit_for_explicit_allow_dir_override(allow_dir, monkeypatch, tmp_path, emitted, admins):
    """scripts/gen_capability_egress_acl.py passes allow_dir= explicitly.

    An operator running that against a scratch directory must not page the
    household with a 'widened' alert for a file the proxy never reads.
    """
    admins(["admin1"])
    _hosts(monkeypatch, [("imap.a.com", 993)])
    acl.regenerate(allow_dir=tmp_path / "scratch")
    assert emitted == []


# ── recipients ───────────────────────────────────────────────────────────────


def test_actor_is_excluded_when_another_admin_exists(allow_dir, monkeypatch, emitted, admins):
    admins(["admin1", "admin2"])
    _hosts(monkeypatch, [("imap.a.com", 993)])
    acl.regenerate(actor_user_id="admin1")
    assert emitted.users() == ["admin2"]


def test_sole_admin_who_is_the_actor_still_gets_a_receipt(allow_dir, monkeypatch, emitted, admins):
    """The rule 'exclude the actor' fails closed-to-nobody in a one-admin household.

    That is the bootstrap default and a very plausible steady state. Excluding the
    only recipient would mean the widening notice reaches no one at all — silently
    forfeiting the audit property the whole design rests on — so the actor is told
    anyway, as a receipt rather than an approval request.
    """
    admins(["solo"])
    _hosts(monkeypatch, [("imap.a.com", 993)])
    acl.regenerate(actor_user_id="solo")
    assert emitted.users() == ["solo"]


def test_one_failing_recipient_does_not_starve_the_rest(allow_dir, monkeypatch, admins):
    """A single try/except around the loop would silently drop everyone after the
    first failure — and would still pass a naive "regenerate didn't raise" test."""
    seen = []

    def _flaky(n):
        seen.append(n.user_id)
        if n.user_id == "admin1":
            raise RuntimeError("channel down")

    monkeypatch.setattr("services.notifications.dispatcher.emit", _flaky)
    admins(["admin1", "admin2"])
    _hosts(monkeypatch, [("imap.a.com", 993)])
    acl.regenerate()
    assert seen == ["admin1", "admin2"]


def test_notification_failure_never_breaks_regeneration(allow_dir, monkeypatch, admins):
    monkeypatch.setattr(
        "services.notifications.dispatcher.emit",
        lambda n: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    admins(["admin1"])
    _hosts(monkeypatch, [("imap.a.com", 993)])
    path = acl.regenerate()  # must not raise — the allow file is the priority
    assert "imap.a.com" in path.read_text(encoding="utf-8")
