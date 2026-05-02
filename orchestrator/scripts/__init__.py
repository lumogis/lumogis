# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Operator entrypoints — runnable as ``python -m scripts.<name>``.

This package is intentionally tiny: it exists only so individual
operator scripts (e.g. :mod:`scripts.rotate_credential_key`) can be
invoked from inside the orchestrator container without polluting the
import surface of the application.

No application code should import from this package; nothing here is
part of the runtime hot path.
"""
