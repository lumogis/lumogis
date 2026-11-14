# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Per-user, per-mailbox runtime config loader for the email (IMAP) connector.

LUM-170. Bridges :mod:`services.connector_credentials` (per-user encrypted
JSON, now multi-instance) with the legacy single-mailbox ``EMAIL_IMAP_*`` env
vars, mirroring the CalDAV precedent (:mod:`services.caldav_credentials`) but
**instance-aware**: a user may configure several mailboxes (personal / work /
shared), each keyed by ``instance = sources.id``.

Auth (v1): **app-password + generic IMAP** only. TLS is required for any
non-loopback host. The mail host is validated with the same IP-range SSRF
blocks the outbound-HTTP guard uses (link-local / metadata always blocked;
loopback/private gated by ``LUMOGIS_ALLOW_PRIVATE_OUTBOUND_URLS`` + allowlist),
NOT the http-scheme validator (which rejects a bare host / imap:// scheme).

Exception contract (same split the adapter maps to one log line):

* :class:`ConnectorNotConfigured` — no row + no usable env fallback.
* :class:`CredentialUnavailable` — substrate decrypt failed OR payload is
  structurally wrong (non-dict, missing key, wrong type).
* ``ValueError`` — payload structurally fine but a field is empty / out of
  range / TLS-required-but-plaintext / host fails the SSRF rule.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any
from typing import Literal

from auth import auth_enabled
from connectors.registry import IMAP
from connectors.registry import SMTP
from services.imap_source_url import imap_host_port_from_source_url
from services.imap_source_url import normalize_imap_host

import config
from services import connector_credentials as ccs
from services import outbound_http_url as _ohu

_log = logging.getLogger(__name__)


class ImapHostBlockedError(ValueError):
    """The IMAP host is refused by the SSRF/IP-range policy (a permanent config/
    attack condition), as distinct from a transient DNS-resolution failure —
    both were previously bare ``ValueError``. Subclasses ``ValueError`` so
    existing ``except ValueError`` callers keep working; the write-back path uses
    the distinct type to dead-letter (permanent) instead of retrying (LUM-625)."""


class ImapHostUnresolvedError(ValueError):
    """DNS did not resolve the IMAP host — the **transient** half of the split
    the sibling class above already describes (LUM-633).

    Why this needs its own type rather than the bare ``ValueError`` it used to
    be: ``_resolve_host_ips`` is called from **two** places — once at
    construction (``_connection_from_payload``) and again at connect time
    (``revalidate_imap_host``). Poll-health classifies those two sites
    separately, so an untyped DNS failure was read as a permanent
    configuration problem at one site and a transient network one at the other,
    for the same mailbox and the same cause, decided only by which of the two
    redundant checks happened to observe it. A user would then be told either
    "this will not fix itself" or "this usually clears up" at random.

    Subclasses ``ValueError`` so every existing ``except ValueError`` — including
    the write-back path's deliberate "let a plain resolve failure through so the
    queue can retry it" behaviour — keeps working unchanged."""


class ImapHostMismatchError(ValueError):
    """Credential payload host/port disagrees with the owning ``sources.url`` (LUM-773)."""


class ImapSourceNotFoundError(ValueError):
    """No IMAP ``sources`` row for the given instance id and user."""


class ImapSourceUrlInvalidError(ValueError):
    """Owned IMAP source exists but ``sources.url`` does not parse."""


# Supported auth modes at load. ``app_password`` is the original path;
# ``xoauth2`` is un-gated by LUM-686 (674a): its access token is minted in
# ``load_connection`` via ``email_oauth.get_access_token`` and both IMAP
# (``oauth2_login``) and SMTP (``AUTH XOAUTH2``) authenticate with it. The
# device-flow that *writes* an xoauth2 row is 674b.
_ALLOWED_AUTH_TYPES = frozenset({"app_password", "xoauth2"})


@dataclass(frozen=True)
class ImapConnection:
    """Resolved per-user, per-mailbox IMAP connection parameters.

    ``frozen`` + a small named field set keep secrets out of repr/log
    surfaces by accident (accidental interpolation is visible at review).
    """

    host: str
    port: int
    username: str
    use_ssl: bool
    auth_type: str
    password: str | None = None
    oauth_access_token: str | None = None


def load_connection(user_id: str, *, instance: str) -> ImapConnection:
    """Return the IMAP connection params for ``user_id`` + mailbox ``instance``.

    ``instance`` is the ``sources.id`` of the mailbox row. Under
    ``AUTH_ENABLED=false`` the legacy single-mailbox ``EMAIL_IMAP_*`` env
    fallback is honored only for ``instance == ""`` (the ``"default"`` user's
    one mailbox).

    Raises:
        ConnectorNotConfigured: no credential row (and, for ``instance==""``
            under ``AUTH_ENABLED=false``, no ``EMAIL_IMAP_HOST`` env).
        CredentialUnavailable: decrypt failure or structurally-wrong payload.
        ValueError: empty/invalid field, bad port, plaintext to a remote host,
            or a host that fails the SSRF policy.
    """
    payload = ccs.get_payload(user_id, IMAP, instance=instance)

    if payload is not None:
        if isinstance(payload, dict) and payload.get("auth_type") == "xoauth2":
            # Mint/refresh the access token from the stored refresh_token, then
            # build the connection with the fresh token. get_access_token reads
            # + writes the same (already-decrypted) payload (single r-m-w).
            from services import email_oauth

            access_token = email_oauth.get_access_token(
                user_id, connector=IMAP, instance=instance, payload=payload
            )
            return _connection_from_payload(payload, access_token=access_token)
        return _connection_from_payload(payload)

    # LUM-834 chunk B — guarded for the same reason as every other env
    # fallback, and unreachable through the API today: the PATCH that sets
    # `enabled` refuses multi-instance connectors with a 422, so no imap row
    # can currently be switched off. Written now rather than when per-instance
    # disable arrives, because the trap is the fallback silently substituting
    # an env credential, and finding that later is the expensive way.
    if ccs.is_disabled(user_id, IMAP, instance=instance):
        raise ccs.disabled_error(IMAP, instance=instance)

    if auth_enabled() or instance != "":
        raise ccs.ConnectorNotConfigured(
            f"no imap credential row for user_id={user_id!r} instance={instance!r}"
        )

    env_host = os.environ.get("EMAIL_IMAP_HOST", "")
    if not env_host:
        raise ccs.ConnectorNotConfigured(
            f"no imap credential row for user_id={user_id!r} and no "
            "EMAIL_IMAP_HOST env fallback (AUTH_ENABLED=false)"
        )
    return _connection_from_payload(
        {
            "host": env_host,
            "port": int(os.environ.get("EMAIL_IMAP_PORT", "993") or "993"),
            "username": os.environ.get("EMAIL_IMAP_USERNAME", ""),
            "password": os.environ.get("EMAIL_IMAP_PASSWORD", ""),
            "use_ssl": True,
            "auth_type": "app_password",
        }
    )


def classify_credential_failure(exc: BaseException) -> str:
    """Classify a credential-resolution failure into one of ADR 195's health codes.

    ADR 195's standing rule is that classifiers of this raise surface must agree.
    There are two consumers — poll health (``signals.feed_monitor``) and the SMTP
    opt-in route (``routes.signals.set_email_smtp``) — so there is one function
    rather than two mappings that drift. It lives here because every type it
    names is defined in this module or in :mod:`services.email_oauth`.

    Always returns a code: the caller has already caught a failure, so "unknown"
    is not an option. The default is ``credential`` because that is what the
    poll-health site has always reported and what its tests pin (LUM-633); only
    conditions we can name by type get something more accurate.

    **The route does not use this for every branch, deliberately.** ADR 195's
    taxonomy was built for a health banner, where ``credential`` reasonably
    covers anything the user might re-enter. An HTTP surface needs a distinction
    the four codes do not carry — user-fixable vs server-broken vs
    operator-owned — so ``routes.signals`` resolves ``OAuthRefreshError`` and a
    bare ``CredentialUnavailable`` at their own catch sites and calls this only
    for the ``ValueError`` branch, which is the split both consumers must agree
    on (LUM-759).
    """
    # Function-local: matches the existing import style in this module
    # (`load_connection` imports `email_oauth` the same way).
    from services.email_oauth import OAuthRefreshError

    if isinstance(exc, OAuthRefreshError):
        # The raise site knows which of ADR 195's codes applies — a revoked grant,
        # a token-endpoint blip, an egress block, and an operator's missing
        # client id are four different stories and used to be told as one
        # ("credential", via the default below). LUM-757.
        return exc.kind
    if isinstance(exc, ImapHostBlockedError):
        return "egress_blocked"
    if isinstance(exc, ImapHostUnresolvedError):
        return "network"
    if isinstance(exc, ValueError):
        # Bad field, bad port, plaintext-to-remote — real configuration
        # problems that never fix themselves.
        return "config"
    return "credential"


def mailbox_account_address(user_id: str, *, instance: str) -> str:
    """The mailbox's own address — its authenticated account (LUM-705).

    The supported way to learn "which address am I?" without holding a
    credential. :class:`ImapConnection` is ``frozen`` with a deliberately small
    field set so secrets stay out of repr/log surfaces by accident; a caller that
    only needs the username should not be carrying the password around to get it.

    This is the same value ``smtp_send`` uses as ``From`` (``conn.username``), so
    the address filtered out of a reply-all Cc is exactly the address the reply
    will be sent from — they cannot drift. ``load_smtp_connection`` builds its
    ``SmtpConnection.username`` from this same payload field on every path,
    including ``reuse_imap=false`` with a standalone app-password, so "cannot
    drift" is one field of one row rather than an agreement between two reads.

    **Reads the payload directly rather than going through**
    ``load_connection`` **(LUM-831).** That constructor resolves the host live
    (``_resolve_host_ips``), walks the SSRF policy, checks TLS, and for an
    ``xoauth2`` mailbox mints and *persists* an access token — none of which a
    username read needs, because ``_connection_from_payload`` extracts
    ``username`` before any of it. Going through the connection made this
    accessor cost a DNS round trip and a credential write, and made a mailbox
    whose host stopped resolving report *no address at all* — which on the
    reply-all path silently left the user in their own Cc.

    Consequence worth knowing: this answers "which address am I?" even for a
    mailbox that currently **cannot be dialled** (unresolvable host, blocked by
    egress policy). That is deliberate. The question has an answer regardless,
    and a send against such a mailbox still fails loudly at send time with a
    real error rather than the caller silently losing the address.

    **This is the one supported way to learn a mailbox's own address** (ADR-214)
    — do not re-derive it and do not cache it in a column. It raises rather than
    returning ``None`` so a caller must decide what an unknown address means;
    the two that exist decide differently and both are right
    (:func:`sending_addresses` omits the mailbox, ``email_send`` degrades to an
    unfiltered Cc). A caller wanting "skip, don't sink" wraps the call — see
    ``paperless_overlap._mailbox_username``.
    """
    payload = ccs.get_payload(user_id, IMAP, instance=instance)
    if payload is None and instance != "" and not auth_enabled():
        # The pre-LUM-170 row (LUM-778). Migration 054 added `instance` with a
        # `DEFAULT ''`, so a mailbox configured before multi-instance shipped
        # still lives at `instance=""` — and no write path can recreate one,
        # because `_validate_instance(..., write=True)` requires a non-empty
        # instance for a multi-instance connector. The row is legacy-only and
        # unreachable by any new write, which is exactly why it needs an
        # explicit read: nothing else will ever find it.
        #
        # Guarded to the single-mailbox deployment on purpose. With AUTH on,
        # "this user's other mailbox" is not a sane answer to "what address is
        # mailbox X?" — it would resolve one mailbox's address from another's
        # credential. The unguarded version of this fallback lived in
        # `paperless_overlap._mailbox_username` and could do exactly that.
        #
        # The borrow is still reachable under AUTH-off for a mailbox that has no
        # row of its own, and that residue is accepted: such a deployment has one
        # user and at most one legacy row, so the answer is at worst *stale* —
        # never a different person's address, which is what the guard prevents.
        # LUM-834 chunk B — this function reads TWO rows, so it names both.
        #
        # HONEST SCOPE, corrected after review: this guard changes the MESSAGE,
        # not the outcome. Remove it and the borrow still fails closed, because
        # the env fallback below is fenced by `if auth_enabled() or instance !=
        # ""` and this branch only runs when `instance != ""`. So the
        # environment is NOT reachable here — an earlier version of this comment
        # claimed it was, and that was wrong.
        #
        # It stays because the message matters: without it a disabled legacy row
        # reports "no imap credential row", which is false about a row that
        # exists, and an operator reading that goes looking for a missing
        # credential instead of a switched-off one. Pinned by a test that
        # asserts the MESSAGE, since asserting the type alone pins nothing.
        if ccs.is_disabled(user_id, IMAP, instance=""):
            raise ccs.disabled_error(IMAP, instance="")
        payload = ccs.get_payload(user_id, IMAP, instance="")
    if payload is not None:
        return _require_str(payload, "username")

    # LUM-834 chunk B — the sixth env-fallback branch, and the easiest one to
    # miss: it lives two hundred lines from `load_connection` and mirrors it
    # by hand. Same guard, same reason, same today-unreachable status (PATCH
    # refuses multi-instance). Enumerating this class per branch rather than
    # counting it is why it was found at all.
    if ccs.is_disabled(user_id, IMAP, instance=instance):
        raise ccs.disabled_error(IMAP, instance=instance)

    # Mirror load_connection's env fallback so behaviour is identical where a
    # credential row is absent: single-user dev, single mailbox, AUTH off.
    if auth_enabled() or instance != "":
        raise ccs.ConnectorNotConfigured(
            f"no imap credential row for user_id={user_id!r} instance={instance!r}"
        )
    username = os.environ.get("EMAIL_IMAP_USERNAME", "")
    if not username:
        raise ccs.ConnectorNotConfigured(
            f"no imap credential row for user_id={user_id!r} and no "
            "EMAIL_IMAP_USERNAME env fallback (AUTH_ENABLED=false)"
        )
    return username


def mailbox_records(user_id: str, connector: str) -> Iterator[ccs.CredentialRecord]:
    """Yield ``user_id``'s credential records for one mailbox connector.

    The one enumeration filter behind :func:`list_mailboxes`,
    :func:`sendable_mailboxes`, :func:`declared_imap_hosts`,
    :func:`imap_auth_methods` and ``smtp_transport.declared_smtp_hosts``: records
    whose ``connector`` matches **and** whose ``instance`` is non-empty (the
    empty instance is the legacy single-mailbox row, not a ``sources.id``).

    Metadata only, never decrypts. ``ccs.list_records`` returns **every**
    connector's rows for the user in one ``SELECT``, so call this once per
    request, never once per source.

    Does **not** look at ``enabled``. That is safe only while no mailbox can be
    switched off (``PATCH`` refuses per-mailbox connectors with 422). When
    per-mailbox disable ships, route this through
    ``credential_tiers.runtime_tier_from_records`` (LUM-850 / ADR-238), or every
    surface listed above will advertise a switched-off mailbox.
    """
    for rec in ccs.list_records(user_id):
        if rec.connector == connector and rec.instance:
            yield rec


def list_mailboxes(user_id: str) -> list[str]:
    """Return the user's configured IMAP mailbox instances (``sources.id``).

    Reads metadata only (never decrypts) so ``GET /sources`` and the
    credential UI can show each mailbox's configured/not-configured state.
    """
    return [rec.instance for rec in mailbox_records(user_id, IMAP)]


def sendable_mailboxes(user_id: str) -> list[str]:
    """Return the mailbox instances (``sources.id``) this user can send FROM.

    **The SMTP row is sendability.** Deliberately *not* "has both an SMTP and an
    IMAP row" (LUM-831): the web asks exactly this question via
    ``hasSmtpCredential`` (``api/connectorCredentials.ts``), and two similar-but-
    different predicates for "can this mailbox send?" is the divergence class
    LUM-773 and LUM-813 both came from. The IMAP row is a runtime dependency of
    *resolving the address*, not part of the predicate — a mailbox missing one
    is omitted by the per-mailbox catch in :func:`sending_addresses`, which is
    the same observable outcome as excluding it here, minus the divergence.

    That state is reachable: deleting one connector's credential row does not
    cascade to its sibling.

    Reads metadata only (never decrypts) — same posture as
    :func:`list_mailboxes`, which is this function with one constant changed.
    """
    return [rec.instance for rec in mailbox_records(user_id, SMTP)]


def mailbox_auth_method(keys: list[str] | None) -> Literal["oauth", "app_password"] | None:
    """Classify an IMAP credential's auth method from its plaintext key names.

    **Display-only, never authorisation.** ``set_email_smtp`` keeps deciding on
    the decrypted ``ImapConnection.auth_type``; this only lets ``GET /sources``
    name the right fix-owner without decrypting anything (ADR-199).

    ``keys`` is ``CredentialRecord.configured_keys``, whose NULL contract
    (ADR-219) is load-bearing: ``None`` means **unknown** (a row written before
    migration 079), never an empty list and never ``app_password``. Rules, in
    order:

    * ``None`` → ``None`` (unknown);
    * both ``refresh_token`` and ``password`` → ``None`` (contradictory; the
      generic ``PUT /me/connector-credentials`` route can write that shape);
    * ``refresh_token`` → ``"oauth"``;
    * ``password`` → ``"app_password"``;
    * anything else → ``None``.

    ``auth_type`` presence cannot discriminate: both kinds of payload carry it,
    and :func:`_connection_from_payload` defaults it to ``app_password`` when
    absent. The classifier mirrors two rules instead: ``_connection_from_payload``
    requires ``password`` unless the mailbox is xoauth2, and ``get_access_token``
    requires ``refresh_token``.

    Accepted limitation: key names cannot see ``auth_type``'s *value*. A
    hand-written payload whose keys disagree with its ``auth_type`` can be
    misread, but such a row cannot connect anyway. A future OAuth mode that
    stores no ``refresh_token`` (a service-account or app-only grant) would
    classify wrongly; this function and the writer round-trip test must change
    with it.
    """
    if keys is None:
        return None
    has_refresh = "refresh_token" in keys
    has_password = "password" in keys
    if has_refresh and has_password:
        return None
    if has_refresh:
        return "oauth"
    if has_password:
        return "app_password"
    return None


def imap_auth_methods(user_id: str) -> dict[str, Literal["oauth", "app_password"] | None]:
    """Map each IMAP mailbox instance (``sources.id``) to its auth method.

    Metadata only, never decrypts: one ``ccs.list_records`` call. Does **not**
    catch: the caller owns the degrade-to-unknown (``list_records`` is one
    ``SELECT`` that fails whole).
    """
    return {
        rec.instance: mailbox_auth_method(rec.configured_keys)
        for rec in mailbox_records(user_id, IMAP)
    }


def sending_addresses(user_id: str) -> dict[str, str]:
    """Map ``sources.id`` → sending address for this user's sendable mailboxes.

    A mailbox whose address cannot be resolved is **omitted**, never raised for
    and never rendered as an empty string: the caller distinguishes "no address
    for this mailbox" by absence, and the UI degrades that mailbox to its name
    rather than dropping it from the picker (dropping it would remove a mailbox
    the user can genuinely send from — the LUM-719 mistake in a new place).

    Skip-don't-sink, like :func:`declared_imap_hosts` and
    :func:`resolve_smtp_host_port`. The log carries the instance and the
    exception **type** only: several ``ValueError`` messages off this raise
    surface embed the configured mail host, so logging ``str(exc)`` would put
    configuration into the log line.
    """
    out: dict[str, str] = {}
    for instance in sendable_mailboxes(user_id):
        try:
            address = mailbox_account_address(user_id, instance=instance)
        except Exception as exc:  # noqa: BLE001 - one bad mailbox must not sink the list
            _log.warning(
                "email.sending_address.skip user_id=%s instance=%s reason=%s",
                user_id,
                instance,
                type(exc).__name__,
            )
            continue
        if address:
            out[instance] = address
    return out


def _hosts_from_source_url(user_id: str, instance: str) -> tuple[str, int] | None:
    from services.imap_source_url import imap_host_port_from_source_url
    from services.imap_source_url import source_url_for_imap_instance

    url = source_url_for_imap_instance(user_id, instance)
    if url is None:
        return None
    return imap_host_port_from_source_url(url)


def _hosts_from_imap_payload(user_id: str, instance: str) -> tuple[str, int] | None:
    """Best-effort decrypt read of host/port from the IMAP payload (url-miss fallback)."""
    try:
        payload = ccs.get_payload(user_id, IMAP, instance=instance)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(payload, dict):
        return None
    host = payload.get("host")
    if not isinstance(host, str) or not host:
        return None
    port = payload.get("port", 993)
    if not isinstance(port, int) or isinstance(port, bool) or not (1 <= port <= 65535):
        port = 993
    return (host, port)


def declared_imap_hosts(user_id: str) -> list[tuple[str, int]]:
    """Return ``(host, port)`` for every IMAP mailbox ``user_id`` has configured.

    Used **only** by the Layer-2 allow-file generator (LUM-625) — the household
    union is built by looping this over ``services.users.list_users()``. Layer 1
    never calls this: it scopes each connection to the single ``conn.host`` in
    hand. A mailbox row whose url and payload are both missing/invalid is skipped
    (the generator must not fail because one mailbox is misconfigured).

    Url-first (LUM-634): reads ``sources.url`` without decrypt when parseable;
    falls back to payload decrypt only on url-miss.  Never calls
    ``load_connection`` (no live DNS on enumeration).
    """
    out: list[tuple[str, int]] = []
    for rec in mailbox_records(user_id, IMAP):
        try:
            url_hp = _hosts_from_source_url(user_id, rec.instance)
        except Exception as exc:  # noqa: BLE001 - one bad mailbox must not sink the union
            _log.warning(
                "imap.egress.acl.enumerate_skip user_id=%s instance=%s reason=%s",
                user_id,
                rec.instance,
                type(exc).__name__,
            )
            continue
        if url_hp is not None:
            out.append(url_hp)
            continue
        payload_hp = _hosts_from_imap_payload(user_id, rec.instance)
        if payload_hp is not None:
            _log.debug(
                "imap.egress.acl.enumerate_url_miss user_id=%s instance=%s",
                user_id,
                rec.instance,
            )
            out.append(payload_hp)
            continue
        _log.warning(
            "imap.egress.acl.enumerate_skip user_id=%s instance=%s reason=no_host",
            user_id,
            rec.instance,
        )
    return out


@dataclass(frozen=True)
class SmtpConnection:
    """Resolved per-user, per-mailbox SMTP submission parameters (LUM-650).

    ``username`` is the mailbox's authenticated account — it is BOTH the SMTP
    login user AND the invariant From address (anti-spoofing). ``frozen`` + the
    small field set keep the app-password out of accidental log/repr surfaces.

    For an ``xoauth2`` mailbox (LUM-686) ``password`` is an unused ``""`` sentinel
    (the real credential is ``oauth_access_token``, reused from the IMAP conn); it
    is only read on the ``app_password`` branch.
    """

    host: str
    port: int
    username: str
    password: str
    auth_type: str = "app_password"
    oauth_access_token: str | None = None


def _smtp_host_port(smtp_payload: dict, imap_host: str) -> tuple[str, int]:
    """Resolve the submission ``(host, port)`` from the smtp payload, falling back
    to the provider registry keyed on the mailbox's IMAP host.

    An explicit ``smtp_host`` in the payload wins (the "Other"/separate-host
    path). Otherwise a known provider (Gmail/iCloud/Fastmail) supplies host/port
    from the static registry. An unknown ("Other") mailbox with no explicit host
    is a config error (the ``PUT …/smtp`` route requires a host for "Other").
    """
    host = smtp_payload.get("smtp_host")
    if isinstance(host, str) and host:
        port = smtp_payload.get("smtp_port", 587)
        if not isinstance(port, int) or isinstance(port, bool) or not (1 <= port <= 65535):
            raise ValueError("smtp payload 'smtp_port' must be an int in 1..65535")
        # LUM-782: same A-label rule as the IMAP connection builder.
        norm = normalize_imap_host(host)
        if norm is None:
            raise ValueError("smtp payload 'smtp_host' is not a valid hostname")
        return (norm, port)

    from services.email_providers import provider_for_host

    provider = provider_for_host(imap_host)
    if provider is None:
        raise ValueError(
            "smtp submission host unknown for this mailbox — an 'Other' mailbox "
            "must configure smtp_host/smtp_port"
        )
    return (provider.smtp_host, provider.smtp_port)


def load_smtp_connection(user_id: str, *, instance: str) -> SmtpConnection:
    """Return the SMTP submission params for ``user_id`` + mailbox ``instance``.

    The reuse-IMAP-password default: unless the stored payload sets
    ``reuse_imap = false`` (and supplies its own ``app_password``), the send uses
    the mailbox's existing IMAP app-password + username. Host/port come from the
    provider registry for a known provider, or the stored payload for "Other".

    Raises:
        ConnectorNotConfigured: no ``smtp`` row (send not enabled for the mailbox).
        ValueError: an "Other" mailbox with no submission host, or a bad field.
    """
    smtp_payload = ccs.get_payload(user_id, SMTP, instance=instance)
    if smtp_payload is None:
        raise ccs.ConnectorNotConfigured(
            f"no smtp credential row for user_id={user_id!r} instance={instance!r}"
        )
    if not isinstance(smtp_payload, dict):
        raise ccs.CredentialUnavailable("smtp payload must be a JSON object")

    # The IMAP row supplies the authenticated account (username = From) and, on
    # the reuse path, the password. It also anchors provider host/port lookup.
    # For an xoauth2 mailbox, loading it mints imap.oauth_access_token.
    imap = load_connection(user_id, instance=instance)

    host, port = _smtp_host_port(smtp_payload, imap.host)

    if imap.auth_type == "xoauth2":
        # Reuse the IMAP-minted OAuth token for SMTP AUTH XOAUTH2 — no second
        # refresh, no app-password. The token always wins for an xoauth2 mailbox;
        # any reuse_imap=false / standalone app_password in the smtp payload is
        # ignored (a Gmail/Outlook OAuth mailbox has no separate submission
        # password). A None token here is a programming error (load_connection
        # mints for xoauth2) — raise OAuthRefreshError so _resolve_smtp's narrow
        # clause maps it to a PermanentActionError.
        if imap.oauth_access_token is None:
            from services.email_oauth import OAuthRefreshError

            raise OAuthRefreshError(
                "xoauth2 smtp: IMAP connection carried no access token", kind="config"
            )
        return SmtpConnection(
            host=host,
            port=port,
            username=imap.username,
            password="",
            auth_type="xoauth2",
            oauth_access_token=imap.oauth_access_token,
        )

    reuse = smtp_payload.get("reuse_imap", True)
    if not isinstance(reuse, bool):
        raise ValueError("smtp payload 'reuse_imap' must be a boolean")
    if reuse:
        password = imap.password or ""
    else:
        password = smtp_payload.get("app_password")
        if not isinstance(password, str) or not password:
            raise ValueError("smtp payload 'app_password' is required when reuse_imap is false")

    if not password:
        raise ValueError("smtp: no usable app-password resolved for the mailbox")

    return SmtpConnection(host=host, port=port, username=imap.username, password=password)


def resolve_smtp_host_port(user_id: str, *, instance: str) -> tuple[str, int] | None:
    """``(host, port)`` for a mailbox's SMTP send, or ``None`` if unresolvable.

    DNS-free and password-free — used only by the egress ceiling/ACL enumeration
    (mirrors ``declared_imap_hosts``). Skips (returns ``None``) rather than
    raising so one misconfigured mailbox never sinks the ceiling union.
    """
    try:
        smtp_payload = ccs.get_payload(user_id, SMTP, instance=instance)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(smtp_payload, dict):
        return None
    imap_host = ""
    url_hp = _hosts_from_source_url(user_id, instance)
    if url_hp is not None:
        imap_host = url_hp[0]
    else:
        payload_hp = _hosts_from_imap_payload(user_id, instance)
        if payload_hp is not None:
            imap_host = payload_hp[0]
    try:
        return _smtp_host_port(smtp_payload, imap_host)
    except ValueError:
        return None


def _connection_from_payload(payload: Any, *, access_token: str | None = None) -> ImapConnection:
    if not isinstance(payload, dict):
        raise ccs.CredentialUnavailable("imap payload must be a JSON object")

    # LUM-782: connect with the A-label, not the stored spelling. The LUM-773
    # compare is IDNA2008; the stdlib resolver/IMAPClient are IDNA2003, so a raw
    # unicode host (``faß``) would resolve and receive the password as a different
    # host (``fass``). Only the host used to resolve / SSRF-check / TLS / connect
    # changes; the stored payload is untouched.
    raw_host = _require_str(payload, "host")
    host = normalize_imap_host(raw_host)
    if host is None:
        raise ValueError("imap payload field 'host' is not a valid hostname")
    username = _require_str(payload, "username")

    port = payload.get("port", 993)
    if not isinstance(port, int) or isinstance(port, bool) or not (1 <= port <= 65535):
        raise ValueError("imap payload 'port' must be an int in 1..65535")

    use_ssl = payload.get("use_ssl", True)
    if not isinstance(use_ssl, bool):
        raise ValueError("imap payload 'use_ssl' must be a boolean")

    auth_type = payload.get("auth_type", "app_password")
    if auth_type not in _ALLOWED_AUTH_TYPES:
        raise ValueError(f"imap payload 'auth_type' must be one of {sorted(_ALLOWED_AUTH_TYPES)}")

    if auth_type == "xoauth2":
        # The caller (load_connection) must mint the token first; a None here is a
        # programming error, not a bad row.
        if access_token is None:
            raise ValueError(
                "xoauth2 connection requires a minted access_token "
                "(load_connection must call email_oauth.get_access_token first)"
            )
        # refresh_token/provider are validated (and the token minted) upstream by
        # get_access_token; the connection itself carries no password.
        password = None
        oauth_access_token = access_token
    else:
        password = _require_str(payload, "password")
        oauth_access_token = None

    # Resolve the host ONCE and reuse for both the SSRF and TLS guards (both modes).
    resolved_ips = _resolve_host_ips(host)
    _validate_imap_host(host, resolved_ips)
    _require_tls(host, use_ssl=use_ssl, resolved_ips=resolved_ips)

    return ImapConnection(
        host=host,
        port=port,
        username=username,
        use_ssl=use_ssl,
        auth_type=auth_type,
        password=password,
        oauth_access_token=oauth_access_token,
    )


def _require_str(payload: dict, key: str) -> str:
    if key not in payload:
        raise ccs.CredentialUnavailable(f"imap payload missing required key: {key!r}")
    value = payload[key]
    if not isinstance(value, str):
        raise ccs.CredentialUnavailable(f"imap payload field {key!r} must be a string")
    if not value:
        raise ValueError(f"imap payload field {key!r} is empty")
    if value.strip() != value:
        raise ValueError(f"imap payload field {key!r} has leading/trailing whitespace")
    return value


def _validate_imap_payload_host_for_compare(payload: dict) -> str:
    """Host preconditions for LUM-773 — ``ValueError`` on bad input, no strip."""
    if "host" not in payload:
        raise ValueError("imap payload missing required key: 'host'")
    host = payload["host"]
    if not isinstance(host, str):
        raise ValueError("imap payload field 'host' must be a string")
    if not host:
        raise ValueError("imap payload field 'host' is empty")
    if host.strip() != host:
        raise ValueError("imap payload field 'host' has leading/trailing whitespace")
    return host


def _validate_imap_payload_port_for_compare(payload: dict) -> int:
    port = payload.get("port", 993)
    if not isinstance(port, int) or isinstance(port, bool) or not (1 <= port <= 65535):
        raise ValueError("imap payload 'port' must be an int in 1..65535")
    return port


def validate_imap_instance_payload_against_source(
    user_id: str, source_id: str, payload: dict
) -> None:
    """Refuse instance-bound IMAP credential PUT when host/port ≠ ``sources.url``."""
    ms = config.get_metadata_store()
    # SCOPE-EXEMPT: user_id predicate is the isolation
    row = ms.fetch_one(
        "SELECT url FROM sources WHERE id = %s AND user_id = %s AND source_type = 'imap' LIMIT 1",
        (source_id, user_id),
    )
    if not row or not row.get("url"):
        raise ImapSourceNotFoundError(source_id)

    parsed = imap_host_port_from_source_url(str(row["url"]))
    if parsed is None:
        raise ImapSourceUrlInvalidError(str(row["url"]))

    expected_host, expected_port = parsed
    got_host = _validate_imap_payload_host_for_compare(payload)
    got_port = _validate_imap_payload_port_for_compare(payload)

    # LUM-782: both sides in one canonical form; an un-normalisable payload host
    # (None) can never equal the source host, so it fails closed as a mismatch.
    if normalize_imap_host(got_host) != expected_host or expected_port != got_port:
        _log.warning(
            "imap_host_mismatch user_id=%s instance=%s expected_host=%s expected_port=%s "
            "got_host=%s got_port=%s",
            user_id,
            source_id,
            expected_host,
            expected_port,
            got_host,
            got_port,
        )
        exc = ImapHostMismatchError(f"IMAP host/port mismatch for instance {source_id!r}")
        exc.expected_host = expected_host
        exc.expected_port = expected_port
        exc.got_host = got_host
        exc.got_port = got_port
        raise exc


def _resolve_host_ips(host: str) -> list[str]:
    """Resolve ``host`` to its addresses once (literal IPs pass through).

    Raises ``ValueError`` when a hostname does not resolve — callers reuse the
    result for both the SSRF and TLS guards so DNS is queried a single time.
    """
    import ipaddress

    try:
        ipaddress.ip_address(host)
        return [host]  # host is itself a literal IP
    except ValueError:
        pass
    try:
        ips = _ohu._resolved_ips(host)
    except OSError as exc:
        raise ImapHostUnresolvedError(f"imap host {host!r} did not resolve: {exc}") from exc
    if not ips:
        raise ImapHostUnresolvedError(f"imap host {host!r} did not resolve to any address")
    return ips


def _host_is_loopback(resolved_ips: list[str]) -> bool:
    """True iff every resolved address is loopback."""
    import ipaddress

    if not resolved_ips:
        return False
    for ip_s in resolved_ips:
        try:
            if not ipaddress.ip_address(ip_s).is_loopback:
                return False
        except ValueError:
            return False
    return True


def _validate_imap_host(host: str, resolved_ips: list[str]) -> None:
    """SSRF guard for a bare IMAP host, over its already-resolved addresses.

    Reuses the outbound-HTTP IP-range policy (link-local/metadata always
    blocked; loopback/private gated by ``LUMOGIS_ALLOW_PRIVATE_OUTBOUND_URLS``
    + allowlist). This is intentionally NOT
    :func:`outbound_http_url.validate_outbound_connector_base_url`, which
    rejects any non-http/https scheme and so cannot validate ``imap://``.
    """
    import ipaddress

    allow_private = _ohu._truthy_env("LUMOGIS_ALLOW_PRIVATE_OUTBOUND_URLS", "true")
    allowlist = _ohu._private_host_allowlist()
    for ip_s in resolved_ips:
        ip = ipaddress.ip_address(ip_s)
        reason = _ohu._ip_policy_violation(
            ip, hostname=host, allow_private=allow_private, allowlist=allowlist
        )
        if reason:
            raise ImapHostBlockedError(f"imap host {host!r} blocked: {reason}")


def revalidate_imap_host(host: str, *, require_loopback: bool = False) -> None:
    """Connect-time SSRF re-check for ``host`` (LUM-625 Layer 1).

    ``load_connection`` validates the host once per poll tick, but the actual
    socket opens later (imapclient re-resolves internally). This re-runs the
    resolve + IP-policy check **immediately before** the connect, narrowing the
    load-time→connect-time DNS-rebinding window — the closure that ``tethered``
    (hostname-matched, LUM-625 spike) cannot provide on its own.

    ``require_loopback`` (set for a **plaintext** mailbox) additionally re-asserts
    that the freshly-resolved IPs are loopback — closing the window where a host
    that resolved to ``127.0.0.1`` at load time rebinds to a public IP at connect
    time and the app-password would be sent in cleartext off-box.

    Note the residual: imapclient performs its *own* independent resolution, so
    this narrows but does not atomically eliminate the window. Atomic closure
    would require connect-by-validated-IP (the Layer-2 shim class) — deferred.
    """
    ips = _resolve_host_ips(host)
    _validate_imap_host(host, ips)
    if require_loopback and not _host_is_loopback(ips):
        raise ImapHostBlockedError(
            f"imap host {host!r} must resolve to loopback for a plaintext connection"
        )


def _require_tls(host: str, *, use_ssl: bool, resolved_ips: list[str]) -> None:
    """Reject a plaintext app-password to a non-loopback host.

    ``use_ssl=True`` (implicit TLS, port 993) satisfies this. Plaintext
    (``use_ssl=False``) is allowed only when the host is loopback (self-hosted
    dev on the same box). STARTTLS upgrade to a remote host is a future
    enhancement; v1 requires implicit TLS for any remote mailbox.
    """
    if use_ssl:
        return
    if _host_is_loopback(resolved_ips):
        return
    raise ValueError(
        f"imap host {host!r} requires TLS (use_ssl=true); plaintext is only "
        "permitted to a loopback host"
    )
