# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Signal infrastructure routes.

POST /sources       — Two-step stateless source addition:
                      confirm=false: detect feed type + preview (no save)
                      confirm=true:  re-detect, save, start polling
GET  /sources       — Active sources with scheduler poll status
GET  /signals       — Recent signals, filterable
PUT  /profile       — Upsert relevance profile
GET  /profile       — Return current profile
POST /feedback      — Record explicit or implicit feedback
"""

import json
import logging
import os
import uuid
from datetime import date
from datetime import datetime
from typing import Literal
from typing import Optional

from auth import UserContext
from authz import require_user
from csrf import require_same_origin
from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Query
from fastapi import Response
from models.email_setup import _MAILBOX_LABEL_RE
from models.email_setup import EmailProbeRequest
from models.email_setup import EmailProbeResponse
from models.email_setup import EmailSmtpResponse
from models.email_setup import EmailSmtpUpdate
from models.email_setup import OAuthDevicePollRequest
from models.email_setup import OAuthDevicePollResponse
from models.email_setup import OAuthDeviceStartRequest
from models.email_setup import OAuthDeviceStartResponse
from models.email_setup import ProbeFolder
from models.signals import SourceConfig
from pydantic import BaseModel
from pydantic import Field
from pydantic import model_validator
from rate_limit import RequestRateLimiter
from services.imap_source_url import normalize_imap_host
from services.outbound_http_url import validate_outbound_connector_base_url
from services.signal_source_detection import detect_signal_source
from visibility import visible_filter

import config
from services import email_taxonomy
from services import outbound_signal_fetch
from services import relevance_learning
from services import signal_triage

_log = logging.getLogger(__name__)

router = APIRouter(tags=["signals"])

# LUM-643: cap for the ``triaged=set`` fetch — its own limit, independent of the
# recency window's ``le=100``. Pagination of the triaged set is a follow-up.
_TRIAGED_SET_LIMIT = 200


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class SourceRequest(BaseModel):
    url: str
    confirm: bool = False
    name: Optional[str] = None
    category: Optional[str] = "general"
    # LUM-837: the floor was only ever enforced by `max(60, ...)` in the
    # scheduler, so this route accepted and STORED `poll_interval=5` — a number
    # nothing honoured, which a list rendering the column would have shown back
    # to the user as fact. Narrowing a shipped route's accepted input is
    # deliberate and safe here: it has never had a client, so nothing can be
    # relying on sub-60 being taken.
    poll_interval: Optional[int] = Field(3600, ge=60)
    source_type: Literal["paperless", "imap"] | None = None
    # LUM-729 — the mirror arming event: connecting paperless while a mailbox is
    # already folder-sorting starves it exactly as turning folder-sort on would.
    acknowledge_external_consumer: bool = False
    # LUM-835 — a SECOND, separately named consent, deliberately not a reuse of
    # the field above. The two arms warn about opposite harms (paperless being
    # starved vs. documents being filed twice), so one shared boolean would mean
    # accepting the starvation dialogue silently accepts the duplication one —
    # and the user would never learn about the harm they were not shown. The name
    # is taken verbatim from `create_attachment_rule`, which already carries this
    # exact consent, so the same consent is called the same thing on both routes.
    #
    # LUM-743's companion `acknowledged_source_id` is deliberately NOT carried:
    # it exists because a rule names ONE mailbox and a consent for A must not
    # create a rule on B. Connecting paperless is a single act against all of the
    # caller's mailboxes at once, so there is no second subject to misapply the
    # consent to. If this route ever becomes per-mailbox, that changes.
    acknowledge_paperless_overlap: bool = False


class EmailSourceRequest(BaseModel):
    """Register one IMAP mailbox for the calling user (LUM-170).

    Credentials are stored separately via
    ``PUT /me/connector-credentials/imap?instance={source_id}`` — they never
    travel in this body. ``mailbox_label`` is a non-PII label (e.g. ``personal``,
    ``work``) that keeps ``sources.url`` unique per mailbox on the same host.
    """

    host: str
    mailbox_label: str
    name: Optional[str] = None
    port: int = 993
    full_ingest: bool = False
    poll_interval: int = 900
    # LUM-644 import scope — every scope reduces to "a start date -> now". Custom
    # takes a start (and optional end); the backfill is additive + re-runnable.
    import_scope: Literal["just_new", "last_7d", "last_30d", "custom"] = "last_30d"
    import_since: Optional[date] = None
    import_before: Optional[date] = None

    @model_validator(mode="after")
    def _validate_import_scope(self) -> "EmailSourceRequest":
        if self.import_scope == "custom":
            if self.import_since is None:
                raise ValueError("custom import_scope requires import_since")
            if self.import_before is not None and self.import_before < self.import_since:
                raise ValueError("import_before must be >= import_since")
        elif self.import_since is not None or self.import_before is not None:
            raise ValueError("import_since/import_before are only valid for custom scope")
        # Import scopes apply to the metadata poll path only. Full-ingest sources
        # use the separate _poll_email_ingest_source path (no seeding/backfill), so
        # explicit dates there are a misuse; a plain full_ingest create ignores the
        # scope (see create route — import_state stays NULL for full-ingest).
        if self.full_ingest and (self.import_since is not None or self.import_before is not None):
            raise ValueError("import date range is not supported with full_ingest")
        return self


class EmailOrganizeUpdate(BaseModel):
    """LUM-645: enable/disable per-mailbox organization opt-ins. Applied via a
    dedicated route (not create) so the well-tested source-create path is untouched."""

    organize_label: bool = False
    organize_folder: bool = False
    organize_folder_categories: list[str] = Field(default_factory=list)
    apply_to_existing: bool = False
    # LUM-729. Defaulting FALSE is the contract, not a convenience: the gate is
    # only meaningful if an unaware client is refused. What the server can
    # enforce is that the default is refusal; it cannot enforce that a client
    # asked a human first, because the acknowledgement is by construction a claim
    # about something that happened off-machine.
    acknowledge_external_consumer: bool = False

    @model_validator(mode="after")
    def _validate(self) -> "EmailOrganizeUpdate":
        # 'other' (protected catch-all) is never folder-sorted; silently drop it.
        self.organize_folder_categories = [
            c for c in self.organize_folder_categories if c and c != "other"
        ][:64]
        if self.organize_folder_categories and not self.organize_folder:
            raise ValueError("organize_folder_categories require organize_folder=true")
        return self


class EmailOrganizeUndo(BaseModel):
    scope: Literal["label", "folder", "all"] = "all"


class SourceModeUpdate(BaseModel):
    """Partial update of one of the caller's sources.

    LUM-628 shipped this as a mode-only toggle (``full_ingest``, IMAP only).
    LUM-837 widens it into the **general source-edit route**: ``name``,
    ``category`` and ``poll_interval`` apply to every ``source_type``, while
    ``full_ingest`` stays IMAP-only and still ``422``s elsewhere.

    ``url`` is deliberately absent. Changing a source's URL is indistinguishable
    from deleting it and creating another — the poll cursor, the detected
    ``source_type`` and the extraction method all derive from the original, and
    ``signals.source_id`` is a plain TEXT column with no FK, so history would
    silently re-attribute. For paperless it would also be close to decorative:
    the poller reads the *credential's* ``base_url``, not ``sources.url``.

    ``min_length=1`` on the two text fields is load-bearing rather than
    decorative: ``POST /sources`` rescues an empty name via
    ``body.name or _infer_name(...)``, and this route has no such rescue, so
    without the bound the first edit with a blank name produces a source the
    list cannot label.

    ``active`` (LUM-844) pauses or resumes polling for non-IMAP sources; IMAP
    refuses it.
    """

    full_ingest: Optional[bool] = None
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    category: Optional[str] = Field(None, min_length=1, max_length=64)
    poll_interval: Optional[int] = Field(None, ge=60)
    active: Optional[bool] = None

    @model_validator(mode="after")
    def _at_least_one_field(self) -> "SourceModeUpdate":
        """A body naming no field is a client bug, and stays a ``422``.

        LUM-837's plan first proposed turning this into a no-op
        ``{"status": "unchanged"}``. That would have inverted
        ``test_missing_full_ingest_returns_422``, shipped since LUM-628 — and it
        is wrong on the merits too: ``unchanged`` means *"the values you supplied
        already match"*, which an empty body cannot be.
        """
        if (
            self.full_ingest is None
            and self.name is None
            and self.category is None
            and self.poll_interval is None
            and self.active is None
        ):
            raise ValueError("at least one field must be supplied")
        return self


class ProfileRequest(BaseModel):
    tracked_locations: list[str] = []
    tracked_topics: list[str] = []
    tracked_entities: list[str] = []
    tracked_keywords: list[str] = []


class FeedbackRequest(BaseModel):
    item_type: str
    item_id: str
    positive: Optional[bool] = None  # explicit feedback
    event_type: Optional[str] = None  # implicit feedback


# ---------------------------------------------------------------------------
# POST /sources
# ---------------------------------------------------------------------------


def _paperless_overlap_matches(user_id: str) -> list[dict]:
    """Rule-bearing mailboxes a paperless-to-be would also watch (LUM-835).

    Returns `[{source_name, rule_count}]` for **`high`**-confidence matches only.
    `low` never gates — ADR-192 D3: a host-only match is frequent (two mailboxes
    at one provider is an ordinary household), and gating on it trains dismissal
    that then transfers to the `high` gate that mattered.

    **Fails open, silently, by design.** Any failure returns `[]`, so connecting
    paperless is never blocked by the advisory being unavailable. A user who
    cannot connect their document scanner because a warning could not be computed
    would rightly regard that as the product being broken.
    """
    from services import email_attachment_rules as rules_svc
    from services import paperless_overlap

    try:
        # Same helper the `/all` advisory route uses, deliberately: the two
        # surfaces must agree on which mailboxes count, and they only stay in
        # agreement if there is one definition of it.
        mailboxes = rules_svc.rule_bearing_mailboxes(user_id)
        if not mailboxes:
            return []

        # `assume_consumer=True`: the paperless `sources` row is created a few
        # lines below, so `has_external_consumer` is false by construction here.
        results = paperless_overlap.overlap_for_sources(
            user_id, list(mailboxes), assume_consumer=True
        )
        out = []
        for sid, meta in mailboxes.items():
            result = results.get(sid)
            ov = result.overlap if result else None
            if ov is not None and ov.confidence == "high":
                out.append(meta)
        return out
    except Exception:  # noqa: BLE001 - never block a connect on the advisory
        _log.warning("paperless_overlap_connect_check_failed", exc_info=True)
        return []


# ─── LUM-839: per-user creation guards ───────────────────────────────────────
#
# `POST /sources` became `require_user` in LUM-839, so both of these exist
# because the caller population widened, not because anything about the route's
# work changed.

#: Detection performs several outbound fetches per call, and the preview path
#: (`confirm=false`) is deliberately exempt from the source cap — so without
#: this the detect path would have no bound at all. Mirrors
#: `_email_test_limiter` exactly: the same shape of route (require_user,
#: outbound work), the same per-user key, the same window.
_source_detect_limiter = RequestRateLimiter(max_requests=10, window_seconds=60.0)

#: Source types this route creates. Deliberately the same set the web surface
#: lists (`MANAGED_TYPES` in `SourcesView.tsx`) so "what the cap counts" and
#: "what the screen shows" cannot drift apart.
_CAPPED_SOURCE_TYPES = ("paperless", "rss", "page")

_DEFAULT_SOURCE_CAP = 100


def _source_cap() -> int:
    """Max sources one user may create through this route.

    Env-readable because an install that upgrades already over the ceiling would
    otherwise be hard-refused with no way out.
    """
    raw = os.environ.get("LUMOGIS_MAX_SOURCES_PER_USER", "")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return _DEFAULT_SOURCE_CAP
    return value if value > 0 else _DEFAULT_SOURCE_CAP


def _source_count(user_id: str) -> int:
    """Count the caller's rows **of the types this route creates**.

    IMAP mailboxes are excluded (owner decision 2026-08-23). They are created by
    `POST /me/email-sources`, which is uncapped, and they are not shown on
    `/me/sources` — so counting them would refuse a user over rows they cannot
    see and cannot delete from the surface doing the refusing. Every row that
    counts here is one the user can act on.
    """
    ms = config.get_metadata_store()
    # SCOPE-EXEMPT: user_id predicate is the isolation
    row = ms.fetch_one(
        "SELECT COUNT(*) AS n FROM sources WHERE user_id = %s AND source_type = ANY(%s)",
        (user_id, list(_CAPPED_SOURCE_TYPES)),
    )
    return int((row or {}).get("n", 0) or 0)


@router.post("/sources", dependencies=[Depends(require_same_origin)])
def add_or_preview_source(
    body: SourceRequest,
    user: UserContext = Depends(require_user),
):
    """Two-step stateless source flow.

    confirm=false → detect + preview (no DB write).
    confirm=true  → detect + save + schedule polling.
    """
    # LUM-839: before any outbound work. A limiter that refuses *after*
    # detection has already fetched protects nothing.
    if not _source_detect_limiter.check(user.user_id):
        raise HTTPException(status_code=429, detail={"code": "rate_limited"})

    # LUM-839: one cap check, above the type branch, because this function has
    # TWO inserts (paperless below, detect further down) and a check placed
    # beside either one leaves the other uncapped.
    #
    # `body.confirm` is what exempts preview — not the position of this check.
    # Refusing a preview teaches the user nothing; they have not asked to keep
    # anything yet. The limiter above is what bounds preview.
    #
    # Best-effort, not atomic: COUNT-then-INSERT without a transaction means two
    # concurrent confirmed creates at the ceiling can both pass and land one row
    # over. That is accepted — this is a resource guard that already fails open
    # on query error, so a serializable transaction would buy a precision the
    # design does not want and put a lock on a human-initiated path.
    if body.confirm:
        try:
            _at_cap = _source_count(user.user_id) >= _source_cap()
        except Exception as exc:  # noqa: BLE001 - a resource guard must not block a create
            # Exception TYPE only, no `exc_info`. A psycopg error string can
            # carry the failing statement and its parameters, which on this path
            # means a user id in the log for a condition the operator can act on
            # without it. Same posture as `sending_addresses` and
            # `paperless_overlap.username_skip`, and for the same reason.
            _log.warning("source_cap_check_failed reason=%s", type(exc).__name__)
            _at_cap = False
        if _at_cap:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "source_cap_reached",
                    "message": (
                        f"You've reached the limit of {_source_cap()} sources. "
                        "Delete one to add another."
                    ),
                },
            )

    if body.source_type == "paperless":
        try:
            feed_url = _normalize_paperless_base_url(body.url)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        if not body.confirm:
            return {
                "source_type": "paperless",
                "url": feed_url,
                "preview_items": [],
            }

        # LUM-729. Gated on CONFIRM only — the preview writes nothing and cannot
        # starve anything, and refusing it would stop the user seeing what they
        # were about to connect.
        from services import external_consumer_guard as ecg
        from services import paperless_overlap

        if not body.acknowledge_external_consumer and ecg.has_folder_sorting_mailbox(user.user_id):
            raise HTTPException(
                status_code=422, detail=ecg.refusal_detail(ecg.ARM_CONNECT_PAPERLESS)
            )

        # LUM-835 — the other coexistence harm, at the same moment. The starvation
        # arm above asks "will paperless miss mail Lumogis moved?"; this asks
        # "will both file the same attachment?". Separate codes and separate
        # acknowledgements, because they are separate facts and a user who
        # answers one has not been told the other.
        #
        # `assume_consumer=True` because the paperless `sources` row does not
        # exist yet — it is inserted a few lines below — so
        # `has_external_consumer` is false by construction here and the probe
        # would short-circuit to "no paperless" forever without it.
        if not body.acknowledge_paperless_overlap:
            _overlap = _paperless_overlap_matches(user.user_id)
            if _overlap:
                raise HTTPException(
                    status_code=422,
                    detail=paperless_overlap.connect_refusal_detail(_overlap),
                )

        source_id = str(uuid.uuid4())
        source_name = body.name or _infer_name(feed_url)
        source_type = "paperless"
        extraction_method = "paperless_http"

        try:
            ms = config.get_metadata_store()
            ms.execute(
                "INSERT INTO sources "
                "(id, user_id, name, source_type, url, category, active, poll_interval, "
                "extraction_method, css_selector_override) "
                "VALUES (%s, %s, %s, %s, %s, %s, TRUE, %s, %s, NULL)",
                (
                    source_id,
                    user.user_id,
                    source_name,
                    source_type,
                    feed_url,
                    body.category or "general",
                    body.poll_interval or 3600,
                    extraction_method,
                ),
            )
        except Exception as exc:
            _log.error("Failed to save paperless source %s: %s", body.url, exc)
            raise HTTPException(status_code=500, detail=f"Could not save source: {exc}") from exc

        source = SourceConfig(
            id=source_id,
            name=source_name,
            source_type=source_type,
            url=feed_url,
            category=body.category or "general",
            active=True,
            poll_interval=body.poll_interval or 3600,
            extraction_method=extraction_method,
            css_selector_override=None,
            last_polled_at=None,
            last_signal_at=None,
            poll_cursor=None,
            user_id=user.user_id,
        )

        try:
            from signals.feed_monitor import schedule_source

            schedule_source(source)
        except Exception as exc:
            _log.warning("Could not schedule poll job for %s: %s", source_id, exc)

        return {
            "status": "created",
            "source_id": source_id,
            "source_type": source_type,
            "url": feed_url,
            "preview_items": [],
        }

    # LUM-838: refuse a URL outbound policy will not fetch, and say so, rather
    # than letting it fail downstream as "no feed found". The adapters validate
    # every hop they actually fetch (including redirect targets); this is the
    # entry check, and it exists to make the refusal *legible* — the user can
    # act on "that scheme is not allowed", not on a generic detection miss.
    try:
        outbound_signal_fetch.validate_signal_source_url(body.url)
    except outbound_signal_fetch.OutboundURLBlocked as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "source_url_not_allowed", "message": str(exc)},
        ) from exc

    detection = _detect_source(body.url)

    if not body.confirm:
        return {
            "source_type": detection["source_type"],
            "url": detection["feed_url"] or body.url,
            "preview_items": detection["preview_items"],
            # LUM-841. `reachable: false` means the fetch itself failed — DNS,
            # refused, timeout, non-2xx. It is NOT "no feed found": a page that
            # answered with nothing extractable is reachable with zero items and
            # is a perfectly good page source. Both used to look identical here,
            # so a typo'd hostname was reported as "we'll watch this page" and
            # then polled nothing forever.
            #
            # Deliberately advisory, not a refusal: `confirm=true` still creates
            # the source. A host can be briefly down, the user may know better
            # than the probe, and the poller now records `poll_failed` health so
            # a genuinely dead source is visible in the list. The flow tells; it
            # does not block — the same posture as the missing-credential case.
            "reachable": detection["reachable"],
        }

    # confirm=true — save and start polling.
    source_id = str(uuid.uuid4())
    source_name = body.name or _infer_name(body.url)
    feed_url = detection["feed_url"] or body.url
    source_type = detection["source_type"]
    extraction_method = "feedparser" if source_type == "rss" else "trafilatura"

    try:
        ms = config.get_metadata_store()
        ms.execute(
            "INSERT INTO sources "
            "(id, user_id, name, source_type, url, category, active, poll_interval, "
            "extraction_method, css_selector_override) "
            "VALUES (%s, %s, %s, %s, %s, %s, TRUE, %s, %s, NULL)",
            (
                source_id,
                user.user_id,
                source_name,
                source_type,
                feed_url,
                body.category or "general",
                body.poll_interval or 3600,
                extraction_method,
            ),
        )
    except Exception as exc:
        _log.error("Failed to save source %s: %s", body.url, exc)
        raise HTTPException(status_code=500, detail=f"Could not save source: {exc}")

    source = SourceConfig(
        id=source_id,
        name=source_name,
        source_type=source_type,
        url=feed_url,
        category=body.category or "general",
        active=True,
        poll_interval=body.poll_interval or 3600,
        extraction_method=extraction_method,
        css_selector_override=None,
        last_polled_at=None,
        last_signal_at=None,
        poll_cursor=None,
        user_id=user.user_id,
    )

    try:
        from signals.feed_monitor import schedule_source

        schedule_source(source)
    except Exception as exc:
        _log.warning("Could not schedule poll job for %s: %s", source_id, exc)

    return {
        "status": "created",
        "source_id": source_id,
        "source_type": source_type,
        "url": feed_url,
        "preview_items": detection["preview_items"],
    }


# _MAILBOX_LABEL_RE is imported from models.email_setup (LUM-687 — the canonical
# home; keeping it in routes would create a models→routes import cycle).


@router.post("/me/email-sources", status_code=201)
def create_email_source(
    body: EmailSourceRequest,
    user: UserContext = Depends(require_user),
):
    """Register one IMAP mailbox for the calling user (LUM-170).

    Per-user (not admin-gated): email is a personal-mailbox feature. Skips the
    HTTP feed detection that ``POST /sources`` runs (an ``imap://`` url is not
    an HTTP feed). After this returns, the client stores the mailbox's
    credentials via ``PUT /me/connector-credentials/imap?instance={source_id}``.
    """
    if not _MAILBOX_LABEL_RE.match(body.mailbox_label or ""):
        raise HTTPException(
            status_code=422,
            detail={"code": "bad_mailbox_label", "message": "must match [a-z0-9_-]{1,64}"},
        )
    # LUM-781: an empty or whitespace-only host builds `imap://:993/label` — a row
    # that is permanently un-credentialable. Under LUM-773's mailbox-host invariant
    # every credential PUT against it returns 422 `imap_source_url_invalid`, and
    # there is no edit path for `sources.url`, so the mailbox can never complete the
    # two-step credential contract. Rejected at creation, where the user can still
    # fix it, rather than at the credential step where they cannot.
    #
    # Trimmed rather than compared raw, for the same reason LUM-775 trims it in the
    # setup form: a hostname never contains spaces, and a bare `str` type does not
    # reject `"   "`. The trimmed value is what gets stored, so a stray space cannot
    # produce a url whose host differs from what the credential layer later parses
    # back out of it.
    host = (body.host or "").strip()
    if not host:
        raise HTTPException(
            status_code=422,
            detail={"code": "bad_host", "message": "host must not be empty"},
        )
    if not (1 <= body.port <= 65535):
        raise HTTPException(status_code=422, detail={"code": "bad_port"})

    source_id = str(uuid.uuid4())
    # LUM-782: store the IDNA A-label so sources.url carries the form an IMAP
    # client connects with; an invalid host is refused here, where it is fixable.
    host = normalize_imap_host(host)
    if host is None:
        raise HTTPException(
            status_code=422,
            detail={"code": "bad_host", "message": "host is not a valid hostname"},
        )
    url = f"imap://{host}:{body.port}/{body.mailbox_label}"
    extraction_method = "imap_full_ingest" if body.full_ingest else "imap_metadata"
    source_name = body.name or f"Email ({body.mailbox_label})"

    # Import scopes apply to the metadata poll path only. Full-ingest sources use the
    # separate ingest path, so leave their import_state NULL (no seeding/backfill).
    if body.full_ingest:
        import_scope = import_since = import_before = import_state = None
    else:
        import_scope, import_since, import_before = (
            body.import_scope,
            body.import_since,
            body.import_before,
        )
        import_state = "pending"

    try:
        ms = config.get_metadata_store()
        ms.execute(
            "INSERT INTO sources "
            "(id, user_id, name, source_type, url, category, active, poll_interval, "
            "extraction_method, css_selector_override, import_scope, import_since, "
            "import_before, import_state) "
            "VALUES (%s, %s, %s, 'imap', %s, 'email', TRUE, %s, %s, NULL, %s, %s, %s, %s)",
            (
                source_id,
                user.user_id,
                source_name,
                url,
                body.poll_interval,
                extraction_method,
                import_scope,
                import_since,
                import_before,
                import_state,
            ),
        )
    except Exception as exc:
        _log.error("Failed to save email source %s: %s", url, exc)
        raise HTTPException(status_code=500, detail=f"Could not save source: {exc}") from exc

    source = _imap_source_config(
        source_id, source_name, url, extraction_method, body.poll_interval, user.user_id, "email"
    )
    try:
        from signals.feed_monitor import schedule_source

        schedule_source(source)
    except Exception as exc:
        _log.warning("Could not schedule poll job for %s: %s", source_id, exc)

    return {
        "status": "created",
        "source_id": source_id,
        "source_type": "imap",
        "url": url,
        "extraction_method": extraction_method,
    }


# LUM-644: pre-save live connection test. Per-user rate-limited (an authenticated
# outbound-connection primitive) + CSRF-guarded (credential-bearing browser POST).
_email_test_limiter = RequestRateLimiter(max_requests=10, window_seconds=60.0)
_EMAIL_TEST_TIMEOUT_SECONDS = 15.0


def _safe_probe_error(exc: Exception) -> str:
    """A non-sensitive, non-credential-bearing reason string for the UI."""
    msg = str(exc)
    return msg[:200] if msg else exc.__class__.__name__


@router.post("/me/email-sources/test", dependencies=[Depends(require_same_origin)])
def test_email_source(
    body: EmailProbeRequest,
    user: UserContext = Depends(require_user),
) -> EmailProbeResponse:
    """Live-test IMAP credentials before saving (LUM-644). Credentials are
    request-scoped only — never persisted, never logged. A failed test is a
    normal ``200 {ok:false}`` outcome, not a 500. Structurally-malformed input is
    a Pydantic 422 (``EmailProbeRequest`` field constraints)."""
    if not _email_test_limiter.check(user.user_id):
        raise HTTPException(status_code=429, detail={"code": "rate_limited"})

    from services import connector_credentials as ccs
    from services import email_credentials
    from services import imap_transport

    try:
        conn = email_credentials._connection_from_payload(body.model_dump())
    except email_credentials.ImapHostBlockedError as exc:  # SSRF block (ValueError subclass)
        return EmailProbeResponse(ok=False, error=_safe_probe_error(exc))
    except ccs.CredentialUnavailable as exc:  # RuntimeError, not ValueError
        return EmailProbeResponse(ok=False, error=_safe_probe_error(exc))
    except ValueError as exc:  # TLS-required / DNS-no-resolve / whitespace — a failed test
        return EmailProbeResponse(ok=False, error=_safe_probe_error(exc))

    try:
        result = imap_transport.probe(
            conn, user_id=user.user_id, timeout=_EMAIL_TEST_TIMEOUT_SECONDS
        )
    except Exception as exc:
        _log.warning("email.test.failed host=%s type=%s", body.host, type(exc).__name__)
        return EmailProbeResponse(ok=False, error=_safe_probe_error(exc))

    return EmailProbeResponse(
        ok=result.ok,
        message_count=result.message_count,
        folders=[ProbeFolder(**f) for f in result.folders],
        capabilities=result.capabilities,
        proxy_unverified=result.proxy_unverified,
    )


# LUM-650 — "let Lumogis send as me" opt-in. Owner-scoped, CSRF-guarded,
# rate-limited (arms authenticated outbound SMTP submission + opens a live
# pre-save connect-test). The compose/reply UX is LUM-652; this is only the
# credential opt-in.
_email_smtp_limiter = RequestRateLimiter(max_requests=10, window_seconds=60.0)

# LUM-759 — ADR 195 health code → (status, API code) for the SMTP opt-in surface.
#
# `config` here means the USER-ENTERED kind — a bad stored port, plaintext to a
# remote host. The OPERATOR kind (no client id configured) is also called
# `config` by ADR 195 and is resolved at the OAuth catch site below, which never
# reaches this table. One name, two owners.
#
# The keys must stay equal to `email_oauth._KINDS`. A test asserts it, so a fifth
# health code fails at edit time rather than as a runtime KeyError.
_SMTP_OPTIN_HTTP: dict[str, tuple[int, str]] = {
    "credential": (422, "imap_reauth_required"),
    "network": (503, "mailbox_unreachable"),
    "egress_blocked": (503, "egress_blocked"),
    "config": (422, "imap_settings_invalid"),
}


def _smtp_optin_failure(source_id: str, exc: Exception, status: int, code: str) -> HTTPException:
    """Log, then map (LUM-759).

    Never carries ``str(exc)`` to the client: a credential-layer message can name
    a host or a payload field, and since LUM-759 this response is *rendered* by
    the setup form. The client gets a fixed code from a closed set.
    """
    _log.warning(
        "email.smtp.optin.imap_load_failed source=%s type=%s code=%s",
        source_id,
        type(exc).__name__,
        code,
    )
    return HTTPException(status_code=status, detail={"error": code, "code": code})


@router.put("/me/email-sources/{source_id}/smtp", dependencies=[Depends(require_same_origin)])
def set_email_smtp(
    source_id: str,
    body: EmailSmtpUpdate,
    user: UserContext = Depends(require_user),
) -> EmailSmtpResponse:
    """Enable/disable outbound send for one owned mailbox (LUM-650).

    ``enabled=false`` removes the ``smtp`` credential row (independent send
    revocation; the IMAP receive credential is untouched). On enable: the From
    address is always the mailbox's authenticated account (never client-set);
    the app-password reuses the IMAP one by default; host/port come from the
    provider registry, except an "Other" mailbox which must supply them (422).
    A live pre-save connect-test fails a bad sending password loudly WITHOUT
    persisting; an egress-contained deployment soft-saves (``tested=false``)."""
    if not _email_smtp_limiter.check(user.user_id):
        raise HTTPException(status_code=429, detail={"code": "rate_limited"})

    owned = _owned_imap_source_id(user.user_id, source_id)
    if not owned:
        raise HTTPException(status_code=404, detail={"error": "not_found"})

    from connectors.registry import SMTP
    from services.email_providers import provider_for_host

    from services import connector_credentials as ccs
    from services import email_credentials
    from services import email_oauth

    if not body.enabled:
        # actor is a fixed audit tier literal (self|system|admin:<id>), NOT the
        # user_id — a route-context write is "self" (LUM-694).
        ccs.delete_payload(user.user_id, SMTP, actor="self", instance=source_id)
        return EmailSmtpResponse(ok=True, enabled=False, tested=False)

    # The mailbox's IMAP row supplies the authenticated account (From = username)
    # and, on the reuse path, the sending password; it also anchors provider
    # host/port lookup. No IMAP creds → nothing to send as.
    # LUM-759 — `load_connection` documents four raise types and, since LUM-686,
    # makes an outbound token-refresh POST for an xoauth2 mailbox. It used to be
    # wrapped in `except Exception` → 422 imap_not_configured `from None`, which
    # reported a decrypt failure, an SSRF-policy block and any internal bug as
    # "your mailbox isn't configured", with no log line and no cause chain.
    #
    # ConnectorNotConfigured's position is FREE — it is a LookupError, disjoint
    # from the two below. THE ORDER OF THE NEXT TWO IS NOT: OAuthRefreshError is
    # a CredentialUnavailable, so reversing them makes it unreachable and every
    # OAuth failure answers 503 credential_unavailable. Silently.
    try:
        imap = email_credentials.load_connection(user.user_id, instance=source_id)
    except ccs.ConnectorNotConfigured as exc:
        raise _smtp_optin_failure(source_id, exc, 422, "imap_not_configured") from exc
    except email_oauth.OAuthRefreshError as exc:
        # `config` from a refresh failure is an OPERATOR problem (no client id
        # configured), not settings the user entered — so it does not go through
        # _SMTP_OPTIN_HTTP, which reads `config` as the user-entered kind. Same
        # code, two owners; see ADR LUM-759 and the LUM-760 copy follow-up.
        if exc.kind == "config":
            raise _smtp_optin_failure(source_id, exc, 503, "oauth_unavailable") from exc
        status, code = _SMTP_OPTIN_HTTP[exc.kind]
        raise _smtp_optin_failure(source_id, exc, status, code) from exc
    except ccs.CredentialUnavailable as exc:
        # NOT through the classifier. Its default is `credential`, which on this
        # surface maps to "reconnect your mailbox" — and reconnecting does not fix
        # a decrypt failure or a malformed stored payload. Those are server-side.
        raise _smtp_optin_failure(source_id, exc, 503, "credential_unavailable") from exc
    except ValueError as exc:
        # The ONE branch the shared classifier serves: the Blocked / Unresolved /
        # other split LUM-633 added and that ADR 195 requires both consumers to
        # agree on. Anything undocumented is NOT caught — it reaches FastAPI as a
        # 500 with a traceback, which alerting can see.
        status, code = _SMTP_OPTIN_HTTP[email_credentials.classify_credential_failure(exc)]
        raise _smtp_optin_failure(source_id, exc, status, code) from exc

    provider = provider_for_host(imap.host)

    # LUM-771 — discriminate on the dataclass's own `auth_type`, NOT on the token
    # being present: a token can outlive an auth mode, and a presence check silently
    # means "app password" for any future path that builds the connection before
    # minting one. Used twice below (the standalone-password refusal, and the verify).
    is_oauth = imap.auth_type == "xoauth2"

    payload: dict = {"reuse_imap": bool(body.reuse_imap_password)}
    if not body.reuse_imap_password:
        # LUM-772 — an xoauth2 mailbox has NO separate submission password.
        # `load_smtp_connection` returns early with the OAuth token for one of these
        # and ignores any stored `app_password` (email_credentials.py:343-364), so
        # accepting one persisted an encrypted secret that nothing would ever read
        # and that no revocation flow would ever clear. REFUSED rather than silently
        # dropped: this route's whole lane exists because it reported the wrong cause,
        # and discarding a password the user typed without saying so is that same
        # failure in a new place. No shipped client sends this — the OAuth device flow
        # enables SMTP server-side and the setup form's opt-in is app-password only.
        if is_oauth:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "smtp_password_not_applicable",
                    "code": "smtp_password_not_applicable",
                },
            )
        if not body.app_password:
            raise HTTPException(
                status_code=422,
                detail={"error": "app_password_required", "code": "app_password_required"},
            )
        payload["app_password"] = body.app_password

    if provider is None:
        # "Other" mailbox — no registry entry, so a submission host is required.
        if not body.smtp_host or not body.smtp_port:
            raise HTTPException(
                status_code=422,
                detail={"error": "smtp_host_required", "code": "smtp_host_required"},
            )
        payload["smtp_host"] = body.smtp_host
        payload["smtp_port"] = int(body.smtp_port)
        host, port = body.smtp_host, int(body.smtp_port)
    else:
        host, port = provider.smtp_host, provider.smtp_port

    password = imap.password if body.reuse_imap_password else (body.app_password or "")

    # Live pre-save connect-test (mirror POST …/test), via the service layer so this
    # route imports no adapters (architecture guard). A real auth/connect failure
    # must NOT persist a broken opt-in; an egress block (containment) cannot verify,
    # so soft-save with tested=false.
    from services import smtp_transport

    # LUM-771 — the verify has accepted auth_type/access_token since LUM-693 and
    # the OAuth device-flow route below passes them; THIS caller did not, so an
    # xoauth2 mailbox (whose `password` is deliberately None) authenticated with an
    # empty app password and could never be re-enabled. `is_oauth` is resolved above.
    ok_to_persist, tested, error_code = smtp_transport.verify_smtp_connection(
        host=host,
        port=port,
        username=imap.username,
        app_password=password or "",
        user_id=user.user_id,
        auth_type="xoauth2" if is_oauth else "app_password",
        access_token=imap.oauth_access_token if is_oauth else None,
    )
    # An OAuth mailbox has NO password to correct, so reporting the provider's
    # auth refusal as `smtp_auth` sends the user hunting for a password field.
    # Narrowed to one code, and ONLY for smtp_auth — `smtp_connect` (server
    # unreachable) must stay itself; `egress_blocked` never reaches here at all
    # (it returns ok_to_persist=True with error_code None — the soft-save).
    if error_code == "smtp_auth" and is_oauth:
        error_code = "smtp_oauth_denied"
    if not ok_to_persist:
        _log.warning("email.smtp.test.failed source=%s code=%s", source_id, error_code)
        return EmailSmtpResponse(ok=False, enabled=False, tested=True, error=error_code)

    # actor="self" (route-context write) — a raw user_id fails the credential
    # store's actor validation (self|system|admin:<id>) (LUM-694).
    ccs.put_payload(user.user_id, SMTP, payload, actor="self", instance=source_id)
    return EmailSmtpResponse(ok=True, enabled=True, tested=tested)


# LUM-687 (674b) — OAuth mailbox connect via the Device Authorization Grant
# (RFC 8628). The browser polls; Core is the RFC 8628 client. START begins the
# flow; POLL forwards ≤1 provider token POST per browser poll and, on authorize,
# LIVE-VERIFIES IMAP then persists the xoauth2 credential + enables SMTP + creates
# the sources row (credential-first; soft-save on a proxied deployment). The
# provider device_code + minted refresh_token never reach the browser.
_oauth_start_limiter = RequestRateLimiter(max_requests=10, window_seconds=60.0)
# POLL is cheap (≤1 provider POST per interval) and runs at the ~5s device cadence
# (~12/min) for up to ~15min — so it needs generous headroom vs START.
_oauth_poll_limiter = RequestRateLimiter(max_requests=60, window_seconds=60.0)


def _resolve_oauth_provider(provider: str):
    """Resolve the ``{provider}`` path param (a registry id, e.g. "outlook") to a
    ProviderInfo that supports OAuth with a configured client_id, else raise."""
    from services import email_oauth
    from services import email_providers

    p = email_providers.get_provider(provider)
    if p is None or not p.supports_oauth:
        raise HTTPException(
            status_code=422,
            detail={"code": "provider_no_oauth", "message": "unknown OAuth provider"},
        )
    if not email_oauth.has_oauth_provider(p.oauth_provider):
        raise HTTPException(
            status_code=503,
            detail={"code": "oauth_unavailable", "message": "OAuth is not configured"},
        )
    return p


@router.post(
    "/me/email-sources/oauth/{provider}/device/start",
    dependencies=[Depends(require_same_origin)],
)
def start_oauth_device(
    provider: str,
    body: OAuthDeviceStartRequest,
    user: UserContext = Depends(require_user),
) -> OAuthDeviceStartResponse:
    if not _oauth_start_limiter.check(user.user_id):
        raise HTTPException(status_code=429, detail={"code": "rate_limited"})
    p = _resolve_oauth_provider(provider)
    from services import email_oauth

    try:
        flow = email_oauth.start_device_flow(
            user.user_id,
            provider=p.oauth_provider,
            email=body.email,
            mailbox_label=body.mailbox_label,
            host=p.imap_host,
            port=p.imap_port,
            smtp_host=p.smtp_host,
            smtp_port=p.smtp_port,
            import_scope=body.import_scope,
            import_since=body.import_since,
            import_before=body.import_before,
        )
    except email_oauth.OAuthDeviceError as exc:
        _log.warning("oauth.device.start_failed provider=%s reason=%s", provider, exc)
        raise HTTPException(
            status_code=502,
            detail={"code": "oauth_start_failed", "message": "couldn't start sign-in — try again"},
        ) from exc
    return OAuthDeviceStartResponse(
        flow_id=flow.flow_id,
        user_code=flow.user_code,
        verification_uri=flow.verification_uri,
        verification_uri_complete=flow.verification_uri_complete,
        interval=flow.interval,
        expires_in=flow.expires_in,
    )


@router.post(
    "/me/email-sources/oauth/{provider}/device/poll",
    dependencies=[Depends(require_same_origin)],
)
def poll_oauth_device(
    provider: str,
    body: OAuthDevicePollRequest,
    user: UserContext = Depends(require_user),
) -> OAuthDevicePollResponse:
    if not _oauth_poll_limiter.check(user.user_id):
        # Treat rate-limit as transient — the panel keeps polling.
        raise HTTPException(status_code=429, detail={"code": "rate_limited"})
    _resolve_oauth_provider(provider)  # 422/503 gate (also on poll)
    from connectors.registry import IMAP
    from connectors.registry import SMTP
    from services.email_credentials import ImapConnection

    from services import connector_credentials as ccs
    from services import email_oauth
    from services import imap_transport

    try:
        poll = email_oauth.poll_device_flow(user.user_id, body.flow_id)
    except email_oauth.OAuthDeviceError as exc:
        _log.warning("oauth.device.poll_failed provider=%s reason=%s", provider, exc)
        return OAuthDevicePollResponse(status="error", error="sign-in failed — try again")

    if poll.status == "not_found":
        return OAuthDevicePollResponse(status="expired")  # flow gone → restart
    if poll.status in ("pending", "expired", "denied"):
        return OAuthDevicePollResponse(status=poll.status)
    # poll.status == "authorized": live-verify, then persist. The flow is kept
    # in_progress until we discard_flow() below (concurrent polls → pending).
    flow = email_oauth.get_flow(user.user_id, body.flow_id)
    if flow is None:  # raced with expiry/discard
        return OAuthDevicePollResponse(status="expired")

    try:
        conn = ImapConnection(
            host=flow["host"],
            port=flow["port"],
            username=flow["email"],
            use_ssl=True,
            auth_type="xoauth2",
            password=None,
            oauth_access_token=poll.access_token,
        )
        result = imap_transport.probe(conn, user_id=user.user_id)
        if not result.ok and not result.proxy_unverified:
            email_oauth.discard_flow(body.flow_id)
            return OAuthDevicePollResponse(
                status="error",
                error="couldn't verify the mailbox — the granted permission may be insufficient",
            )
        verified = bool(result.ok)  # False on the proxy soft-save branch

        # LUM-693 — best-effort xoauth2 SMTP connect-test. Non-blocking: SMTP send
        # is auto-enabled on the OAuth path (reuses the already IMAP-verified
        # token+scope, unlike the app-password PUT .../smtp opt-in) — a failure
        # here must not stop the IMAP-verified mailbox from connecting; it is
        # only logged (post-connect health surfacing to the UI is LUM-690).
        from services import smtp_transport

        _smtp_ok, _smtp_tested, _smtp_error_code = smtp_transport.verify_smtp_connection(
            host=flow["smtp_host"],
            port=flow["smtp_port"],
            username=flow["email"],
            app_password="",
            user_id=user.user_id,
            auth_type="xoauth2",
            access_token=poll.access_token,
        )
        if _smtp_tested and not _smtp_ok:
            _log.warning(
                "oauth.device.smtp_verify_failed mailbox_label=%s code=%s",
                flow["mailbox_label"],
                _smtp_error_code,
            )

        source_id = str(uuid.uuid4())
        url = f"imap://{flow['host']}:{flow['port']}/{flow['mailbox_label']}"
        imap_payload = {
            "host": flow["host"],
            "port": flow["port"],
            "username": flow["email"],
            "use_ssl": True,
            "auth_type": "xoauth2",
            "provider": flow["provider"],
            "refresh_token": poll.refresh_token,
        }
        if poll.access_token:
            imap_payload["access_token"] = poll.access_token
        if poll.expiry:
            imap_payload["expiry"] = poll.expiry
        smtp_payload = {
            "reuse_imap": True,
            "smtp_host": flow["smtp_host"],
            "smtp_port": flow["smtp_port"],
        }
        # Credential-FIRST, sources-row LAST (see the plan): an orphan credential is
        # inert for polling; a credential-write failure leaves no sources row.
        ccs.put_payload(user.user_id, IMAP, imap_payload, actor="self", instance=source_id)
        ccs.put_payload(user.user_id, SMTP, smtp_payload, actor="self", instance=source_id)
        try:
            ms = config.get_metadata_store()
            ms.execute(
                "INSERT INTO sources "
                "(id, user_id, name, source_type, url, category, active, poll_interval, "
                "extraction_method, css_selector_override, import_scope, import_since, "
                "import_before, import_state) "
                "VALUES (%s, %s, %s, 'imap', %s, 'email', TRUE, %s, %s, NULL, %s, %s, %s, %s)",
                (
                    # LUM-691 — import scope/dates come from the flow (set at
                    # start_oauth_device from OAuthDeviceStartRequest), same triple
                    # the app-password create_email_source path stores.
                    source_id,
                    user.user_id,
                    f"Email ({flow['mailbox_label']})",
                    url,
                    900,
                    "imap_metadata",
                    flow["import_scope"],
                    flow["import_since"],
                    flow["import_before"],
                    "pending",
                ),
            )
        except HTTPException:
            raise
        except Exception as exc:
            # Compensating delete — an orphan credential is visible to the egress
            # ACL / credential UI (benign, but clean it up) — then a clean 500.
            for connector in (IMAP, SMTP):
                try:
                    ccs.delete_payload(user.user_id, connector, actor="self", instance=source_id)
                except Exception:  # noqa: BLE001 - best-effort
                    pass
            _log.error(
                "oauth.device.persist_failed source_id=%s type=%s",
                source_id,
                type(exc).__name__,
            )
            raise HTTPException(status_code=500, detail={"code": "persist_failed"}) from exc
    finally:
        email_oauth.discard_flow(body.flow_id)

    try:
        source = _imap_source_config(
            source_id,
            f"Email ({flow['mailbox_label']})",
            url,
            "imap_metadata",
            900,
            user.user_id,
            "email",
        )
        from signals.feed_monitor import schedule_source

        schedule_source(source)
    except Exception as exc:  # noqa: BLE001 - non-fatal (mirrors create_email_source)
        _log.warning("Could not schedule poll job for %s: %s", source_id, exc)

    return OAuthDevicePollResponse(status="connected", source_id=source_id, verified=verified)


# LUM-645 — opt-in mailbox organization. Owner-scoped, CSRF-guarded, rate-limited
# (each can arm many authenticated outbound IMAP writes). The IMAP work runs on the
# poll-tick organize sweep — these routes only set state and return 202.
_email_organize_limiter = RequestRateLimiter(max_requests=20, window_seconds=60.0)


def _owned_imap_source_id(user_id: str, source_id: str) -> Optional[str]:
    ms = config.get_metadata_store()
    row = ms.fetch_one(
        "SELECT id::text AS id FROM sources "
        "WHERE id::text = %s AND user_id = %s AND source_type = 'imap'",
        (source_id, user_id),
    )
    return row["id"] if row else None


def _owned_imap_source_organize(user_id: str, source_id: str) -> Optional[dict]:
    """Ownership check that also returns the CURRENT folder-sort opt-in.

    A separate reader rather than widening `_owned_imap_source_id`, which nine
    other routes call and none of them need this column. LUM-729's gate fires on
    the OFF→ON transition, so it needs the value *before* the write — and getting
    it in the same round trip as the ownership check keeps the guard at two cheap
    reads on a rate-limited config path.
    """
    ms = config.get_metadata_store()
    return ms.fetch_one(
        "SELECT id::text AS id, COALESCE(organize_folder_enabled, FALSE) AS folder_on "
        "FROM sources WHERE id::text = %s AND user_id = %s AND source_type = 'imap'",
        (source_id, user_id),
    )


@router.patch(
    "/me/email-sources/{source_id}/organize",
    status_code=202,
    dependencies=[Depends(require_same_origin)],
)
def set_email_organize(
    source_id: str,
    body: EmailOrganizeUpdate,
    user: UserContext = Depends(require_user),
):
    if not _email_organize_limiter.check(user.user_id):
        raise HTTPException(status_code=429, detail={"code": "rate_limited"})
    owned = _owned_imap_source_organize(user.user_id, source_id)
    if owned is None:
        raise HTTPException(status_code=404, detail={"code": "not_found"})
    from services import email_organize
    from services import external_consumer_guard as ecg

    # LUM-729: moving classified mail out of INBOX starves a paperless mail rule
    # watching INBOX, silently on both sides. Gate the two arming events — never
    # the value, or a user editing their category list is tolled on every save.
    arming = ecg.organize_arming(
        currently_on=bool(owned.get("folder_on")),
        requested_on=body.organize_folder,
        apply_to_existing=body.apply_to_existing,
    )
    if (
        arming is not None
        and not body.acknowledge_external_consumer
        and ecg.has_external_consumer(user.user_id)
    ):
        raise HTTPException(status_code=422, detail=ecg.refusal_detail(arming))

    ms = config.get_metadata_store()
    # organize_since: NOW() on first enable (go-forward only); epoch if applying to
    # existing mail (widens the standing reconcile sweep over history).
    since_sql = (
        "'epoch'::timestamptz" if body.apply_to_existing else "COALESCE(organize_since, NOW())"
    )
    ms.execute(
        "UPDATE sources SET organize_label_enabled = %s, organize_folder_enabled = %s, "
        "organize_folder_categories = %s::jsonb, "
        f"organize_since = CASE WHEN %s OR %s THEN {since_sql} ELSE organize_since END "
        "WHERE id::text = %s",
        (
            body.organize_label,
            body.organize_folder,
            json.dumps(body.organize_folder_categories),
            body.organize_label,
            body.organize_folder,
            source_id,
        ),
    )
    email_organize.invalidate_opt_in_cache(source_id)
    return {"updated": True, "apply_to_existing": body.apply_to_existing}


@router.post(
    "/me/email-sources/{source_id}/organize/undo",
    status_code=202,
    dependencies=[Depends(require_same_origin)],
)
def undo_email_organize(
    source_id: str,
    body: EmailOrganizeUndo,
    user: UserContext = Depends(require_user),
):
    if not _email_organize_limiter.check(user.user_id):
        raise HTTPException(status_code=429, detail={"code": "rate_limited"})
    if _owned_imap_source_id(user.user_id, source_id) is None:
        raise HTTPException(status_code=404, detail={"code": "not_found"})
    from services import email_organize

    ms = config.get_metadata_store()
    # Disable the opt-ins the undo covers, so the standing reconcile sweep and the
    # go-forward hook STOP re-organizing what undo is reversing (otherwise undo never
    # wins — reversed rows have no active ledger row and would be re-applied next tick).
    disable_label = body.scope in ("label", "all")
    disable_folder = body.scope in ("folder", "all")
    # LUM-729 follow-up (FP-076): a NEW undo gets a full tick budget and clears any
    # previous give-up stamp. Without the reset, an undo that ran to the bound once
    # would leave the counter at its limit and the next one would abandon on its
    # first sweep — the bound would silently become "one attempt, ever".
    ms.execute(
        "UPDATE sources SET organize_undo_scope = %s, organize_undo_ticks = 0, "
        "organize_undo_gave_up_at = NULL, "
        "organize_label_enabled = CASE WHEN %s THEN FALSE ELSE organize_label_enabled END, "
        "organize_folder_enabled = CASE WHEN %s THEN FALSE ELSE organize_folder_enabled END "
        "WHERE id::text = %s",
        (body.scope, disable_label, disable_folder, source_id),
    )
    email_organize.invalidate_opt_in_cache(source_id)
    return {"undo_pending": True, "scope": body.scope}


@router.get("/me/email-sources/{source_id}/organize/log")
def get_email_organize_log(
    source_id: str,
    limit: int = 100,
    offset: int = 0,
    user: UserContext = Depends(require_user),
):
    if _owned_imap_source_id(user.user_id, source_id) is None:
        raise HTTPException(status_code=404, detail={"code": "not_found"})
    ms = config.get_metadata_store()
    limit = max(1, min(int(limit), 200))
    rows = (
        ms.fetch_all(
            "SELECT signal_url, op, target, label_kind, applied_at, reversed_at "
            "FROM email_organize_log WHERE source_id = %s "
            "ORDER BY applied_at DESC LIMIT %s OFFSET %s",
            (source_id, limit, max(0, int(offset))),
        )
        or []
    )
    # LUM-729 follow-up (FP-076). When the undo sweep hits its bound it stops
    # trying, and the user has to be able to see that. Reported here rather than
    # only in a server log — a bounded-but-invisible give-up would trade one
    # silent failure for another, which is the complaint LUM-729 exists about.
    state = ms.fetch_one(
        "SELECT organize_undo_gave_up_at FROM sources WHERE id::text = %s", (source_id,)
    )
    gave_up = (state or {}).get("organize_undo_gave_up_at")
    unreversed = 0
    if gave_up is not None:
        # Counted only when there is something to report — no cost on the
        # ordinary path, which is every request that is not this one.
        cnt = ms.fetch_one(
            "SELECT COUNT(*) AS n FROM email_organize_log "
            "WHERE source_id = %s AND reversed_at IS NULL",
            (source_id,),
        )
        unreversed = int((cnt or {}).get("n") or 0)
    return {
        "entries": rows,
        "undo_gave_up_at": gave_up,
        "undo_unreversed": unreversed,
    }


class EmailCleanupStart(BaseModel):
    """LUM-646: start a date-range cleanup pass. Both bounds optional (start None =
    unbounded lower; end None = today)."""

    window_start: Optional[date] = None
    window_end: Optional[date] = None

    @model_validator(mode="after")
    def _validate(self) -> "EmailCleanupStart":
        if (
            self.window_start is not None
            and self.window_end is not None
            and self.window_end < self.window_start
        ):
            raise ValueError("window_end must be >= window_start")
        return self


class CleanupAction(BaseModel):
    category: str
    move: bool = False  # file this category into Lumogis/<label> (out of INBOX)


class EmailCleanupApprove(BaseModel):
    actions: list[CleanupAction] = Field(default_factory=list)
    # LUM-729 — same field, same default, same reason as EmailOrganizeUpdate.
    acknowledge_external_consumer: bool = False


class EmailCleanupUndo(BaseModel):
    ops: list[Literal["label", "move", "trash"]] = Field(
        default_factory=lambda: ["label", "move", "trash"]
    )


_email_cleanup_limiter = RequestRateLimiter(max_requests=20, window_seconds=60.0)


def _cleanup_or_http(fn, *args, **kwargs):
    """Call an email_cleanup function, mapping its CleanupError to an HTTPException."""
    from services.email_cleanup import CleanupError

    try:
        return fn(*args, **kwargs)
    except CleanupError as exc:
        # A refusal the user is expected to act on carries a body that says what
        # will happen (LUM-729); the rest stay the bare code they have always been.
        detail = getattr(exc, "detail", None) or {"code": exc.code}
        raise HTTPException(status_code=exc.status, detail=detail) from exc


@router.post(
    "/me/email-sources/{source_id}/cleanup",
    status_code=202,
    dependencies=[Depends(require_same_origin)],
)
def start_email_cleanup(
    source_id: str,
    body: EmailCleanupStart,
    user: UserContext = Depends(require_user),
):
    if not _email_cleanup_limiter.check(user.user_id):
        raise HTTPException(status_code=429, detail={"code": "rate_limited"})
    if _owned_imap_source_id(user.user_id, source_id) is None:
        raise HTTPException(status_code=404, detail={"code": "not_found"})
    from services import email_cleanup

    return _cleanup_or_http(
        email_cleanup.start_job, source_id, user.user_id, body.window_start, body.window_end
    )


@router.get("/me/email-sources/{source_id}/cleanup/{job_id}")
def get_email_cleanup_job(
    source_id: str,
    job_id: str,
    user: UserContext = Depends(require_user),
):
    if _owned_imap_source_id(user.user_id, source_id) is None:
        raise HTTPException(status_code=404, detail={"code": "not_found"})
    from services import email_cleanup

    return _cleanup_or_http(email_cleanup.job_summary, job_id, user.user_id, source_id)


@router.post(
    "/me/email-sources/{source_id}/cleanup/{job_id}/approve",
    status_code=202,
    dependencies=[Depends(require_same_origin)],
)
def approve_email_cleanup(
    source_id: str,
    job_id: str,
    body: EmailCleanupApprove,
    user: UserContext = Depends(require_user),
):
    if not _email_cleanup_limiter.check(user.user_id):
        raise HTTPException(status_code=429, detail={"code": "rate_limited"})
    if _owned_imap_source_id(user.user_id, source_id) is None:
        raise HTTPException(status_code=404, detail={"code": "not_found"})
    from services import email_cleanup

    return _cleanup_or_http(
        email_cleanup.approve,
        job_id,
        user.user_id,
        source_id,
        [a.model_dump() for a in body.actions],
        body.acknowledge_external_consumer,
    )


@router.post(
    "/me/email-sources/{source_id}/cleanup/{job_id}/undo",
    status_code=202,
    dependencies=[Depends(require_same_origin)],
)
def undo_email_cleanup(
    source_id: str,
    job_id: str,
    body: EmailCleanupUndo,
    user: UserContext = Depends(require_user),
):
    if not _email_cleanup_limiter.check(user.user_id):
        raise HTTPException(status_code=429, detail={"code": "rate_limited"})
    if _owned_imap_source_id(user.user_id, source_id) is None:
        raise HTTPException(status_code=404, detail={"code": "not_found"})
    from services import email_cleanup

    return _cleanup_or_http(email_cleanup.undo_job, job_id, user.user_id, source_id, body.ops)


@router.get("/me/email-sources/{source_id}/cleanup-spam")
def get_email_cleanup_spam(
    source_id: str,
    user: UserContext = Depends(require_user),
):
    # This GET opens a read-only IMAP session (to count \Junk), so it is rate-limited like
    # the other IMAP-touching endpoints (e.g. POST /me/email-sources/test) to prevent a
    # client from hammering the provider into a lockout.
    if not _email_cleanup_limiter.check(user.user_id):
        raise HTTPException(status_code=429, detail={"code": "rate_limited"})
    if _owned_imap_source_id(user.user_id, source_id) is None:
        raise HTTPException(status_code=404, detail={"code": "not_found"})
    from services import email_cleanup

    return _cleanup_or_http(email_cleanup.spam_folder_count, source_id, user.user_id)


@router.post(
    "/me/email-sources/{source_id}/cleanup-spam",
    status_code=202,
    dependencies=[Depends(require_same_origin)],
)
def start_email_cleanup_spam(
    source_id: str,
    user: UserContext = Depends(require_user),
):
    if not _email_cleanup_limiter.check(user.user_id):
        raise HTTPException(status_code=429, detail={"code": "rate_limited"})
    if _owned_imap_source_id(user.user_id, source_id) is None:
        raise HTTPException(status_code=404, detail={"code": "not_found"})
    from services import email_cleanup

    return _cleanup_or_http(email_cleanup.start_spam_job, source_id, user.user_id)


@router.post("/sources/{source_id}/sync", status_code=202)
def sync_source_now(source_id: str, user: UserContext = Depends(require_user)):
    """Trigger an immediate poll of one of the caller's sources ("Sync now").

    Enqueue-only: returns 202 fast; the IMAP/LLM work runs off the request
    thread in a one-off APScheduler job. Returns 404 for an unknown/foreign
    source, 409 ``already_running`` when a manual sync for this source is already
    queued, and 409 ``source_paused`` (LUM-844) when the source is paused.

    NOTE: the 409 guard is per-process (APScheduler job id); the poll body
    additionally holds an in-process in-flight guard
    (``feed_monitor._poll_email_dispatch``) so a scheduled poll and a manual
    sync cannot double-run in the scheduler process. Cross-process overlap is
    harmless — dedup-by-url drops any duplicate signal.
    """
    from signals.feed_monitor import enqueue_sync_now

    try:
        result = enqueue_sync_now(source_id, user.user_id)
    except Exception as exc:
        _log.warning("Could not enqueue sync for %s: %s", source_id, exc)
        raise HTTPException(status_code=500, detail={"code": "enqueue_failed"}) from exc

    if result == "not_found":
        raise HTTPException(status_code=404, detail={"code": "source_not_found"})
    if result == "paused":
        raise HTTPException(status_code=409, detail={"code": "source_paused"})
    if result == "already_running":
        raise HTTPException(status_code=409, detail={"code": "already_running"})

    return {"status": "queued", "source_id": source_id}


def _imap_source_config(source_id, name, url, extraction_method, poll_interval, user_id, category):
    return SourceConfig(
        id=source_id,
        name=name,
        source_type="imap",
        url=url,
        category=category,
        active=True,
        poll_interval=poll_interval,
        extraction_method=extraction_method,
        css_selector_override=None,
        last_polled_at=None,
        last_signal_at=None,
        poll_cursor=None,
        user_id=user_id,
    )


# ---------------------------------------------------------------------------
# PATCH /sources/{source_id} — mode toggle (LUM-628)
# ---------------------------------------------------------------------------


# The ONLY columns `PATCH /sources/{id}` may write. The SET clause is assembled
# from this frozenset, never from request keys, so no caller-supplied string can
# reach the SQL text. Adding a column here is a deliberate act; adding a field to
# `SourceModeUpdate` alone changes nothing.
_SOURCE_UPDATE_COLUMNS: frozenset[str] = frozenset(
    {"extraction_method", "name", "category", "poll_interval", "active"}
)


@router.patch("/sources/{source_id}", dependencies=[Depends(require_same_origin)])
def update_source_mode(
    source_id: str,
    body: SourceModeUpdate,
    user: UserContext = Depends(require_user),
):
    """Partially update one of the caller's sources.

    LUM-628 shipped this as an IMAP mode toggle; LUM-837 widens it into the
    general source-edit route, because nothing could change any source's name,
    category or poll interval. ``name``/``category``/``poll_interval`` apply to
    every ``source_type``; ``full_ingest`` stays IMAP-only and still returns
    ``422 mode_toggle_not_supported`` elsewhere.

    Owner-only (404 for a foreign/unknown source — no existence oracle). An empty
    body is a ``422`` from the model validator, not a no-op: ``"unchanged"`` means
    *"the values you supplied already match"*.

    ``active`` (LUM-844) pauses (``false``) or resumes (``true``) polling for any
    non-IMAP source; an IMAP mailbox answers ``422 pause_not_supported``. Supplying
    ``active`` **always** reconciles the scheduler with the column, even when the
    value is unchanged, so re-sending the same toggle repairs a schedule a failed
    earlier call left wrong. A real resume also queues one immediate check.
    ``schedule_synced`` is ``false`` when the scheduler could not be updated.

    Scheduling is reconciled from the *committed* row under a lock
    (``feed_monitor.reconcile_source_schedule``), not from the pre-update SELECT —
    the scheduled job holds a ``SourceConfig`` snapshot, so this is what makes the
    next poll use the new values, and it never resurrects a paused source.
    ``poll_cursor`` is left untouched (forward-only).

    Full-ingest actually runs only when ``EMAIL_FULL_INGEST_ENABLED`` is on; like
    the create path, this route stores the intent regardless, but returns
    ``full_ingest_active`` (and logs a WARNING when off) so the effective state is
    never a silent write-only bit. That field is ``None`` — not ``False`` — when
    the request never mentioned mode.
    """
    ms = config.get_metadata_store()
    row = ms.fetch_one(
        "SELECT id, name, source_type, url, category, active, poll_interval, "
        "extraction_method, css_selector_override, poll_cursor, user_id "
        "FROM sources WHERE id::text = %s AND user_id = %s",
        (source_id, user.user_id),
    )
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "source_not_found"})

    is_imap = row["source_type"] == "imap"
    if body.full_ingest is not None and not is_imap:
        # The IMAP-only refusal narrows to `full_ingest` alone (LUM-837): the
        # general fields below apply to every source type.
        raise HTTPException(status_code=422, detail={"code": "mode_toggle_not_supported"})
    if body.active is not None and is_imap:
        # Refuses whenever `active` is supplied on a mailbox, even when it matches
        # the stored value (mirrors `full_ingest` on non-IMAP rows). Send, the From
        # picker, backfills and organize all assume an active mailbox.
        raise HTTPException(status_code=422, detail={"code": "pause_not_supported"})

    from signals.feed_monitor import _email_full_ingest_enabled

    # `full_ingest_active` is None — NOT False — when mode was not supplied.
    # `False` already means "requested and inert because the env flag is off",
    # so a request that never mentioned mode must not assert anything about it.
    new_method: Optional[str] = None
    full_ingest_active: Optional[bool] = None
    if body.full_ingest is not None:
        new_method = "imap_full_ingest" if body.full_ingest else "imap_metadata"
        full_ingest_active = new_method == "imap_full_ingest" and _email_full_ingest_enabled()

    # Build the SET clause from a FIXED ALLOWLIST of column names, with every
    # value passed as a parameter. A request key never reaches the SQL text.
    # `_SOURCE_UPDATE_COLUMNS` is the allowlist; nothing else can be written here.
    changes: dict[str, object] = {}
    if new_method is not None and new_method != row["extraction_method"]:
        changes["extraction_method"] = new_method
    if body.name is not None and body.name != row["name"]:
        changes["name"] = body.name
    if body.category is not None and body.category != row["category"]:
        changes["category"] = body.category
    if body.poll_interval is not None and body.poll_interval != row["poll_interval"]:
        changes["poll_interval"] = body.poll_interval
    if body.active is not None and body.active != row["active"]:
        changes["active"] = body.active

    # Echoed back so the client need not refetch — the values as they now stand,
    # for the fields the request actually supplied.
    echoed: dict[str, object] = {}
    if body.name is not None:
        echoed["name"] = body.name
    if body.category is not None:
        echoed["category"] = body.category
    if body.poll_interval is not None:
        echoed["poll_interval"] = body.poll_interval

    if not changes:
        rescheduled, schedule_synced, state = _reconcile_after_edit(
            source_id, user.user_id, body, changes
        )
        if body.active is not None:
            echoed["active"] = state if isinstance(state, bool) else body.active
        return {
            "status": "unchanged",
            "source_id": source_id,
            "source_type": row["source_type"],
            "extraction_method": row["extraction_method"],
            "full_ingest_active": full_ingest_active,
            "rescheduled": rescheduled,
            "schedule_synced": schedule_synced,
            **echoed,
        }

    assert set(changes).issubset(_SOURCE_UPDATE_COLUMNS)  # allowlist, not a guess
    set_clause = ", ".join(f"{col} = %s" for col in changes)
    try:
        # `RETURNING id` rather than a bare execute, so a row deleted between the
        # SELECT above and here is caught. Without it a PATCH racing a DELETE
        # updates nothing, reports `"updated"`, and then **reschedules a poll job
        # for a row that no longer exists** — re-creating exactly the orphaned
        # job that hoisting `unschedule_source` out of `delete_source`'s IMAP
        # branch exists to prevent. Same pattern `delete_source` already uses.
        updated = ms.fetch_one(
            f"UPDATE sources SET {set_clause} "  # noqa: S608
            "WHERE id::text = %s AND user_id = %s RETURNING id",
            (*changes.values(), source_id, user.user_id),
        )
    except Exception as exc:
        _log.error("Failed to update source %s: %s", source_id, exc)
        raise HTTPException(status_code=500, detail={"code": "update_failed"}) from exc

    if updated is None:
        # Raced with a delete of the same row. Already gone either way; 404 is
        # the honest answer, and nothing is rescheduled.
        raise HTTPException(status_code=404, detail={"code": "source_not_found"})

    if body.full_ingest and not full_ingest_active:
        _log.warning(
            "email.full_ingest.requested_but_disabled source_id=%s — mode stored but "
            "inert until EMAIL_FULL_INGEST_ENABLED is set",
            source_id,
            extra={"event": "email.full_ingest.requested_but_disabled", "source_id": source_id},
        )

    rescheduled, schedule_synced, state = _reconcile_after_edit(
        source_id, user.user_id, body, changes
    )
    # The reconciled read wins over the request, so a request that lost a race
    # echoes the column as it now stands.
    if body.active is not None:
        echoed["active"] = state if isinstance(state, bool) else body.active

    return {
        "status": "updated",
        "source_id": source_id,
        "source_type": row["source_type"],
        "extraction_method": changes.get("extraction_method", row["extraction_method"]),
        "full_ingest_active": full_ingest_active,
        "rescheduled": rescheduled,
        "schedule_synced": schedule_synced,
        **echoed,
    }


def _reconcile_after_edit(
    source_id: str,
    user_id: str,
    body: SourceModeUpdate,
    changes: dict[str, object],
) -> tuple[bool, bool, Optional[bool]]:
    """Make the scheduler match the committed row after a source edit (LUM-844).

    Returns ``(rescheduled, schedule_synced, state)``. Supplying ``active`` always
    reconciles (so re-sending a toggle repairs a stale schedule); otherwise only an
    edit that staled the job snapshot (mode or interval) does. A failure is logged
    and reported as ``schedule_synced=False``; the column write has already
    committed, and a pause still removes the jobs best-effort (``stop_on_error``).
    """
    needs_reconcile = body.active is not None or bool(
        {"extraction_method", "poll_interval"} & changes.keys()
    )
    if not needs_reconcile:
        return False, True, None
    resuming = changes.get("active") is True  # a real false->true, not a repair
    try:
        from signals.feed_monitor import reconcile_source_schedule

        state = reconcile_source_schedule(
            source_id, user_id, run_now=resuming, stop_on_error=body.active is False
        )
    except Exception as exc:
        _log.warning("Could not reconcile poll job for %s: %s", source_id, exc)
        return False, False, None
    return state is True, True, state


# ---------------------------------------------------------------------------
# DELETE /sources/{source_id} — remove a mailbox (LUM-701)
# ---------------------------------------------------------------------------


@router.delete("/sources/{source_id}", dependencies=[Depends(require_same_origin)])
def delete_source(source_id: str, user: UserContext = Depends(require_user)):
    """Remove one of the caller's sources. Owner-scoped (404, no-oracle).

    **Retention decision (LUM-701):** ingested signals are NOT deleted. They
    remain as a historical record — ``signals.source_id`` is a plain TEXT
    column with no FK to ``sources`` (migration 013), and each signal already
    carries its own denormalized ``source_label``/``source_url``/``from_addr``,
    so rendering never depends on the source row still existing. Only the
    ``sources`` row itself, and (for an IMAP mailbox) its background
    footprint, are removed.

    For ``source_type == 'imap'`` this additionally, in order:
    1. Pauses the row and reconciles (every source type, LUM-844) — nothing
       fires or syncs against the row again.
    2. Cancels any in-flight cleanup job (``email_cleanup_job`` has no FK to
       ``sources``, so an active job would otherwise orphan and error every
       poll tick forever, silently — LUM-701).
    3. Invalidates the organize opt-in cache (closes the ~20s TTL window where
       a stale "still on" read could fire one more IMAP call against a
       just-deleted mailbox).
    4. Deletes the ``sources`` row (``email_organize_log`` cascades via its own
       FK; ``email_cleanup_job`` rows are left as the failed record from step 2).
    5. Compensating-deletes the IMAP and SMTP credential rows — best-effort;
       failure here doesn't roll back the delete, since the row (and therefore
       the mailbox as the user sees it) is already gone either way.

    No in-flight OAuth device-flow collision is possible: ``start_device_flow``
    only ever mints a brand-new ``source_id`` at persist time, never operates
    on an existing row.
    """
    ms = config.get_metadata_store()
    row = ms.fetch_one(
        "SELECT id::text AS id, source_type FROM sources WHERE id::text = %s AND user_id = %s",
        (source_id, user.user_id),
    )
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "source_not_found"})

    is_imap = row["source_type"] == "imap"

    # LUM-837: unscheduling is for EVERY source type, not just mailboxes. This
    # call used to sit inside the `is_imap` branch below — its only call site in
    # the codebase — so deleting a paperless, RSS or page source removed the row
    # and left an APScheduler job holding a `SourceConfig` snapshot, polling the
    # deleted source's URL on its interval forever. Nothing else removed it.
    # Invisible until now because those three types had no delete client.
    #
    # LUM-844: pause the ROW first (not just the job), so that with the slow vector
    # purge below running, a PATCH reconcile or Sync now landing meanwhile reads it
    # as paused and schedules nothing — no poll can ingest into vectors the purge
    # just removed. If the DELETE later fails (500) a non-IMAP row is left paused,
    # the safer leftover for a row the user asked to remove (a mailbox is restored).
    from signals.feed_monitor import begin_delete
    from signals.feed_monitor import end_delete
    from signals.feed_monitor import reconcile_source_schedule

    # Marks the source as deleting so a concurrent resume or Sync now during the
    # purge still reads as paused (see `feed_monitor._deleting`).
    begin_delete(source_id)
    try:
        try:
            ms.execute(
                "UPDATE sources SET active = FALSE WHERE id::text = %s AND user_id = %s",
                (source_id, user.user_id),
            )
        except Exception:
            _log.warning(
                "delete_source: pre-delete pause failed source_id=%s", source_id, exc_info=True
            )
        try:
            reconcile_source_schedule(source_id, user.user_id, stop_on_error=True)
        except Exception:
            _log.warning("delete_source: reconcile failed source_id=%s", source_id, exc_info=True)

        if is_imap:
            from services import email_cleanup

            try:
                email_cleanup.cancel_active_jobs_for_source(source_id, user.user_id)
            except Exception:
                _log.warning(
                    "delete_source: cleanup job cancel failed source_id=%s",
                    source_id,
                    exc_info=True,
                )

            from services import email_organize

            email_organize.invalidate_opt_in_cache(source_id)

        # BEFORE the delete, not after: `external_documents` and
        # `email_attachment_ingest_log` both CASCADE on `sources(id)`, and neither
        # path writes a `file_index` row (LUM-724), so those ledgers are the only
        # records naming this source's Qdrant points. Deleting the row first left
        # every document it had filed searchable forever with nothing to enumerate
        # it from — silent, and a privacy failure rather than untidiness.
        from services.document_purge import purge_source_vectors

        try:
            purge_source_vectors(user_id=user.user_id, source_id=source_id)
        except Exception:
            # Best-effort by design. A vector store that is down must not leave the
            # user unable to remove a mailbox; the alternative failure (a source
            # they cannot delete) is worse and more visible than the one it avoids.
            _log.warning(
                "delete_source: vector purge failed source_id=%s", source_id, exc_info=True
            )

        try:
            deleted = ms.fetch_one(
                "DELETE FROM sources WHERE id::text = %s AND user_id = %s RETURNING id",
                (source_id, user.user_id),
            )
        except Exception:
            if is_imap:
                # A paused non-IMAP row can be resumed from /me/sources; a paused
                # MAILBOX cannot (PATCH refuses `active` on IMAP), and send, the From
                # picker and backfills all filter on `active`. Put it back.
                end_delete(source_id)
                try:
                    ms.execute(
                        "UPDATE sources SET active = TRUE WHERE id::text = %s AND user_id = %s",
                        (source_id, user.user_id),
                    )
                    reconcile_source_schedule(source_id, user.user_id)
                except Exception:
                    _log.warning(
                        "delete_source: could not restore mailbox after failed delete source_id=%s",
                        source_id,
                        exc_info=True,
                    )
            raise
        if deleted is None:
            # Raced with a concurrent delete of the same row between the SELECT
            # above and here — already gone either way; 404 is the honest answer.
            raise HTTPException(status_code=404, detail={"code": "source_not_found"})

        # The row is gone, so this unschedules. It covers a pre-delete pause write
        # that failed, and a job a racing PATCH reconcile planted during the purge.
        try:
            reconcile_source_schedule(source_id, user.user_id, stop_on_error=True)
        except Exception:
            _log.warning("delete_source: reconcile failed source_id=%s", source_id, exc_info=True)
    finally:
        end_delete(source_id)

    if is_imap:
        from connectors.registry import IMAP
        from connectors.registry import SMTP

        from services import connector_credentials as ccs

        for connector in (IMAP, SMTP):
            try:
                ccs.delete_payload(user.user_id, connector, actor="self", instance=source_id)
            except Exception:
                _log.warning(
                    "delete_source: credential cleanup failed source_id=%s connector=%s",
                    source_id,
                    connector,
                    exc_info=True,
                )

    return {"status": "deleted", "source_id": source_id}


# ---------------------------------------------------------------------------
# GET /sources
# ---------------------------------------------------------------------------


def _smtp_host_required(row) -> bool:
    """Would enabling send on this mailbox need a submission host/port? (LUM-771)

    A mailbox whose IMAP host has no provider-registry entry is an "Other" mailbox:
    ``set_email_smtp`` answers ``422 smtp_host_required`` unless the client supplies
    both. Today a client can only learn that by trying and failing, so it either
    shows two fields to everybody or sends the user through an avoidable error.

    Computed on read by the same ``provider_for_host`` the route matches with —
    one matcher, nothing to drift — and NOT stored: a column would be a second
    source of truth for a question already answerable from ``sources.url``.
    """
    # The gate is load-bearing. `list_sources` returns EVERY source type, and
    # `provider_for_host` is None for an RSS or Paperless host — ungated, this
    # would publish `true` for every non-mailbox row in the response.
    if row["source_type"] != "imap":
        return False

    from services.email_providers import provider_for_host
    from services.imap_source_url import imap_hostname_from_source_url

    host = imap_hostname_from_source_url(row["url"] or "")
    if host is None:
        # Unparseable url (bad scheme, missing hostname, invalid IPv6) — unknown
        # host ⇒ treat a submission host as required (same as the pre-refactor
        # `urlsplit` ValueError path).
        return True
    return provider_for_host(host) is None


@router.get("/sources")
def list_sources(user: UserContext = Depends(require_user)):
    """Return all of the calling user's sources, paused included, with scheduler job status."""
    try:
        ms = config.get_metadata_store()
        # SCOPE-EXEMPT: `sources` is in plan §2.10's excluded-from-scope
        # list — sources are per-user signal-polling config, not memory
        # content; no `scope` column exists.
        rows = ms.fetch_all(
            "SELECT id, name, source_type, url, category, active, poll_interval, "
            "extraction_method, last_polled_at, last_signal_at, last_poll_error_code, "
            "last_poll_error_at, attachment_ingest_enabled, attachment_ingest_since "
            # SCOPE-EXEMPT: repeated here because the tag above is now outside the
            # grep gate's 6-line lookback — LUM-742 added two columns and pushed
            # this line down. Same justification: `sources` is per-user polling
            # config, not memory content, and has no `scope` column.
            "FROM sources WHERE user_id = %s ORDER BY name",
            (user.user_id,),
        )
    except Exception as exc:
        _log.warning("list_sources: DB query failed — %s", exc)
        return {"sources": [], "total": 0}

    # LUM-628: full-ingest actually runs only when the operator flag is on; expose
    # both the stored mode and whether it is in effect so a client can render it.
    from signals.feed_monitor import _email_full_ingest_enabled

    full_ingest_on = _email_full_ingest_enabled()

    scheduler = config.get_scheduler()
    scheduled_ids = {job.id for job in scheduler.get_jobs()}

    # LUM-777: the mailbox auth method, read from plaintext credential key names.
    # Skipped entirely for a listing with no IMAP row (an RSS- or paperless-only
    # listing never touches the credential store). The degrade is all-or-nothing
    # on purpose: `list_records` is one SELECT that fails whole, so there is no
    # per-mailbox failure to skip (unlike `declared_imap_hosts`, whose per-mailbox
    # body does its own I/O). Unknown (`null`) is always safe: the client then
    # shows its hedged copy.
    auth_methods: dict[str, str | None] = {}
    if any(r["source_type"] == "imap" for r in rows):
        try:
            from services import email_credentials

            auth_methods = email_credentials.imap_auth_methods(user.user_id)
        except Exception as exc:
            # Type name only: the store can raise with SQL or parameters in the
            # message, and the listing log must never carry them.
            _log.warning("list_sources: credential metadata read failed — %s", type(exc).__name__)

    result = []
    for row in rows:
        job_id = f"signal_poll_{row['id']}"
        result.append(
            {
                "id": str(row["id"]),
                "name": row["name"],
                "source_type": row["source_type"],
                "url": row["url"],
                "category": row["category"],
                "active": row["active"],
                "poll_interval": row["poll_interval"],
                "extraction_method": row.get("extraction_method"),
                "full_ingest_active": (
                    row.get("extraction_method") == "imap_full_ingest" and full_ingest_on
                ),
                "last_polled_at": row["last_polled_at"].isoformat()
                if row["last_polled_at"]
                else None,
                "last_signal_at": row["last_signal_at"].isoformat()
                if row["last_signal_at"]
                else None,
                "polling_active": job_id in scheduled_ids,
                # LUM-690/633: last poll's credential-health state (None = healthy).
                "last_poll_error_code": row.get("last_poll_error_code"),
                "last_poll_error_at": row["last_poll_error_at"].isoformat()
                if row.get("last_poll_error_at")
                else None,
                # LUM-742: the attachment-filing opt-in. Written by PATCH
                # /attachment-ingest since LUM-702 and read by NOTHING until now,
                # which is why `AttachmentIngestToggle` — whose `enabled` prop is
                # required — could never have a truthful consumer. The watermark
                # comes with it because the three activation outcomes are only
                # distinguishable from its presence: absent means a first-ever
                # enable starts from now, present means a re-enable resumes from
                # the original opt-in and surfaces everything since.
                "attachment_ingest_enabled": bool(row.get("attachment_ingest_enabled")),
                "attachment_ingest_since": row["attachment_ingest_since"].isoformat()
                if row.get("attachment_ingest_since")
                else None,
                # LUM-771: derived, never stored — see `_smtp_host_required`.
                "smtp_host_required": _smtp_host_required(row),
                # LUM-777: derived, never stored, never decrypted — see
                # `email_credentials.mailbox_auth_method`. Gated on IMAP for the
                # same reason as `_smtp_host_required` (an RSS row's id could
                # collide with an IMAP instance). Non-IMAP rows carry `null`, not
                # `smtp_host_required`'s `false`: `null` here means "unknown", and
                # a non-mailbox has no auth method to know. Display-only.
                "auth_method": auth_methods.get(str(row["id"]))
                if row["source_type"] == "imap"
                else None,
            }
        )
    return {"sources": result, "total": len(result)}


# ---------------------------------------------------------------------------
# GET /signals
# ---------------------------------------------------------------------------


@router.get("/signals")
def list_signals(
    topic: Optional[str] = Query(None),
    entity: Optional[str] = Query(None),
    source_id: Optional[str] = Query(None),
    source_type: Optional[str] = Query(
        None, description="Narrow to a source type; 'imap' returns only email signals."
    ),
    min_relevance_score: Optional[float] = Query(None),
    scope: Optional[str] = Query(
        None,
        regex="^(personal|shared|system)$",
        description="Narrow to one scope; default is household union.",
    ),
    triaged: Optional[str] = Query(
        None,
        regex="^set$",
        description=(
            "triaged=set returns the caller's FULL triaged set (any signal with a "
            "triage row), ordered by triage recency and independent of the created_at "
            "window — so Done/Snoozed items older than the recency LIMIT are never lost. "
            "Every other filter (e.g. source_type=imap) still applies."
        ),
    ),
    triaged_before: Optional[str] = Query(
        None,
        description=(
            "Keyset pagination cursor for triaged=set: an ISO-8601 timestamp; returns "
            "only rows whose triage was last updated STRICTLY BEFORE it. Pass the "
            "`next_before` from the previous page. Ignored unless triaged=set (LUM-659)."
        ),
    ),
    limit: int = Query(20, ge=1, le=100),
    user: UserContext = Depends(require_user),
):
    """Return recent signals visible to the caller (household union by default).

    The default visibility surface is the household union via
    :func:`visibility.visible_filter`:
    `(scope='personal' AND user_id=$me) OR scope IN ('shared','system')`.
    System signals (`source_id='__system__'`, `scope='system'`) are
    therefore visible to every user — that is the contract; the dashboard
    System panel relies on this.

    Selecting `?scope=personal` returns only the caller's own personal
    signals (admins included — see the headline-test invariant in
    plan §2.8). `source_url` and `source_label` are denormalized at
    write time so shared/system signals render without joining
    `sources`.
    """
    conditions: list[str] = []
    # LUM-643: the triage LATERAL's ``st.user_id = %s`` is the FIRST placeholder in
    # the assembled SQL (FROM emits before WHERE), so the caller's id must be
    # PREPENDED — before visible_filter / other predicate params. A late append or
    # insert(0, …) would bind the caller to the wrong ``%s``.
    params: list = [user.user_id]

    where_clause, where_params = visible_filter(user, scope)
    conditions.append(where_clause)
    params.extend(where_params)

    if topic:
        conditions.append("topics::text ILIKE %s")
        params.append(f"%{topic}%")
    if entity:
        conditions.append("entities::text ILIKE %s")
        params.append(f"%{entity}%")
    if source_id:
        conditions.append("source_id = %s")
        params.append(source_id)
    if source_type == "imap":
        # LUM-642: email signals carry an ``imap://{source_id}/{uidvalidity}/{uid}``
        # url (email_imap_source._build_signal). Filtering on the url scheme scopes
        # the household-union result to email — including historical rows — without
        # a persisted source_type column or a sources join. Parameterised: the ``%``
        # is a literal in the bound value.
        conditions.append("url LIKE %s")
        params.append("imap://%")
    if min_relevance_score is not None:
        conditions.append("relevance_score >= %s")
        params.append(min_relevance_score)

    if triaged == "set":
        # Only rows the caller has actually triaged (the LATERAL exposed
        # triage_status). Recency-independent: ordered by triage recency with its
        # OWN cap, so a Done/Snoozed signal older than the created_at window is
        # never lost. Reuses every other predicate above (incl. source_type=imap),
        # so non-email triage cannot leak into an email-scoped fetch.
        conditions.append("t.triage_status IS NOT NULL")
        # LUM-659: keyset pagination. `triaged_before` is the previous page's
        # `next_before` cursor — the ORDER BY is triage_updated_at DESC, so a
        # strict `< cursor` fetches the next (older) page with no overlap. The
        # cast makes psycopg bind the ISO string as a timestamptz.
        if triaged_before:
            conditions.append("t.triage_updated_at < %s::timestamptz")
            params.append(triaged_before)
        order_by = "t.triage_updated_at DESC"
        effective_limit = _TRIAGED_SET_LIMIT
    else:
        order_by = "created_at DESC"
        effective_limit = limit

    where = " AND ".join(conditions)
    params.append(effective_limit)

    try:
        ms = config.get_metadata_store()
        rows = ms.fetch_all(
            # user_id (the signal OWNER) is selected for server-side label
            # resolution only (LUM-632) — it is NEVER serialized into the
            # response (owner attribution is LUM-585's surface). The triage columns
            # (triage_status/snoozed_until) come from the per-caller LATERAL; a plain
            # LEFT JOIN would throw 42702 (ambiguous user_id/signal_id).
            signal_triage.build_signals_query(where, order_by),
            tuple(params),
        )
    except Exception as exc:
        # Raised to error (not warning): a LATERAL binding/ambiguity bug would
        # otherwise degrade every consumer to an empty list silently (Fable D4.1).
        _log.error("list_signals: DB query failed — %s", exc)
        return {"signals": [], "total": 0}

    # LUM-632: resolve each signal's category slug to a human label via the
    # signal OWNER's taxonomy, so a household member sees the owner's label
    # (e.g. "Landlord") on a shared signal rather than a raw slug they can't
    # resolve. Batched per DISTINCT owner (≤ household size per page), never
    # per row. A failed owner load degrades that owner's rows to the raw slug
    # (logged at warning) — the list never fails. Runs OUTSIDE the query
    # catch-all above so a resolution hiccup can't blank the whole response.
    owner_label_maps: dict[str, dict[str, str]] = {}
    for owner_id in {str(r["user_id"]) for r in rows if r.get("category") and r.get("user_id")}:
        try:
            owner_label_maps[owner_id] = email_taxonomy.label_map(owner_id)
        except Exception as exc:
            _log.warning("list_signals: label_map(owner=%s) failed — %s", owner_id, exc)
            owner_label_maps[owner_id] = {}

    # LUM-649: fetch the CALLER's own learned relevance weights ONCE per request
    # (one indexed point-read, UNIQUE(user_id)) — then compute a per-viewer
    # learned_relevance_delta per row in Python, never touching the stored/shared
    # relevance_score/importance_score columns. load_profile_weights never raises
    # (degrades to empty maps → delta 0.0 everywhere) so a nudge fetch failure
    # can never blank or 500 the inbox.
    caller_weights = relevance_learning.load_profile_weights(user.user_id)

    result = []
    for row in rows:
        slug = row.get("category") or ""
        owner_id = str(row["user_id"]) if row.get("user_id") else ""
        # Empty category → "" (no "general" special-case: signals.category is
        # never "general" — that's sources.category — and "general" is a legal
        # custom slug). Unresolvable slug → the raw slug (never blank/raises).
        category_label = owner_label_maps.get(owner_id, {}).get(slug, slug) if slug else ""
        result.append(
            {
                "signal_id": str(row["signal_id"]),
                "source_id": str(row["source_id"]),
                "title": row["title"],
                "url": row["url"],
                "published_at": row["published_at"].isoformat() if row["published_at"] else None,
                "content_summary": row["content_summary"],
                "entities": row["entities"] or [],
                "topics": row["topics"] or [],
                "importance_score": float(row["importance_score"]),
                "relevance_score": float(row["relevance_score"]),
                # LUM-649: per-caller learned nudge, added to importance_score for
                # lane placement web-side. Pure per-request computation from the
                # caller's own weights — never touches stored/shared columns.
                "learned_relevance_delta": round(
                    relevance_learning.compute_learned_relevance_delta(
                        caller_weights,
                        topics=row.get("topics") or [],
                        entities=row.get("entities") or [],
                        from_addr=row.get("from_addr") or "",
                        category=row.get("category") or "",
                    ),
                    4,
                ),
                "notified": row["notified"],
                "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                "category": slug,
                "category_label": category_label,
                "from_addr": row.get("from_addr", ""),
                "from_display": row.get("from_display", ""),
                "scope": row.get("scope", "personal"),
                "source_url": row.get("source_url"),
                "source_label": row.get("source_label"),
                # LUM-643: per-caller triage state (null → untriaged → client
                # applies default lane derivation). published_from enables a future
                # owner/projection dedupe (additive, optional-safe).
                "triage_status": row.get("triage_status"),
                "snoozed_until": (
                    row["snoozed_until"].isoformat() if row.get("snoozed_until") else None
                ),
                "published_from": (
                    str(row["published_from"]) if row.get("published_from") else None
                ),
                # LUM-659: the keyset cursor field — the client hands the last
                # row's value back as `triaged_before` to page older.
                "triage_updated_at": (
                    row["triage_updated_at"].isoformat() if row.get("triage_updated_at") else None
                ),
            }
        )
    resp = {"signals": result, "total": len(result)}
    # LUM-659: hand back the next-page cursor when the triaged page filled to the
    # cap (more may exist). Ordered by triage_updated_at DESC, so the last row is
    # the oldest — its timestamp is the exclusive upper bound for the next page.
    if triaged == "set" and len(result) == effective_limit and result:
        resp["next_before"] = result[-1]["triage_updated_at"]
    return resp


# ---------------------------------------------------------------------------
# PATCH/DELETE /signals/{signal_id}/triage  (LUM-643)
# ---------------------------------------------------------------------------


class TriageRequest(BaseModel):
    """Body for ``PATCH /signals/{signal_id}/triage``.

    ``status`` is validated in :func:`services.signal_triage.set_triage` against the
    frozen set (never interpolated). ``snoozed_until`` is required & strictly-future
    & TZ-aware iff ``status='snoozed'``; nulled otherwise.
    """

    status: str
    snoozed_until: Optional[datetime] = None


def _signal_visible_to(user: UserContext, signal_id: uuid.UUID) -> bool:
    """True iff ``signal_id`` is returned by the caller's household ``visible_filter``.

    The single-signal guard for the triage mutations: the caller may only triage
    (or probe the existence of) a signal they can already see. Binds ``signal_id``
    FIRST, then the ``visible_filter`` params — note this is the OPPOSITE order to
    the GET LATERAL (caller-id first); the two adjacent queries are kept distinct on
    purpose to avoid copy-paste binding bugs (Fable D5.2).
    """
    where_clause, where_params = visible_filter(user)
    ms = config.get_metadata_store()
    row = ms.fetch_one(
        f"SELECT 1 AS ok FROM signals WHERE signal_id = %s AND {where_clause} LIMIT 1",
        (str(signal_id), *where_params),
    )
    return row is not None


@router.patch("/signals/{signal_id}/triage")
def set_signal_triage(
    signal_id: uuid.UUID,
    body: TriageRequest,
    user: UserContext = Depends(require_user),
):
    """Set the caller's durable triage state on one signal (LUM-643).

    404 if the signal is not visible to the caller (never reveal existence outside
    ``visible_filter``); 422 on a bad status / snoozed-without-future-TZ-aware
    snoozed_until; 500 ``triage_update_failed`` on an unexpected DB error.
    """
    if not _signal_visible_to(user, signal_id):
        raise HTTPException(status_code=404, detail="not_found")
    try:
        row = signal_triage.set_triage(user.user_id, signal_id, body.status, body.snoozed_until)
    except signal_triage.TriageValidationError:
        raise HTTPException(status_code=422, detail="invalid_triage")
    except signal_triage.TriageSignalMissing:
        # FK race: signal deleted between the guard and the UPSERT.
        raise HTTPException(status_code=404, detail="not_found")
    except HTTPException:
        raise
    except Exception as exc:
        _log.error("set_signal_triage failed for %s: %s", signal_id, exc)
        raise HTTPException(status_code=500, detail="triage_update_failed")
    return {
        "signal_id": str(row["signal_id"]),
        "status": row["status"],
        "snoozed_until": row["snoozed_until"].isoformat() if row["snoozed_until"] else None,
        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
    }


@router.delete("/signals/{signal_id}/triage", status_code=204)
def clear_signal_triage(
    signal_id: uuid.UUID,
    user: UserContext = Depends(require_user),
):
    """Clear the caller's triage state on one signal → back to default derivation.

    Idempotent (204 even if there was no row). The visibility check runs BEFORE the
    delete — otherwise DELETE would become a 204-for-everything endpoint (Fable
    D5.1).
    """
    if not _signal_visible_to(user, signal_id):
        raise HTTPException(status_code=404, detail="not_found")
    try:
        signal_triage.clear_triage(user.user_id, signal_id)
    except Exception as exc:
        _log.error("clear_signal_triage failed for %s: %s", signal_id, exc)
        raise HTTPException(status_code=500, detail="triage_update_failed")
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# POST /signals/triage/bulk  (LUM-696)
# ---------------------------------------------------------------------------


class BulkTriageRequest(BaseModel):
    """Body for ``POST /signals/triage/bulk``.

    ``status=None`` means **clear** the caller's triage rows for these signals —
    the bulk equivalent of ``DELETE /signals/{id}/triage``. It is a field value
    rather than a separate route because the web client's undo replays a mix of
    set-batches and clear-batches through one code path.

    ``signal_ids`` is typed ``uuid.UUID`` so a non-UUID id is a 422 from Pydantic
    before any SQL is composed — the same typing the single-signal routes get
    from their path parameter.
    """

    signal_ids: list[uuid.UUID] = Field(min_length=1, max_length=signal_triage.MAX_BULK_SIGNALS)
    status: Optional[str] = None
    snoozed_until: Optional[datetime] = None


class BulkTriageResult(BaseModel):
    signal_id: str
    status: Optional[str] = None
    snoozed_until: Optional[str] = None
    updated_at: Optional[str] = None


class BulkTriageResponse(BaseModel):
    """``skipped`` is *requested minus visible* — never *requested minus written*.

    A cleared signal that never had a triage row is a **success** (idempotent,
    like the single-signal DELETE), so it appears in ``updated`` with null
    columns. Only ids the caller cannot see are skipped. Reporting them leaks
    nothing: the caller supplied the ids, and the single-signal route already
    answers this exact question with a 404 — ``skipped`` never distinguishes
    "does not exist" from "belongs to someone else".
    """

    updated: list[BulkTriageResult]
    skipped: list[str]
    count: int


def _iso(value) -> Optional[str]:
    return value.isoformat() if value else None


@router.post("/signals/triage/bulk", response_model=BulkTriageResponse)
def bulk_signal_triage(
    body: BulkTriageRequest,
    user: UserContext = Depends(require_user),
):
    """Set (or clear) the caller's triage state on up to 200 signals at once.

    One request, one statement. Visibility is enforced *inside* the write via the
    ``visible`` CTE, so — unlike the single-signal path's check-then-write — a
    signal purged mid-flight cannot fail the batch; it simply is not selected and
    comes back in ``skipped``.

    422 on a bad status, a snooze without a strictly-future TZ-aware
    ``snoozed_until``, an empty list, more than ``MAX_BULK_SIGNALS`` ids, or a
    non-UUID id. 500 ``triage_update_failed`` on an unexpected DB error.
    """
    requested = [str(sid) for sid in body.signal_ids]
    where_clause, where_params = visible_filter(user)
    try:
        if body.status is None:
            rows = signal_triage.clear_triage_bulk(
                user.user_id,
                requested,
                visible_clause=where_clause,
                visible_params=where_params,
            )
        else:
            rows = signal_triage.set_triage_bulk(
                user.user_id,
                requested,
                body.status,
                body.snoozed_until,
                visible_clause=where_clause,
                visible_params=where_params,
            )
    except signal_triage.TriageValidationError:
        raise HTTPException(status_code=422, detail="invalid_triage")
    except HTTPException:
        raise
    except Exception as exc:
        # Log the COUNT, not the ids: a 200-id dump per failure is noise, and the
        # sibling handlers log a single id at most.
        _log.error("bulk_signal_triage failed for %d signals: %s", len(requested), exc)
        raise HTTPException(status_code=500, detail="triage_update_failed")

    updated = [
        BulkTriageResult(
            signal_id=str(r["signal_id"]),
            status=r.get("status"),
            snoozed_until=_iso(r.get("snoozed_until")),
            updated_at=_iso(r.get("updated_at")),
        )
        for r in rows
    ]
    visible_ids = {u.signal_id for u in updated}
    # De-duplicated: a requested id repeated twice must not be reported skipped.
    skipped = [sid for sid in dict.fromkeys(requested) if sid not in visible_ids]
    return BulkTriageResponse(updated=updated, skipped=skipped, count=len(updated))


# ---------------------------------------------------------------------------
# PUT /profile
# ---------------------------------------------------------------------------


@router.put("/profile")
def upsert_profile(
    body: ProfileRequest,
    user: UserContext = Depends(require_user),
):
    """Create or update the relevance profile for the calling user."""
    try:
        ms = config.get_metadata_store()
        # SCOPE-EXEMPT: `relevance_profiles` is in plan §2.10's
        # excluded-from-scope list — relevance is a per-user signal-routing
        # config, not memory content; no `scope` column exists.
        existing = ms.fetch_one(
            "SELECT id FROM relevance_profiles WHERE user_id = %s", (user.user_id,)
        )
        if existing:
            # SCOPE-EXEMPT: see above.
            ms.execute(
                "UPDATE relevance_profiles SET "
                "tracked_locations = %s::jsonb, tracked_topics = %s::jsonb, "
                "tracked_entities = %s::jsonb, tracked_keywords = %s::jsonb, "
                "updated_at = NOW() WHERE user_id = %s",
                (
                    json.dumps(body.tracked_locations),
                    json.dumps(body.tracked_topics),
                    json.dumps(body.tracked_entities),
                    json.dumps(body.tracked_keywords),
                    user.user_id,
                ),
            )
            profile_id = str(existing["id"])
        else:
            profile_id = str(uuid.uuid4())
            ms.execute(
                "INSERT INTO relevance_profiles "
                "(id, user_id, tracked_locations, tracked_topics, "
                "tracked_entities, tracked_keywords) "
                "VALUES (%s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb)",
                (
                    profile_id,
                    user.user_id,
                    json.dumps(body.tracked_locations),
                    json.dumps(body.tracked_topics),
                    json.dumps(body.tracked_entities),
                    json.dumps(body.tracked_keywords),
                ),
            )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    return {
        "status": "ok",
        "profile_id": profile_id,
        "tracked_locations": body.tracked_locations,
        "tracked_topics": body.tracked_topics,
        "tracked_entities": body.tracked_entities,
        "tracked_keywords": body.tracked_keywords,
    }


# ---------------------------------------------------------------------------
# GET /profile
# ---------------------------------------------------------------------------


@router.get("/profile")
def get_profile(user: UserContext = Depends(require_user)):
    """Return the calling user's current relevance profile."""
    try:
        ms = config.get_metadata_store()
        # SCOPE-EXEMPT: `relevance_profiles` is in plan §2.10's
        # excluded-from-scope list — per-user routing config.
        row = ms.fetch_one(
            "SELECT id, tracked_locations, tracked_topics, tracked_entities, "
            "tracked_keywords, updated_at FROM relevance_profiles "
            "WHERE user_id = %s ORDER BY updated_at DESC LIMIT 1",
            (user.user_id,),
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    if not row:
        return {
            "profile_id": None,
            "tracked_locations": [],
            "tracked_topics": [],
            "tracked_entities": [],
            "tracked_keywords": [],
            "updated_at": None,
        }

    return {
        "profile_id": str(row["id"]),
        "tracked_locations": row["tracked_locations"] or [],
        "tracked_topics": row["tracked_topics"] or [],
        "tracked_entities": row["tracked_entities"] or [],
        "tracked_keywords": row["tracked_keywords"] or [],
        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
    }


# ---------------------------------------------------------------------------
# GET /profile/learned
# ---------------------------------------------------------------------------


@router.get("/profile/learned")
def get_learned_weights(user: UserContext = Depends(require_user)):
    """Return the calling user's current feedback-learned relevance weights (LUM-649).

    Human-readable, sparse (zero-weight terms omitted), demoted-first within each axis.
    200 with all-empty lists when no profile row exists yet — matches GET /profile's
    "no profile" behavior (never 404). Caller-scoped; no cross-user parameter accepted.
    """
    return relevance_learning.fetch_learned_weights_view(user.user_id)


# ---------------------------------------------------------------------------
# POST /feedback
# ---------------------------------------------------------------------------


@router.post("/feedback")
def record_feedback(
    body: FeedbackRequest,
    user: UserContext = Depends(require_user),
):
    """Record explicit (positive=true/false) or implicit (event_type=...) feedback."""
    from services.feedback import record_explicit
    from services.feedback import record_implicit

    if body.positive is not None:
        record_explicit(
            item_type=body.item_type,
            item_id=body.item_id,
            positive=body.positive,
            user_id=user.user_id,
        )
        resp = {"status": "ok", "type": "explicit", "positive": body.positive}
        # LUM-649: explicit signal feedback additionally nudges the caller's
        # learned relevance weights and names which term(s) moved, synchronously
        # (so the response can report the effect in the same request). Passes the
        # full UserContext so _load_signal_row can call visibility.visible_filter.
        # apply_signal_feedback never raises — no caller-side try/except needed.
        if body.item_type == "signal":
            resp["effect"] = relevance_learning.apply_signal_feedback(
                user=user, signal_id=body.item_id, positive=body.positive
            )
        return resp

    if body.event_type:
        record_implicit(
            item_type=body.item_type,
            item_id=body.item_id,
            event_type=body.event_type,
            user_id=user.user_id,
        )
        return {"status": "ok", "type": "implicit", "event_type": body.event_type}

    raise HTTPException(
        status_code=422,
        detail=(
            "Provide either 'positive' (bool) for explicit feedback "
            "or 'event_type' (str) for implicit."
        ),
    )


# ---------------------------------------------------------------------------
# Source detection helper
# ---------------------------------------------------------------------------


def _normalize_paperless_base_url(url: str) -> str:
    """Trim whitespace/slash and validate outbound policy (LUM-281)."""
    u = (url or "").strip().rstrip("/")
    if not u:
        raise ValueError("paperless base URL is empty")
    validate_outbound_connector_base_url(u)
    return u


def _detect_source(url: str) -> dict:
    """Try to detect the source type and return up to 3 preview items.

    Detection order:
      1. Try RSS/Atom auto-detection (feedparser + link tag scanning).
      2. Fall back to page scraping via trafilatura.

    Implementation lives in :mod:`services.signal_source_detection` so routes
    do not import :mod:`adapters` (``architecture_import_boundary_tests``).
    """
    return detect_signal_source(url)


def _infer_name(url: str) -> str:
    """Extract a readable source name from a URL."""
    from urllib.parse import urlparse

    host = urlparse(url).netloc or url
    return host.replace("www.", "").split(".")[0].capitalize()
