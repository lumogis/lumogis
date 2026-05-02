# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Connector substrate.

Holds the canonical registry of per-user connector ids — see
:mod:`connectors.registry` for the source of truth and the validation
helpers used by :mod:`services.connector_credentials` and the
``/api/v1/{me,admin/users/{user_id}}/connector-credentials`` routes.
"""
