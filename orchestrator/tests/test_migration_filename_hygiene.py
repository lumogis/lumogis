# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Migration filename hygiene guard (LUM-590).

The migration runner (:mod:`db_migrations`) tracks applied migrations **by
filename** in ``schema_migrations`` and discovers them by glob + lexical sort.
Two consequences follow, and this test enforces both:

1. **No new duplicate integer prefixes.** Parallel branches that each grab the
   same next number produce colliding files (e.g. ``044-a.sql`` and
   ``044-b.sql``). Lexical order still applies them deterministically, but the
   collision is confusing for operators and a smell. Three such collisions
   already exist in history (024/043/044) and are *grandfathered* here: because
   the runner keys on filename, renaming an already-applied migration would make
   the renamed file re-run on every existing install. Each grandfathered pair
   creates disjoint objects behind idempotent guards, so re-application order is
   irrelevant — but they must not be renumbered. New collisions are rejected.

2. **The next free integer is unambiguous.** ``max(prefix) + 1`` must be unused,
   so a contributor can always compute the next number without ambiguity.

This is a pure-filesystem test — it needs no database and runs in the fast unit
suite.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_MIGRATIONS_DIR = _REPO_ROOT / "postgres" / "migrations"

# Integer prefixes with a known, accepted historical duplicate. Keyed on the
# prefix; the value is the exact set of filenames that share it. Grandfathered
# because they are already applied in the field and the runner tracks by
# filename (renaming would re-trigger them). Do NOT add to this set — resolve
# new collisions by picking the next free integer instead.
_GRANDFATHERED_COLLISIONS: dict[int, set[str]] = {
    24: {
        "024-paperless-external-documents.sql",
        "024-sessions-user-updated-at-index.sql",
    },
    43: {
        "043-biography-conflict-resolutions.sql",
        "043-privacy-mode.sql",
    },
    44: {
        "044-entities-write-isolation.sql",
        "044-user-invites.sql",
    },
}

# Tokens that make a migration safe to re-apply / order-independent. At least
# one must appear in each grandfathered colliding file.
_IDEMPOTENT_GUARD_TOKENS = (
    "IF NOT EXISTS",
    "OR REPLACE",
    "IF EXISTS",
    "ON CONFLICT",
)

_PREFIX_RE = re.compile(r"^(\d+)-")


def _migration_files() -> list[Path]:
    files = sorted(_MIGRATIONS_DIR.glob("*.sql"))
    assert files, f"no migration files found under {_MIGRATIONS_DIR}"
    return files


def _prefix_map() -> dict[int, list[str]]:
    by_prefix: dict[int, list[str]] = defaultdict(list)
    for path in _migration_files():
        m = _PREFIX_RE.match(path.name)
        assert m, f"migration {path.name!r} does not start with an integer prefix"
        by_prefix[int(m.group(1))].append(path.name)
    return by_prefix


def test_every_migration_has_integer_prefix() -> None:
    for path in _migration_files():
        assert _PREFIX_RE.match(path.name), f"{path.name!r} lacks an NNN- integer prefix"


def test_no_unexpected_prefix_collisions() -> None:
    collisions = {
        prefix: sorted(names) for prefix, names in _prefix_map().items() if len(names) > 1
    }
    expected = {prefix: sorted(names) for prefix, names in _GRANDFATHERED_COLLISIONS.items()}
    assert collisions == expected, (
        "migration integer-prefix collisions changed.\n"
        f"  found:       {collisions}\n"
        f"  grandfathered: {expected}\n"
        "Pick the next free integer for a new migration instead of reusing a "
        "prefix; renaming an applied migration re-triggers it (runner keys on "
        "filename). If a historical collision was intentionally resolved, update "
        "_GRANDFATHERED_COLLISIONS."
    )


def test_grandfathered_collisions_are_idempotent() -> None:
    for prefix, names in _GRANDFATHERED_COLLISIONS.items():
        for name in names:
            sql = (_MIGRATIONS_DIR / name).read_text(encoding="utf-8").upper()
            assert any(tok in sql for tok in _IDEMPOTENT_GUARD_TOKENS), (
                f"grandfathered colliding migration {name!r} (prefix {prefix}) has no "
                f"idempotent guard {_IDEMPOTENT_GUARD_TOKENS}; order-independence is "
                "not guaranteed"
            )


def test_next_free_integer_is_unambiguous() -> None:
    by_prefix = _prefix_map()
    next_free = max(by_prefix) + 1
    assert next_free not in by_prefix, (
        f"computed next free migration integer {next_free:03d} is already in use"
    )
    # Guard against silent drift: keep this in step with the highest migration.
    assert next_free == 52, (
        f"next free migration integer is {next_free:03d}; update this assertion "
        "when adding migrations so the expected next number stays documented"
    )
