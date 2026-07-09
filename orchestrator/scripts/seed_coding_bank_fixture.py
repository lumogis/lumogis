# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
#
# Idempotent coding-bank fixture seed (LUM-540). Lands `tests/fixtures/coding_bank.json`
# into Postgres `memories` + Qdrant `memories` with deterministic fixture ids for the
# tier-2 cursor integration p95 gate.
#
# Intended for `docker compose exec` against lumogis-test only — see
# scripts/seed-cursor-integration-fixture.sh.

from __future__ import annotations

import argparse
import json
import logging
import os
import uuid
from pathlib import Path

import auth as auth_mod
from services.memories import COLLECTION
from tests.cursor_integration.fixture_loader import load_coding_bank

import config
from services import mcp_tokens as mcp_tokens_svc

_log = logging.getLogger("scripts.seed_coding_bank_fixture")

_DEFAULT_USER_ID = "cursor-integration-full"
_DEFAULT_EMAIL = "cursor-integration-full@example.com"
_DEFAULT_ENV_FILE = Path("/project/ai-workspace/mcp/cursor-integration-full.env")
_BANKS = ("coding", "personal")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed coding_bank.json on lumogis-test")
    parser.add_argument(
        "--user-id", default=os.environ.get("CURSOR_INTEGRATION_FULL_USER_ID", _DEFAULT_USER_ID)
    )
    parser.add_argument("--fixture-path", type=Path, default=None)
    parser.add_argument("--wipe", action="store_true")
    parser.add_argument("--env-file", type=Path, default=_DEFAULT_ENV_FILE)
    return parser.parse_args()


def _qdrant_filter(*, user_id: str, bank: str) -> dict:
    return {
        "must": [
            {"key": "user_id", "match": {"value": user_id}},
            {"key": "bank", "match": {"value": bank}},
        ]
    }


def _ensure_user(user_id: str) -> None:
    if not auth_mod.auth_enabled():
        return
    ms = config.get_metadata_store()
    ms.execute(
        "INSERT INTO users (id, email, password_hash, role, disabled) "
        "VALUES (%s, %s, %s, %s, FALSE) "
        "ON CONFLICT (id) DO NOTHING",
        (user_id, _DEFAULT_EMAIL, "!", "user"),
    )


def _wipe(user_id: str) -> None:
    ms = config.get_metadata_store()
    vs = config.get_vector_store()
    for bank in _BANKS:
        ms.execute(
            "DELETE FROM memories WHERE user_id = %s AND bank = %s",
            (user_id, bank),
        )
        vs.delete_where(collection=COLLECTION, filter=_qdrant_filter(user_id=user_id, bank=bank))


def _seed_memory_row(
    *,
    user_id: str,
    bank: str,
    memory_id: str,
    content: str,
    tags: list[str],
    ms,
    embedder,
    vs,
) -> None:
    ms.execute(
        "INSERT INTO memories (id, user_id, bank, content, tags, metadata) "
        "VALUES (%s, %s, %s, %s, %s, %s::jsonb) "
        "ON CONFLICT (id) DO NOTHING",
        (memory_id, user_id, bank, content, tags, json.dumps({})),
    )
    vector = embedder.embed(content)
    point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"memory::{user_id}::{memory_id}"))
    vs.upsert(
        collection=COLLECTION,
        id=point_id,
        vector=vector,
        payload={"memory_id": memory_id, "user_id": user_id, "bank": bank},
    )


def _verify_counts(user_id: str, fixture) -> dict[str, int]:
    ms = config.get_metadata_store()
    counts: dict[str, int] = {}
    for bank in _BANKS:
        expected = len(fixture.memory_ids(bank))
        row = ms.fetch_one(
            "SELECT COUNT(*) AS n FROM memories WHERE user_id = %s AND bank = %s",
            (user_id, bank),
        )
        actual = int(row["n"]) if row else 0
        if actual != expected:
            raise RuntimeError(
                f"memory count mismatch bank={bank} expected={expected} actual={actual}"
            )
        counts[bank] = actual
    return counts


def _write_env_file(path: Path, token: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"LUMOGIS_CURSOR_INTEGRATION_MCP_TOKEN={token}\n", encoding="utf-8")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    args = _parse_args()
    user_id = args.user_id.strip()
    if not user_id:
        _log.error("user_id must be non-empty")
        return 2

    fixture = load_coding_bank(args.fixture_path) if args.fixture_path else load_coding_bank()

    try:
        ms = config.get_metadata_store()
        embedder = config.get_embedder()
        vs = config.get_vector_store()
    except Exception as exc:  # noqa: BLE001
        _log.error("start full lumogis-test stack (orchestrator + Postgres + Qdrant): %s", exc)
        return 1

    _ensure_user(user_id)
    if args.wipe:
        _log.info("wiping memories for user_id=%s banks=%s", user_id, _BANKS)
        _wipe(user_id)

    try:
        embedder.embed("warmup probe for cursor integration seed")
    except Exception as exc:  # noqa: BLE001
        _log.error("semantic vectors required — embedder/Ollama unavailable: %s", exc)
        return 1

    try:
        for bank in _BANKS:
            for mem in fixture.memories(bank):
                _seed_memory_row(
                    user_id=user_id,
                    bank=bank,
                    memory_id=mem["memory_id"],
                    content=mem["content"],
                    tags=list(mem.get("tags") or []),
                    ms=ms,
                    embedder=embedder,
                    vs=vs,
                )
    except Exception as exc:  # noqa: BLE001
        _log.error(
            "seed failed (Postgres may be partially written; re-run with --wipe): %s",
            exc,
        )
        return 1

    try:
        memory_counts = _verify_counts(user_id, fixture)
    except RuntimeError as exc:
        _log.error("%s", exc)
        return 2

    _row, plaintext = mcp_tokens_svc.mint(
        user_id,
        "cursor-integration-full",
        scopes=["mcp:read"],
    )
    env_file = args.env_file
    _write_env_file(env_file, plaintext)
    payload = {
        "ok": True,
        "user_id": user_id,
        "token": plaintext,
        "token_prefix": _row.token_prefix,
        "memory_counts": memory_counts,
        "env_file": str(env_file),
    }
    print(json.dumps(payload, sort_keys=True))
    _log.info(
        "seeded coding bank user_id=%s coding=%s personal=%s token_prefix=%s env_file=%s",
        user_id,
        memory_counts.get("coding"),
        memory_counts.get("personal"),
        _row.token_prefix,
        env_file,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
