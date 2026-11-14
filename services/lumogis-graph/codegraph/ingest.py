# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Code-structure ingest orchestration (LUM-301).

Walks configured repo roots → parse → map → project into the bank graph, then
sweeps prior-run rows. Driven by ``POST /codegraph/reindex`` and an optional
APScheduler job. Off by default (requires the KG configured **and**
``LUMOGIS_CODEGRAPH_ENABLED=true``).
"""

from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from datetime import datetime
from datetime import timezone

from graph import writer

import config
from codegraph.mapper import map_module
from codegraph.models import IngestReport
from codegraph.models import to_posix_rel
from codegraph.parser import CodeParser
from codegraph.parser import lang_for_path

_log = logging.getLogger(__name__)

# P2-4: code-structure ingest is a service-scoped, single-tenant operation in v1 —
# the configured roots are an instance setting, not per-user. Every node/edge is
# stamped with this constant so MERGE/sweep stay internally consistent. If roots
# ever become per-user, thread a real user_id from the admin context (tracked as a
# known limitation in the LUM-301 plan; multi-user isolation is inert until then).
_DEFAULT_USER_ID = "default"

# Serialize project+sweep across HTTP reindex and the APScheduler job. Without
# this, overlapping runs can delete a newer generation: run A projects run_id=RA,
# run B projects RB, then A's per-file sweep deletes nodes where ingest_run_id<>RA
# (including B's fresh rows for shared files).
_INGEST_LOCK = threading.Lock()


def _new_run_id() -> str:
    return f"{datetime.now(timezone.utc).isoformat()}|{uuid.uuid4().hex[:8]}"


def _within(path: str, root: str) -> bool:
    root = root.rstrip(os.sep)
    return path == root or path.startswith(root + os.sep)


def _lang_enabled(path: str, enabled: set[str]) -> bool:
    tok = lang_for_path(path)
    if tok is None:
        return False
    norm = "python" if tok == "python" else "typescript"
    return norm in enabled


def _iter_files(root_abs: str, enabled_langs: set[str], ignore_dirs: set[str]):
    for dirpath, dirnames, filenames in os.walk(root_abs):
        dirnames[:] = [d for d in dirnames if d not in ignore_dirs]
        for name in filenames:
            full = os.path.join(dirpath, name)
            if _lang_enabled(full, enabled_langs):
                yield full


def ingest_roots(
    roots: list[str] | None = None,
    bank: str | None = None,
    *,
    user_id: str = _DEFAULT_USER_ID,
    gs=None,
) -> IngestReport:
    """Parse the configured roots and project code structure into the bank graph.

    Raises ``RuntimeError`` if the graph store is unavailable (the route maps
    that to 503). Per-file parse/read errors are skipped and recorded, never
    fatal.
    """
    start = time.monotonic()
    roots = roots if roots is not None else config.get_codegraph_roots()
    bank = bank or config.get_codegraph_bank()
    enabled_langs = set(config.get_codegraph_langs())
    ignore_dirs = set(config.get_codegraph_ignore_dirs())
    max_file_kb = config.get_codegraph_max_file_kb()
    run_id = _new_run_id()
    report = IngestReport(roots=list(roots), ingest_run_id=run_id)

    gs = gs or config.get_graph_store(bank)
    if gs is None:
        raise RuntimeError("graph store unavailable")

    parser = CodeParser()
    components: list = []
    edges: list = []
    swept_files: list[str] = []

    for root in roots:
        root_abs = os.path.realpath(root)
        if not os.path.isdir(root_abs):
            report.skipped.append(root)
            continue
        for path in _iter_files(root_abs, enabled_langs, ignore_dirs):
            real = os.path.realpath(path)
            if not _within(real, root_abs):  # symlink / traversal escape
                report.skipped.append(path)
                continue
            try:
                if os.path.getsize(real) / 1024.0 > max_file_kb:
                    report.oversize_skipped.append(real)
                    continue
                with open(real, "rb") as fh:
                    src = fh.read()
                file_rel = to_posix_rel(real, root_abs)
                parsed = parser.parse_file(src, file_rel)
            except Exception:
                report.skipped.append(path)
                continue
            res = map_module(parsed, bank=bank)
            components.extend(res.components)
            edges.extend(res.edges)
            report.unresolved_calls += res.unresolved_calls
            report.files_parsed += 1
            swept_files.append(file_rel)

    # Dedupe across files so report counts (and graph writes) are accurate:
    # a Library imported by N files yields one node; an identical edge once.
    components = list({c.lumogis_id: c for c in components}.values())
    edges = list({e.evidence_id: e for e in edges}.values())

    with _INGEST_LOCK:
        try:
            n_nodes, n_edges = writer.project_code_structure(
                gs,
                components=components,
                edges=edges,
                bank=bank,
                user_id=user_id,
                ingest_run_id=run_id,
            )
            report.components = n_nodes
            report.edges = n_edges
            writer.sweep_code_structure(
                gs, user_id=user_id, files=swept_files, ingest_run_id=run_id
            )
        except Exception:
            # Write-new-then-sweep-old: a mid-projection failure leaves the prior
            # complete generation intact; the next run reconciles.
            _log.warning("codegraph_ingest_partial run=%s files=%d", run_id, report.files_parsed)
            raise

    report.duration_ms = int((time.monotonic() - start) * 1000)
    _log.info(
        "codegraph ingest run=%s files=%d nodes=%d edges=%d unresolved=%d",
        run_id,
        report.files_parsed,
        report.components,
        report.edges,
        report.unresolved_calls,
    )
    return report


def register_codegraph_jobs(scheduler) -> None:
    """Register the periodic re-index job when enabled and interval > 0."""
    if not config.get_codegraph_enabled():
        return
    interval = config.get_codegraph_interval_min()
    if interval <= 0:
        _log.info("codegraph: enabled, interval=0 → manual /codegraph/reindex only")
        return

    def _job():
        try:
            ingest_roots()
        except Exception:
            _log.warning("codegraph_ingest_unavailable (scheduled run skipped)")

    scheduler.add_job(
        _job,
        "interval",
        minutes=interval,
        id="codegraph_reindex",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    _log.info("codegraph: scheduled re-index every %d min", interval)
