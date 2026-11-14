# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Local FastMCP wiring for the KG service.

This package is named `kg_mcp` (not `mcp`) on purpose — using `mcp` here
would shadow the installed `mcp` PyPI package and silently break every
`from mcp.server.fastmcp import FastMCP` import in this service.
"""
