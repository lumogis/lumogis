# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""paperless-ngx REST poller (read-only, LUM-281 v0.1).

Uses ``Authorization: Token <token>`` per paperless-ngx API conventions.
"""

from __future__ import annotations

import json
import logging
import os
import random
import time
from dataclasses import dataclass
from typing import Any

import httpx
from services.paperless_credentials import PaperlessConnection

_log = logging.getLogger(__name__)

_MAX_ATTEMPTS = 5
_BASE_DELAY_S = 1.0
_BACKOFF_FACTOR = 2.0
_JITTER_FRAC = 0.25


@dataclass(frozen=True)
class PaperlessDocument:
    """Normalised paperless document row for ingest."""

    id: int
    content: str
    added: str
    #: The document's human title (LUM-724). Paperless always has one — it
    #: defaults to the original filename — but it is only useful here because
    #: `/documents` now lists these rows, and `Path(logical_path).name` would
    #: otherwise render every one of them as a bare integer. Defaulted so a
    #: response without the field degrades to the constructed fallback rather
    #: than dropping the document.
    title: str = ""


class PaperlessPoller:
    """httpx-backed single-page / multi-page fetch for ``/api/documents/``."""

    def __init__(self, connection: PaperlessConnection) -> None:
        self._conn = connection
        self._base = connection.base_url.rstrip("/")

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Token {self._conn.token}",
            "Accept": "application/json",
        }

    def _sleep_backoff(self, attempt: int) -> None:
        raw = _BASE_DELAY_S * (_BACKOFF_FACTOR**attempt)
        jitter = 1.0 + random.uniform(-_JITTER_FRAC, _JITTER_FRAC)
        time.sleep(min(30.0, max(0.05, raw * jitter)))

    def _request_json(self, method: str, path: str, *, params: dict[str, Any]) -> Any:
        url = f"{self._base}{path}"
        last_exc: object | None = None
        for attempt in range(_MAX_ATTEMPTS):
            try:
                with httpx.Client(timeout=httpx.Timeout(60.0)) as client:
                    resp = client.request(
                        method,
                        url,
                        params=params,
                        headers=self._headers(),
                    )
                if resp.status_code in (401, 403):
                    return {"__auth_error__": True, "status": resp.status_code}
                if resp.status_code == 404:
                    # A 404 is not transient. Retrying one costs all five attempts
                    # and ~31s of backoff (1+2+4+8+16) for an answer that will never
                    # change, then reports a `paperless_http_final_failure` ERROR for
                    # a healthy system.
                    #
                    # This is not an edge case: DRF's PageNumberPagination answers
                    # *any* page beyond the last with 404 — not an empty page — and
                    # `_poll_paperless_source` always requests one page past the end,
                    # so every productive poll tick paid it. Confirmed against live
                    # paperless 3.0.4: page 4 of 3 → 404 {"detail":"Invalid page."}
                    # (LUM-309 Step 0, 2026-07-30).
                    return {"__not_found__": True}
                if resp.status_code == 429 or resp.status_code >= 500:
                    last_exc = resp.status_code
                    self._sleep_backoff(attempt)
                    continue
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPError as exc:
                last_exc = exc
                self._sleep_backoff(attempt)
                continue
        _log.error(
            "paperless_http_final_failure type=%s msg=%s",
            type(last_exc).__name__ if last_exc else "None",
            str(last_exc)[:200] if last_exc else "",
        )
        return None

    def fetch_documents_page(
        self,
        *,
        since_cursor: str | None,
        page: int,
    ) -> tuple[list[PaperlessDocument], bool]:
        """Return one page of documents oldest-first; bool = auth failure."""
        page_size = int(os.environ.get("PAPERLESS_POLL_PAGE_SIZE", "25") or "25")
        page_size = max(1, min(100, page_size))
        params: dict[str, Any] = {
            "page": page,
            "page_size": page_size,
            "ordering": "added",
        }
        if since_cursor:
            params["added__gt"] = since_cursor

        data = self._request_json("GET", "/api/documents/", params=params)
        if data is None:
            return [], False
        if isinstance(data, dict) and data.get("__auth_error__"):
            return [], True
        if isinstance(data, dict) and data.get("__not_found__"):
            # The page does not exist, which for a paginated walk means "no more
            # documents" — the caller's `if not docs: break` reads it correctly.
            #
            # But page 1 is different, and the distinction is load-bearing. DRF's
            # `allow_empty_first_page` defaults True, so an empty first page is a
            # 200 with `count: 0` — never a 404. A 404 *here* therefore means the
            # endpoint is wrong (bad base URL, paperless moved, reverse proxy
            # rewriting the path), not that the walk ended.
            #
            # Without this split, LUM-747's fix would silently downgrade a broken
            # connector from a per-tick ERROR to an INFO line: the poll would
            # return "no documents" forever and nothing in the logs would say why.
            # That traded one observability bug for another.
            if page <= 1:
                _log.warning(
                    # %r, not %s: the base URL is user-supplied and
                    # `validate_outbound_connector_base_url` only rejects *leading and
                    # trailing* whitespace, so an embedded newline in the path reaches
                    # here and would forge a log line. repr() escapes it. Same reason
                    # `feed_monitor` logs `credential_base=%r`.
                    "paperless_documents_endpoint_not_found base=%r — "
                    "check the source URL; an empty archive returns 200, not 404",
                    self._base,
                )
            else:
                _log.info("paperless_page_past_end page=%s", page)
            return [], False
        if not isinstance(data, dict):
            _log.warning("paperless unexpected_json_type type=%s", type(data).__name__)
            return [], False

        results = data.get("results")
        if not isinstance(results, list):
            _log.warning("paperless missing_results")
            return [], False

        out: list[PaperlessDocument] = []
        for row in results:
            if not isinstance(row, dict):
                continue
            try:
                rid = int(row.get("id"))
            except (TypeError, ValueError):
                _log.warning("paperless bad_document_id raw=%r", row.get("id"))
                continue
            content = row.get("content")
            if content is None:
                content_s = ""
            elif isinstance(content, str):
                content_s = content
            else:
                content_s = ""
            added = row.get("added")
            added_s = added if isinstance(added, str) else ""
            if not content_s.strip():
                _log.info(
                    "paperless_skip_empty_content doc_id=%s user_content_absent=True",
                    rid,
                )
                continue
            if not added_s:
                _log.warning("paperless_skip_missing_added doc_id=%s", rid)
                continue
            title = row.get("title")
            title_s = title.strip() if isinstance(title, str) else ""
            out.append(PaperlessDocument(id=rid, content=content_s, added=added_s, title=title_s))
        return out, False


# ─── LUM-728 — mailbox-overlap config read ───────────────────────────────────


#: Total deadline shared across BOTH mail-config requests. Not per-request: two
#: sequential 3s calls cannot honour a 3.5s p99, which is what the plan's first
#: draft claimed (critique R1, D1.1).
#: Refuse absurd bodies rather than buffering them on a request path.
_OVERLAP_MAX_BYTES = 8 * 1024 * 1024


def bounded_env_deadline_s(var: str, *, default: float = 5.0, floor: float = 0.5) -> float:
    """Read a deadline from ``var``, clamped to ``floor``, defaulting to ``default``.

    One implementation, because there is now more than one caller
    (``PAPERLESS_OVERLAP_TIMEOUT_S`` here, ``LUMOGIS_CONNECTOR_PROBE_TIMEOUT_S``
    in :mod:`services.connector_probes`). The env vars stay separate — they are
    independent knobs for independent subsystems — but the floor/default/
    junk-tolerance POLICY is one thing, and two copies of it is a change that
    lands in one and misses the other.

    A junk value falls back to ``default`` rather than raising: a typo in an
    operator's env file should not take the process down.
    """
    try:
        return max(floor, float(os.environ.get(var, str(default)) or str(default)))
    except ValueError:
        return default


def _overlap_deadline_s() -> float:
    return bounded_env_deadline_s("PAPERLESS_OVERLAP_TIMEOUT_S")


class PaperlessMailConfigReader:
    """Bounded, single-attempt read of paperless's mail-account/rule config.

    **Deliberately not a method on `PaperlessPoller`.** That class's
    ``_request_json`` retries ``_MAX_ATTEMPTS`` (5) times at 60s each with
    1→2→4→8→16s backoff — correct for a background document poll, and
    catastrophic here: this runs on the rule-creation request path, where the
    same policy costs **~5.5 minutes** against an unreachable host. The form
    would hang for minutes exactly when paperless is down, which is when the
    warning is least useful.

    (A 404 used to cost ~31s here too. LUM-747 made it terminal and immediate,
    so that particular case no longer argues for this class — the unreachable-host
    cost, which is the worse one, still does.)

    So: one attempt per endpoint, one **shared** deadline across both, no retry,
    no backoff. ``_MAX_ATTEMPTS`` is left alone because the document poll
    depends on it.

    **SSRF.** The constructor takes a ``PaperlessConnection`` and never a raw
    URL. That connection can only come from ``paperless_credentials.load_connection``,
    which runs ``_validate_outbound_and_token``. There is deliberately no path by
    which a request-supplied value becomes an outbound host.
    """

    def __init__(
        self,
        connection: PaperlessConnection,
        *,
        deadline_s: float | None = None,
    ) -> None:
        self._conn = connection
        self._base = connection.base_url.rstrip("/")
        self._deadline_s = _overlap_deadline_s() if deadline_s is None else deadline_s

    def _headers(self) -> dict[str, str]:
        # Same shape as PaperlessPoller._headers(). A wrong header is a 401,
        # which maps to `auth`, which fails open — the gate would never fire,
        # forever, and every fake-based test would still pass. Pinned by a test
        # that asserts against PaperlessPoller._headers() itself.
        return {
            "Authorization": f"Token {self._conn.token}",
            "Accept": "application/json",
        }

    def fetch_mail_config(self) -> tuple[list[dict], list[dict], str]:
        """``(mail_accounts, mail_rules, status)``; status ∈ ok|auth|unavailable.

        **When ``status != "ok"`` both lists are empty — never partial.** A
        partial read is worse than no read: accounts fetched and rules timed out
        would let the matcher return a confident "no overlap" from half the data,
        a false negative wearing ``ok``'s clothes.
        """
        start = time.monotonic()

        def _remaining() -> float:
            return self._deadline_s - (time.monotonic() - start)

        accounts, status = self._get_list("/api/mail_accounts/", _remaining())
        if status != "ok":
            return [], [], status
        if _remaining() <= 0:
            return [], [], "unavailable"
        rules, status = self._get_list("/api/mail_rules/", _remaining())
        if status != "ok":
            return [], [], status
        return accounts, rules, "ok"

    def _get_list(self, path: str, budget: float) -> tuple[list[dict], str]:
        if budget <= 0:
            return [], "unavailable"
        # `httpx.Timeout(budget)` bounds each PHASE (connect / read / write /
        # pool), not the whole call: a host that drips the body one byte at a
        # time resets the read timeout forever and never trips it. On a request
        # path that is the exact hang this class exists to prevent, so the body
        # is streamed against a wall-clock deadline as well.
        deadline = time.monotonic() + budget
        try:
            with httpx.Client(timeout=httpx.Timeout(budget)) as client:
                with client.stream(
                    "GET",
                    f"{self._base}{path}",
                    params={"page_size": 100},
                    headers=self._headers(),
                ) as resp:
                    if resp.status_code in (401, 403):
                        return [], "auth"
                    if resp.status_code != 200:
                        # 404 = a paperless old enough to lack the mail
                        # endpoints. Same bucket as unreachable: we do not know,
                        # so we do not claim.
                        return [], "unavailable"
                    chunks: list[bytes] = []
                    size = 0
                    for chunk in resp.iter_bytes():
                        if time.monotonic() > deadline:
                            _log.warning("paperless_overlap_deadline path=%s", path)
                            return [], "unavailable"
                        size += len(chunk)
                        if size > _OVERLAP_MAX_BYTES:
                            _log.warning("paperless_overlap_oversize path=%s", path)
                            return [], "unavailable"
                        chunks.append(chunk)
                    body = b"".join(chunks)
        except httpx.HTTPError:
            return [], "unavailable"
        try:
            data = json.loads(body)
        except ValueError:
            return [], "unavailable"
        if not isinstance(data, dict):
            return [], "unavailable"
        # Parse permissively. Paperless's StandardPagination envelope carries an
        # extra `all` key beyond DRF's {count,next,previous,results}; a strict
        # envelope check would turn a paperless version bump into a permanent
        # fail-open.
        results = data.get("results")
        if not isinstance(results, list):
            return [], "unavailable"
        if data.get("next"):
            # Truncation is NOT a partial `ok`. `fetch_mail_config` promises that
            # `ok` means the lists are complete, and a matching rule sitting on
            # page 2 would otherwise produce a confident "no overlap" — the
            # silent false negative this whole feature exists to prevent. We do
            # not know, so we say so.
            _log.warning(
                "paperless_overlap_truncated path=%s count=%s returned=%d",
                path,
                data.get("count"),
                len(results),
            )
            return [], "unavailable"
        return [r for r in results if isinstance(r, dict)], "ok"


# ─── LUM-834 — connector test-connection probe ───────────────────────────────


class PaperlessAuthProbe:
    """One bounded, read-only, authenticated GET: "do these credentials work?"

    A sibling of :class:`PaperlessMailConfigReader` rather than a second HTTP
    client, and deliberately lighter than it: the test-connection route wants
    "can I authenticate?", not paperless's mail configuration. Same
    single-attempt, no-retry, no-backoff discipline for the same reason — this
    runs on a settings-page click, where ``PaperlessPoller``'s five attempts at
    60s would cost minutes against a host that is down.

    **The response body is never read.** Only the status line is needed, so a
    server returning an enormous 200 costs nothing here and the byte-cap and
    wall-clock body loop that :class:`PaperlessMailConfigReader` needs do not
    apply.

    **SSRF.** Takes a ``PaperlessConnection`` and never a raw URL, exactly as
    its sibling does — the connection can only come from
    ``paperless_credentials.load_connection``, which runs the outbound
    validation. There is deliberately no path by which a request-supplied value
    becomes an outbound host.

    **Redirects are refused, explicitly rather than by default.**
    ``services/outbound_signal_fetch.py`` already writes down why this matters:
    validating an entry URL and then following redirects is *a partial fix that
    reads like a complete one* — the first hop is checked and every later hop is
    not, so a user-configured ``https://harmless.example/r`` redirecting to
    ``http://169.254.169.254/`` walks straight past the guard. That module needs
    hop-by-hop re-validation because feeds legitimately redirect. A probe does
    not: the user typed their own paperless address, and if it 3xx's, "I could
    not verify this" is the honest answer. Stated as a keyword because relying on
    httpx's current default would make this a security property owned by a
    dependency's release notes.

    **Read-only by construction.** The verb is a literal ``"GET"``. A probe that
    could mutate is the thing "lightweight probe" gets quietly extended into.
    """

    #: The DRF API root. Authenticated (paperless-ngx defaults to
    #: ``IsAuthenticated``), tiny, and present in every version that has an API
    #: at all — which a version-specific endpoint like ``/api/ui_settings/``
    #: is not, and a 404 from one of those would read as "unreachable" for a
    #: credential that authenticates perfectly.
    _PATH = "/api/"

    def __init__(
        self,
        connection: PaperlessConnection,
        *,
        deadline_s: float,
    ) -> None:
        self._conn = connection
        self._base = connection.base_url.rstrip("/")
        self._deadline_s = deadline_s

    def _headers(self) -> dict[str, str]:
        # Same shape as PaperlessPoller._headers(). A wrong header is a 401,
        # which this class reports as `auth` — so a header drift would tell
        # every user their token was rejected while the token was fine.
        return {
            "Authorization": f"Token {self._conn.token}",
            "Accept": "application/json",
        }

    def check(self) -> str:
        """``"ok"`` | ``"auth"`` | ``"unavailable"``.

        ``auth`` and ``unavailable`` are kept apart because they send the user
        to fix different things: a rejected token is not an unreachable host,
        and conflating them is advice pointed at the wrong problem.
        """
        if self._deadline_s <= 0:
            return "unavailable"
        # HALVED, and that is the whole point. `httpx.Timeout(x)` bounds each
        # PHASE independently — connect, read, write, pool — not the call, which
        # `PaperlessMailConfigReader._get_list` already documents. Passing the
        # full budget would let a host connect slowly (nearly the budget) and
        # then answer slowly (nearly the budget again) and hold a settings-page
        # click for ~2× the deadline the route advertises. This class reads no
        # body, so connect and read are the only two phases that can elapse:
        # capping each at half makes the advertised ceiling an actual one,
        # without the wall-clock streaming loop its sibling needs.
        per_phase = self._deadline_s / 2
        try:
            with httpx.Client(
                timeout=httpx.Timeout(per_phase),
                follow_redirects=False,
            ) as client:
                with client.stream(
                    "GET",
                    f"{self._base}{self._PATH}",
                    headers=self._headers(),
                ) as resp:
                    if resp.status_code in (401, 403):
                        return "auth"
                    if resp.status_code != 200:
                        return "unavailable"
                    return "ok"
        except httpx.HTTPError:
            return "unavailable"
