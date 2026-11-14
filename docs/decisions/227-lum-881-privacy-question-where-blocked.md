# ADR-227: Ask the privacy-mode question where cloud models are blocked, not in first-run onboarding (LUM-881)
**Status:** Finalised
**Created:** 2026-11-06
**Last updated:** 2026-11-06
**Decided by:** /explore (claude-opus-5-5, headless run for LUM-881); revised in /review-plan arbitration R1
**Finalised by:** /verify-plan 2026-11-06 (claude-opus-5-5) — implementation confirmed decision
**Plan:** `.cursor/plans/LUM-881-explain-privacy-mode-block.plan.md`
**Exploration:** `.cursor/explorations/LUM-881-first-run-privacy-question.md`
**Draft mirror:** `.cursor/adrs/LUM-881-first-run-privacy-question.md`
**Linear:** LUM-881
**Extends:** [ADR 147](147-lum-194-cloud-llm-privacy-mode.md) (presentation only; enforcement unchanged)

## Context

ADR 147 makes fresh installs local-only, so remote models stay off until an admin allows them on `/admin/privacy-mode`. Nothing tells anyone the default exists. An admin who saves a cloud LLM key, or sets one in `.env`, sees the cloud models as `— unavailable` in the chat picker with no reason. LUM-881 asked for the question to be put at first-run setup, and LUM-882 (folded in) asked for a knowledge-graph offer there too.

Two facts shaped the options. First, the onboarding modal never renders when `AUTH_ENABLED=false` (`me.py` synthetic `completed_at`), and that is the default for Persona A and for Persona C until sharing is on. ADR 221 already moved the paperless offer out of the modal for this reason, and LUM-853 recommends keeping it that way. Second, `GRAPH_MODE` is boot-time env backed by a compose overlay, the public Web has no graph surface, ADR 042 strips the graph from the public export, ADR 101 makes it an add-in that is off by default, and LUM-855 keeps `disabled` as the default.

## Decision

Lumogis asks the privacy-mode question **at the moment a cloud model is configured and blocked**, not in the onboarding modal. `GET /api/v1/models` gains an additive `disabled_reason` on `ModelDescriptor`, so a model that privacy mode blocks reads differently from one with no key. The chat and document-chat pickers show a one-line hint when privacy mode is the cause. A successful LLM key save says the models stay off when the caller's model list then shows a model blocked by privacy mode. All three hints use that one condition. Both link admins to `/admin/privacy-mode` and tell members to ask their admin, or point them to their own restriction. ADR 147 enforcement, defaults and the single write path are unchanged. The knowledge-graph offer is **not** part of first-run setup. It waits for LUM-855 and a runtime-visible graph mode, and is recorded in LUM-855's "Later".

## Alternatives Considered

See the exploration (`.cursor/explorations/LUM-881-first-run-privacy-question.md`).
- **Admin-only onboarding modal step:** invisible on the default install, and asks before there is any cloud model to allow.
- **"Not yet reviewed" badge on the admin page:** honest but reaches only admins who already open the page. Kept as a later polish in LUM-881.
- **Install-time env var / installer question:** re-opens the env-only policy ADR 147 rejected, and asks even earlier.
- **Graph offer in first-run:** no runtime control to bind to, and it breaks the ADR 042 export boundary before LUM-855.
- **Inline "Allow cloud" action in the hint:** left open (exploration question 2). The default is a link, which keeps one write path and shows the lock.

## Consequences

- **Easier:** every persona meets the privacy default the first time it matters, with no onboarding state, no dismissal store and no dependency on LUM-853. On `AUTH_ENABLED=false` installs this holds for keys in `.env` or the legacy dashboard (`app_settings`), not for keys saved in the Web: availability under auth-off does not read `user_connector_credentials`, so those models read `no_credentials` and no privacy hint shows. That is a separate, pre-existing bug (filed from the LUM-881 plan), not a gap in this decision.
- **Harder / foreclosed:** "first-run asks every admin up front" is not delivered. An admin with no cloud key is never asked, on purpose. A Web graph toggle is out until the graph has a runtime mode.
- **Future chunks must know:** any new surface that lists or picks models (LUM-877 task slots and diagnostics, a future "Auto" choice, LUM-169 managed keys) should read `disabled_reason` rather than re-deriving why a model is off. **Precedence:** availability first, privacy last. `privacy_mode` means "usable except for privacy mode"; a model with no key reads `no_credentials` whatever the policy, so admins with no cloud key are never told about privacy. A new availability reason must slot in before `privacy_mode`. Adding a value is an oasdiff warning, not a free change. The OpenAI-compatible `/v1/models` keeps hiding blocked models and does not carry the reason.

## Revisit conditions

- If LUM-853 lights the onboarding modal on default installs (its Option 1), revisit whether a modal mention is worth adding alongside the hint.
- If LUM-855 ships **and** `GRAPH_MODE` becomes readable at runtime by the Web, revisit a graph pointer in admin settings or first run.
- If support reports show admins still confused by "cloud models off" after this ships, revisit an inline admin-only "Allow cloud models" action.
- If a cloud call path is added that bypasses `is_model_enabled` / `get_llm_provider` (ADR 147's own revisit condition), the hint would under-report. Revisit then.

## Status history
- 2026-11-06: Draft created by /explore (headless, LUM-881)
- 2026-11-06: Revised during /review-plan --arbitrate R1 — recorded the availability-first precedence the plan chose over the exploration's privacy-first step; corrected the auth-off consequence (Web-saved keys are not seen by availability); key-save hint now shown only when the model list confirms a privacy block
- 2026-11-06: Finalised by /verify-plan — implementation confirmed decision (`config.model_disabled_reason`, `ModelDescriptor.disabled_reason`, `PrivacyBlockedHint` on chat, document chat and `ConnectorCard`). Numbered 227 because 225/226 are claimed by unmerged `agent/lum-777` / `agent/lum-844`
