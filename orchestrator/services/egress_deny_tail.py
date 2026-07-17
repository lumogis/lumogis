# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-621 — tail Squid access.log and emit structured egress.denied events.

Enabled only when ``LUMOGIS_EGRESS_ACCESS_LOG`` is set (community-egress overlay).
Not a security control — operator signal only (ADR-173 containment is the gate).
"""
from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import structlog

from services.capability_scopes import is_grantable_capability_id

_log = logging.getLogger(__name__)
_slog = structlog.get_logger("lumogis.egress")

_ENV_LOG = "LUMOGIS_EGRESS_ACCESS_LOG"
_ENV_MAP = "LUMOGIS_EGRESS_SRC_MAP"
_ENV_DEDUP = "LUMOGIS_EGRESS_DENY_DEDUP_SECONDS"

_stop = threading.Event()
_thread: threading.Thread | None = None
_perm_failures = 0

_src_map_cache: dict[str, str] | None = None
_src_map_mtime: float | None = None
_dedup: dict[tuple[str, str], float] = {}


def dst_host_from_ru(ru: str) -> str | None:
    """Extract hostname from Squid ``%ru`` (never return raw URL/query)."""
    s = (ru or "").strip()
    if not s or s == "-":
        return None
    if "://" not in s and "/" not in s.split("?", 1)[0]:
        host = s.rsplit("@", 1)[-1].split(":", 1)[0]
        return host.lower() or None
    p = urlparse(s if "://" in s else "http://" + s)
    host = (p.hostname or "").lower()
    return host or None


def parse_lumogis_egress_line(line: str) -> dict[str, str] | None:
    """Parse one ``lumogis_egress`` access-log line; return deny fields or None."""
    parts = line.strip().split()
    if len(parts) < 6:
        return None
    # 0=ts 1=tr 2=src 3=Ss/Hs 4=rm 5=ru [6=user]
    status_field = parts[3]
    status = status_field.split("/", 1)[0]
    if "DENIED" not in status:
        return None
    dst = dst_host_from_ru(parts[5])
    if not dst:
        return None
    return {
        "src_ip": parts[2],
        "squid_status": status,
        "http_method": parts[4],
        "dst_host": dst,
    }


def load_src_map(*, refresh: bool = False) -> dict[str, str]:
    """Load ``ipv4 -> capability_id`` map with mtime reload (keep-last-good)."""
    global _src_map_cache, _src_map_mtime
    path_str = os.environ.get(_ENV_MAP, "").strip()
    if not path_str:
        return {}
    path = Path(path_str)
    if refresh:
        _src_map_cache = None
        _src_map_mtime = None
    try:
        mtime = path.stat().st_mtime
    except OSError:
        if _src_map_cache is not None:
            return _src_map_cache
        return {}
    if _src_map_cache is not None and mtime == _src_map_mtime:
        return _src_map_cache

    mapping: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        if _src_map_cache is not None:
            return _src_map_cache
        return {}
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) != 2:
            _log.warning("egress src map: ignoring malformed line %r", raw)
            continue
        ip, cap_id = parts
        if not is_grantable_capability_id(cap_id):
            _log.warning("egress src map: ignoring ungrantable id %r", cap_id)
            continue
        if ip in mapping:
            _log.warning("egress src map: duplicate IP %s — last-wins", ip)
        mapping[ip] = cap_id
    _src_map_cache = mapping
    _src_map_mtime = mtime
    return mapping


def _dedup_ok(src_ip: str, dst_host: str, window: float) -> bool:
    now = time.monotonic()
    key = (src_ip, dst_host)
    last = _dedup.get(key)
    if last is not None and (now - last) < window:
        return False
    _dedup[key] = now
    # prune occasionally
    if len(_dedup) > 512:
        cutoff = now - window
        for k, t in list(_dedup.items()):
            if t < cutoff:
                del _dedup[k]
    return True


def _emit(fields: dict[str, str]) -> None:
    try:
        window = float(os.environ.get(_ENV_DEDUP, "60") or "60")
    except ValueError:
        window = 60.0
    if not _dedup_ok(fields["src_ip"], fields["dst_host"], window):
        return
    src_map = load_src_map()
    cap_id = src_map.get(fields["src_ip"])
    kwargs = {
        "src_ip": fields["src_ip"],
        "dst_host": fields["dst_host"],
        "http_method": fields["http_method"],
        "squid_status": fields["squid_status"],
    }
    if cap_id:
        kwargs["capability_id"] = cap_id
    _slog.warning("egress.denied", **kwargs)


def _open_log(path: Path):
    global _perm_failures
    while not _stop.is_set():
        try:
            fh = path.open("r", encoding="utf-8", errors="replace")
            fh.seek(0, os.SEEK_END)
            _perm_failures = 0
            return fh
        except OSError as exc:
            _perm_failures += 1
            _log.warning(
                "egress deny tail: cannot open %s (%s) — retry (%s/5)",
                path,
                exc,
                _perm_failures,
            )
            if _perm_failures >= 5:
                _log.warning(
                    "egress deny tail: giving up for process lifetime after 5 open failures"
                )
                return None
            _stop.wait(2.0)
    return None


def _run(path: Path) -> None:
    fh = _open_log(path)
    if fh is None:
        return
    try:
        while not _stop.is_set():
            line = fh.readline()
            if not line:
                _stop.wait(0.25)
                continue
            try:
                fields = parse_lumogis_egress_line(line)
                if fields:
                    _emit(fields)
            except Exception:
                _log.debug("egress deny tail: skip bad line", exc_info=True)
    except Exception:
        _log.exception("egress deny tail: thread exiting on error")
    finally:
        try:
            fh.close()
        except OSError:
            pass


def start() -> None:
    """Start the deny-tail daemon thread if ``LUMOGIS_EGRESS_ACCESS_LOG`` is set."""
    global _thread
    path_str = os.environ.get(_ENV_LOG, "").strip()
    if not path_str:
        _log.info("egress deny tail: disabled (LUMOGIS_EGRESS_ACCESS_LOG unset)")
        return
    if _thread is not None and _thread.is_alive():
        return
    _stop.clear()
    path = Path(path_str)
    _thread = threading.Thread(
        target=_run, args=(path,), name="egress-deny-tail", daemon=True
    )
    _thread.start()
    _log.info("egress deny tail: started on %s", path)


def stop() -> None:
    """Stop the deny-tail thread."""
    global _thread
    _stop.set()
    t = _thread
    if t is not None:
        t.join(timeout=2.0)
    _thread = None
