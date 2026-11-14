# ADR-180: Proactive email surfacing — delivery trigger + morning brief

**Status:** Finalised
**Created:** 2026-07-21
**Last updated:** 2026-07-21
**Decided by:** Opus 4.8 (/explore LUM-648)
**Linear:** LUM-648

## Context

LUM-648 (chunk #6 of the LUM-641 email arc) makes the value *come to* the user: a snooze-expiry re-surface ping, a needs-you notification with meaningful copy, and a morning brief. The notification backend (unified `dispatcher.emit` → ntfy/web-push/in-app SSE, with tier policy + per-user prefs + quiet-hours) already exists but is unsurfaced for email. LUM-643 deliberately made snooze re-surface **query-time in the frontend with no scheduler** — so a *push* at expiry has no trigger. LUM-643 shipped `idx_signal_triage_user_status_due` and the `snoozed_until_pairing` CHECK expressly to feed this chunk's expiry scan; they have no consumer yet. Constraints: local-first, in-process preferred, no new Docker service, reuse the five-concept model and existing dispatcher.

## Decision

Deliver via an **in-process periodic APScheduler scan**, mirroring the existing `signals/digest.py` job (its advisory-lock + per-user-fanout shape is the proven precedent). The scan reads `signal_triage` for due snoozes (`status='snoozed' AND snoozed_until <= now()`), **flips each expired row `snoozed → needs_you`** via one atomic guarded `UPDATE … RETURNING` (idempotent — the `WHERE status='snoozed'` guard makes it re-snooze-race-safe, and it reconciles server state with the frontend `effectiveLane` which already shows expired-snooze as needs-you), and calls the unified `emit()` per affected user with meaningful, deep-linked copy. Two new `NotificationType`s are added — `SIGNAL_NEEDS_YOU` (tier `ACTION_REQUIRED`) and `SIGNAL_MORNING_BRIEF` (tier `INFORMATIONAL`) — each with an in-app SSE branch and a **dynamic-body** web-push path (see the R1 revision below). The **morning brief** (v1) is a scheduled per-user local-morning `emit()` summarising the needs-you subset and deep-linking to the existing `/email` lanes (the SW click allow-list gains `/email`) — **no new home surface**. At ingest, a needs-you email (`importance ≥ 0.6`) is the single needs-you push; every other email is downgraded to in-app-only (`BACKGROUND`) and no longer sets `signals.notified` — `notified` is redefined as "a needs-you push fired". All delivery honours per-user prefs + quiet-hours; **silence when nothing needs the user**.

## Alternatives Considered

- **Event-driven only** (re-surface on next ingest/poll) — misses the time event when no new mail arrives.
- **Frontend SW timers / Periodic Background Sync** — unreliable, needs the app installed/open and a Chromium engine; can't drive ntfy.
- **External cron/systemd → internal endpoint** — works but adds an ops surface the in-process scheduler removes; fallback only.
- **Dedicated job runner (Celery/dramatiq/RQ)** — new Docker service + broker for one periodic scan; APScheduler already runs this exact shape.
- **New `/today` home dashboard for v1** — deferred (Option M2); reuse `/email` as the "today" destination first.

## Consequences

**Easier:** snooze-expiry pings, needs-you notifications, and the morning brief all ride one proven pattern (advisory-lock scan) + one send path (`emit`), reusing tier/prefs/quiet-hours/channels for free; no new infra; the LUM-643 index/CHECK finally get their consumer.

**Harder / committed:** the server now mutates the user's own triage row (snooze→needs_you flip); two new `NotificationType`s are wired across enum + tier map + in-app SSE + web-push; the SW click allow-list gains `/email`; a new advisory-lock key pair is reserved (`8420607/40` scan + `/41` brief, distinct from the digest's `/39`); `signals.notified` semantics change (cross-chunk contract); `webpush_subscriptions.notify_on_signals` becomes the sole live web-push gate for the two new types and is reset to FALSE once on deploy for fresh consent. Triage re-derivation stays on the frontend `effectiveLane` — the scan reconciles, it does not replace it.

**Future chunks must know:** LUM-649 (feedback→relevance) is orthogonal and composes via the same needs-you lane; a dedicated home dashboard (M2) is deferred; advanced agentic surfacing stays app-scope (LUM-654). `services/triage_surfacing.count_needs_you_email` is the reusable server-side needs-you selection future surfaces should call.

## Revisit conditions

- If in-process APScheduler proves insufficient at self-hoster scale (many users × frequent scan), revisit the external-runner or a dedicated job service.
- If per-user local-morning brief scheduling needs sub-hour precision across timezones beyond an hourly check, revisit the trigger design.
- If a second triage surface earns its keep, revisit the deferred `/today` dashboard (M2).

## Decision revision — dynamic-body web push (over fixed template)

The original draft said needs-you web push uses "a web-push **template**". The shipped chunk instead uses a **dynamic-body** path in v1 (user decision #6 requires meaningful copy — sender + subject). This is a deliberate posture change from `WebPushTemplate`'s "no external string injection" stance; it is safe because (a) the service worker renders **text-only** and **truncates** (120/240 chars), (b) content is E2E-encrypted to the subscription, and (c) `build_safe_push_json_from_dict` remains the single serialization choke point (`{title, body, url}` allowlist only). Fixed templates (ROUTINE_ELEVATION) are untouched. Rejected alternative: a fixed `SIGNAL_NEEDS_YOU` template with a generic body ("An email needs you") — preserves the no-injection posture but loses the meaningful copy the user explicitly required.

## Status history
- 2026-07-21: Draft created by /explore (LUM-648); revised during /review-plan R1 (dynamic-body web push) and finalised by /verify-plan — implementation confirmed the decision. Draft lineage: `.cursor/adrs/proactive-email-surfacing.md`.
