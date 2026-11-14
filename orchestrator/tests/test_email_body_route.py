# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""`POST /api/v1/email/body` — the read route (LUM-810).

The ownership gate is kept **real** in these tests and only IMAP is faked.
Monkeypatching the ownership chain and then asserting the mock would prove
nothing about the one barrier standing between a household member and someone
else's mail.

One named test per `reason` value, so the response `Literal`, the error table
and the tests stay a closed triple rather than drifting apart.
"""

from __future__ import annotations

import pytest
from adapters.email_imap_source import FetchedBody
from fastapi.testclient import TestClient
from routes.api_v1 import email_send


@pytest.fixture
def client(monkeypatch):
    # `main.load_dotenv()` runs at first import and can re-apply operator
    # AUTH_ENABLED from the repo-root `.env` after conftest's delenv (LUM-804).
    import main

    monkeypatch.delenv("AUTH_ENABLED", raising=False)
    email_send._body_limiter.clear()
    with TestClient(main.app) as c:
        yield c


@pytest.fixture(autouse=True)
def _reset_limiter():
    email_send._body_limiter.clear()
    yield
    email_send._body_limiter.clear()


def _own(monkeypatch, *, uid: int = 7, uidvalidity: int = 100):
    """Make the ownership gate pass, without faking what it proves."""
    owned = email_send.OwnedSignal(source=object(), uidvalidity=uidvalidity, uid=uid)
    monkeypatch.setattr(email_send, "_assert_owned_signal", lambda s, u: owned)
    return owned


def _imap(monkeypatch, result: FetchedBody, *, capture: dict | None = None):
    class _Adapter:
        def __init__(self, source):
            pass

        def fetch_body_bounded(self, **kw):
            if capture is not None:
                capture.update(kw)
            return result

    monkeypatch.setattr(email_send, "EmailImapSource", _Adapter, raising=False)


def _producers(monkeypatch, *, message_id="<m@example.com>", folder=None):
    monkeypatch.setattr(email_send, "_message_id_for", lambda s, u: message_id)
    monkeypatch.setattr(email_send, "_organize_folder_for", lambda s, u: folder)


# ---------------------------------------------------------------------------
# Happy path and the producer contract
# ---------------------------------------------------------------------------


def test_body_route_when_owned_signal_then_200_with_text(client, monkeypatch):
    _own(monkeypatch)
    _producers(monkeypatch)
    _imap(monkeypatch, FetchedBody("Hello there.", "ok"))

    r = client.post("/api/v1/email/body", json={"signal_id": "sig-1"})

    assert r.status_code == 200
    assert r.json() == {
        "body": "Hello there.",
        "truncated": False,
        "degraded": False,
        "reason": "ok",
        "inline_images": [],
        "inline_images_truncated": False,
    }


def test_body_route_passes_all_four_locate_inputs_to_the_adapter(client, monkeypatch):
    """`uid` and `uidvalidity` from the gate; `message_id` and `folder_hint`
    from the shipped lookups. Passing None for the latter two forces a
    full-folder sweep on every organized user's mail."""
    _own(monkeypatch, uid=42, uidvalidity=77)
    _producers(monkeypatch, message_id="<x@example.com>", folder="Lumogis/Receipts")
    seen: dict = {}
    _imap(monkeypatch, FetchedBody("t", "ok"), capture=seen)

    client.post("/api/v1/email/body", json={"signal_id": "sig-1"})

    assert seen == {
        "uid": 42,
        "uidvalidity": 77,
        "message_id": "<x@example.com>",
        "folder_hint": "Lumogis/Receipts",
    }


def test_body_route_annotates_bidi_before_returning(client, monkeypatch):
    _own(monkeypatch)
    _producers(monkeypatch)
    _imap(monkeypatch, FetchedBody("pay ‮alice‬ now", "ok"))

    body = client.post("/api/v1/email/body", json={"signal_id": "sig-1"}).json()["body"]

    assert "␎U+202E␏" in body
    assert "‮" not in body


def test_body_route_truncates_at_the_char_cap(client, monkeypatch):
    _own(monkeypatch)
    _producers(monkeypatch)
    _imap(monkeypatch, FetchedBody("x" * 25_000, "ok"))

    payload = client.post("/api/v1/email/body", json={"signal_id": "sig-1"}).json()

    assert payload["truncated"] is True
    assert len(payload["body"]) == email_send._MAX_BODY_CHARS


def test_body_route_at_exactly_the_cap_is_not_truncated(client, monkeypatch):
    _own(monkeypatch)
    _producers(monkeypatch)
    _imap(monkeypatch, FetchedBody("x" * email_send._MAX_BODY_CHARS, "ok"))

    payload = client.post("/api/v1/email/body", json={"signal_id": "sig-1"}).json()

    assert payload["truncated"] is False


# ---------------------------------------------------------------------------
# One test per reason
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "reason,degraded",
    [
        ("no_body", False),
        ("mailbox_unreachable", True),
        ("not_found", False),
        ("too_large", False),
        ("ambiguous", False),
    ],
)
def test_body_route_reason_is_200_with_degraded_derived(client, monkeypatch, reason, degraded):
    """`degraded` is derived, never set by hand: it is exactly
    `reason == "mailbox_unreachable"`. None of these may be a 5xx."""
    _own(monkeypatch)
    _producers(monkeypatch)
    _imap(monkeypatch, FetchedBody("", reason))

    r = client.post("/api/v1/email/body", json={"signal_id": "sig-1"})

    assert r.status_code == 200
    assert r.json()["reason"] == reason
    assert r.json()["degraded"] is degraded
    assert r.json()["body"] == ""


def test_body_route_when_adapter_construction_raises_then_200_not_500(client, monkeypatch):
    """`EmailImapSource.__init__` loads the credential, and for an xoauth2
    mailbox that is a live token refresh. A revoked grant used to 500 — the
    single most likely field failure, on a route whose contract is 200 with a
    reason for every expected failure."""
    _own(monkeypatch)
    _producers(monkeypatch)

    class _Boom:
        def __init__(self, source):
            raise RuntimeError("token refresh failed")

    monkeypatch.setattr(email_send, "EmailImapSource", _Boom, raising=False)

    r = client.post("/api/v1/email/body", json={"signal_id": "sig-1"})

    assert r.status_code == 200
    assert r.json()["reason"] == "mailbox_unreachable"
    assert r.json()["degraded"] is True


def test_body_route_when_message_id_lookup_raises_then_200_not_500(client, monkeypatch):
    """The producers run inside the same guard — a metadata-store failure is a
    reason we cannot locate the message, not a stack trace."""
    _own(monkeypatch)
    monkeypatch.setattr(
        email_send,
        "_message_id_for",
        lambda s, u: (_ for _ in ()).throw(RuntimeError("db down")),
    )
    monkeypatch.setattr(email_send, "_organize_folder_for", lambda s, u: None)
    _imap(monkeypatch, FetchedBody("t", "ok"))

    r = client.post("/api/v1/email/body", json={"signal_id": "sig-1"})

    assert r.status_code == 200
    assert r.json()["reason"] == "mailbox_unreachable"


def test_body_route_truncates_before_annotating_so_sentinels_stay_whole(client, monkeypatch):
    """Annotation turns 1 char into 8, so slicing afterwards can cut a sentinel
    in half — leaving a visible `␎U+` tail — and makes the cap bound the
    escaping rather than the message."""
    _own(monkeypatch)
    _producers(monkeypatch)
    cap = email_send._MAX_BODY_CHARS
    _imap(monkeypatch, FetchedBody("x" * (cap - 2) + "‮" + "y" * 50, "ok"))

    payload = client.post("/api/v1/email/body", json={"signal_id": "sig-1"}).json()

    assert payload["truncated"] is True
    assert "␎U+202E␏" in payload["body"]
    assert not payload["body"].endswith("␎U+")
    assert not payload["body"].endswith("␎")


def test_body_route_when_imap_down_then_200_not_5xx(client, monkeypatch):
    _own(monkeypatch)
    _producers(monkeypatch)
    _imap(monkeypatch, FetchedBody("", "mailbox_unreachable"))

    assert client.post("/api/v1/email/body", json={"signal_id": "sig-1"}).status_code == 200


class _Store:
    """Answers the two lookups `_message_id_for` makes, recording which ran.

    Same shape as `test_signal_message_id.py`'s fake — deliberately, so this
    test pins the route against the *same* contract that file pins the helper
    against.
    """

    def __init__(self, *, signal_mid=None, organize_mid=None, folder=None):
        self.signal_mid = signal_mid
        self.organize_mid = organize_mid
        self.folder = folder
        self.queried: list[str] = []

    def fetch_one(self, sql, params=None):
        if "published_from" in sql:
            # LUM-661 taught `_lookup_ids` to resolve a shared projection back to
            # its personal original before looking up the Message-ID. This fake
            # raises on any query it does not recognise, so it must know about
            # that lookup or every caller of `message_id_for` fails here.
            # None = "not a projection", which is the case these tests exercise.
            return None
        if "FROM signals" in sql and "email_message_id" in sql:
            self.queried.append("signals")
            return {"email_message_id": self.signal_mid} if self.signal_mid is not None else None
        if "FROM email_organize_log" in sql and "target" in sql:
            self.queried.append("organize_folder")
            return {"target": self.folder} if self.folder else None
        if "FROM email_organize_log" in sql:
            self.queried.append("organize_log")
            if self.organize_mid is None:
                return None
            return {"reverse_token": {"message_id": self.organize_mid}}
        raise AssertionError(f"unexpected query: {sql!r}")


def test_body_route_uses_signals_email_message_id_before_organize_log(client, monkeypatch):
    """The route must resolve a Message-ID with **no** organize-log row present.

    Two `_message_id_for` implementations ship. The one in
    `routes/api_v1/email_attachments.py` reads `email_organize_log` only, and
    it is the copy sitting next to the route an implementer opens first. Wire
    that one in and every fake-IMAP test here stays green — the fixtures hand
    the Message-ID over directly — while mail moved by the user, by a
    paperless rule, or by a server-side filter silently reports "no longer in
    your mailbox" in production. This is the test that tells them apart.
    """
    import config

    st = _Store(signal_mid="<from-column@example.com>", organize_mid=None)
    monkeypatch.setattr(config, "get_metadata_store", lambda: st)
    _own(monkeypatch)
    seen: dict = {}
    _imap(monkeypatch, FetchedBody("t", "ok"), capture=seen)

    client.post("/api/v1/email/body", json={"signal_id": "sig-1"})

    assert seen["message_id"] == "<from-column@example.com>"
    assert "signals" in st.queried, "the signals column was never consulted"


def test_body_route_falls_back_to_organize_log_for_a_pre_069_signal(client, monkeypatch):
    """Migration 069 added the column with **no backfill**, so every historical
    signal carries `''`. The old path has to survive or the read view breaks
    for exactly the oldest mail."""
    import config

    st = _Store(signal_mid="", organize_mid="<from-log@example.com>")
    monkeypatch.setattr(config, "get_metadata_store", lambda: st)
    _own(monkeypatch)
    seen: dict = {}
    _imap(monkeypatch, FetchedBody("t", "ok"), capture=seen)

    client.post("/api/v1/email/body", json={"signal_id": "sig-1"})

    assert seen["message_id"] == "<from-log@example.com>"
    assert st.queried[:2] == ["signals", "organize_log"]


# ---------------------------------------------------------------------------
# Ownership — the only barrier on this path
# ---------------------------------------------------------------------------


def test_body_route_when_signal_not_owned_then_404_with_no_oracle(client, monkeypatch):
    """404, not 403: a 403 would confirm the signal exists."""
    from fastapi import HTTPException
    from fastapi import status as st

    def _deny(signal_id, user_id):
        raise HTTPException(st.HTTP_404_NOT_FOUND, detail={"error": "not_found"})

    monkeypatch.setattr(email_send, "_assert_owned_signal", _deny)

    r = client.post("/api/v1/email/body", json={"signal_id": "someone-elses"})

    assert r.status_code == 404
    assert r.json()["detail"] == {"error": "not_found"}


def test_body_route_does_not_fetch_when_ownership_fails(client, monkeypatch):
    """The gate runs BEFORE any IMAP work — it must not spend the owner's
    credential on another member's behalf."""
    from fastapi import HTTPException
    from fastapi import status as st

    monkeypatch.setattr(
        email_send,
        "_assert_owned_signal",
        lambda s, u: (_ for _ in ()).throw(HTTPException(st.HTTP_404_NOT_FOUND)),
    )
    called: list = []

    class _Adapter:
        def __init__(self, source):
            called.append(source)

        def fetch_body_bounded(self, **kw):
            called.append(kw)
            return FetchedBody("leaked", "ok")

    monkeypatch.setattr(email_send, "EmailImapSource", _Adapter, raising=False)

    client.post("/api/v1/email/body", json={"signal_id": "someone-elses"})

    assert called == []


# ---------------------------------------------------------------------------
# Rate limiting — its own bucket
# ---------------------------------------------------------------------------


def test_body_route_429_after_limit_with_retry_after(client, monkeypatch):
    _own(monkeypatch)
    _producers(monkeypatch)
    _imap(monkeypatch, FetchedBody("t", "ok"))

    for _ in range(email_send._BODY_LIMIT):
        assert client.post("/api/v1/email/body", json={"signal_id": "s"}).status_code == 200

    r = client.post("/api/v1/email/body", json={"signal_id": "s"})

    assert r.status_code == 429
    assert r.headers.get("Retry-After") == "60"


def test_body_bucket_is_independent_of_quote(client, monkeypatch):
    """A bounded IMAP fetch must not consume the LLM budgets, and exhausting
    one read surface must not disable the composer."""
    _own(monkeypatch)
    _producers(monkeypatch)
    _imap(monkeypatch, FetchedBody("t", "ok"))
    monkeypatch.setattr(email_send.email_quote, "build_quote", lambda s, *, user_id, style: "> q")

    for _ in range(email_send._BODY_LIMIT + 1):
        client.post("/api/v1/email/body", json={"signal_id": "s"})

    assert client.post("/api/v1/email/quote", json={"signal_id": "s"}).status_code == 200


# ---------------------------------------------------------------------------
# The ownership gate, kept REAL
# ---------------------------------------------------------------------------


class _GateStore:
    """A metadata store the REAL `_assert_owned_signal` chain can run against.

    `_signal_url` reads `signals.url`; `_load_owned_source` reads `sources`
    filtered by `user_id`. Faking those two rows exercises the actual gate —
    URL parse, uidvalidity/uid recovery, ownership filter — instead of asserting
    that a monkeypatched stand-in was called.
    """

    def __init__(self, *, url: str | None, source_row: dict | None):
        self._url = url
        self._source_row = source_row

    def fetch_one(self, sql, params=None):
        if "FROM signals" in sql and "url" in sql:
            return {"url": self._url} if self._url else None
        if "FROM sources" in sql:
            return self._source_row
        return None


def test_body_route_404_for_other_users_signal_through_the_real_gate(client, monkeypatch):
    """user_id isolation, proven with the ownership chain LIVE.

    The plan is explicit that route tests must keep this gate real and fake only
    IMAP — "never monkeypatch the chain then assert the mock". A stubbed gate
    proves the route calls something; it says nothing about whether the one
    barrier between a household member and someone else's mail actually holds.

    Here the signal resolves to a real imap:// URL, and `sources` returns no row
    for this caller — exactly what another member's mailbox looks like.
    """
    import config

    store = _GateStore(url="imap://11111111-1111-1111-1111-111111111111/100/7", source_row=None)
    monkeypatch.setattr(config, "get_metadata_store", lambda: store)
    _producers(monkeypatch)
    reached: list = []

    class _Adapter:
        def __init__(self, source):
            reached.append(source)

        def fetch_body_bounded(self, **kw):
            return FetchedBody("someone else's mail", "ok")

    monkeypatch.setattr(email_send, "EmailImapSource", _Adapter, raising=False)

    r = client.post("/api/v1/email/body", json={"signal_id": "not-mine"})

    assert r.status_code == 404
    assert r.json()["detail"] == {"error": "not_found"}
    # The owner's IMAP credential must never be spent on another member's behalf.
    assert reached == []
    # And no image bytes leaked alongside the refusal (LUM-713): the strip rides
    # `/body`, so the gate that protects the text is the gate that protects it.
    assert "inline_images" not in r.text


def test_body_route_404_is_identical_for_a_signal_that_does_not_exist(client, monkeypatch):
    """No oracle: "not yours" and "does not exist" must be indistinguishable."""
    import config

    monkeypatch.setattr(config, "get_metadata_store", lambda: _GateStore(url=None, source_row=None))
    _producers(monkeypatch)
    _imap(monkeypatch, FetchedBody("", "ok"))

    missing = client.post("/api/v1/email/body", json={"signal_id": "no-such-signal"})

    store = _GateStore(url="imap://11111111-1111-1111-1111-111111111111/100/7", source_row=None)
    monkeypatch.setattr(config, "get_metadata_store", lambda: store)
    not_owned = client.post("/api/v1/email/body", json={"signal_id": "not-mine"})

    assert missing.status_code == not_owned.status_code == 404
    assert missing.json() == not_owned.json()


# ---------------------------------------------------------------------------
# Inline images ride the body response (LUM-713)
# ---------------------------------------------------------------------------

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def _png_part(cid: str = "<i1>", data: bytes = _PNG):
    import email
    from base64 import b64encode

    head = (
        "Content-Type: image/png\r\n"
        "Content-Disposition: inline\r\n"
        f"Content-ID: {cid}\r\n"
        "Content-Transfer-Encoding: base64\r\n\r\n"
    )
    return email.message_from_bytes(head.encode() + b64encode(data))


def _images(parts):
    from services.email_inline_images import inline_images_from_parts

    return inline_images_from_parts(parts)


def test_body_route_ships_inline_images_with_the_text(client, monkeypatch):
    _own(monkeypatch)
    _producers(monkeypatch)
    images, cut = _images([_png_part()])
    _imap(monkeypatch, FetchedBody("Sports day is Thursday.", "ok", tuple(images), cut))

    r = client.post("/api/v1/email/body", json={"signal_id": "sig-1"})

    assert r.status_code == 200
    payload = r.json()
    assert payload["body"] == "Sports day is Thursday."
    assert [i["mime_type"] for i in payload["inline_images"]] == ["image/png"]
    assert payload["inline_images"][0]["data_uri"].startswith("data:image/png;base64,")
    assert payload["inline_images_truncated"] is False


def test_body_route_never_ships_a_part_number_or_fetch_handle(client, monkeypatch):
    """The arbitrated design exposes nothing the client could use to address a
    MIME part — so there is no part-number field to tamper with."""
    _own(monkeypatch)
    _producers(monkeypatch)
    images, cut = _images([_png_part()])
    _imap(monkeypatch, FetchedBody("hi", "ok", tuple(images), cut))

    payload = client.post("/api/v1/email/body", json={"signal_id": "sig-1"}).json()

    assert set(payload["inline_images"][0]) == {
        "content_id",
        "mime_type",
        "size_bytes",
        "data_uri",
    }


def test_body_route_data_uri_media_type_is_the_sniffed_type_not_the_declared_one(
    client, monkeypatch
):
    """**The security assertion at the route boundary.** The part announces
    `image/png`; the bytes are a GIF. What ships is what the bytes are."""
    _own(monkeypatch)
    _producers(monkeypatch)
    images, cut = _images([_png_part(data=b"GIF89a" + b"\x00" * 32)])
    _imap(monkeypatch, FetchedBody("hi", "ok", tuple(images), cut))

    payload = client.post("/api/v1/email/body", json={"signal_id": "sig-1"}).json()

    assert payload["inline_images"][0]["mime_type"] == "image/gif"
    assert payload["inline_images"][0]["data_uri"].startswith("data:image/gif;base64,")


def test_body_route_omits_a_part_that_does_not_sniff(client, monkeypatch):
    """An SVG announced as a PNG is absent, and absence is silent — the strip
    shows no broken-image icon for a part it refused."""
    _own(monkeypatch)
    _producers(monkeypatch)
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script/></svg>'
    images, cut = _images([_png_part(data=svg)])
    _imap(monkeypatch, FetchedBody("hi", "ok", tuple(images), cut))

    payload = client.post("/api/v1/email/body", json={"signal_id": "sig-1"}).json()

    assert payload["inline_images"] == []
    assert payload["inline_images_truncated"] is False


def test_body_route_reports_the_cap_so_the_ui_can_say_how_many_are_missing(client, monkeypatch):
    from services.email_inline_images import MAX_INLINE_IMAGES

    _own(monkeypatch)
    _producers(monkeypatch)
    images, cut = _images([_png_part(cid=f"<{n}>") for n in range(MAX_INLINE_IMAGES + 2)])
    _imap(monkeypatch, FetchedBody("hi", "ok", tuple(images), cut))

    payload = client.post("/api/v1/email/body", json={"signal_id": "sig-1"}).json()

    assert len(payload["inline_images"]) == MAX_INLINE_IMAGES
    assert payload["inline_images_truncated"] is True


def test_body_route_when_imap_is_down_the_strip_is_empty_not_absent(client, monkeypatch):
    """The degraded response keeps the field, so the client never branches on
    whether a key exists."""
    _own(monkeypatch)
    _producers(monkeypatch)

    class _Adapter:
        def __init__(self, source):
            raise RuntimeError("token endpoint unreachable")

    monkeypatch.setattr(email_send, "EmailImapSource", _Adapter, raising=False)

    payload = client.post("/api/v1/email/body", json={"signal_id": "sig-1"}).json()

    assert payload["reason"] == "mailbox_unreachable"
    assert payload["inline_images"] == []
    assert payload["inline_images_truncated"] is False


def test_body_route_401_unauthenticated(client, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_SECRET", "test-email-body-401-secret")

    r = client.post("/api/v1/email/body", json={"signal_id": "sig-1"})

    assert r.status_code == 401
