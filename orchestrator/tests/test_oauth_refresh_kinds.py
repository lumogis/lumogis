# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""An OAuth refresh failure says which kind it is (LUM-757).

ADR 195 gave mailbox health four codes — ``credential`` / ``egress_blocked`` /
``network`` / ``config`` — so the banner would stop telling household members to
fix credentials that are fine. It reached the IMAP-connect path and not the
OAuth-refresh one: every ``OAuthRefreshError`` fell through
``_classify_construction_failure``'s default to ``credential``, so a token
endpoint that timed out told the user to re-run a device-code flow.

**Why the kind is decided at the raise site.** The message deliberately carries
only a status code and provider name (``email_oauth`` module docstring), so it
cannot be classified after the fact — and string-matching it was rejected for
the neighbouring IMAP case as fragile and provider-dependent. The response is in
scope at the raise site and nowhere else.

**Why the mapping keys on RFC 6749 ``error`` values.** ``invalid_grant`` and
``invalid_client`` are standard; ``AADSTS50173`` is Microsoft-only and would not
survive a second provider (LUM-689).
"""

from __future__ import annotations

import ast
import logging
import pathlib

import httpx
import pytest
from services.email_oauth import _KINDS
from services.email_oauth import OAuthRefreshError
from services.email_oauth import _kind_for_token_error
from services.email_oauth import _token_error_code

# --- the mapping ------------------------------------------------------------ #


@pytest.mark.parametrize(
    ("status", "err", "expected"),
    [
        (400, "invalid_grant", "credential"),
        (400, "invalid_client", "config"),
        (401, "invalid_client", "config"),
        (400, "unauthorized_client", "config"),
        (429, None, "network"),
        (500, None, "network"),
        (503, None, "network"),
        (400, "something_new", "credential"),
        (400, None, "credential"),
    ],
)
def test_the_token_error_mapping(status, err, expected):
    assert _kind_for_token_error(status, err) == expected


def test_status_wins_over_a_stale_error_body():
    """A rate-limited provider echoing a stale error must not trigger re-auth.

    429 means "slow down", not "your grant is gone". Reading the body first would
    send the user through a device-code flow to fix a rate limit.
    """
    assert _kind_for_token_error(429, "invalid_grant") == "network"
    assert _kind_for_token_error(503, "invalid_grant") == "network"


# --- the error-code extractor ----------------------------------------------- #


def test_an_unparseable_error_body_yields_no_code_rather_than_raising():
    """A failure to parse an error body must not mask the error it describes."""
    assert _token_error_code(httpx.Response(400, text="<html>gateway</html>")) is None
    assert _token_error_code(httpx.Response(400, json=["not", "a", "dict"])) is None
    assert _token_error_code(httpx.Response(400, json={"error": 42})) is None
    ok = httpx.Response(400, json={"error": "invalid_grant"})
    assert _token_error_code(ok) == "invalid_grant"


# --- the kind attribute ----------------------------------------------------- #


def test_the_default_kind_is_todays_behaviour():
    """A raise site that forgets must degrade to the status quo, not to something new."""
    assert OAuthRefreshError("boom").kind == "credential"


def test_an_unrecognised_kind_is_coerced_and_logged(caplog):
    """The classifier writes this string verbatim to `sources.last_poll_error_code`.

    An unvalidated typo would reach the database and render as the UI's fallback
    copy, which is the vaguest string it has.
    """
    with caplog.at_level(logging.WARNING, logger="services.email_oauth"):
        exc = OAuthRefreshError("boom", kind="banana")

    assert exc.kind == "credential"
    assert any("oauth_refresh_error_unknown_kind" in r.getMessage() for r in caplog.records)


def test_the_kinds_are_exactly_adr_195s_health_codes():
    """Not a parallel vocabulary — `feed_monitor` returns these straight through."""
    assert _KINDS == {"credential", "network", "config", "egress_blocked"}


# --- every raise site sets one ---------------------------------------------- #


def test_every_raise_site_sets_a_kind():
    """A `kind` attribute cannot force a new raise site to set one; this can.

    Scans BOTH modules that raise the type. Planning this chunk miscounted the
    sites twice — "eight" when there are ten in `email_oauth.py` alone, and the
    one in `email_credentials.py` was found only on a second pass — which is
    exactly the argument for enforcing it mechanically rather than by review.
    """
    root = pathlib.Path(__file__).resolve().parents[1]
    missing: list[str] = []

    for rel in ("services/email_oauth.py", "services/email_credentials.py"):
        path = root / rel
        tree = ast.parse(path.read_text())

        # Resolve aliases: `from services.email_oauth import OAuthRefreshError as X`
        # must still be caught. Matching the bare name would miss it, and the
        # scanner failing open is the one way this guard is worse than useless.
        names = {"OAuthRefreshError"}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name == "OAuthRefreshError" and alias.asname:
                        names.add(alias.asname)

        for node in ast.walk(tree):
            if not isinstance(node, ast.Raise) or node.exc is None:
                continue
            call = node.exc
            if not isinstance(call, ast.Call):
                continue
            func = call.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name not in names:
                continue
            if not any(kw.arg == "kind" for kw in call.keywords):
                missing.append(f"{rel}:{node.lineno}")

    assert not missing, f"raise sites without an explicit kind=: {missing}"


def test_the_ast_scan_resolves_aliased_imports():
    """A scanner that fails open is worse than no scanner.

    `from ... import OAuthRefreshError as _ORE` then `raise _ORE(...)` must still
    be caught; matching the bare name alone would wave it through.
    """
    src = 'from services.email_oauth import OAuthRefreshError as _ORE\nraise _ORE("no kind here")\n'
    tree = ast.parse(src)
    names = {"OAuthRefreshError"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "OAuthRefreshError" and alias.asname:
                    names.add(alias.asname)
    assert "_ORE" in names

    raises = [n for n in ast.walk(tree) if isinstance(n, ast.Raise)]
    assert raises[0].exc.func.id in names
    assert not any(kw.arg == "kind" for kw in raises[0].exc.keywords)


def test_the_ast_scan_would_actually_catch_a_missing_kind():
    """Guards the guard — a scanner that matches nothing passes vacuously."""
    tree = ast.parse('raise OAuthRefreshError("no kind here")')
    raises = [n for n in ast.walk(tree) if isinstance(n, ast.Raise)]
    assert len(raises) == 1
    assert not any(kw.arg == "kind" for kw in raises[0].exc.keywords)


# --- the security rule ------------------------------------------------------ #


def test_classifying_never_leaks_the_response_body_into_the_message():
    """`email_oauth`'s standing rule: status code + provider only.

    The body is read to classify and must then be dropped. The likeliest future
    regression is someone adding the error text "for debuggability".
    """
    body = {"error": "invalid_grant", "error_description": "SEKRIT-refresh-token-abc123"}
    resp = httpx.Response(400, json=body)

    kind = _kind_for_token_error(resp.status_code, _token_error_code(resp))
    exc = OAuthRefreshError(f"token refresh failed ({resp.status_code}) for microsoft", kind=kind)

    assert kind == "credential"
    rendered = str(exc) + repr(exc.args)
    assert "SEKRIT" not in rendered
    assert "error_description" not in rendered
