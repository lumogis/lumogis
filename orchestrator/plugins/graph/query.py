# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Compatibility shim: Core tests import ``plugins.graph.query``."""

from graph.query import *  # noqa: F403
from graph.query import _detect_entities_in_query  # noqa: F401
