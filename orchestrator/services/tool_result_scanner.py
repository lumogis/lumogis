# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Tool-result injection scanning (LUM-362).

A middleware scan of live MCP connector results *before* they are injected into
the LLM context — the third injection surface after document ingest (LUM-127)
and user config (LUM-361). When a connector result carries an instruction that
looks like a prompt injection, the content is **redacted** before it reaches the
model; the caller writes the raw result to the audit log (``injection_flagged``)
for forensics and surfaces a user notification.

Reuses the LUM-127 pattern loader (:func:`services.injection_sanitiser.\
load_pattern_rows_from_path`) over the base ``injection_patterns.yaml`` plus the
tool-result-specific ``tool_result_patterns.yaml`` additions — no loader
duplication. Regex-only (no LLM), well under the 5ms/result budget.

Wiring: a future middleware in the tool-execution path (``services/tools.py`` /
``mcp_server.py``) calls :func:`scan_tool_result` on every connector result,
injects ``safe_text``, and — when ``flagged`` — audits the raw result and
notifies the user. No live connectors ship yet (P2 now, P0 at connector ship),
so this module is the mechanism, not the wiring.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from ports.tool_result_scanner import ToolResultScanner
from ports.tool_result_scanner import ToolResultScanResult
from services.injection_sanitiser import injection_default_pattern_file
from services.injection_sanitiser import load_pattern_rows_from_path

_log = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_ADDITIONS_FILE = _DATA_DIR / "tool_result_patterns.yaml"

# Injected into LLM context in place of a flagged result.
REDACTION_PLACEHOLDER = (
    "[Tool result: content flagged for possible injection — raw result available in audit log]"
)
# Surfaced to the user by the caller when a result is flagged.
USER_NOTIFICATION = (
    "Lumogis detected and blocked a possible injection attempt in a connector result"
)

_compiled: tuple[tuple[str, re.Pattern[str]], ...] | None = None


def _load_all_rules() -> tuple[tuple[str, re.Pattern[str]], ...]:
    """Base LUM-127 patterns + tool-result additions, compiled (enabled only).

    Both files are parsed by the shared LUM-127 loader, which validates regex
    compilation, rejects catastrophe motifs, and enforces unique ids per file.

    The base set is read from the fixed default file (not
    ``injection_pattern_source_path()``), so an ``INJECTION_PATTERN_FILE``
    override that weakens document-ingest patterns cannot silently weaken the
    tool-result scan — this surface always enforces the shipped baseline.
    """

    base = load_pattern_rows_from_path(injection_default_pattern_file())
    adds = load_pattern_rows_from_path(_ADDITIONS_FILE)
    out: list[tuple[str, re.Pattern[str]]] = []
    seen: set[str] = set()
    for rule in (*base, *adds):
        if not rule.enabled:
            continue
        if rule.id in seen:
            # Loader enforces uniqueness within a file but not across the two.
            # A cross-file id collision means an addition would be silently
            # dropped in favour of the base rule — fail loudly instead.
            raise RuntimeError(
                f"tool_result_patterns.yaml reuses base pattern id {rule.id!r}; "
                "give the addition a distinct id (e.g. tr_ prefix)"
            )
        seen.add(rule.id)
        out.append((rule.id, re.compile(rule.regex)))
    return tuple(out)


def ensure_rules_loaded() -> tuple[tuple[str, re.Pattern[str]], ...]:
    global _compiled
    if _compiled is None:
        _compiled = _load_all_rules()
        _log.info("Tool-result injection patterns loaded: %d rules", len(_compiled))
    return _compiled


def reset_cache_for_tests() -> None:
    """Test-only."""

    global _compiled
    _compiled = None


class PatternToolResultScanner:
    """Regex scan of a tool result; redacts the whole result on any hit."""

    def scan(self, text: str) -> ToolResultScanResult:
        if not text:
            return {"flagged": False, "safe_text": text, "pattern_hits": []}

        compiled = ensure_rules_loaded()
        hits = [pid for pid, rx in compiled if rx.search(text)]
        if not hits:
            return {"flagged": False, "safe_text": text, "pattern_hits": []}

        _log.warning(
            "Tool-result scan flagged a connector result: %d pattern(s) matched (ids=%s); "
            "content redacted before LLM injection, raw preserved for audit",
            len(hits),
            ",".join(hits),
        )
        return {"flagged": True, "safe_text": REDACTION_PLACEHOLDER, "pattern_hits": hits}


def scan_tool_result(
    text: str, *, scanner: ToolResultScanner | None = None
) -> ToolResultScanResult:
    """Middleware entry point: scan a tool result, return the injection-safe form.

    The caller injects ``result["safe_text"]`` and, when ``result["flagged"]``,
    writes the raw ``text`` to the audit log (``injection_flagged=True``) and
    notifies the user with :data:`USER_NOTIFICATION`.
    """

    if scanner is None:
        import config

        scanner = config.get_tool_result_scanner()
    return scanner.scan(text)
