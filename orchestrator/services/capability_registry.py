# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Capability service registry (Area 2 ecosystem plumbing).

Discovers, validates, and holds out-of-process Lumogis capability services.
Discovery always uses **GET {base_url}/capabilities** (fixed path). The manifest
field ``capabilities_endpoint`` is **validated** (LUM-41): a manifest declaring
anything other than ``/capabilities`` is rejected — see ``CapabilityManifest``
docs and ``_fetch_one``. Each successful response must validate as
:class:`~models.capability.CapabilityManifest`.

Lifecycle:
    Startup: main.py lifespan calls `await registry.discover(urls)` once.
    Refresh: an APScheduler job re-runs discovery every 5 minutes via
             `registry.discover_sync(urls)` to pick up services that came
             online after Core started.

Design notes:
    - The registry is the first async-using service in the codebase. The
      rest of the orchestrator is synchronous; we honor the prompt's
      `async def discover` signature because it pairs naturally with the
      FastAPI async lifespan and allows parallel manifest fetches via
      asyncio.gather.
    - Per-URL fetch failures NEVER raise. Capability service availability
      is a soft dependency — Core must boot and continue to run even if
      every declared service is unreachable.
    - Compatibility check uses packaging.version.Version (no new semver
      dependency added).
"""

import asyncio
import logging
import threading
from datetime import datetime
from datetime import timezone

import httpx
from __version__ import __version__ as CORE_VERSION
from models.capability import CapabilityLicenseMode
from models.capability import CapabilityManifest
from models.capability import CapabilityTool
from packaging.version import InvalidVersion
from packaging.version import Version
from pydantic import BaseModel
from pydantic import ValidationError
from services.capability_egress import normalise_base_url as _normalise

_log = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_SECONDS = 5.0
"""Hard cap on every manifest fetch. Prevents a slow or hung capability
service from blocking startup or the refresh job."""

SUPPORTED_CONTRACT_MAJOR = 1
"""The capability invoke contract MAJOR version Core speaks (LUM-41). A manifest
declaring a different major is refused at registration; a differing MINOR is
accepted (forward-compatible)."""

# JSON Schema meta-validation of CapabilityTool.input_schema /
# .output_schema and CapabilityManifest.config_schema via the `jsonschema`
# library is a deliberate non-goal here. Manifest authors are trusted to
# provide valid JSON Schema; bad schemas surface at tool-invocation time
# in Area 4. Revisit if/when manifest-author errors become a real failure
# mode in the wild.


class RegisteredService(BaseModel):
    """A capability service whose manifest has been fetched and validated.

    Health state is mutated in place by `check_health()` from a scheduled
    job. CPython attribute assignment is atomic for these primitive fields,
    so concurrent reads from request handlers are safe — the worst case is
    a stale-by-one-tick value, never a torn write.

    `last_seen_healthy` is populated only by successful health probes per
    the Area 3 prompt; failed probes flip `healthy` False but leave the
    last-known-good timestamp untouched.

    `last_unhealthy_reason` carries a coarse structured code for the most
    recent failed probe (`timeout`, `connection_error`, `network_error`, or
    `http_<status>` — incl. `http_401` / `http_403` for a rejected bearer);
    it is cleared back to `None` on the next successful probe. Read-only
    consumers (e.g. the `/api/v1/me/tools` catalog facade) map it to specific
    operator-facing copy instead of a generic "not healthy" string (LUM-61).
    """

    manifest: CapabilityManifest
    base_url: str
    registered_at: datetime
    last_seen_healthy: datetime | None = None
    healthy: bool = False
    last_unhealthy_reason: str | None = None
    is_community: bool = False
    """LUM-613 trust classification: True → untrusted community capability, gated
    fail-closed at dispatch. Set explicitly by the registry from the origin-pinned
    first-party allowlist (`services.capability_egress`); the ``False`` default
    serves only direct construction of trusted test doubles — the production
    ``_upsert`` path always classifies explicitly, so it is never accidentally
    defaulted."""
    external_endpoints: tuple[str, ...] = ()
    """LUM-613 declared egress hosts (from the manifest, normalised). Read by the
    OOP tool audit + /me/tools visibility; LUM-618 enforces at the network layer."""

    async def check_health(self, transport: httpx.AsyncBaseTransport | None = None) -> bool:
        """Probe the capability service's declared health endpoint.

        Returns True iff the endpoint responds with HTTP 200 within the
        timeout. Updates `self.healthy` and (on success only) the
        `self.last_seen_healthy` timestamp. Never raises — capability
        service health is a soft signal, not a Core failure trigger.

        `transport` is the same TEST-ONLY seam used by CapabilityRegistry
        for hermetic testing. Production code does not pass it.
        """
        url = self.base_url.rstrip("/") + self.manifest.health_endpoint
        client_kwargs: dict = {"timeout": _DEFAULT_TIMEOUT_SECONDS}
        if transport is not None:
            client_kwargs["transport"] = transport

        try:
            async with httpx.AsyncClient(**client_kwargs) as client:
                resp = await client.get(url)
        except httpx.HTTPError as exc:
            self.healthy = False
            if isinstance(exc, httpx.TimeoutException):
                self.last_unhealthy_reason = "timeout"
            elif isinstance(exc, httpx.ConnectError):
                self.last_unhealthy_reason = "connection_error"
            else:
                self.last_unhealthy_reason = "network_error"
            _log.warning(
                "Capability service %s health probe failed: %s (%s)",
                self.manifest.id,
                url,
                exc.__class__.__name__,
            )
            return False

        if resp.status_code != 200:
            self.healthy = False
            # Concrete status is preserved (incl. 401/403); the read-only
            # catalog facade maps it to specific copy — auth-rejection for
            # 401/403, generic HTTP for the rest (LUM-61).
            self.last_unhealthy_reason = f"http_{resp.status_code}"
            _log.warning(
                "Capability service %s health probe returned HTTP %d at %s",
                self.manifest.id,
                resp.status_code,
                url,
            )
            return False

        self.healthy = True
        self.last_seen_healthy = datetime.now(timezone.utc)
        self.last_unhealthy_reason = None
        return True


class CapabilityRegistry:
    """Thread-safe registry of out-of-process capability services.

    Identified by manifest `id` (not URL) so URL changes do not duplicate
    entries. The lock guards both reads and writes; entries are immutable
    Pydantic models so callers can safely hold returned references.
    """

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None):
        # `transport` is a TEST-ONLY seam. Production callers must leave
        # it as None so a real httpx transport is used. Tests inject
        # httpx.MockTransport to drive the registry without a network.
        self._services: dict[str, RegisteredService] = {}
        self._lock = threading.Lock()
        self._transport = transport

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    async def discover(self, base_urls: list[str]) -> None:
        """Fetch and register manifests from each base URL in parallel.

        Per-URL failures are logged at WARNING and swallowed — this method
        never raises. Re-running discovery against an already-registered
        service updates its manifest in place (keyed by manifest.id), so
        scheduled refresh does not accumulate duplicates.
        """
        if not base_urls:
            return

        # LUM-613: warm the first-party trust map once here so the concurrent
        # per-URL `_fetch_one` classifications hit an in-memory cache rather than
        # racing to do the (blocking) file read on the event loop.
        from services.capability_egress import load_first_party_capabilities

        load_first_party_capabilities()

        client_kwargs: dict = {"timeout": _DEFAULT_TIMEOUT_SECONDS}
        if self._transport is not None:
            client_kwargs["transport"] = self._transport

        async with httpx.AsyncClient(**client_kwargs) as client:
            results = await asyncio.gather(
                *(self._fetch_one(client, url) for url in base_urls),
                return_exceptions=True,
            )

        registered = sum(1 for r in results if r is True)
        _log.info(
            "Capability discovery complete: %d/%d services registered",
            registered,
            len(base_urls),
        )

    def discover_sync(self, base_urls: list[str]) -> None:
        """Sync wrapper for APScheduler. Do not call from async contexts.

        APScheduler's BackgroundScheduler runs jobs in worker threads where
        no event loop exists; asyncio.run() is safe there. Calling this
        from within an async function (e.g. a FastAPI route) will raise
        a RuntimeError because asyncio.run() refuses to nest event loops.
        """
        try:
            asyncio.run(self.discover(base_urls))
        except Exception:
            _log.exception("Capability registry refresh failed")

    async def _fetch_one(self, client: httpx.AsyncClient, base_url: str) -> bool:
        """Fetch one manifest. Returns True on successful registration."""
        url = base_url.rstrip("/") + "/capabilities"
        try:
            resp = await client.get(url)
        except httpx.HTTPError as exc:
            _log.warning(
                "Capability service unreachable at %s (%s) — skipping",
                url,
                exc.__class__.__name__,
            )
            return False

        if resp.status_code != 200:
            _log.warning(
                "Capability service at %s returned HTTP %d — skipping",
                url,
                resp.status_code,
            )
            return False

        try:
            manifest = CapabilityManifest.model_validate_json(resp.content)
        except ValidationError as exc:
            _log.warning(
                "Capability service at %s returned an invalid manifest: %s",
                url,
                exc.errors(include_url=False)[:3],
            )
            # LUM-613: content-validation failure on an already-registered base_url
            # must evict the stale registration (fail-closed for the refresh case).
            # The manifest failed to parse, so key eviction by base_url, not id.
            self._evict_by_base_url(base_url, reason="invalid_manifest")
            return False
        except ValueError as exc:
            _log.warning(
                "Capability service at %s returned non-JSON content: %s",
                url,
                exc,
            )
            self._evict_by_base_url(base_url, reason="non_json_manifest")
            return False

        # capabilities_endpoint is validated, not documentary (LUM-41): Core
        # discovered this manifest at the fixed /capabilities bootstrap path, so
        # the declared value must agree — a self-declared discovery path is a
        # bootstrap paradox and a divergent value is a manifest authoring error.
        ce = (manifest.capabilities_endpoint or "").strip()
        if ce != "/capabilities":
            _log.warning(
                "Capability service %s declares capabilities_endpoint=%r but Core "
                "discovered it at /capabilities; the field must match — skipping",
                manifest.id,
                ce,
            )
            self._evict_by_base_url(base_url, reason="capabilities_endpoint_mismatch")
            return False

        # LUM-612: the manifest id becomes the `capability.{id}` permission
        # connector; it must be grant-shaped or its scopes could be enforced
        # (fail-closed) yet never granted (silent lockout). Refuse loudly instead.
        from services.capability_scopes import is_grantable_capability_id
        from services.capability_scopes import malformed_required_scopes

        if not is_grantable_capability_id(manifest.id):
            _log.warning(
                "Capability service id=%r is not grant-shaped (expected "
                "[a-z0-9][a-z0-9._-]{0,42}) — skipping so it can't be a "
                "permanently-ungrantable capability",
                manifest.id,
            )
            self._evict_by_base_url(base_url, reason="ungrantable_id")
            return False

        # LUM-612: every declared required scope must be grantable (resource:action);
        # a malformed one would be enforced fail-closed yet never grantable — the
        # same silent-lockout hazard as a bad id. Refuse the whole manifest loudly.
        bad_scopes = malformed_required_scopes(manifest)
        if bad_scopes:
            _log.warning(
                "Capability service id=%r declares malformed permissions_required "
                "%r (expected resource:action) — skipping so it can't enforce an "
                "ungrantable scope",
                manifest.id,
                bad_scopes,
            )
            self._evict_by_base_url(base_url, reason="malformed_required_scopes")
            return False

        if not self._is_compatible(manifest):
            self._evict_by_base_url(base_url, reason="incompatible_version")
            return False

        # LUM-613: classify trust from the origin-pinned first-party allowlist
        # (id + expected base_url), then upsert.
        from services.capability_egress import is_community_capability
        from services.capability_egress import load_first_party_capabilities

        is_community = is_community_capability(
            capability_id=manifest.id,
            base_url=base_url,
            first_party=load_first_party_capabilities(),
        )
        self._upsert(manifest, base_url, is_community)
        return True

    def _is_compatible(self, manifest: CapabilityManifest) -> bool:
        """Compare manifest.min_core_version against CORE_VERSION and negotiate the
        invoke contract version.

        An unparseable version on either side is treated as incompatible
        (logged) rather than crashing the registry. For the invoke contract
        (LUM-41): an unknown MAJOR contract_version is refused (Core cannot speak
        it); an unknown MINOR is accepted (forward-compatible).
        """
        if not self._contract_version_ok(manifest):
            return False
        try:
            required = Version(manifest.min_core_version)
            current = Version(CORE_VERSION)
        except InvalidVersion as exc:
            _log.warning(
                "Capability service %s declares unparseable min_core_version=%r "
                "(or Core version %r is unparseable): %s — skipping",
                manifest.id,
                manifest.min_core_version,
                CORE_VERSION,
                exc,
            )
            return False

        if current < required:
            _log.warning(
                "Capability service %s requires Core >= %s but running %s — skipping",
                manifest.id,
                required,
                current,
            )
            return False
        return True

    def _contract_version_ok(self, manifest: CapabilityManifest) -> bool:
        """Negotiate the invoke contract version (LUM-41).

        Unknown MAJOR → refuse (Core cannot invoke it); unknown MINOR → accept
        (forward-compatible). An unparseable value is refused.
        """
        raw = (manifest.contract_version or "").strip()
        try:
            major = int(raw.split(".", 1)[0])
        except (ValueError, IndexError):
            _log.warning(
                "Capability service %s declares unparseable contract_version=%r — skipping",
                manifest.id,
                raw,
            )
            return False
        if major != SUPPORTED_CONTRACT_MAJOR:
            _log.warning(
                "Capability service %s declares contract_version=%r (major %d) but "
                "Core speaks major %d — skipping",
                manifest.id,
                raw,
                major,
                SUPPORTED_CONTRACT_MAJOR,
            )
            return False
        return True

    def _upsert(self, manifest: CapabilityManifest, base_url: str, is_community: bool) -> None:
        now = datetime.now(timezone.utc)
        endpoints = tuple(manifest.external_endpoints)
        with self._lock:
            existing = self._services.get(manifest.id)
            if existing is None:
                self._services[manifest.id] = RegisteredService(
                    manifest=manifest,
                    base_url=base_url,
                    registered_at=now,
                    is_community=is_community,
                    external_endpoints=endpoints,
                )
                _log.info(
                    "Registered capability service: %s v%s (%d tools) at %s [trust=%s, egress=%s]",
                    manifest.id,
                    manifest.version,
                    len(manifest.tools),
                    base_url,
                    "community" if is_community else "first-party",
                    list(endpoints),  # hostnames are non-secret
                )
                return

            # LUM-613 (origin-conflict resolution): the manifest fetch is
            # unauthenticated, so an existing `id` re-appearing from a DIFFERENT
            # origin needs care. The tie-breaker is trust, and trust is anchored
            # to the operator's pinned origin (`first_party_capabilities.txt`):
            #   - Incoming is **first-party** (is_community=False ⇒ it matched the
            #     operator's pinned origin) → it WINS and replaces the existing
            #     entry. This lets the genuine service reclaim its id from a shadow
            #     that registered first (fixes the discovery-race lock-out) and lets
            #     an operator legitimately move a service's origin (after updating
            #     the pin). A community/shadow can NOT reach this branch as
            #     first-party without controlling the pinned origin (network
            #     compromise — the documented residual closed by LUM-614 signing).
            #   - Incoming is **community** at a different origin → REFUSE. This is
            #     the shadow-takeover guard: a community/untrusted response can
            #     never displace an existing registration of the same id.
            if _normalise(existing.base_url) != _normalise(base_url):
                if is_community:
                    _log.warning(
                        "capability_identity_conflict: id=%r already registered at "
                        "origin=%r; refusing a COMMUNITY response from a different "
                        "origin=%r (possible shadow/takeover) — keeping the existing "
                        "registration",
                        manifest.id,
                        existing.base_url,
                        base_url,
                    )
                    return
                _log.info(
                    "capability_identity_reclaim: id=%r first-party service at its "
                    "pinned origin=%r replaces the prior registration at origin=%r "
                    "(shadow reclaim or operator-approved origin change)",
                    manifest.id,
                    base_url,
                    existing.base_url,
                )
                # fall through to the drift audit + in-place replace below

            # LUM-613 (drift audit): endpoints can change at any 5-min refresh
            # with no version bump — record the added/removed-hosts diff so a
            # post-incident review (and a future re-consent flow) has the record.
            old_endpoints = tuple(existing.manifest.external_endpoints)
            if set(old_endpoints) != set(endpoints):
                added = sorted(set(endpoints) - set(old_endpoints))
                removed = sorted(set(old_endpoints) - set(endpoints))
                _log.info(
                    "capability_egress_drift: id=%r external_endpoints changed added=%s removed=%s",
                    manifest.id,
                    added,
                    removed,
                )

            # Refresh in place — preserve registered_at and health state,
            # recompute the derived trust + egress fields (never carry stale).
            self._services[manifest.id] = existing.model_copy(
                update={
                    "manifest": manifest,
                    "base_url": base_url,
                    "is_community": is_community,
                    "external_endpoints": endpoints,
                }
            )
            if existing.manifest.version != manifest.version:
                _log.info(
                    "Updated capability service: %s %s -> %s",
                    manifest.id,
                    existing.manifest.version,
                    manifest.version,
                )

    def _evict_by_base_url(self, base_url: str, *, reason: str) -> None:
        """Remove any registered service discovered at ``base_url`` (LUM-613).

        Called only on **content-validation** failures of an already-registered
        base_url (bad manifest / id / scopes / incompatible) — NOT on transient
        network/HTTP failures, which stay soft so a blip never evicts a healthy
        service. The registry is keyed by manifest id, so this reverse-looks-up
        by base_url (a failed manifest may not parse to an id).
        """
        target = _normalise(base_url)
        with self._lock:
            victims = [
                sid for sid, svc in self._services.items() if _normalise(svc.base_url) == target
            ]
            for sid in victims:
                del self._services[sid]
                _log.warning(
                    "capability_evicted: id=%r at %s removed (reason=%s) — "
                    "stale/invalid registration no longer dispatchable",
                    sid,
                    base_url,
                    reason,
                )

    # ------------------------------------------------------------------
    # Read API (lock-guarded; returns copies so callers cannot mutate
    # internal state)
    # ------------------------------------------------------------------

    def get_service(self, service_id: str) -> RegisteredService | None:
        with self._lock:
            return self._services.get(service_id)

    def get_tools(self, license_mode: CapabilityLicenseMode | None = None) -> list[CapabilityTool]:
        with self._lock:
            services = list(self._services.values())
        tools: list[CapabilityTool] = []
        for svc in services:
            for tool in svc.manifest.tools:
                if license_mode is None or tool.license_mode == license_mode:
                    tools.append(tool)
        return tools

    def all_services(self) -> list[RegisteredService]:
        with self._lock:
            return list(self._services.values())

    # ------------------------------------------------------------------
    # Health probing (Area 3)
    # ------------------------------------------------------------------

    async def check_all_health(self) -> None:
        """Probe every registered service's health endpoint in parallel.

        Each probe mutates its own `RegisteredService` in place. Per-service
        failures are swallowed (handled inside `check_health()` which never
        raises), so this method itself never raises. A capability service
        being unhealthy is reported but never escalated into Core failure.
        """
        with self._lock:
            services = list(self._services.values())
        if not services:
            return
        await asyncio.gather(
            *(svc.check_health(transport=self._transport) for svc in services),
            return_exceptions=True,
        )

    def check_all_health_sync(self) -> None:
        """Sync wrapper for APScheduler. Do not call from async contexts.

        APScheduler's BackgroundScheduler runs jobs in worker threads where
        no event loop exists; asyncio.run() is safe there. Calling this
        from within an async function will raise RuntimeError.
        """
        try:
            asyncio.run(self.check_all_health())
        except Exception:
            _log.exception("Capability registry health refresh failed")
