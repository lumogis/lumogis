<!-- Last audited: 2026-06-14 against dev — LUM-93 + LUM-185 merge-workflow -->

# TEST-COVERAGE-MATRIX — Web (lumogis-web)

## Legend

| Symbol | Meaning |
| --- | --- |
| ✅ | At least one test **asserts** the behaviour (see **Notes**) |
| 🟡 | Related tests exist; dedicated assertion for this feature not confirmed at seed |
| ❌ | No matching automated test found in code audit |
| 🚫 | Not automatable — manual checklist (`MS-###` in [RELEASE-MANUAL-CHECKLIST.md](../RELEASE-MANUAL-CHECKLIST.md)) |

**Maintenance:** v1 baseline **LUM-384**; **LUM-428** strict citations + plan cross-check. Ongoing via **`/verify-plan`**.

**Evidence:** ✅ = `` `test_name` in `file` `` in repo; may cite **plan** (`LUM-###` / `.cursor/plans/…`). Plans: active + archived.

| ID | Feature | Test source(s) | Layer | Status | Notes |
| --- | --- | --- | --- | --- | --- |

### §2.1

| 2.1.1 | Me profile | clients/lumogis-web/tests/features/me/MeProfileView.test.tsx | web | ✅ | `MeProfileView.test` in `clients/lumogis-web/tests/features/me/MeProfileView.test.tsx`; code audit |
| 2.1.2 | Me connectors | clients/lumogis-web/tests/features/me/MeConnectorsView.test.tsx | web | ✅ | `MeConnectorsView.test` in `clients/lumogis-web/tests/features/me/MeConnectorsView.test.tsx`; plan `plan` (plan path) |
| 2.1.3 | Me connector permissions | clients/lumogis-web/tests/features/me/MePermissionsView.test.tsx | web | ✅ | `MePermissionsView.test` in `clients/lumogis-web/tests/features/me/MePermissionsView.test.tsx`; plan `plan` (plan path) |
| 2.1.4 | Me LLM providers | clients/lumogis-web/tests/features/me/MeLlmProvidersView.test.tsx | web | ✅ | LUM-44; `MeLlmProvidersView.test` in `clients/lumogis-web/tests/features/me/MeLlmProvidersView.test.tsx`; code audit |
| 2.1.5 | Me MCP tokens | clients/lumogis-web/tests/features/me/MeMcpTokensView.test.tsx | web | ✅ | LUM-44; `MeMcpTokensView.test` in `clients/lumogis-web/tests/features/me/MeMcpTokensView.test.tsx`; plan `plan` (plan path) |
| 2.1.6 | Me notifications (status façade) | clients/lumogis-web/tests/features/me/MeNotificationsView.test.tsx | web | ✅ | LUM-93; status table + Web Push opt-in; `MeNotificationsView.test` in `clients/lumogis-web/tests/features/me/MeNotificationsView.test.tsx` |
| 2.1.9 | Me notification preferences matrix (LUM-93) | clients/lumogis-web/tests/features/me/MeNotificationsView.test.tsx | vitest | ✅ | LUM-93; `renders preference matrix with checkboxes`, `toggle calls PATCH with optimistic update`, `rollback on PATCH error` in `MeNotificationsView.test.tsx` |
| 2.1.10 | Me notification prefs matrix axe e2e (LUM-481) | clients/lumogis-web/tests/e2e/notification_prefs_matrix_a11y.spec.ts | playwright | ✅ | LUM-481; `login, open /me/notifications, axe prefs matrix table` in `notification_prefs_matrix_a11y.spec.ts`; smoke creds + `web-e2e-prove` pattern; local prove 2026-06-14 on dev `d338b4903` |
| 2.1.7 | Me export | clients/lumogis-web/tests/features/me/MeExportView.test.tsx | web | ✅ | LUM-44; `MeExportView.test` in `clients/lumogis-web/tests/features/me/MeExportView.test.tsx`; plan `plan` (plan path) |
| 2.1.8 | Me tools/capabilities overview | clients/lumogis-web/tests/features/me/MeToolsCapabilitiesView.test.tsx | web | ✅ | `MeToolsCapabilitiesView.test` in `clients/lumogis-web/tests/features/me/MeToolsCapabilitiesView.test.tsx`; code audit |

### §2.2

| 2.2.1 | Admin users | clients/lumogis-web/tests/features/admin/AdminUsersView.test.tsx | web | ✅ | LUM-44, LUM-29; `AdminUsersView.test` in `clients/lumogis-web/tests/features/admin/AdminUsersView.test.tsx`; plan `plan` (plan path) |
| 2.2.2 | Admin connector credentials | — | web | ❌ | code audit: no test match |
| 2.2.3 | Admin connector permissions | — | web | ❌ | code audit: no test match |
| 2.2.4 | Admin MCP tokens | — | web | ❌ | code audit: no test match |
| 2.2.5 | Admin audit log | clients/lumogis-web/tests/features/admin/AdminAuditView.test.tsx | web | ✅ | `AdminAuditView.test` in `clients/lumogis-web/tests/features/admin/AdminAuditView.test.tsx`; plan `plan` (plan path) |
| 2.2.6 | Admin diagnostics | clients/lumogis-web/tests/features/admin/AdminDiagnosticsView.test.tsx | web | ✅ | LUM-178, LUM-330, LUM-396; `AdminDiagnosticsView.test` in `clients/lumogis-web/tests/features/admin/AdminDiagnosticsView.test.tsx`; plan `plan` (plan path) |
| 2.2.7 | Admin system status + Ollama pull/delete (LUM-178 / LUM-423 / LUM-449 / LUM-452 / LUM-451) | clients/lumogis-web/tests/features/admin/AdminSystemStatusView.test.tsx | vitest | ✅ | LUM-423, LUM-449, LUM-452, LUM-451; async pull `POST /api/v1/admin/ollama/pull/async`, progress bar, active-job resume, 409 mutex; `shows qdrant init warning when pull returns qdrant_init_warning`, `delete confirms then POST` in `clients/lumogis-web/tests/features/admin/AdminSystemStatusView.test.tsx` |
| 2.2.8 | Admin Ollama pull/delete Playwright (LUM-450) | clients/lumogis-web/tests/e2e/admin_ollama_mutations.spec.ts | playwright | ✅ | LUM-450; optional-gated `LUMOGIS_E2E_EXPECT_OLLAMA=1`; `pull then delete ephemeral model` in `clients/lumogis-web/tests/e2e/admin_ollama_mutations.spec.ts`; not in default `web-e2e-prove` |
| 2.2.9 | Admin system status DR backup panel (LUM-185 / LUM-487) | clients/lumogis-web/tests/features/admin/AdminSystemStatusView.test.tsx, clients/lumogis-web/tests/e2e/admin_system_status.spec.ts | vitest + playwright | ✅ | LUM-185 Vitest: `shows stale DR backup warning when backup-status reports stale` in `AdminSystemStatusView.test.tsx`; LUM-487 Playwright: `shows DR backup panel for admin` in `admin_system_status.spec.ts` (admin smoke creds + `LUMOGIS_E2E_EXPECT_ADMIN=1`) |

### §2.3

| 2.3.1 | Chat page + stream | clients/lumogis-web/tests/features/chat/ConversationSidebar.test.tsx | web | ✅ | LUM-44, LUM-162; `ConversationSidebar.test` in `clients/lumogis-web/tests/features/chat/ConversationSidebar.test.tsx`; plan `plan` (plan path) |
| 2.3.2 | Search page | clients/lumogis-web/tests/features/memory/SearchPage.test.tsx | web | ✅ | LUM-44, LUM-329, LUM-384; `SearchPage.test` in `clients/lumogis-web/tests/features/memory/SearchPage.test.tsx`; code audit |
| 2.3.3 | Approvals page | clients/lumogis-web/tests/features/approvals/ApprovalsPage.test.tsx | web | ✅ | LUM-44, LUM-76, LUM-123; `ApprovalsPage.test` in `clients/lumogis-web/tests/features/approvals/ApprovalsPage.test.tsx`; code audit |
| 2.3.4 | Conversation history UI (LUM-162) | clients/lumogis-web/tests/features/chat/ConversationSidebar.test.tsx | e2e | ✅ | LUM-162; `ConversationSidebar.test` in `clients/lumogis-web/tests/features/chat/ConversationSidebar.test.tsx`; plan `plan` (plan path) |
| 2.3.5 | First-wow gating (LUM-216) | clients/lumogis-web/tests/features/wow/WowGate.test.tsx | e2e | ✅ | LUM-216; `WowGate.test` in `clients/lumogis-web/tests/features/wow/WowGate.test.tsx`; plan `plan` (plan path) |
| 2.3.6 | Onboarding dismiss persists | clients/lumogis-web/tests/features/onboarding/OnboardingModal.test.tsx | e2e | ✅ | LUM-165; `OnboardingModal.test` in `clients/lumogis-web/tests/features/onboarding/OnboardingModal.test.tsx`; plan `plan` (plan path) |

### §2.4

| 2.4.1 | PWA manifest + service worker | clients/lumogis-web/tests/pwa/manifest.test.ts | web | ✅ | `manifest.test` in `clients/lumogis-web/tests/pwa/manifest.test.ts`; code audit |
| 2.4.2 | Web push browser flow | clients/lumogis-web/tests/pwa/webPushBrowser.test.ts | web | ✅ | `webPushBrowser.test` in `clients/lumogis-web/tests/pwa/webPushBrowser.test.ts`; code audit |
| 2.4.3 | Capture outbox / offline queue | clients/lumogis-web/tests/pwa/captureOutbox.test.ts | web | ✅ | `captureOutbox.test` in `clients/lumogis-web/tests/pwa/captureOutbox.test.ts`; code audit |
| 2.4.4 | Mobile shell e2e | clients/lumogis-web/tests/e2e/me_admin_mobile_shell.spec.ts | e2e | ✅ | `me_admin_mobile_shell.spec` in `clients/lumogis-web/tests/e2e/me_admin_mobile_shell.spec.ts`; code audit |
| 2.4.5 | Admin shell e2e | clients/lumogis-web/tests/e2e/admin_shell.spec.ts | e2e | ✅ | `admin_shell.spec` in `clients/lumogis-web/tests/e2e/admin_shell.spec.ts`; code audit |
| 2.4.6 | LUM-44: Cross-Device Lumogis Web | orchestrator/tests/test_auth_phase1.py | e2e | 🟡 | LUM-44; `test_user_context_default_is_admin_in_dev_mode` in `orchestrator/tests/test_auth_phase1.py`; plan partial — orchestrator/API only — not web UI (LUM-428) |
| 2.4.7 | LUM-165: First-run onboarding and zero states | orchestrator/tests/test_me_onboarding_routes.py | e2e | 🟡 | LUM-165; `test_onboarding_get_dev_synthetic` in `orchestrator/tests/test_me_onboarding_routes.py`; plan partial — orchestrator/API only — not web UI (LUM-428) |
