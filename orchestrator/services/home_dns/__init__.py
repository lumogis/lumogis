# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Lumogis Home DNS — a free secure address on the home network (LUM-331).

Kept empty on purpose: ``csrf.py`` and ``routes/auth.py`` import
``services.home_dns.validate`` on the request path, and importing a submodule runs
this file first. Re-exporting the I/O modules here would pull the Worker client and
the scheduler job into every authenticated write. Import submodules directly.
"""
