# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Task-based model routing for background LLM jobs (LUM-877).

Every background LLM feature declares a ``TaskSpec`` here, with an explicit code
default and rules. Never add a per-feature env var or hardcode an alias at the
call site. The list is chosen from configuration when the job starts; it is not
runtime failover (ADR 148's breaker is separate).

The operator gives a task an ordered model list in the ``tasks:`` block of
``config/models.yaml``. :func:`resolve_task_model` returns the first usable
entry, else the task's code default. It returns a model **name** only: it never
builds an adapter and never bypasses ``privacy_mode.resolve_job_model`` /
``config.get_llm_provider``, which stay the next steps at the call site.

Rules for future tasks:

* If a new input path brings email text into a task, that task becomes
  ``local_only`` in the same change (ADR 174).
* Anything whose output deletes or invalidates data is ``destructive``: first
  listed entry only, no fall-through, and its call site must not call
  ``resolve_job_model`` (a privacy swap would run an unmeasured model).
"""

from __future__ import annotations

import ipaddress
import logging
import os
import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal
from typing import Mapping
from urllib.parse import urlparse

import config
from services import outbound_http_url as _ohu

_log = logging.getLogger(__name__)

# Superset of ``config.ModelDisabledReason``. If ``model_disabled_reason`` gains a
# value, add it here, to ``AdminDiagnosticsTaskSkipReason`` (models/api_v1.py) and
# to the web labels (AdminDiagnosticsView.tsx) in the same change.
SkipReason = Literal[
    "unknown",
    "not_on_box",
    "no_tools",
    "no_credentials",
    "optional_disabled",
    "privacy_mode",
    "error",
]


@dataclass(frozen=True)
class TaskSpec:
    name: str
    default: str  # code default; unconditional last resort
    local_only: bool  # on-box only (is_on_box_local), never loosened by config
    needs_tools: bool  # model cfg must have tools: true
    threads_user_id: bool  # False => some callers pass user_id=None
    destructive: bool  # output can invalidate data: first listed entry only, no fall-through
    email_boundary: bool  # ADR 174 / LUM-170: resolve_on_box_model also refuses an off-box
    # code default. Implies local_only. Only the three email tasks.
    description: str  # one plain sentence, shown in diagnostics


def _spec(name: str, default: str, description: str, **flags: bool) -> TaskSpec:
    base = dict(
        local_only=False,
        needs_tools=False,
        threads_user_id=True,
        destructive=False,
        email_boundary=False,
    )
    base.update(flags)
    return TaskSpec(name=name, default=default, description=description, **base)


# ``needs_tools`` is False for all ten in v1; the field exists so chat "Auto" can
# use it later. The temporal_judge default is the ADR 151 eval reference (LUM-927).
_SPECS: tuple[TaskSpec, ...] = (
    _spec(
        "signal_enrichment",
        "llama",
        "Summarises and scores incoming feed and page signals.",
        threads_user_id=False,
    ),
    # Changing email_boundary on the next three rows removes the ADR 174 email boundary.
    _spec(
        "email_classify",
        "llama",
        "Classifies incoming mail and scores its importance (on-box only).",
        local_only=True,
        email_boundary=True,
    ),
    _spec(
        "email_reply_draft",
        "llama",
        "Drafts a reply to an email (on-box only).",
        local_only=True,
        email_boundary=True,
    ),
    _spec(
        "email_compose_refine",
        "llama",
        "Refines a message you are writing (on-box only).",
        local_only=True,
        email_boundary=True,
    ),
    # local_only is task-wide, not keyed on input provenance: four callers share one
    # call site, email attachment text reaches it (ingest.py), session text can
    # quote email too (session_summary reads the same text, so it is local_only
    # too, LUM-899), and the task is hardcoded to llama today, so no capability is
    # lost (supplement ADR section 9; a provenance gate is the revisit path).
    _spec(
        "entity_extraction",
        "llama",
        "Finds people, places and organisations in documents and notes.",
        local_only=True,
        threads_user_id=False,
    ),
    _spec(
        "relation_extraction",
        "llama",
        "Finds relations between entities in memory you add.",
        threads_user_id=False,
    ),
    # LUM-899: local_only for the same reason as entity_extraction. Both read the
    # same session text (batch_handlers/session_end.py), which can quote email
    # (ADR 174). The stored summary also feeds temporal_extract for SESSION
    # episodes, so the temporal tasks are local_only too (ADR 250).
    _spec(
        "session_summary",
        "llama",
        "Summarises a finished chat session (on-box only).",
        local_only=True,
    ),
    _spec(
        "routine_weekly_review",
        "llama",
        "Writes the prose of the weekly review (reads email signals, on-box only).",
        local_only=True,
    ),
    # LUM-899 (ADR 250, supersedes ADR 241 §9 for these two): SESSION episodes
    # hydrate from sessions.summary (temporal_pipeline._HYDRATION_QUERIES), and a
    # summary of a chat that quoted email can carry that text. temporal_extract
    # reads it and temporal_judge reads the facts extracted from it, so both stay
    # on the box.
    _spec(
        "temporal_extract",
        "llama",
        "Extracts when a fact became true for the temporal knowledge graph (on-box only).",
        local_only=True,
    ),
    # LUM-927: the static `llama` entry (llama3.2:3b, pulled by a standard
    # install) is the judge default AND the ADR 151 reference
    # (temporal_pipeline.TEMPORAL_JUDGE_REFERENCE_MODEL); change both together.
    # The old default, the Ollama tag "llama3.1:8b", was never a model alias and
    # never resolved, so every judgment ended judge_failed.
    _spec(
        "temporal_judge",
        "llama",
        "Judges whether a new fact contradicts an older one (can invalidate facts, on-box only).",
        local_only=True,
        destructive=True,
    ),
)

TASKS: Mapping[str, TaskSpec] = MappingProxyType({s.name: s for s in _SPECS})

# Retired env var -> the tasks it used to drive (shown in the boot warning).
# NOTE: drop this table and the warning one public release after LUM-877 ships
# (ADR revisit condition).
RETIRED_MODEL_ENV: Mapping[str, str] = MappingProxyType(
    {
        "SIGNAL_LLM_MODEL": "signal_enrichment (and the three email tasks when "
        "EMAIL_CLASSIFY_MODEL was unset)",
        "EMAIL_CLASSIFY_MODEL": "email_classify, email_reply_draft, email_compose_refine",
        "LUMOGIS_TEMPORAL_EXTRACT_MODEL": "temporal_extract",
        "LUMOGIS_TEMPORAL_JUDGE_MODEL": "temporal_judge",
    }
)

ModelMap = Mapping[str, dict]


@dataclass(frozen=True)
class TaskEntry:
    model: str
    skip_reason: SkipReason | None  # None => this entry would be chosen


@dataclass(frozen=True)
class TaskExplanation:
    spec: TaskSpec
    entries: tuple[TaskEntry, ...]  # every configured entry, in order, ALL evaluated
    resolved_model: str
    resolved_from: Literal["list", "default"]
    default_skip_reason: SkipReason | None  # _skip_reason(spec, spec.default); informational


class _BootRefused(RuntimeError):
    """Raised by :func:`check_tasks_at_boot` to stop boot (ADR 026, generalised)."""


# Task names already warned about a full fall-through (once per process per task).
_fallthrough_warned: set[str] = set()


# ---------------------------------------------------------------------------
# On-box check (moved from services/email_classifier.py, LUM-170)
# ---------------------------------------------------------------------------

# Hostnames that count as on-box/LAN even when DNS can't be resolved in a test
# environment (docker-compose service names + loopback aliases).
_DEFAULT_LOCAL_HOSTS = frozenset({"localhost", "ollama", "host.docker.internal"})


def _email_llm_allowlist() -> frozenset[str]:
    raw = os.environ.get("EMAIL_LLM_ALLOWLIST", "") or ""
    return frozenset(p.lower() for p in re.split(r"[\s,]+", raw.strip()) if p)


def is_on_box_local(name: str, *, cfg: dict | None = None) -> bool:
    """Stricter than ``config.is_local_model``.

    ``config.is_local_model`` is only a ``"ollama" in base_url`` substring test,
    so a *remote* Ollama endpoint would pass. For email content we additionally
    require the model's ``base_url`` host to be loopback/LAN (or explicitly in
    ``EMAIL_LLM_ALLOWLIST``). With ``cfg`` given, no config lookup is made.
    """
    if cfg is None:
        if not config.is_local_model(name):
            return False
        try:
            cfg = config.get_model_config(name)
        except Exception:
            return False
    elif "ollama" not in (cfg.get("base_url") or "").lower():
        # Never drop this pre-check: an entry with an empty base_url (for example
        # an anthropic adapter) is NOT on-box.
        return False
    base = (cfg.get("base_url") or "").strip()
    if not base:
        # Unreachable: an empty base_url fails the "ollama" pre-check above.
        return True
    host = (urlparse(base).hostname or "").strip().lower()
    if not host:
        return True
    if host in _email_llm_allowlist() or host in _DEFAULT_LOCAL_HOSTS:
        return True
    try:
        ips = _ohu._resolved_ips(host)
    except OSError:
        return False
    if not ips:
        return False
    for ip_s in ips:
        try:
            ip = ipaddress.ip_address(ip_s)
        except ValueError:
            return False
        if isinstance(ip, ipaddress.IPv4Address) and ip in _ohu._LINK_LOCAL_V4:
            return False
        if getattr(ip, "is_link_local", False):
            return False
        if not (ip.is_loopback or ip.is_private or getattr(ip, "is_unique_local", False)):
            return False
    return True


# ---------------------------------------------------------------------------
# Model lookup (one per resolution)
# ---------------------------------------------------------------------------


class _ModelLookup:
    """name -> model config, built at most once per resolution.

    Checks ``config._load_models_yaml()`` first (no I/O after the first load).
    Only on a miss does it call ``config.get_all_models_config()`` once and reuse
    the result, so a resolution makes at most ONE Ollama ``/api/tags`` call, and
    none when every name is a YAML alias.

    Notes: (a) this mirrors ``config.get_model_config``'s lookup order (YAML first,
    then the dynamic Ollama aliases); T-R17 holds the two equal, so a change to
    alias derivation cannot fix one copy only. (b) No cross-call cache in v1;
    ``signal_enrichment`` with a remote listed entry is the first hot path to
    watch (one ``model_disabled_reason`` per signal, then ``_call_llm``'s own
    ``resolve_job_model``): add a short TTL here if profiling shows it.
    """

    def __init__(self, preset: ModelMap | None = None) -> None:
        self._preset = preset
        self._full: Mapping[str, dict] | None = None

    def is_yaml(self, name: str) -> bool:
        return name in config._load_models_yaml()

    def get(self, name: str) -> dict | None:
        if self._preset is not None:
            return self._preset.get(name)
        yaml_models = config._load_models_yaml()
        if name in yaml_models:
            return yaml_models[name]
        if self._full is None:
            self._full = config.get_all_models_config()
        return self._full.get(name)

    def dynamic_count(self) -> int | None:
        """Number of non-YAML models in the full map, or ``None`` if not loaded."""
        if self._full is None:
            return None
        yaml_models = config._load_models_yaml()
        return sum(1 for k in self._full if k not in yaml_models)


# ---------------------------------------------------------------------------
# Configured list
# ---------------------------------------------------------------------------


def _normalised_list(task: str) -> tuple[str, ...]:
    raw = config._load_tasks_yaml().get(task)
    if not isinstance(raw, list):
        return ()
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        item = item.strip()
        if item and item not in out:
            out.append(item)
    return tuple(out)


def configured_list(task: str) -> tuple[str, ...]:
    """The operator's ordered model list for ``task`` (``()`` when none).

    Non-string items are dropped one by one; a scalar or dict gives ``()``; for a
    ``destructive`` spec the result is truncated to its first item.
    """
    entries = _normalised_list(task)
    spec = TASKS.get(task)
    if spec is not None and spec.destructive:
        # The FIRST item only: a malformed first item must not promote the second
        # (boot warns about the bad item).
        raw = config._load_tasks_yaml().get(task)
        if isinstance(raw, list) and raw and not (isinstance(raw[0], str) and raw[0].strip()):
            return ()
        return entries[:1]
    return entries


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def _skip_reason(
    spec: TaskSpec,
    name: str,
    *,
    user_id: str | None,
    models: _ModelLookup,
) -> SkipReason | None:
    """Why ``name`` cannot serve ``spec`` for ``user_id``, or ``None`` if it can."""
    try:
        cfg = models.get(name)
        if cfg is None:
            return "unknown"
        if spec.local_only and not is_on_box_local(name, cfg=cfg):
            return "not_on_box"
        if spec.needs_tools and not cfg.get("tools"):
            return "no_tools"
        # A local model cannot be blocked by privacy mode, so pass the hint False
        # (the answer _model_blocked_by_privacy gives, without its settings read).
        # A remote model keeps None so the real per-user policy is read.
        local_hint = False if "ollama" in (cfg.get("base_url") or "").lower() else None
        reason = config.model_disabled_reason(
            name, user_id=user_id, _cfg=cfg, _privacy_blocks_remote=local_hint
        )
        return reason
    except Exception as exc:
        _log.warning(
            "task_models: entry check failed task=%s model=%s (%s)",
            spec.name,
            name,
            type(exc).__name__,
        )
        return "error"


def resolve_task_model(task: str, *, user_id: str | None) -> str:
    """Return the model name ``task`` uses now (never raises past an unknown task).

    First usable listed entry; else the code default, unconditionally. A
    destructive task returns its first entry unchecked. With an empty list this
    makes zero settings, DB, file or Ollama calls.
    """
    spec = TASKS.get(task)
    if spec is None:
        raise ValueError(f"unknown task {task!r}")
    entries = configured_list(task)
    if not entries:
        return spec.default
    if spec.destructive:
        # No skip check: if the entry is unusable, get_llm_provider fails at the
        # call site and the judge ends judge_failed. It never moves down a list.
        return entries[0]
    lookup = _ModelLookup()
    reasons: list[str] = []
    for m in entries:
        reason = _skip_reason(spec, m, user_id=user_id, models=lookup)
        if reason is None:
            return m
        reasons.append(f"{m}={reason}")
    if task not in _fallthrough_warned:
        _fallthrough_warned.add(task)
        _log.warning(
            "task_models: no listed model usable for task=%s (reasons: %s); using code default %s",
            task,
            ", ".join(reasons),
            spec.default,
        )
    return spec.default


def resolve_on_box_model(task: str, *, user_id: str | None) -> str | None:
    """Resolve an ``email_boundary`` task and assert the final name is on-box.

    The last line of defence for email content (ADR 174 / LUM-170): returns
    ``None`` when the resolved model, including the code default, is off-box.
    A known task without ``email_boundary`` fails closed (ERROR, ``None``).
    """
    spec = TASKS.get(task)
    if spec is None:
        raise ValueError(f"unknown task {task!r}")
    if not spec.email_boundary:
        _log.error(
            "task_models: resolve_on_box_model called for task=%s without email_boundary; refusing",
            task,
        )
        return None
    name = resolve_task_model(task, user_id=user_id)
    return name if is_on_box_local(name) else None


def explain_task(
    task: str,
    *,
    user_id: str | None,
    _models: ModelMap | None = None,
) -> TaskExplanation:
    """Evaluate every configured entry of ``task`` (diagnostics only)."""
    spec = TASKS.get(task)
    if spec is None:
        raise ValueError(f"unknown task {task!r}")
    lookup = _ModelLookup(_models)
    names = configured_list(task)
    entries = tuple(
        TaskEntry(model=m, skip_reason=_skip_reason(spec, m, user_id=user_id, models=lookup))
        for m in names
    )
    if spec.destructive and entries:
        resolved, resolved_from = entries[0].model, "list"
    else:
        usable = next((e.model for e in entries if e.skip_reason is None), None)
        if usable is not None:
            resolved, resolved_from = usable, "list"
        else:
            resolved, resolved_from = spec.default, "default"
    return TaskExplanation(
        spec=spec,
        entries=entries,
        resolved_model=resolved,
        resolved_from=resolved_from,  # type: ignore[arg-type]
        default_skip_reason=_skip_reason(spec, spec.default, user_id=user_id, models=lookup),
    )


# ---------------------------------------------------------------------------
# Boot check (ADR 026, generalised)
# ---------------------------------------------------------------------------


def check_tasks_at_boot() -> None:
    """Warn about retired env vars and a bad ``tasks:`` block; guard no-user tasks.

    Only a ``_BootRefused`` (no keyless candidate for a task that runs without a
    user, under ``AUTH_ENABLED=true``) stops boot. Any other error logs one
    warning and boot continues.
    """
    try:
        _check_tasks_at_boot()
    except _BootRefused:
        raise
    except Exception as exc:
        _log.warning(
            "task_models_boot_check_failed: boot model check did not complete (%s)",
            type(exc).__name__,
        )


def _check_tasks_at_boot() -> None:
    # 1. Retired variables (name only, never the value).
    for var, drove in RETIRED_MODEL_ENV.items():
        if (os.environ.get(var) or "").strip():
            _log.warning(
                "retired_model_env: %s is set but no longer read (LUM-877); it drove %s — "
                "list the model under that task in the tasks: block of config/models.yaml",
                var,
                drove,
            )

    # 2. Config shape.
    tasks_cfg = config._load_tasks_yaml()
    if not tasks_cfg:
        try:
            raw_top = config._read_tasks_raw()
        except Exception:
            raw_top = None
        if raw_top is not None and not isinstance(raw_top, dict):
            _log.warning("task_config_invalid: the tasks: value in models.yaml must be a mapping")
    for key, value in tasks_cfg.items():
        spec = TASKS.get(key)
        if spec is None:
            _log.warning("task_config_invalid: unknown task %r in models.yaml tasks:", key)
            continue
        if not isinstance(value, list):
            if value is not None:
                _log.warning("task_config_invalid: tasks.%s must be a list of model names", key)
            continue
        for i, item in enumerate(value):
            if not isinstance(item, str):
                _log.warning(
                    "task_config_invalid: tasks.%s item %d is not a model name (ignored)", key, i
                )
        full = _normalised_list(key)
        if spec.destructive and len(full) > 1:
            _log.warning(
                "task_config_invalid: tasks.%s lists %d models; a destructive task uses "
                "only the first",
                key,
                len(full),
            )

    # 3. Static satisfiability (one lookup for the whole check).
    lookup = _ModelLookup()
    unchecked = 0
    listed: list[tuple[TaskSpec, str]] = [(TASKS[t], m) for t in TASKS for m in configured_list(t)]
    for spec, name in listed:
        lookup.get(name)  # may trigger the single Ollama listing
    ollama_empty = lookup.dynamic_count() == 0
    for spec, name in listed:
        try:
            cfg = lookup.get(name)
            if cfg is None:
                if ollama_empty and not lookup.is_yaml(name):
                    unchecked += 1
                    continue
                _log.warning(
                    "task_model_unsatisfiable: task=%s model=%s reason=unknown — this entry is "
                    "skipped while this holds (not in models.yaml or the Ollama catalogue "
                    "right now)",
                    spec.name,
                    name,
                )
                continue
            if spec.local_only and not is_on_box_local(name, cfg=cfg):
                _log.warning(
                    "task_model_unsatisfiable: task=%s model=%s reason=not_on_box — this entry "
                    "is skipped while this holds (or its host did not resolve at boot)",
                    spec.name,
                    name,
                )
            if spec.needs_tools and not cfg.get("tools"):
                _log.warning(
                    "task_model_unsatisfiable: task=%s model=%s reason=no_tools — this entry is "
                    "skipped while this holds",
                    spec.name,
                    name,
                )
            # 3b. Prompt budget.
            if cfg.get("context_budget") is None:
                _log.warning(
                    "task_model_no_context_budget: task=%s model=%s has no context_budget; "
                    "prompts are sized to 2048 tokens — define it under models: with a "
                    "context_budget",
                    spec.name,
                    name,
                )
        except Exception as exc:
            _log.warning(
                "task_model_unsatisfiable: task=%s model=%s reason=error — this entry is "
                "skipped while this holds (%s)",
                spec.name,
                name,
                type(exc).__name__,
            )
    if unchecked:
        _log.warning(
            "task_models_ollama_unavailable_at_boot: %d listed dynamic model(s) not checked; "
            "Ollama was not reachable (or listed no models) at boot",
            unchecked,
        )

    # 4. ADR 026, generalised: tasks that run without a user, under auth.
    from auth import auth_enabled

    if not auth_enabled():
        return
    for spec in TASKS.values():
        if spec.threads_user_id:
            continue
        entries = configured_list(spec.name)
        for name in entries:
            cfg = lookup.get(name)
            if cfg is not None and cfg.get("api_key_env"):
                _log.warning(
                    "task_model_needs_user: task=%s model=%s needs a per-user key; jobs without "
                    "a user skip it and use the next entry",
                    spec.name,
                    name,
                )
        candidates = entries + (spec.default,)
        keyless = False
        keyed = False
        for name in candidates:
            cfg = lookup.get(name)
            if cfg is None:
                continue  # unknown here: warned elsewhere, as before; never a refusal
            if cfg.get("api_key_env"):
                keyed = True
            else:
                keyless = True
                break
        if keyed and not keyless:
            raise _BootRefused(
                f"task {spec.name!r} has no model that works without a user under "
                "AUTH_ENABLED=true: every candidate (its tasks: entries and its code default "
                f"{spec.default!r}) needs a per-user API key, so the job would silently do "
                "nothing for any run that has no user_id. List a local model (for example "
                f"llama) for it under tasks: in config/models.yaml. This boot check guards "
                "jobs that run without a user only."
            )
