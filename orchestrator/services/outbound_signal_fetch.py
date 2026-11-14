# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Validated outbound fetch for the RSS / web-page source class (LUM-838).

Every other source class in this product has an explicit SSRF guard: connectors
get :func:`validate_outbound_connector_base_url`, and IMAP gets
``_validate_imap_host`` plus a Squid ``CONNECT`` tunnel that allowlists mail
hosts by SNI (ADR-174/178). The RSS / web-page class had **none**, at detection
or at poll, and had never been looked at — the other two paths each got a
dedicated decision, this one was simply never on anyone's list.

**This is not a fourth guard.** It reuses the connector validator, which already
encodes the scheme allowlist, the credentials-in-authority rejection, and the
private-IP policy with its env flag and allowlist. What this module adds is the
part the connector path does not need: **following redirects safely**.

## Why redirects needed their own answer

Validating an entry URL while leaving ``follow_redirects=True`` is a partial fix
that reads like a complete one — the first hop is checked and every subsequent
hop is not, so `https://harmless.example/r` → `http://169.254.169.254/` walks
straight past the guard. The two ways out:

* **Disable redirects.** Simplest, and wrong for feeds: `http` → `https`
  upgrades, feed hosts, and moved domains all redirect legitimately, and a feed
  reader that refuses them would break real sources for no security gain.
* **Re-validate every hop.** What this does. Redirects are followed manually, up
  to :data:`_MAX_REDIRECTS`, and each ``Location`` is validated before it is
  fetched.

## The private-address default, decided rather than inherited

``LUMOGIS_ALLOW_PRIVATE_OUTBOUND_URLS`` defaults to ``true``, and this class
**keeps that shared default** rather than tightening it for itself:

* Watching an internal wiki page or a LAN service's feed is an ordinary thing to
  want in a self-hosted product — the same reasoning that makes
  ``paperless.lan`` the normal case for connectors.
* A per-class divergence would be a fourth policy for a reader to hold in their
  head, and this module exists to avoid exactly that.
* The flag is what an operator sets when they want it off. Before this change it
  did not apply to this class at all; now it does, which is the actual
  improvement.

The delta this closes is therefore **not** "can reach 192.168.x" — that is
deliberate. It is non-``http(s)`` schemes, credentials embedded in the
authority, link-local and metadata addresses when the flag is off, and every
redirect hop that previously bypassed all of it.
"""

from __future__ import annotations

import logging
from urllib.parse import urljoin

import httpx
from services.outbound_http_url import validate_outbound_connector_base_url

_log = logging.getLogger(__name__)

# Enough for the ordinary http→https and domain-move chains, far short of a
# redirect loop. httpx's own default is 20.
_MAX_REDIRECTS = 5


class OutboundURLBlocked(ValueError):
    """The URL is refused by outbound policy — distinct from "it did not answer".

    A caller must be able to tell these apart. A blocked URL is a decision the
    product made and can explain; an unreachable one is a fact about the world.
    Collapsing them is how a refused source ends up reported as "no feed found",
    which tells the user nothing they can act on.
    """


def validate_signal_source_url(url: str) -> None:
    """Raise :class:`OutboundURLBlocked` if `url` must not be fetched."""
    try:
        validate_outbound_connector_base_url(url)
    except ValueError as exc:
        raise OutboundURLBlocked(str(exc)) from exc


def fetch_text(url: str, *, timeout: float) -> str | None:
    """Fetch `url` with outbound policy applied to **every** hop.

    Returns the body text, or ``None`` when the host did not answer usefully
    (non-2xx, connection error, timeout, or too many redirects).

    Raises :class:`OutboundURLBlocked` when the URL — or any redirect target —
    is refused by policy. A blocked redirect raises rather than returning
    ``None`` on purpose: it is a policy event worth surfacing, not a network
    fact, and silently treating it as "unreachable" would hide the one case this
    module exists to catch.
    """
    current = url
    for _hop in range(_MAX_REDIRECTS + 1):
        validate_signal_source_url(current)
        try:
            resp = httpx.get(current, timeout=timeout, follow_redirects=False)
        except Exception as exc:
            _log.debug("signal_fetch_failed url=%s err=%s", current, exc)
            return None

        if resp.is_redirect:
            location = resp.headers.get("location")
            if not location:
                _log.debug("signal_fetch_redirect_without_location url=%s", current)
                return None
            # Relative Locations are legal and common; resolve against the hop
            # we actually fetched, then validate the result — the validator
            # needs an absolute URL to have a host to check.
            current = urljoin(current, location)
            continue

        if resp.status_code != 200:
            _log.debug("signal_fetch_status url=%s status=%s", current, resp.status_code)
            return None
        return resp.text

    _log.info("signal_fetch_too_many_redirects url=%s", url)
    return None
