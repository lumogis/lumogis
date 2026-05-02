# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Process-local Fernet key for tests.

Generated once per Python process at import time so secret scanners never
see static Fernet material in the repo; semantics are unchanged from a
pinned test key.
"""

from __future__ import annotations

from cryptography.fernet import Fernet

TEST_FERNET_KEY = Fernet.generate_key().decode()
