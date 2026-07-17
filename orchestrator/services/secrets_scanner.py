# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Secrets scanning for user-authored config files (LUM-361).

Static regex pass over the *content* of Lumogis config files (pipe.md routines,
``~/.lumogis/procedures/*``, ``LUMOGIS_POLICY.md``, ``WAKE.md``) run *before* the
file is executed or injected into LLM context. Any match blocks the load so an
accidental credential never reaches a prompt, an audit log, or a cloud API call.

Mirrors the ports-and-adapters shape of :mod:`services.injection_sanitiser`
(``data/secret_patterns.yaml`` + a :class:`ports.secrets_scanner.SecretsScanner`
implementation), but is deliberately simpler: no severity tiers (a secret is a
secret → block), no external-scanner merge, and — critically — it never logs the
raw match. Callers surface the offending line to the operator for remediation.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import yaml
from ports.secrets_scanner import SecretMatch
from ports.secrets_scanner import SecretsScanResult

_log = logging.getLogger(__name__)

_ORCHESTRATOR_ROOT = Path(__file__).resolve().parent.parent
_DATA_DIR = _ORCHESTRATOR_ROOT / "data"
_PATTERN_FILE = _DATA_DIR / "secret_patterns.yaml"


@dataclass(frozen=True)
class SecretRule:
    id: str
    name: str
    regex: str
    enabled: bool


_compiled: tuple[tuple[SecretRule, re.Pattern[str]], ...] | None = None


def _regex_catastrophe_lint(pat: str) -> None:
    """Shallow refusal of catastrophe motifs (stdlib ``re`` has no timeout).

    Mirrors ``services.injection_sanitiser._regex_catastrophe_lint``.
    """

    if "\x00" in pat:
        raise RuntimeError("Pattern contains NUL byte")
    stripped = "".join(pat.split())
    if "({0,}" in stripped or "){0,}" in stripped:
        raise RuntimeError("Nested empty-count quantifiers are not allowed")


def load_secret_rules(path: Path = _PATTERN_FILE) -> tuple[SecretRule, ...]:
    """Parse + validate ``secret_patterns.yaml`` (regex compiles, unique ids)."""

    with open(path, encoding="utf-8") as fh:
        raw_doc = yaml.safe_load(fh)
    rows = (raw_doc or {}).get("patterns")
    if not isinstance(rows, list) or len(rows) < 1:
        raise RuntimeError("secret_patterns.yaml must define a non-empty patterns list")

    seen: set[str] = set()
    out: list[SecretRule] = []
    for entry in rows:
        if not isinstance(entry, Mapping):
            raise RuntimeError("Pattern row must be a mapping")
        pid = str(entry.get("id") or "").strip()
        name = str(entry.get("name") or pid).strip()
        regex_txt = entry.get("regex")
        if not pid or pid in seen:
            raise RuntimeError(f"Duplicate or empty pattern id: {pid!r}")
        seen.add(pid)
        if not isinstance(regex_txt, str) or not regex_txt.strip():
            raise RuntimeError(f"Pattern {pid!r} needs a regex string")
        _regex_catastrophe_lint(regex_txt)
        try:
            re.compile(regex_txt)
        except re.error as exc:
            raise RuntimeError(f"Bad regex on pattern {pid!r}: {exc}") from exc
        enabled = entry.get("enabled", True)
        if isinstance(enabled, str):
            enabled = enabled.strip().lower() in ("true", "1", "yes", "on")
        out.append(SecretRule(id=pid, name=name, regex=regex_txt, enabled=bool(enabled)))
    return tuple(out)


def ensure_rules_loaded() -> tuple[tuple[SecretRule, re.Pattern[str]], ...]:
    """Load + compile the enabled rules once (process lifetime)."""

    global _compiled
    if _compiled is None:
        rules = load_secret_rules()
        _compiled = tuple((r, re.compile(r.regex)) for r in rules if r.enabled)
        _log.info(
            "Secret patterns loaded: %d active rule(s) (%d defined) from %s",
            len(_compiled),
            len(rules),
            _PATTERN_FILE,
        )
    return _compiled


def reset_cache_for_tests() -> None:
    """Test-only."""

    global _compiled
    _compiled = None


class PatternSecretsScanner:
    """Regex secrets scan over user-authored config content (LUM-361).

    Reports the first matching line per pattern so the operator can fix the file.
    Never logs the raw content or the matched value — only pattern ids + counts.
    """

    def scan(self, text: str) -> SecretsScanResult:
        if not text:
            return {"blocked": False, "matches": []}

        compiled = ensure_rules_loaded()
        total = len(compiled)
        matches: list[SecretMatch] = []
        hit_ids: set[str] = set()
        for lineno, line in enumerate(text.splitlines(), start=1):
            if len(hit_ids) == total:
                break  # every pattern already matched — no need to scan further
            for rule, rx in compiled:
                if rule.id in hit_ids:
                    continue
                if rx.search(line):
                    matches.append({"pattern_id": rule.id, "name": rule.name, "line": lineno})
                    hit_ids.add(rule.id)

        if matches:
            # Log ids + line count only — never the raw content or the secret.
            _log.warning(
                "Secrets scan blocked config content: %d pattern(s) matched (ids=%s); "
                "raw content withheld from logs by design (LUM-361)",
                len(matches),
                ",".join(m["pattern_id"] for m in matches),
            )
        return {"blocked": bool(matches), "matches": matches}


def remediation_message(result: SecretsScanResult) -> str:
    """Human-facing actionable error for a blocked file (safe to show the user).

    Names the pattern + line but not the secret value.
    """

    matches = result.get("matches") or []
    if not matches:
        return ""
    lines = ", ".join(f"{m['name']} (line {m['line']})" for m in matches)
    return (
        "This file appears to contain a credential: "
        f"{lines}. Remove it before Lumogis can use this file."
    )


class UserConfigSecretsBlockedError(ValueError):
    """Raised when user-authored config content matches a secrets pattern (LUM-361)."""

    def __init__(self, result: SecretsScanResult, *, source: str) -> None:
        self.source = source
        self.result = result
        super().__init__(remediation_message(result))


def scan_user_config_for_llm(text: str, *, source: str) -> str:
    """Run the LUM-361 secrets scan before config text enters LLM context.

    Call this (or :func:`read_user_config_for_llm`) from every loader of
    ``pipe.md``, procedures, ``LUMOGIS_POLICY.md``, or ``WAKE.md``.
    Never logs the raw secret — only raises with a remediation message.
    """

    import config as _cfg

    result = _cfg.get_secrets_scanner().scan(text)
    if result["blocked"]:
        raise UserConfigSecretsBlockedError(result, source=source)
    return text


def read_user_config_for_llm(path: str | Path) -> str:
    """Read a user config file from disk after the LUM-361 secrets scan."""

    p = Path(path)
    text = p.read_text(encoding="utf-8")
    return scan_user_config_for_llm(text, source=str(p))
