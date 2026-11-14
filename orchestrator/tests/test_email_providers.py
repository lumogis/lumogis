# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for the static email provider registry (LUM-644)."""

from __future__ import annotations

from services import email_providers


def test_registry_lists_app_password_providers():
    by_id = {p.id: p for p in email_providers.list_providers()}
    for pid in ("gmail", "icloud", "fastmail"):
        p = by_id[pid]
        assert p.enabled is True
        assert p.auth_status == "app_password"
        assert p.app_password_url  # non-empty deep link
        assert p.help_steps  # non-empty guidance
        assert p.imap_host and p.imap_port == 993
        assert p.smtp_host  # LUM-650 reads this


def test_outlook_registry_oauth_fields():
    # LUM-687: Outlook is now an OAuth (device-flow) provider. The registry keeps
    # a STATIC (unconfigured) enabled=False; the /me/email-providers route flips
    # it dynamically on has_oauth_provider (tested in test_email_oauth_routes.py).
    outlook = email_providers.get_provider("outlook")
    assert outlook is not None
    assert outlook.supports_oauth is True
    assert outlook.oauth_provider == "microsoft"
    assert outlook.auth_status == "oauth_only"  # NOT "oauth" — the web union
    assert outlook.display_name == "Outlook.com"  # personal accounts (v1)
    assert outlook.disabled_reason  # honest default disabled-with-reason card


def test_gmail_has_no_oauth_in_v1():
    gmail = email_providers.get_provider("gmail")
    assert gmail.supports_oauth is False and gmail.oauth_provider is None


def test_get_provider_unknown_is_none():
    assert email_providers.get_provider("nope") is None
