# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Code-structure ingest for the coding KG (LUM-301).

Native tree-sitter parsing of source repos into ``Component`` / ``Library``
nodes and ``CALLS`` / ``DEPENDS_ON`` / ``IMPLEMENTS`` edges, projected into the
coding bank's FalkorDB graph.

Part of the knowledge-graph service (``services/lumogis-graph/``), AGPL-3.0-only
like the rest of it (ADR 229). Enabled only when the graph is configured and
``LUMOGIS_CODEGRAPH_ENABLED=true`` (off by default). Design: ADR 134.
"""

# Lazy exports — avoid importing the graph-backed ingest module (and tree-sitter)
# at package import time so the pure parser/mapper modules stay light to import.
__all__ = ["ingest_roots", "register_codegraph_jobs"]


def __getattr__(name):  # PEP 562 lazy attribute access
    if name in __all__:
        from codegraph import ingest

        return getattr(ingest, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
