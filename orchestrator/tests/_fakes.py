# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Standalone test doubles, importable without the heavy ``conftest`` chain.

``conftest.py`` imports the full app (``config`` → adapters → credentials …),
so a test that only needs an in-memory vector store should import from here
instead of ``tests.conftest`` — this keeps such tests runnable in minimal
environments (e.g. a ``--noconftest`` harness) while ``conftest`` re-exports
these names for back-compat. Pure stdlib only — no app imports.
"""

from __future__ import annotations


def _match_clause(payload: dict, clause: dict) -> bool:
    """Evaluate a single Qdrant filter clause against a payload dict.

    Supports the small subset Lumogis actually uses today:
      * ``{"key": k, "match": {"value": v}}`` — equality
      * ``{"key": k, "match": {"any": [...]}}`` — membership
      * Nested ``{"must": [...]}`` / ``{"should": [...]}`` blocks

    The real Qdrant filter language is much richer; mirroring just what
    ``visibility.visible_qdrant_filter`` and the per-user/scope routes
    actually emit keeps the mock honest without re-implementing Qdrant.
    """
    if "must" in clause or "should" in clause:
        return _matches_qdrant_filter(payload, clause)
    key = clause["key"]
    match = clause["match"]
    actual = payload.get(key)
    if "value" in match:
        return actual == match["value"]
    if "any" in match:
        return actual in match["any"]
    raise NotImplementedError(f"MockVectorStore: unsupported match shape {match!r}")


def _matches_qdrant_filter(payload: dict, flt: dict) -> bool:
    """Top-level filter eval: AND across ``must``, OR across ``should``."""
    if "must" in flt:
        if not all(_match_clause(payload, c) for c in flt["must"]):
            return False
    if "should" in flt:
        if not any(_match_clause(payload, c) for c in flt["should"]):
            return False
    return True


class MockVectorStore:
    def __init__(self):
        self._collections: dict[str, list] = {}

    def ping(self) -> bool:
        return True

    def create_collection(self, name: str, vector_size: int) -> None:
        self._collections[name] = []

    def ensure_payload_index(self, collection: str, field: str) -> None:
        """No-op — in-memory mock has no payload-index API."""

    def ensure_tenant_payload_index(self, collection: str, field: str) -> None:
        """No-op — in-memory mock has no tenant-index API."""

    def upsert(self, collection: str, id: str, vector: list[float], payload: dict) -> None:
        self._collections.setdefault(collection, []).append(
            {"id": id, "vector": vector, "payload": payload}
        )

    def search(
        self,
        collection: str,
        vector: list[float],
        limit: int,
        threshold: float,
        filter: dict | None = None,
        sparse_query: str | None = None,
    ) -> list[dict]:
        items = self._collections.get(collection, [])
        if filter:
            items = [i for i in items if _matches_qdrant_filter(i.get("payload", {}), filter)]
        return [{"id": i["id"], "score": 1.0, "payload": i["payload"]} for i in items[:limit]]

    def delete(self, collection: str, id: str) -> None:
        items = self._collections.get(collection, [])
        self._collections[collection] = [i for i in items if i["id"] != id]

    def delete_where(self, collection: str, filter: dict) -> None:
        items = self._collections.get(collection, [])
        self._collections[collection] = [
            i for i in items if not _matches_qdrant_filter(i.get("payload", {}), filter)
        ]

    def count(self, collection: str) -> int:
        return len(self._collections.get(collection, []))

    def count_where(self, collection: str, filter: dict) -> int:
        items = self._collections.get(collection, [])
        return sum(1 for i in items if _matches_qdrant_filter(i.get("payload", {}), filter))


class FakeImapClient:
    """A fake IMAPClient modelling per-FOLDER mailboxes and ``RFC822.SIZE``.

    Shared by the attachment read path (LUM-695) and the body read path
    (LUM-810), which need the same mailbox model: `_locate_message` walks
    folders by ``Message-ID``, so a single-folder fake cannot exercise it.

    Lives here rather than in either test module because it was copied between
    them once already — the second copy drifted immediately (it grew
    ``withhold_size`` and lost ``listed``), which is how two fakes end up
    asserting subtly different contracts against the same adapter.

    ``withhold_size`` models a server that does not answer ``RFC822.SIZE``. The
    precheck then degrades **open**, so it is the case where a post-fetch bound
    is the only bound left.
    """

    def __init__(
        self,
        folders: dict[str, dict[int, bytes]],
        *,
        sizes: dict[int, int] | None = None,
        withhold_size: bool = False,
        withhold_bodystructure: bool = False,
    ):
        self._folders = folders
        self._sizes = sizes or {}
        self._withhold_size = withhold_size
        # A server that will not answer BODYSTRUCTURE. The text-only fetch
        # (LUM-814) must fall back rather than report the message unreadable.
        self._withhold_bodystructure = withhold_bodystructure
        self.selected: str | None = None
        self.body_peek_calls: list[list[int]] = []
        self.size_calls: list[list[int]] = []
        self.part_fetch_keys: list[str] = []
        self.listed = False

    # -- session ---------------------------------------------------------
    def select_folder(self, folder, readonly=False):
        if folder not in self._folders:
            raise RuntimeError(f"no such folder: {folder}")
        self.selected = folder
        return {b"UIDVALIDITY": 100}

    def list_folders(self):
        self.listed = True
        return [((), b"/", name) for name in self._folders]

    def logout(self):
        return None

    # -- queries ---------------------------------------------------------
    def search(self, criteria):
        if criteria and criteria[0] == "HEADER":
            wanted = criteria[2]
            box = self._folders.get(self.selected or "", {})
            return [uid for uid, raw in box.items() if wanted.encode() in raw]
        return sorted(self._folders.get(self.selected or "", {}))

    def fetch(self, uids, keys):
        box = self._folders.get(self.selected or "", {})
        out: dict[int, dict] = {}
        if "RFC822.SIZE" in keys:
            self.size_calls.append(list(uids))
            if self._withhold_size:
                return {}
            for u in uids:
                if u in box:
                    out[u] = {b"RFC822.SIZE": self._sizes.get(u, len(box[u]))}
            return out
        if "BODY.PEEK[]" in keys:
            self.body_peek_calls.append(list(uids))
            for u in uids:
                if u in box:
                    out[u] = {b"BODY[]": box[u]}
            return out
        if "BODYSTRUCTURE" in keys:
            if self._withhold_bodystructure:
                return {}
            for u in uids:
                if u in box:
                    out[u] = {b"BODYSTRUCTURE": _bodystructure(box[u])}
            return out
        if any(k.startswith("BODY.PEEK[") for k in keys):
            # Named-part fetch: BODY.PEEK[1.2] / BODY.PEEK[1.2.MIME].
            self.part_fetch_keys.extend(keys)
            for u in uids:
                if u in box:
                    out[u] = _named_parts(box[u], keys)
            return out
        raise AssertionError(f"unexpected fetch keys: {keys}")


# --- BODYSTRUCTURE / named-part support for FakeImapClient -------------------
#
# Derived from the real message bytes rather than hand-written per test: a fake
# whose structure and whose body can disagree is a fake that proves nothing
# about a path whose entire job is to line those two up.


def _is_encapsulated(msg) -> bool:
    """True for a ``message/rfc822`` part.

    **Python's ``is_multipart()`` says True for one of these** — its payload is
    a list holding the encapsulated message — but a real IMAP server does not.
    RFC 3501 gives ``message/rfc822`` its own ``body-type-msg`` production: a
    single part carrying the encapsulated body as *extension data*, so its
    children are numbered under the PARENT (``2.1``, ``2.2``), not one level
    deeper (``2.1.1``, ``2.1.2``) as a multipart's would be.

    Branching on ``is_multipart()`` made this fake number a forwarded message's
    parts one level too deep, which meant the walkers under test could be wrong
    in exactly the same way and still pass (LUM-713 review round 2).
    """
    return msg.get_content_type() == "message/rfc822"


def _encapsulated(msg):
    """The message inside a ``message/rfc822`` part."""
    payload = msg.get_payload()
    return payload[0] if isinstance(payload, list) and payload else payload


def _walk_numbered(msg, prefix: str = ""):
    """``(part_number, part)`` for every leaf, numbered per RFC 3501."""
    if _is_encapsulated(msg):
        # The encapsulated message's parts sit at the SAME level as this one:
        # a forwarded mail at part 2 has its own parts at 2.1 and 2.2.
        inner = _encapsulated(msg)
        if inner is not None and inner.is_multipart():
            for index, sub in enumerate(inner.get_payload(), start=1):
                yield from _walk_numbered(sub, f"{prefix}{index}.")
            return
        # A single-part forwarded message is addressed as `<n>.1`.
        yield (f"{prefix}1", inner if inner is not None else msg)
        return
    if msg.is_multipart():
        for index, sub in enumerate(msg.get_payload(), start=1):
            child = f"{prefix}{index}." if prefix else f"{index}."
            yield from _walk_numbered(sub, child)
        return
    yield (prefix.rstrip(".") or "1", msg)


def _disposition(msg):
    """The ``body-fld-dsp`` extension field, or ``None`` when absent."""
    value = msg.get_content_disposition()
    if not value:
        return None
    return (value.upper().encode(), None)


def _bodystructure(raw: bytes):
    """A `BodyData` mirroring `raw`'s real MIME tree."""
    import email as _email

    from imapclient.response_types import BodyData

    def one_part(msg, *, inner=None):
        payload = msg.get_payload(decode=False)
        if not isinstance(payload, str):
            payload = ""
        fields = [
            msg.get_content_maintype().upper().encode(),
            msg.get_content_subtype().upper().encode(),
            (b"CHARSET", (msg.get_content_charset() or "utf-8").upper().encode()),
            # Field 4 (index 3) is the Content-ID. This was hardcoded to
            # None, which made the oversized-path inline-image tests
            # unwritable — the walk looks here to decide what is inline
            # (LUM-713 review round 1 found it).
            (msg.get("Content-ID") or "").encode() or None,
            None,
            (msg.get("Content-Transfer-Encoding") or "7BIT").upper().encode(),
            # ENCODED octets, as a real server reports. The decoded size is not
            # available from a BODYSTRUCTURE at all, which is exactly why the
            # adapter may only use this as a transfer bound.
            len(payload),
        ]
        if inner is not None:
            # body-type-msg: envelope, body, lines — then the same extension
            # data as any other single part.
            fields.extend([None, build(inner), 0])
        fields.extend(
            [
                None,  # body-fld-md5
                _disposition(msg),  # body-fld-dsp — index 8 for a non-text part
            ]
        )
        return BodyData.create(tuple(fields))

    def build(msg):
        if _is_encapsulated(msg):
            return one_part(msg, inner=_encapsulated(msg))
        if msg.is_multipart():
            subs = tuple(build(p) for p in msg.get_payload())
            return BodyData.create(subs + (msg.get_content_subtype().upper().encode(),))
        return one_part(msg)

    return build(_email.message_from_bytes(raw))


def _named_parts(raw: bytes, keys) -> dict:
    """Answer `BODY.PEEK[N]` / `BODY.PEEK[N.MIME]` from `raw`."""
    import email as _email

    msg = _email.message_from_bytes(raw)
    by_number = dict(_walk_numbered(msg))
    out: dict[bytes, bytes] = {}
    for key in keys:
        inner = key[len("BODY.PEEK[") : -1]
        want_mime = inner.endswith(".MIME")
        number = inner[: -len(".MIME")] if want_mime else inner
        part = by_number.get(number)
        if part is None:
            continue
        if want_mime:
            head = "".join(f"{k}: {v}\r\n" for k, v in part.items())
            out[f"BODY[{inner}]".encode()] = head.encode()
        else:
            body = part.get_payload(decode=False) or ""
            out[f"BODY[{inner}]".encode()] = body.encode()
    return out
