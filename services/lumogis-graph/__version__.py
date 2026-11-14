# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Single source of truth for the lumogis-graph service version.

Read by `routes/health.py`, `routes/capabilities.py` (manifest
`service_version`), and the `/mcp` server tool descriptions. Bump on
every release; CI verifies parity with the version reported by Core's
`CapabilityRegistry` health probe.
"""

__version__ = "0.1.0a1"
