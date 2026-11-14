# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Static, in-repo email provider registry (LUM-644 Email Setup v2).

The single source of truth for provider host/port/app-password guidance used by
the guided mailbox setup flow. Zero external lookup — nothing leaves the box at
setup time (no Mozilla ISPDB, no DNS SRV), keeping the local-first/egress posture
LUM-625 exists to contain. LUM-650 (SMTP send) reads ``smtp_host``/``smtp_port``
from here rather than forking its own table.

Auth model: **app-password only** (the credential layer accepts ``app_password``
and rejects ``xoauth2``). Outlook.com/Office365 retired Basic Auth / app passwords
for IMAP/SMTP (enforcement completed 30 Apr 2026), so Outlook is listed but
``enabled=False`` (``auth_status="oauth_only"``) — shown disabled in the picker
until a future OAuth2 chunk lands. "Other" is not a registry row: the web renders
it as a fixed manual-entry card.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderInfo:
    id: str
    display_name: str
    imap_host: str
    imap_port: int
    smtp_host: str
    smtp_port: int
    app_password_url: str
    help_steps: tuple[str, ...]
    auth_status: str  # "app_password" | "oauth_only"
    enabled: bool
    disabled_reason: str | None
    notes: str
    # LUM-645: does this provider DISPLAY Lumogis's labels to the user? Gmail shows
    # native labels; iCloud/Fastmail store IMAP keywords the common clients (Apple
    # Mail/iCloud web) do not surface — so the web steers those users to folder-sort
    # (the visible organization path) and shows a caveat on "label".
    label_visible: bool = True
    # LUM-687 (674b): does this provider connect via OAuth (Device Authorization
    # Grant)? ``oauth_provider`` is the ``services.email_oauth`` key. The route
    # gates the *wire* ``supports_oauth``/``enabled`` on whether the operator's
    # BYO client_id is configured (``email_oauth.has_oauth_provider``).
    supports_oauth: bool = False
    oauth_provider: str | None = None
    # LUM-715: the provider's own outbound attachment ceiling, in MB. Providers
    # differ, and a single global constant is wrong in both directions — too
    # strict for a provider that allows more, and too loose for one that allows
    # less, where the user only discovers it as an SMTP refusal AFTER approving
    # the send. ``None`` means unknown ("Other" hosts), which falls back to the
    # global EMAIL_ATTACHMENT_MAX_MB rather than guessing.
    max_attachment_mb: int | None = None


PROVIDERS: tuple[ProviderInfo, ...] = (
    ProviderInfo(
        id="gmail",
        max_attachment_mb=25,
        display_name="Gmail",
        imap_host="imap.gmail.com",
        imap_port=993,
        smtp_host="smtp.gmail.com",
        smtp_port=587,
        app_password_url="https://myaccount.google.com/apppasswords",
        help_steps=(
            "Turn on 2-Step Verification for your Google account.",
            "Open Google App passwords and create a password for 'Mail'.",
            "Paste that 16-character app password below (not your normal password).",
        ),
        auth_status="app_password",
        enabled=True,
        disabled_reason=None,
        notes="Gmail requires an app password; your normal password will not work.",
    ),
    ProviderInfo(
        id="icloud",
        max_attachment_mb=20,
        display_name="iCloud Mail",
        imap_host="imap.mail.me.com",
        imap_port=993,
        smtp_host="smtp.mail.me.com",
        smtp_port=587,
        app_password_url="https://account.apple.com/account/manage",
        help_steps=(
            "Turn on two-factor authentication for your Apple Account.",
            "At account.apple.com, under Sign-In and Security, create an app-specific password.",
            "Paste that app-specific password below.",
        ),
        auth_status="app_password",
        enabled=True,
        disabled_reason=None,
        notes="iCloud requires an app-specific password.",
        label_visible=False,
    ),
    ProviderInfo(
        id="fastmail",
        max_attachment_mb=70,
        display_name="Fastmail",
        imap_host="imap.fastmail.com",
        imap_port=993,
        smtp_host="smtp.fastmail.com",
        smtp_port=465,
        app_password_url="https://app.fastmail.com/settings/security/apppassword",
        help_steps=(
            "In Fastmail, open Settings -> Privacy & Security -> App Passwords.",
            "Create a new app password with IMAP/SMTP access.",
            "Paste that app password below.",
        ),
        auth_status="app_password",
        enabled=True,
        disabled_reason=None,
        notes="Fastmail requires an app password.",
        label_visible=False,
    ),
    ProviderInfo(
        id="outlook",
        max_attachment_mb=20,
        display_name="Outlook.com",
        imap_host="outlook.office365.com",
        imap_port=993,
        smtp_host="smtp.office365.com",
        smtp_port=587,
        app_password_url="",
        help_steps=(),
        auth_status="oauth_only",
        # ``enabled``/``disabled_reason`` here are the DEFAULT (unconfigured) wire
        # values; the /me/email-providers route recomputes them dynamically from
        # ``email_oauth.has_oauth_provider("microsoft")`` (LUM-687) so a configured
        # install shows Outlook enabled and an unconfigured one keeps this honest
        # disabled-with-reason card (no dead-end).
        enabled=False,
        disabled_reason=(
            "Connecting Outlook needs a one-time OAuth app setup — "
            "see docs/operators/oauth-email-setup.md."
        ),
        notes=(
            "Personal Microsoft accounts (Outlook.com / Hotmail / Live) via OAuth "
            "sign-in. Microsoft retired app passwords for Outlook IMAP."
        ),
        label_visible=False,
        supports_oauth=True,
        oauth_provider="microsoft",
    ),
)


def list_providers() -> list[ProviderInfo]:
    """All known providers, in display order."""
    return list(PROVIDERS)


def get_provider(provider_id: str) -> ProviderInfo | None:
    """Look up one provider by id, or ``None`` if unknown."""
    for p in PROVIDERS:
        if p.id == provider_id:
            return p
    return None


def provider_for_host(host: str) -> ProviderInfo | None:
    """Match a stored source's IMAP host back to its provider (LUM-645 — an existing
    source has no provider_id, only ``sources.url`` with the host)."""
    h = (host or "").strip().lower()
    for p in PROVIDERS:
        if p.imap_host.lower() == h:
            return p
    return None


def max_attachment_mb_for_host(host: str) -> int | None:
    """The provider's outbound attachment ceiling for ``host``, or ``None``.

    ``None`` for an unknown ("Other") host — the caller falls back to the global
    cap rather than inventing a limit for a server whose policy we do not know.
    """
    provider = provider_for_host(host)
    return provider.max_attachment_mb if provider else None


def label_visible_for_host(host: str) -> bool:
    """Whether Lumogis labels are user-visible for this host. Unknown ("Other")
    hosts default to False — the honest, conservative choice (steer to folder-sort)."""
    p = provider_for_host(host)
    return bool(p and p.label_visible)
