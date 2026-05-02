# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Side-effect imports register all batch queue handlers."""

from . import entities_extract as _entities_extract  # noqa: F401
from . import ingest_folder as _ingest_folder  # noqa: F401
from . import session_end as _session_end  # noqa: F401
