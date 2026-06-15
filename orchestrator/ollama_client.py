# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Helpers for interacting with the local Ollama daemon and the public model catalog."""

import json
import logging
import os
import re
from collections.abc import Iterator
from pathlib import Path
from typing import TypedDict

import httpx

_log = logging.getLogger(__name__)

_OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://ollama:11434")


def _resolve_catalog_path() -> Path:
    """Resolve the fallback catalog, preferring bind mount, then image baked-in copy."""
    override = os.environ.get("OLLAMA_CATALOG_FALLBACK")
    if override:
        return Path(override)
    candidates = [
        Path(__file__).parent / "config" / "ollama_catalog_fallback.json",
        Path("/opt/lumogis/config/ollama_catalog_fallback.json"),
    ]
    for p in candidates:
        if p.is_file():
            return p
    return candidates[0]


_FALLBACK_CATALOG_PATH = _resolve_catalog_path()

# Public registry — we only show the top-N curated models
_CATALOG_URL = "https://ollama.com/api/tags"


# ---------------------------------------------------------------------------
# Local Ollama helpers
# ---------------------------------------------------------------------------


def list_local_models(timeout: float = 5.0) -> list[dict]:
    """Return models currently pulled in Ollama as a list of dicts with keys:
    name, size, parameter_size, quantization_level, modified_at.
    """
    try:
        r = httpx.get(f"{_OLLAMA_URL}/api/tags", timeout=timeout)
        r.raise_for_status()
        return r.json().get("models", [])
    except Exception as exc:
        _log.warning("Could not reach Ollama: %s", exc)
        return []


class PullProgressEvent(TypedDict):
    status: str
    completed: int | None
    total: int | None
    progress_pct: int | None


def _pull_timeout_sec() -> float:
    raw = os.environ.get("OLLAMA_PULL_TIMEOUT_SEC", "7200").strip()
    try:
        return float(raw)
    except ValueError:
        return 7200.0


def iter_pull_progress(name: str, timeout: float | None = None) -> Iterator[PullProgressEvent]:
    """Stream Ollama pull NDJSON and yield progress events.

    Raises RuntimeError on Ollama error lines or HTTP failure.
    """
    if timeout is None:
        timeout = _pull_timeout_sec()
    with httpx.stream(
        "POST",
        f"{_OLLAMA_URL}/api/pull",
        json={"name": name, "stream": True},
        timeout=timeout,
    ) as response:
        response.raise_for_status()
        for raw_line in response.iter_lines():
            if not raw_line:
                continue
            try:
                data = json.loads(raw_line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"Invalid Ollama pull JSON: {exc}") from exc
            if not isinstance(data, dict):
                continue
            if data.get("error"):
                raise RuntimeError(str(data["error"]))
            total = data.get("total")
            completed = data.get("completed")
            progress_pct: int | None = None
            if isinstance(total, int) and total > 0 and isinstance(completed, int):
                progress_pct = min(100, max(0, round(100 * completed / total)))
            status = str(data.get("status") or "pulling")
            yield PullProgressEvent(
                status=status,
                completed=completed if isinstance(completed, int) else None,
                total=total if isinstance(total, int) else None,
                progress_pct=progress_pct,
            )


def pull_model(name: str, timeout: float | None = None) -> None:
    """Trigger an Ollama model pull (blocking until complete).

    Default timeout is long — multi-GB models (e.g. Gemma 4) often need 30+ minutes.
    Override with OLLAMA_PULL_TIMEOUT_SEC (seconds) or pass ``timeout`` explicitly.

    Raises httpx.HTTPStatusError on failure.
    """
    if timeout is None:
        timeout = _pull_timeout_sec()
    r = httpx.post(
        f"{_OLLAMA_URL}/api/pull",
        json={"name": name, "stream": False},
        timeout=timeout,
    )
    if r.is_success:
        return
    try:
        data = r.json()
        msg = data.get("error") if isinstance(data, dict) else None
    except Exception:
        msg = None
    if not msg:
        msg = (r.text or "").strip() or f"HTTP {r.status_code}"
    raise RuntimeError(msg)


def delete_model(name: str, timeout: float = 30.0) -> None:
    """Remove a locally pulled Ollama model.

    Raises httpx.HTTPStatusError on failure.
    """
    r = httpx.request(
        "DELETE",
        f"{_OLLAMA_URL}/api/delete",
        json={"name": name},
        timeout=timeout,
    )
    r.raise_for_status()


# ---------------------------------------------------------------------------
# Public catalog helpers
# ---------------------------------------------------------------------------


def _load_fallback_catalog() -> list[dict]:
    """Load the bundled fallback catalog from disk."""
    try:
        with open(_FALLBACK_CATALOG_PATH) as f:
            return json.load(f)
    except Exception as exc:
        _log.error("Could not load fallback catalog: %s", exc)
        return []


_DISPLAY_NAME_OVERRIDES: dict[str, str] = {
    "deepseek-r1": "DeepSeek R1",
    "deepseek-v3.1": "DeepSeek V3.1",
    "deepseek-v3.2": "DeepSeek V3.2",
    "deepseek-r1.5": "DeepSeek R1.5",
    "gemma4": "Gemma 4",
}


def _prettify_name(raw: str) -> str:
    """Turn a slug like 'qwen2.5' or 'phi4-mini' into 'Qwen 2.5' or 'Phi4 Mini'.

    Checks _DISPLAY_NAME_OVERRIDES first for slugs the regex handles poorly
    (e.g. proper nouns that need exact capitalisation).
    """
    if raw in _DISPLAY_NAME_OVERRIDES:
        return _DISPLAY_NAME_OVERRIDES[raw]
    parts = re.split(r"[-_]", raw)
    result = []
    for p in parts:
        m = re.match(r"^([a-zA-Z]+)([\d].*)$", p)
        if m:
            result.append(m.group(1).capitalize() + " " + m.group(2))
        else:
            result.append(p.capitalize())
    return " ".join(result)


def get_curated_catalog() -> list[dict]:
    """Return the bundled curated catalog (public API wrapping the private loader)."""
    return _load_fallback_catalog()


def _base_name(name: str) -> str:
    """Return the base model name without tag (e.g. 'llama3.2:7b' → 'llama3.2')."""
    return name.split(":")[0].lower()


def _extract_tag(name: str) -> str | None:
    """Return the tag portion of a 'model:tag' string, or None."""
    parts = name.split(":", 1)
    return parts[1] if len(parts) == 2 else None


def fetch_catalog(timeout: float = 8.0) -> list[dict]:
    """Fetch the Ollama public catalog and return a normalised list.

    Falls back to the bundled JSON on any network/parse error.
    Each entry: {name, description, tags, pulls, updated_at}.
    """
    fallback = _load_fallback_catalog()
    # Build lookup: base_name → fallback entry (for enrichment)
    fallback_by_name = {_base_name(f["name"]): f for f in fallback}

    try:
        r = httpx.get(_CATALOG_URL, timeout=timeout, follow_redirects=True)
        r.raise_for_status()
        raw = r.json()
        # The Ollama registry API returns {"models": [...]}
        raw_models = raw if isinstance(raw, list) else raw.get("models", [])

        # Group live entries by base name so we can collect available tags.
        from collections import defaultdict

        by_base: dict[str, list[dict]] = defaultdict(list)
        for m in raw_models:
            full_name = m.get("name") or m.get("model", "")
            base = _base_name(full_name)
            tag = _extract_tag(full_name) or "latest"
            by_base[base].append({"full": full_name, "tag": tag, "raw": m})

        result = []
        for base, entries in by_base.items():
            tags = sorted({e["tag"] for e in entries if e["tag"] != "latest"}) or ["latest"]
            fb = fallback_by_name.get(base, {})
            first = entries[0]["raw"]
            entry: dict = {
                "name": base,
                "description": fb.get("description") or first.get("description", ""),
                "tags": fb.get("tags") or tags,
                "pulls": first.get("pull_count") or first.get("pulls", 0),
                "updated_at": first.get("updated_at", ""),
            }
            if fb.get("capabilities") is not None:
                entry["capabilities"] = fb["capabilities"]
            if fb.get("training_cutoff") is not None:
                entry["training_cutoff"] = fb["training_cutoff"]
            result.append(entry)

        # Prepend fallback entries that aren't in the live catalog, preserving order.
        live_bases = {r["name"] for r in result}
        prepend = [f for f in fallback if _base_name(f["name"]) not in live_bases]
        return prepend + result

    except Exception as exc:
        _log.warning("Catalog fetch failed (%s); using fallback", exc)
        return fallback
