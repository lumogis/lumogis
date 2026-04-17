# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Lumogis
"""Capability service registry (Area 2 ecosystem plumbing).

Discovers, validates, and holds out-of-process Lumogis capability services.
Each service must expose GET /capabilities returning a CapabilityManifest
(see orchestrator/models/capability.py).

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
from packaging.version import InvalidVersion
from packaging.version import Version
from pydantic import BaseModel
from pydantic import ValidationError

from __version__ import __version__ as CORE_VERSION
from models.capability import CapabilityLicenseMode
from models.capability import CapabilityManifest
from models.capability import CapabilityTool

_log = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_SECONDS = 5.0
"""Hard cap on every manifest fetch. Prevents a slow or hung capability
service from blocking startup or the refresh job."""

# JSON Schema meta-validation of CapabilityTool.input_schema /
# .output_schema and CapabilityManifest.config_schema via the `jsonschema`
# library is a deliberate non-goal here. Manifest authors are trusted to
# provide valid JSON Schema; bad schemas surface at tool-invocation time
# in Area 4. Revisit if/when manifest-author errors become a real failure
# mode in the wild.


class RegisteredService(BaseModel):
    """A capability service whose manifest has been fetched and validated.

    `last_seen_healthy` is populated by Area 3's health check. It stays
    None until the first successful health probe.
    """

    manifest: CapabilityManifest
    base_url: str
    registered_at: datetime
    last_seen_healthy: datetime | None = None
    healthy: bool = False


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
            return False
        except ValueError as exc:
            _log.warning(
                "Capability service at %s returned non-JSON content: %s",
                url,
                exc,
            )
            return False

        if not self._is_compatible(manifest):
            return False

        self._upsert(manifest, base_url)
        return True

    def _is_compatible(self, manifest: CapabilityManifest) -> bool:
        """Compare manifest.min_core_version against CORE_VERSION.

        An unparseable version on either side is treated as incompatible
        (logged) rather than crashing the registry.
        """
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

    def _upsert(self, manifest: CapabilityManifest, base_url: str) -> None:
        now = datetime.now(timezone.utc)
        with self._lock:
            existing = self._services.get(manifest.id)
            if existing is None:
                self._services[manifest.id] = RegisteredService(
                    manifest=manifest,
                    base_url=base_url,
                    registered_at=now,
                )
                _log.info(
                    "Registered capability service: %s v%s (%d tools) at %s",
                    manifest.id,
                    manifest.version,
                    len(manifest.tools),
                    base_url,
                )
            else:
                # Refresh in place — preserve registered_at and health
                # state, replace mutable manifest + base_url.
                self._services[manifest.id] = existing.model_copy(
                    update={"manifest": manifest, "base_url": base_url}
                )
                if existing.manifest.version != manifest.version:
                    _log.info(
                        "Updated capability service: %s %s -> %s",
                        manifest.id,
                        existing.manifest.version,
                        manifest.version,
                    )

    # ------------------------------------------------------------------
    # Read API (lock-guarded; returns copies so callers cannot mutate
    # internal state)
    # ------------------------------------------------------------------

    def get_service(self, service_id: str) -> RegisteredService | None:
        with self._lock:
            return self._services.get(service_id)

    def get_tools(
        self, license_mode: CapabilityLicenseMode | None = None
    ) -> list[CapabilityTool]:
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
