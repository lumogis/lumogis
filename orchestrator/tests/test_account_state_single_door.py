# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Structural guard: account-state SQL has one home (LUM-885).

Security audit #2 ID-001 came from three divergent copies of "bump
``token_version`` and revoke what the user holds". After LUM-885 the only
statement that bumps ``token_version`` lives in ``services/auth_sessions.py``
(``bump_token_version_in_transaction``), and the only statements that change a
user's role or disable them live in ``services/users.py``, next to the helper
that revokes the credentials. A new call site that writes either elsewhere
re-opens the gap, so it fails here.
"""

from __future__ import annotations

import re
from pathlib import Path

_ORCH = Path(__file__).resolve().parent.parent

_TV_BUMP = re.compile(r"token_version\s*=\s*token_version\s*\+", re.IGNORECASE)
_ROLE_OR_DISABLE = re.compile(r"update\s+users\s+set\s+(role|disabled\s*=\s*true)", re.IGNORECASE)

_TV_HOME = "services/auth_sessions.py"
_STATE_HOME = "services/users.py"


def violations(sources: dict[str, str]) -> list[str]:
    """``{relative path: source}`` -> messages for statements outside their home."""
    out: list[str] = []
    for rel, text in sorted(sources.items()):
        if rel != _TV_HOME and _TV_BUMP.search(text):
            out.append(
                f"{rel}: bumps users.token_version outside {_TV_HOME}; "
                "call services.users.revoke_credentials_in_transaction instead"
            )
        if rel != _STATE_HOME and _ROLE_OR_DISABLE.search(text):
            out.append(
                f"{rel}: changes users.role / users.disabled outside {_STATE_HOME}; "
                "call services.users.update_role / set_disabled "
                "(they use revoke_credentials_in_transaction)"
            )
    return out


def _production_sources() -> dict[str, str]:
    sources: dict[str, str] = {}
    for path in _ORCH.rglob("*.py"):
        rel = path.relative_to(_ORCH).as_posix()
        if rel.startswith("tests/"):
            continue
        sources[rel] = path.read_text(encoding="utf-8")
    return sources


def test_no_token_version_or_role_update_outside_owners():
    sources = _production_sources()
    assert _TV_HOME in sources and _STATE_HOME in sources  # the walk found the homes
    assert violations(sources) == []


def test_guard_is_not_vacuous():
    """The guard fails on a synthetic offender, and passes on the real homes."""
    offenders = {
        "routes/oops.py": 'ms.execute("UPDATE users SET role = %s WHERE id = %s", (r, u))',
        "services/oops2.py": 'ms.execute("update users set disabled = TRUE where id = %s")',
        "services/oops3.py": 'q = "UPDATE users SET token_version = token_version + 1"',
    }
    msgs = violations(offenders)
    assert len(msgs) == 3
    assert any("revoke_credentials_in_transaction" in m for m in msgs)
    homes = {
        _TV_HOME: 'q = "UPDATE users SET token_version = token_version + 1"',
        _STATE_HOME: 'q = "UPDATE users SET role = %s WHERE id = %s"',
    }
    assert violations(homes) == []
