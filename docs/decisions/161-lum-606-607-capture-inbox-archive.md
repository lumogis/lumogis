# ADR-161: Capture inbox note-vault and read-only archive (LUM-606 / LUM-607)

**Status:** Finalised
**Created:** 2026-07-10
**Last updated:** 2026-07-10
**Decided by:** as-shipped implementation (retrospective)
**Finalised by:** /record-retro 2026-07-10 (Composer)
**Plan:** none — shipped on `claude/design-context-status-w7xez3` (5 commits fast-forwarded to `dev`)
**Exploration:** `.cursor/explorations/archived/capture_inbox_archive_retro.md`
**Draft mirror:** `.cursor/adrs/lum_606_607_capture_inbox_archive.md`

**Linear:** [LUM-606](https://linear.app/lumogis/issue/LUM-606), [LUM-607](https://linear.app/lumogis/issue/LUM-607)

## Context

Quick Capture already created server-side capture rows (`pending` → `indexed` / `failed`). Operators needed a household-member **inbox** to edit/delete/retry/commit notes before indexing, and a separate **archive** of committed captures for provenance — not an Obsidian-style vault. LUM-606 owns the mutable inbox; LUM-607 owns the read-only archive tab.

## Decision

1. **Status filter API** — `GET /api/v1/captures?status=pending,failed` (and `indexed`, etc.) with filtered `total`; `CaptureListItem.last_error` on list rows for failed-state UX.
2. **Inbox (LUM-606)** — `CaptureInboxView` lists `pending` + `failed`; `CaptureDetailPanel` supports edit, delete, retry, and **commit to memory** (`POST …/index`); committed rows leave the inbox.
3. **Archive (LUM-607)** — `CaptureArchiveView` lists `indexed` only; `CaptureArchiveDetail` is **read-only** (provenance: `note_id`, `indexed_at`; allowlist guard — single Close action).
4. **Quick Capture tabs** — Compose / Inbox / Outbox / Archive on `QuickCapturePage`; shared `CaptureCard` + `useCaptureList` hook.
5. **Mode-aware copy** — online/offline messaging for capture flows (LUM-606 scope).
6. **Tests** — pytest `test_api_v1_captures_status_filter.py`; Vitest `CaptureInboxView`, `CaptureArchiveView`, `QuickCapturePage`.
7. **Coverage matrix** — rows **2.3.23** (LUM-606), **2.3.24** (LUM-607).

## Alternatives considered

- **Single combined list with inline state** — rejected; inbox vs archive mental model is clearer with separate tabs.
- **Reuse mutate panel in archive** — rejected; read-only archive prevents accidental edits to committed memory.

## Consequences

**Positive:** Capture lifecycle is visible end-to-end in Lumogis Web; failed captures surface `last_error` and retry without admin tooling.

**Limits:** Playwright capture inbox/archive e2e deferred (matrix notes); local pytest for captures may 401 without compose-test auth fixtures — CI/stack gate is authoritative.

## Revisit conditions

- Playwright smoke for inbox commit + archive read-only guard when capture e2e harness matures.
- Future inbox sync/offline queue work stays under LUM-606 programme.

## Testing retrospective

| Layer | Command | Result |
|-------|---------|--------|
| Vitest | `npm test -- --run tests/features/capture/CaptureInboxView.test.tsx tests/features/capture/CaptureArchiveView.test.tsx tests/features/capture/QuickCapturePage.test.tsx` | **27 passed** |
| pytest | `test_api_v1_captures_status_filter.py` | **green on compose-test / CI**; local bare `.venv` run may 401 without auth env |
| Build gate | `npm run codegen && npm run build` | **green** (2026-07-10) |
| Matrix | `make coverage-matrix-check` | **202/202 green** |

## Linear linkage (Product OS)

- **LUM-606** — inbox scope complete; apply `/linear-update apply-closure LUM-606 --done`.
- **LUM-607** — archive scope complete; apply `/linear-update apply-closure LUM-607 --done`.
