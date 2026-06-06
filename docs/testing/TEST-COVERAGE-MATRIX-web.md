<!-- Last audited: 2026-06-04 against 308da8d17 on dev — LUM-428 code + plan audit -->

# TEST-COVERAGE-MATRIX — Web (lumogis-web)

## Legend

| Symbol | Meaning |
| --- | --- |
| ✅ | At least one test **asserts** the behaviour (see **Notes**) |
| 🟡 | Related tests exist; dedicated assertion for this feature not confirmed at seed |
| ❌ | No matching automated test found in code audit |
| 🚫 | Not automatable — manual smoke (`MS-TBD` until LUM-385) |

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
| 2.1.6 | Me notifications | clients/lumogis-web/tests/features/me/MeNotificationsView.test.tsx | web | ✅ | LUM-189, LUM-39; `MeNotificationsView.test` in `clients/lumogis-web/tests/features/me/MeNotificationsView.test.tsx`; plan `plan` (plan path) |
| 2.1.7 | Me export | clients/lumogis-web/tests/features/me/MeExportView.test.tsx | web | ✅ | LUM-44; `MeExportView.test` in `clients/lumogis-web/tests/features/me/MeExportView.test.tsx`; plan `plan` (plan path) |
| 2.1.8 | Me tools/capabilities overview | clients/lumogis-web/tests/features/me/MeToolsCapabilitiesView.test.tsx | web | ✅ | `MeToolsCapabilitiesView.test` in `clients/lumogis-web/tests/features/me/MeToolsCapabilitiesView.test.tsx`; code audit |

### §2.2

| 2.2.1 | Admin users | clients/lumogis-web/tests/features/admin/AdminUsersView.test.tsx | web | ✅ | LUM-44, LUM-29; `AdminUsersView.test` in `clients/lumogis-web/tests/features/admin/AdminUsersView.test.tsx`; plan `plan` (plan path) |
| 2.2.2 | Admin connector credentials | — | web | ❌ | code audit: no test match |
| 2.2.3 | Admin connector permissions | — | web | ❌ | code audit: no test match |
| 2.2.4 | Admin MCP tokens | — | web | ❌ | code audit: no test match |
| 2.2.5 | Admin audit log | clients/lumogis-web/tests/features/admin/AdminAuditView.test.tsx | web | ✅ | `AdminAuditView.test` in `clients/lumogis-web/tests/features/admin/AdminAuditView.test.tsx`; plan `plan` (plan path) |
| 2.2.6 | Admin diagnostics | clients/lumogis-web/tests/features/admin/AdminDiagnosticsView.test.tsx | web | ✅ | LUM-178, LUM-330, LUM-396; `AdminDiagnosticsView.test` in `clients/lumogis-web/tests/features/admin/AdminDiagnosticsView.test.tsx`; plan `plan` (plan path) |
| 2.2.7 | Admin system status (LUM-178) | clients/lumogis-web/tests/features/admin/AdminSystemStatusView.test.tsx | web | ✅ | LUM-178; `AdminSystemStatusView.test` in `clients/lumogis-web/tests/features/admin/AdminSystemStatusView.test.tsx`; plan `plan` (plan path) |

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
