# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-892 — file half of the DR round trip (``scripts/integration-backup-roundtrip.sh``).

Runs INSIDE the orchestrator image (it needs the service code), as the non-root
runtime uid, with ``PYTHONPATH=/app``. Subcommands:

``seed``
    Inserts one uploaded document (``file_index`` row + file under the uploads dir)
    and one capture photo (``captures`` + ``capture_attachments`` rows + blob written
    through ``media_storage.safe_attachment_path``, the same path
    ``services/captures.py`` takes), and adds a phrase to the admin's
    stop-entities list through the admin route (LUM-922). Writes
    ``/workspace/dr-roundtrip-seed.json``.

``assert-restored``
    After wipe + restore: the document is openable (``source_available`` is what
    makes Re-ingest available in Lumogis Web), the capture downloads with the same
    sha256, the stop phrase is in force from ``<workspace>/config``, and nothing is
    reported missing.

``assert-missing``
    Negative control, run after the workspace is wiped again: the same rows must now
    be reported missing and the stop phrase must be gone. It proves the positive
    assertions are not vacuous.
    ``--seed-json`` points at the seed file saved on the host before the wipe.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from pathlib import Path

from auth import UserContext

import config
from services import backup_status
from services import captures
from services import documents
from services import media_storage

USER_ID = "dr-roundtrip"
SEED_FILE = Path("/workspace/dr-roundtrip-seed.json")
DOC_BYTES = b"dr roundtrip uploaded document\n"
CAPTURE_BYTES = b"\xff\xd8\xff\xe0 dr roundtrip capture photo \xff\xd9"
STOP_PHRASE = "DR Roundtrip Stop Phrase"


def _stop_phrase_in_force() -> bool:
    config._stop_entity_set = set()
    config._stop_entity_mtime = None
    return STOP_PHRASE.lower() in config.get_stop_entity_set()


def seed() -> int:
    ms = config.get_metadata_store()

    # Uploaded document: the file first, then the row that points at it.
    doc_path = config.get_uploads_path() / USER_ID / "1_roundtrip.txt"
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    doc_path.write_bytes(DOC_BYTES)
    row = ms.fetch_one(
        "INSERT INTO file_index (file_path, file_hash, file_type, chunk_count, user_id, scope) "
        "VALUES (%s, %s, 'txt', 1, %s, 'personal') "
        "ON CONFLICT (user_id, file_path) WHERE published_from IS NULL DO UPDATE SET "
        "file_hash = EXCLUDED.file_hash RETURNING id",
        (str(doc_path), hashlib.sha256(DOC_BYTES).hexdigest(), USER_ID),
    )
    assert row is not None
    doc_id = int(row["id"])

    # Capture photo: parent row, then the blob, then the attachment row.
    cap = ms.fetch_one(
        "INSERT INTO captures (user_id, status, capture_type) VALUES (%s, 'pending', 'photo') "
        "RETURNING id",
        (USER_ID,),
    )
    assert cap is not None
    capture_id = str(cap["id"])
    attachment_id = str(uuid.uuid4())
    leaf = media_storage.leaf_filename_for_mime("image/jpeg")
    storage_key = f"{USER_ID}/{capture_id}/{attachment_id}/{leaf}"
    disk_path = media_storage.safe_attachment_path(USER_ID, capture_id, attachment_id, leaf)
    disk_path.parent.mkdir(parents=True, exist_ok=True)
    disk_path.write_bytes(CAPTURE_BYTES)
    sha = hashlib.sha256(CAPTURE_BYTES).hexdigest()
    ms.execute(
        "INSERT INTO capture_attachments (id, capture_id, user_id, attachment_type, storage_key, "
        "original_filename, mime_type, size_bytes, sha256, processing_status) "
        "VALUES (%s::uuid, %s::uuid, %s, 'image', %s, 'roundtrip.jpg', 'image/jpeg', "
        "%s, %s, 'stored')",
        (attachment_id, capture_id, USER_ID, storage_key, len(CAPTURE_BYTES), sha),
    )

    # LUM-922: the admin's stop-entities list, as boot and the admin route write it.
    from routes.admin import StopEntityRequest
    from routes.admin import kg_stop_entities_post

    config.ensure_stop_entities_file()
    kg_stop_entities_post(StopEntityRequest(action="add", phrase=STOP_PHRASE))
    stop_path = config.get_stop_entities_path()
    if not stop_path.startswith(str(config.get_workspace_path()) + "/"):
        raise SystemExit(f"stop-entities list is not in the workspace: {stop_path}")

    SEED_FILE.write_text(
        json.dumps(
            {
                "document_id": doc_id,
                "capture_id": capture_id,
                "attachment_id": attachment_id,
                "capture_sha256": sha,
            }
        ),
        encoding="utf-8",
    )
    print(f"seeded document_id={doc_id} capture_id={capture_id} attachment_id={attachment_id}")
    return 0


def _load_seed(path: str | None) -> dict:
    return json.loads(Path(path or SEED_FILE).read_text(encoding="utf-8"))


def _user() -> UserContext:
    return UserContext(user_id=USER_ID, is_authenticated=True, role="user")


def assert_restored(seed_json: str | None) -> int:
    seeded = _load_seed(seed_json)
    problems: list[str] = []

    detail = documents.get_document(_user(), int(seeded["document_id"]))
    if detail.source_available is not True:
        problems.append("restored document is not openable (source_available is not true)")

    path, mime, _name = captures.get_attachment_download(
        config.get_metadata_store(),
        user_id=USER_ID,
        capture_id=seeded["capture_id"],
        attachment_id=seeded["attachment_id"],
    )
    got = hashlib.sha256(path.read_bytes()).hexdigest()
    if got != seeded["capture_sha256"]:
        problems.append(f"capture bytes differ after restore ({got} != {seeded['capture_sha256']})")
    if mime != "image/jpeg":
        problems.append(f"capture mime changed to {mime}")

    if config.get_stop_entities_read_path() != config.get_stop_entities_path():
        problems.append("the stop-entities list was not restored to the workspace")
    elif not _stop_phrase_in_force():
        problems.append("the restored stop-entities list does not hold the seeded phrase")

    backup_status.clear_missing_files_cache_for_tests()
    missing = backup_status.count_missing_files()
    if missing is None or missing.total != 0:
        problems.append(f"files reported missing after restore: {missing}")

    if problems:
        print("assert-restored FAILED:\n  " + "\n  ".join(problems), file=sys.stderr)
        return 1
    print(
        "assert-restored ok: document openable, capture bytes identical, "
        "stop-entities list back, 0 files missing"
    )
    return 0


def assert_missing(seed_json: str | None) -> int:
    seeded = _load_seed(seed_json)
    problems: list[str] = []

    backup_status.clear_missing_files_cache_for_tests()
    missing = backup_status.count_missing_files()
    if missing is None or missing.total != 2:
        problems.append(f"expected 2 files missing (1 upload + 1 capture), got {missing}")
    detail = documents.get_document(_user(), int(seeded["document_id"]))
    if detail.source_available is not False:
        problems.append("document still reports source_available after the workspace was wiped")
    if _stop_phrase_in_force():
        problems.append("the stop phrase is still in force after the workspace was wiped")

    if problems:
        print("assert-missing FAILED:\n  " + "\n  ".join(problems), file=sys.stderr)
        return 1
    print("assert-missing ok: the wiped workspace is detected (the positive check is not vacuous)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["seed", "assert-restored", "assert-missing"])
    parser.add_argument("--seed-json", default=None)
    args = parser.parse_args(argv)
    if args.command == "seed":
        return seed()
    if args.command == "assert-restored":
        return assert_restored(args.seed_json)
    return assert_missing(args.seed_json)


if __name__ == "__main__":
    sys.exit(main())
