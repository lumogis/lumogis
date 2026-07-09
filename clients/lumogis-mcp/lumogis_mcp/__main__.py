# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis

"""CLI entrypoint for ``lumogis-mcp``."""

from __future__ import annotations

import asyncio
import sys

from lumogis_mcp.config import ConfigError
from lumogis_mcp.config import load_config
from lumogis_mcp.proxy import run_stdio_proxy


def main() -> None:
    try:
        config = load_config()
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

    if config.token is None:
        print(
            "Warning: LUMOGIS_MCP_TOKEN is unset — bridge omits Authorization. "
            "Mint lmcp_… when Core requires auth (connect-and-verify Step 9d).",
            file=sys.stderr,
        )

    try:
        asyncio.run(run_stdio_proxy(config))
    except (ConfigError, ConnectionRefusedError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
