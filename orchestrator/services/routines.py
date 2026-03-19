# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Lumogis
"""Routine scheduling and execution service.

register_routine(spec): saves to routines table, schedules via APScheduler if approved.
run_routine(name): executes the routine, writes audit trail, saves output to outbox/.
register_all(): called from main.py — registers two built-in routines on startup.

Built-in routines:
  weekly_review — Sunday 18:00, collects week's signals/sessions/entities into JSON.
    Requires approval. Optionally appends LLM prose summary.
    Context budget: top 10 signals (relevance DESC), top 10 entities (mention_count DESC),
    5 session summaries (recency DESC). Truncated if it still exceeds llama budget - 600.
  inbox_digest — daily at DIGEST_TIME - 30min, lists new inbox files with metadata.
    Auto-approved.
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import config
from models.actions import RoutineSpec

_log = logging.getLogger(__name__)

_WORKSPACE = Path(os.environ.get("WORKSPACE_PATH", "/workspace"))
_OUTBOX = _WORKSPACE / "outbox"
_REVIEW = _WORKSPACE / "review"

# In-memory registry of running APScheduler job IDs.
_routine_jobs: dict[str, str] = {}  # routine_name -> job_id

# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------


def register_all() -> None:
    """Register built-in routines on startup. Called from main.py lifespan."""
    _ensure_weekly_review()
    _ensure_inbox_digest()
    _log.info("Built-in routines registered")


def register_routine(spec: RoutineSpec) -> None:
    """Upsert a RoutineSpec in the DB. Schedule if approved."""
    ms = config.get_metadata_store()
    ms.execute(
        "INSERT INTO routines "
        "(id, user_id, name, description, schedule_cron, steps, requires_approval, "
        "approved_at, enabled) "
        "VALUES (gen_random_uuid(), %s, %s, %s, %s, %s::jsonb, %s, %s, %s) "
        "ON CONFLICT (name, user_id) DO UPDATE SET "
        "description = EXCLUDED.description, schedule_cron = EXCLUDED.schedule_cron, "
        "steps = EXCLUDED.steps, requires_approval = EXCLUDED.requires_approval, "
        "approved_at = EXCLUDED.approved_at, enabled = EXCLUDED.enabled",
        (
            spec.user_id,
            spec.name,
            spec.description,
            spec.schedule_cron,
            json.dumps(spec.steps),
            spec.requires_approval,
            spec.approved_at,
            spec.enabled,
        ),
    )
    _maybe_schedule(spec)


def run_routine(name: str, user_id: str = "default") -> dict:
    """Execute a routine by name. Returns {success, output, error}."""
    row = _load_routine_row(name, user_id)
    if not row:
        return {"success": False, "error": f"Routine {name!r} not found"}

    if row["requires_approval"] and not row["approved_at"]:
        return {"success": False, "error": f"Routine {name!r} requires approval"}

    _log.info("Running routine: %s", name)
    try:
        output = _dispatch(name, row)
        _update_last_run(name, user_id)
        _write_routine_audit(name, output, user_id)
        return {"success": True, "output": output}
    except Exception as exc:
        _log.error("Routine %r failed: %s", name, exc)
        return {"success": False, "error": str(exc)}


def approve_routine(name: str, user_id: str = "default") -> bool:
    """Mark a routine as approved and start its scheduler job."""
    try:
        ms = config.get_metadata_store()
        ms.execute(
            "UPDATE routines SET approved_at = NOW() WHERE name = %s AND user_id = %s",
            (name, user_id),
        )
        row = _load_routine_row(name, user_id)
        if row:
            spec = _row_to_spec(row)
            _maybe_schedule(spec)
        return True
    except Exception as exc:
        _log.error("approve_routine error: %s", exc)
        return False


def revoke_routine(name: str, user_id: str = "default") -> bool:
    """Revoke approval and remove the scheduler job."""
    try:
        ms = config.get_metadata_store()
        ms.execute(
            "UPDATE routines SET approved_at = NULL WHERE name = %s AND user_id = %s",
            (name, user_id),
        )
        _unschedule(name)
        return True
    except Exception as exc:
        _log.error("revoke_routine error: %s", exc)
        return False


def list_routines(user_id: str = "default") -> list[dict]:
    try:
        ms = config.get_metadata_store()
        rows = ms.fetch_all(
            "SELECT name, description, schedule_cron, requires_approval, approved_at, "
            "last_run_at, enabled FROM routines WHERE user_id = %s ORDER BY name",
            (user_id,),
        )
        scheduler = config.get_scheduler()
        scheduled = {j.id for j in scheduler.get_jobs()}
        result = []
        for r in rows:
            result.append(
                {
                    "name": r["name"],
                    "description": r["description"],
                    "schedule_cron": r["schedule_cron"],
                    "requires_approval": r["requires_approval"],
                    "approved": r["approved_at"] is not None,
                    "approved_at": r["approved_at"].isoformat() if r["approved_at"] else None,
                    "last_run_at": r["last_run_at"].isoformat() if r["last_run_at"] else None,
                    "enabled": r["enabled"],
                    "scheduled": f"routine_{r['name']}" in scheduled,
                }
            )
        return result
    except Exception as exc:
        _log.error("list_routines error: %s", exc)
        return []


# --------------------------------------------------------------------------
# Built-in routine: weekly_review
# --------------------------------------------------------------------------


def _run_weekly_review(user_id: str = "default") -> str:
    """Collect week's signals, entities, sessions → JSON + optional LLM prose."""
    from services.context_budget import get_budget, truncate_text

    ms = config.get_metadata_store()
    vs = config.get_vector_store()

    # Top 10 signals by relevance.
    signals = ms.fetch_all(
        "SELECT title, url, content_summary, importance_score, relevance_score, created_at "
        "FROM signals WHERE user_id = %s AND created_at >= NOW() - INTERVAL '7 days' "
        "ORDER BY relevance_score DESC LIMIT 10",
        (user_id,),
    )

    # Top 10 entities by mention_count.
    entities = ms.fetch_all(
        "SELECT name, entity_type, mention_count FROM entities WHERE user_id = %s "
        "ORDER BY mention_count DESC LIMIT 10",
        (user_id,),
    )

    # 5 most recent session summaries from Qdrant conversations collection.
    sessions: list[dict] = []
    try:
        scroll = getattr(vs, "scroll_collection", None)
        if scroll:
            pts = scroll("conversations", with_vectors=False)
            pts_sorted = sorted(pts, key=lambda p: p.get("payload", {}).get("created_at", ""), reverse=True)
            for pt in pts_sorted[:5]:
                payload = pt.get("payload", {})
                sessions.append(
                    {
                        "session_id": pt.get("id"),
                        "summary": payload.get("summary", ""),
                        "topics": payload.get("topics", []),
                        "created_at": payload.get("created_at"),
                    }
                )
    except Exception as exc:
        _log.debug("weekly_review: session fetch error: %s", exc)

    review = {
        "period": "last_7_days",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "signal_count": len(signals),
        "top_signals": [dict(s) for s in signals],
        "top_entities": [dict(e) for e in entities],
        "recent_sessions": sessions,
    }
    review_json = json.dumps(review, default=str, indent=2)

    # Apply context budget before LLM call.
    budget = get_budget("llama") - 600
    review_json_trimmed = truncate_text(review_json, budget)

    # Optional LLM prose summary.
    prose = ""
    try:
        llm = config.get_llm_provider("llama")
        resp = llm.chat(
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Write a concise 3-5 sentence weekly review summary based on this data:\n\n"
                        + review_json_trimmed
                    ),
                }
            ],
            system="You are a concise analyst. Summarise what happened this week.",
            max_tokens=300,
        )
        prose = resp.text.strip()
    except Exception as exc:
        _log.warning("weekly_review: LLM prose generation failed: %s", exc)

    if prose:
        review["prose_summary"] = prose

    # Save to outbox.
    _OUTBOX.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_path = _OUTBOX / f"weekly_review_{date_str}.json"
    out_path.write_text(json.dumps(review, default=str, indent=2))
    _log.info("weekly_review saved: %s", out_path)
    return str(out_path)


# --------------------------------------------------------------------------
# Built-in routine: inbox_digest
# --------------------------------------------------------------------------


def _run_inbox_digest(user_id: str = "default") -> str:
    """List new inbox files with metadata → JSON in outbox."""
    inbox = _WORKSPACE / "inbox"
    if not inbox.exists():
        return "Inbox directory not found"

    files = []
    for f in sorted(inbox.iterdir()):
        if not f.is_file():
            continue
        stat = f.stat()
        files.append(
            {
                "name": f.name,
                "type": f.suffix,
                "size_bytes": stat.st_size,
                "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            }
        )

    digest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "inbox_count": len(files),
        "files": files,
    }

    _OUTBOX.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_path = _OUTBOX / f"inbox_digest_{date_str}.json"
    out_path.write_text(json.dumps(digest, indent=2))
    _log.info("inbox_digest saved: %s (%d files)", out_path, len(files))
    return str(out_path)


# --------------------------------------------------------------------------
# Dispatcher
# --------------------------------------------------------------------------

_BUILTIN_HANDLERS: dict[str, callable] = {
    "weekly_review": _run_weekly_review,
    "inbox_digest": _run_inbox_digest,
}


def _dispatch(name: str, row: dict) -> str:
    """Call the built-in handler or execute steps via executor."""
    if name in _BUILTIN_HANDLERS:
        return _BUILTIN_HANDLERS[name](user_id=row.get("user_id", "default"))

    # Generic step-by-step execution via executor.
    from actions.executor import execute

    steps = row.get("steps") or []
    outputs = []
    for step in steps:
        action_name = step.get("action_name")
        step_input = step.get("input", {})
        if not action_name:
            continue
        result = execute(action_name, step_input, user_id=row.get("user_id", "default"))
        outputs.append(f"{action_name}: {'OK' if result.success else result.error}")
    return "; ".join(outputs) or "no steps"


# --------------------------------------------------------------------------
# Scheduling helpers
# --------------------------------------------------------------------------


def _maybe_schedule(spec: RoutineSpec) -> None:
    """Add an APScheduler CronTrigger job if routine is approved and enabled."""
    if not spec.enabled:
        return
    if spec.requires_approval and not spec.approved_at:
        return

    scheduler = config.get_scheduler()
    if not scheduler.running:
        return

    job_id = f"routine_{spec.name}"
    _unschedule(spec.name)

    parts = spec.schedule_cron.split()
    if len(parts) != 5:
        _log.warning("Invalid cron for routine %r: %r", spec.name, spec.schedule_cron)
        return

    minute, hour, day, month, day_of_week = parts
    scheduler.add_job(
        _job_callback,
        trigger="cron",
        minute=minute,
        hour=hour,
        day=day,
        month=month,
        day_of_week=day_of_week,
        args=[spec.name],
        id=job_id,
        name=f"Routine: {spec.name}",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    _routine_jobs[spec.name] = job_id
    _log.info("Scheduled routine %r (cron=%r)", spec.name, spec.schedule_cron)


def _unschedule(name: str) -> None:
    try:
        scheduler = config.get_scheduler()
        job_id = f"routine_{name}"
        job = scheduler.get_job(job_id)
        if job:
            job.remove()
        _routine_jobs.pop(name, None)
    except Exception:
        pass


def _job_callback(name: str) -> None:
    run_routine(name)


# --------------------------------------------------------------------------
# DB helpers
# --------------------------------------------------------------------------


def _ensure_weekly_review() -> None:
    spec = RoutineSpec(
        name="weekly_review",
        description="Collects the week's signals, entities, and session summaries into a structured JSON report saved to ai-workspace/outbox/.",
        schedule_cron="0 18 * * 0",  # Sunday 18:00
        steps=[{"action_name": "__builtin__weekly_review"}],
        requires_approval=True,
        enabled=True,
    )
    try:
        register_routine(spec)
    except Exception as exc:
        _log.warning("weekly_review registration error: %s", exc)


def _ensure_inbox_digest() -> None:
    briefing_time = os.environ.get("DIGEST_TIME", "08:00")
    try:
        h_str, m_str = briefing_time.split(":")
        h, m = int(h_str), int(m_str)
        m -= 30
        if m < 0:
            m += 60
            h = (h - 1) % 24
    except Exception:
        h, m = 7, 30

    spec = RoutineSpec(
        name="inbox_digest",
        description="Daily listing of new inbox files with metadata, saved to ai-workspace/outbox/.",
        schedule_cron=f"{m} {h} * * *",  # daily at DIGEST_TIME - 30min
        steps=[{"action_name": "__builtin__inbox_digest"}],
        requires_approval=False,
        approved_at=datetime.now(timezone.utc),  # auto-approved
        enabled=True,
    )
    try:
        register_routine(spec)
    except Exception as exc:
        _log.warning("inbox_digest registration error: %s", exc)


def _load_routine_row(name: str, user_id: str) -> Optional[dict]:
    try:
        ms = config.get_metadata_store()
        return ms.fetch_one(
            "SELECT name, description, schedule_cron, steps, requires_approval, "
            "approved_at, last_run_at, enabled, user_id FROM routines "
            "WHERE name = %s AND user_id = %s",
            (name, user_id),
        )
    except Exception:
        return None


def _row_to_spec(row: dict) -> RoutineSpec:
    steps = row.get("steps") or []
    if isinstance(steps, str):
        import json as _json
        steps = _json.loads(steps)
    return RoutineSpec(
        name=row["name"],
        description=row.get("description", ""),
        schedule_cron=row.get("schedule_cron", ""),
        steps=steps,
        requires_approval=row.get("requires_approval", True),
        approved_at=row.get("approved_at"),
        last_run_at=row.get("last_run_at"),
        enabled=row.get("enabled", True),
        user_id=row.get("user_id", "default"),
    )


def _update_last_run(name: str, user_id: str) -> None:
    try:
        ms = config.get_metadata_store()
        ms.execute(
            "UPDATE routines SET last_run_at = NOW() WHERE name = %s AND user_id = %s",
            (name, user_id),
        )
    except Exception as exc:
        _log.debug("update last_run_at error: %s", exc)


def _write_routine_audit(name: str, output: str, user_id: str) -> None:
    from actions.audit import write_audit
    from models.actions import AuditEntry

    entry = AuditEntry(
        action_name=f"routine:{name}",
        connector="routines",
        mode="DO",
        input_summary=f"Routine {name!r} triggered",
        result_summary=output[:500],
        executed_at=datetime.now(timezone.utc),
        user_id=user_id,
    )
    write_audit(entry)
