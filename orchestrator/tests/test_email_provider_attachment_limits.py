# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Per-provider outbound attachment ceilings (LUM-715).

A single global constant is wrong in both directions: too strict for a provider
that allows more, and too loose for one that allows less — where the user only
finds out as an SMTP refusal *after* approving the send.
"""

from __future__ import annotations

import pytest
from actions.handlers import smtp_send

from services import email_providers


class _Conn:
    def __init__(self, host: str) -> None:
        self.host = host


@pytest.mark.parametrize(
    "provider_id",
    [p.id for p in email_providers.PROVIDERS],
)
def test_every_known_provider_declares_a_ceiling(provider_id: str) -> None:
    """An unset limit on a KNOWN provider is a gap, not a default."""
    provider = email_providers.get_provider(provider_id)
    assert provider is not None
    assert provider.max_attachment_mb, f"{provider_id} has no declared attachment ceiling"


def test_the_ceiling_is_looked_up_by_imap_host() -> None:
    gmail = email_providers.get_provider("gmail")
    assert email_providers.max_attachment_mb_for_host(gmail.imap_host) == gmail.max_attachment_mb


def test_an_unknown_host_has_no_ceiling_rather_than_an_invented_one() -> None:
    assert email_providers.max_attachment_mb_for_host("mail.some-selfhosted.example") is None
    assert email_providers.max_attachment_mb_for_host("") is None


def test_the_handler_converts_a_provider_ceiling_to_bytes() -> None:
    gmail = email_providers.get_provider("gmail")

    got = smtp_send._provider_total_bytes(_Conn(gmail.imap_host))

    assert got == gmail.max_attachment_mb * 1024 * 1024


def test_an_unknown_host_leaves_the_global_cap_in_force() -> None:
    assert smtp_send._provider_total_bytes(_Conn("mail.some-selfhosted.example")) is None


def test_a_connection_without_a_host_is_unknown_not_an_error() -> None:
    """A narrower connection object must degrade, not raise mid-send."""
    assert smtp_send._provider_total_bytes(object()) is None


def test_a_registry_failure_degrades_to_the_global_cap(monkeypatch) -> None:
    def _boom(host):
        raise RuntimeError("registry unavailable")

    monkeypatch.setattr(email_providers, "max_attachment_mb_for_host", _boom)

    assert smtp_send._provider_total_bytes(_Conn("imap.gmail.com")) is None


def test_providers_differ_so_the_lookup_is_not_a_constant() -> None:
    """If every provider carried the same number this feature would be theatre."""
    limits = {p.id: p.max_attachment_mb for p in email_providers.PROVIDERS}

    assert len(set(limits.values())) > 1, f"all providers share one ceiling: {limits}"
