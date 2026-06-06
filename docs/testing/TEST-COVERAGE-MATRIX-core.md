<!-- Last audited: 2026-06-05 against agent/lum-433 — LUM-433 verify-plan -->

# TEST-COVERAGE-MATRIX — Core (orchestrator & platform)

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

### §1.1

| 1.1.1 | Health / readiness (`/healthz`) | orchestrator/tests/test_web_shell.py | unit | ✅ | LUM-77; `test_healthz_unauthenticated` in `orchestrator/tests/test_web_shell.py`; code audit |
| 1.1.2 | Stack-control supervision | stack-control/test_main.py | unit | 🟡 | LUM-101, LUM-103, LUM-178; `test_health_ok` in `stack-control/test_main.py`; plan partial — weak path/name match (LUM-428) |
| 1.1.3 | Compose policy guard (LUM-43) | scripts/tests/test_check_compose_policy.py | unit | ✅ | `test_adversarial_overlay_violation_reported` in `scripts/tests/test_check_compose_policy.py`; code audit |
| 1.1.4 | Inbox folder-watch auto-ingest (LUM-330) | orchestrator/tests/test_inbox_enqueue.py | integration | ✅ | LUM-76, LUM-330; `test_wait_for_stable_file_when_unchanging_then_true` in `orchestrator/tests/test_inbox_enqueue.py`; plan `plan` (plan path) |
| 1.1.5 | Multi-path ingest compose binds (LUM-401) | orchestrator/tests/test_ingest_paths_settings.py | unit | ✅ | LUM-397, LUM-400, LUM-401; `test_migrate_filesystem_root_to_ingest_paths` in `orchestrator/tests/test_ingest_paths_settings.py`; plan `plan` (plan path) |
| 1.1.6 | Debug test inventory (LUM-377) | — | release-rc | ❌ | code audit: no test match |

### §1.2

| 1.2.1 | Admin settings ingest_paths GET/PUT | orchestrator/tests/test_ingest_paths_settings.py | unit | 🟡 | LUM-397, LUM-400, LUM-401; `test_migrate_filesystem_root_to_ingest_paths` in `orchestrator/tests/test_ingest_paths_settings.py`; plan partial — weak path/name match (LUM-428) |
| 1.2.2 | Legacy + v1 data ingest | orchestrator/tests/test_api_v1_ingest_upload.py | unit | ✅ | LUM-397; `test_upload_requires_auth` in `orchestrator/tests/test_api_v1_ingest_upload.py`; plan `plan` (plan path) |
| 1.2.3 | Ingest upload API | orchestrator/tests/test_api_v1_ingest_upload.py | unit | ✅ | LUM-397; `test_upload_requires_auth` in `orchestrator/tests/test_api_v1_ingest_upload.py`; plan `plan` (plan path) |
| 1.2.4 | Ingest path watcher | orchestrator/tests/test_ingest_paths_watcher.py | unit | 🟡 | LUM-397; `test_ingest_path_handler_rejects_symlink_escape` in `orchestrator/tests/test_ingest_paths_watcher.py`; plan partial — weak path/name match (LUM-428) |
| 1.2.5 | Admin browse / review-queue | — | unit | ❌ | code audit: no test match |

### §1.3

| 1.3.1 | Signal sources CRUD | — | unit | ❌ | code audit: no test match |
| 1.3.2 | Signal profile + feedback | — | unit | ❌ | code audit: no test match |
| 1.3.3 | Routines approve/run | orchestrator/tests/test_routines_per_user_scheduling.py | unit | ✅ | `test_maybe_schedule_uses_per_user_job_id` in `orchestrator/tests/test_routines_per_user_scheduling.py`; plan `plan` (plan path) |
| 1.3.4 | SSE `/events` stream | clients/lumogis-web/tests/features/wow/useWowReadinessSse.test.tsx | unit | ✅ | LUM-44; `useWowReadinessSse.test` in `clients/lumogis-web/tests/features/wow/useWowReadinessSse.test.tsx`; code audit |
| 1.3.5 | Notifications API | — | unit | ❌ | code audit: no test match |

### §1.4

| 1.4.1 | Captures ledger CRUD (`/api/v1/captures`) | orchestrator/tests/test_api_v1_captures.py | unit | ✅ | LUM-44; `test_create_capture_201` in `orchestrator/tests/test_api_v1_captures.py`; plan `plan` (plan path) |
| 1.4.2 | Capture attachments | orchestrator/tests/test_api_v1_captures.py | unit | ✅ | LUM-44; `test_create_capture_201` in `orchestrator/tests/test_api_v1_captures.py`; plan `plan` (plan path) |
| 1.4.3 | Voice transcribe STT | orchestrator/tests/test_voice_transcribe_route.py | unit | ✅ | LUM-384; `test_stt_disabled_503` in `orchestrator/tests/test_voice_transcribe_route.py`; plan `plan` (plan path) |
| 1.4.4 | Quick capture web route (client) | clients/lumogis-web/tests/e2e/first_slice.spec.ts | web | 🟡 | LUM-44, LUM-76, LUM-162; `first_slice.spec` in `clients/lumogis-web/tests/e2e/first_slice.spec.ts`; plan partial — `first_slice.spec` missing feature token (quick, capture) |

### §1.5

| 1.5.1 | Memory semantic search | orchestrator/tests/test_mcp_server.py | unit | ✅ | `test_memory_search_tool_wraps_retrieve_context` in `orchestrator/tests/test_mcp_server.py`; code audit |
| 1.5.2 | Recent sessions API | orchestrator/tests/test_api_v1_memory.py | unit | 🟡 | LUM-44, LUM-162, LUM-209; `test_search_rejects_blank_q` in `orchestrator/tests/test_api_v1_memory.py`; plan partial — `test_search_rejects_blank_q` missing feature token (recent) |
| 1.5.3 | Entity extraction + listing | orchestrator/tests/test_entities.py | unit | 🟡 | `test_restore_path_remains_generic_on_conflict_do_nothing` in `orchestrator/tests/test_entities.py`; plan partial — `test_restore_path_remains_generic_on_conflict_do_nothing` missing feature token (extract, entities, merge) |
| 1.5.4 | Qdrant user_id filter isolation | orchestrator/tests/test_phase3_user_id_contracts.py | unit | ✅ | `test_loop_ask_requires_user_id_kwarg` in `orchestrator/tests/test_phase3_user_id_contracts.py`; plan `plan` (plan path) |
| 1.5.5 | Auto-RAG injection (LUM-308) | orchestrator/tests/test_auto_rag.py | unit | ✅ | LUM-308; `test_auto_rag_disabled_returns_empty` in `orchestrator/tests/test_auto_rag.py`; plan `plan` (plan path) |
| 1.5.6 | Data search (`/data/search`) | — | unit | ❌ | code audit: no test match |

### §1.6

| 1.6.1 | JWT auth login/refresh | orchestrator/tests/test_auth_phase1.py | unit | 🟡 | LUM-44, LUM-183, LUM-23; `test_user_context_default_is_admin_in_dev_mode` in `orchestrator/tests/test_auth_phase1.py`; plan partial — `test_user_context_default_is_admin_in_dev_mode` missing feature token (login, refresh) |
| 1.6.2 | Bootstrap admin | orchestrator/tests/test_auth_phase1.py | unit | ✅ | `test_bootstrap_if_empty_creates_admin_when_env_set` in `orchestrator/tests/test_auth_phase1.py`; code audit |
| 1.6.3 | Credential tiers + resolver | orchestrator/tests/integration/test_credential_tier_precedence.py | unit | ✅ | `test_resolver_walks_user_household_system_then_unconfigured` in `orchestrator/tests/integration/test_credential_tier_precedence.py`; plan `plan` (plan path) |
| 1.6.4 | Connector credentials (me + admin) | orchestrator/tests/test_caldav_connector_credentials.py | unit | ✅ | LUM-281; `test_caldav_is_registered` in `orchestrator/tests/test_caldav_connector_credentials.py`; plan `plan` (plan path) |
| 1.6.5 | Connector permissions per-user | orchestrator/tests/test_per_user_connector_permissions.py | unit | ✅ | `test_get_connector_mode_returns_per_user_row_when_present` in `orchestrator/tests/test_per_user_connector_permissions.py`; plan `plan` (plan path) |
| 1.6.6 | Admin users + sessions | orchestrator/tests/test_csrf_origin_check.py | unit | ✅ | `test_admin_users_post_unauthenticated_rejected_outright` in `orchestrator/tests/test_csrf_origin_check.py`; code audit |
| 1.6.7 | User data export ZIP | orchestrator/tests/test_user_export_routes.py | unit | ✅ | LUM-35; `test_me_export_self_returns_zip_in_dev_mode` in `orchestrator/tests/test_user_export_routes.py`; plan `plan` (plan path) |
| 1.6.8 | CSRF / origin check on refresh | orchestrator/tests/test_csrf_origin_check.py | unit | ✅ | `test_refresh_403_when_origin_mismatch` in `orchestrator/tests/test_csrf_origin_check.py`; plan `plan` (plan path) |
| 1.6.9 | Scope publish endpoints | orchestrator/tests/test_mcp_scope_schema.py | unit | ✅ | `test_session_summary_schema_advertises_scope` in `orchestrator/tests/test_mcp_scope_schema.py`; code audit |

### §1.7

| 1.7.1 | MCP tokens mint/revoke | orchestrator/tests/test_mcp_tokens.py | unit | ✅ | LUM-44, LUM-29; `test_mint_returns_lmcp_prefixed_50_char_token` in `orchestrator/tests/test_mcp_tokens.py`; plan `plan` (plan path) |
| 1.7.2 | MCP bearer wiring | orchestrator/tests/test_phase3_1_mcp_bearer_wiring.py | unit | ✅ | `test_mcp_request_resolves_user_id_from_jwt_sub` in `orchestrator/tests/test_phase3_1_mcp_bearer_wiring.py`; plan `plan` (plan path) |
| 1.7.3 | Me tools catalog read model | orchestrator/tests/test_api_v1_me_tools.py | unit | ✅ | `test_me_tools_401_when_auth_enabled_without_token` in `orchestrator/tests/test_api_v1_me_tools.py`; code audit |
| 1.7.4 | Capabilities HTTP registry | orchestrator/tests/test_mcp_server.py | unit | 🟡 | `test_capabilities_route_returns_valid_manifest_json` in `orchestrator/tests/test_mcp_server.py`; weak path/name match (LUM-428) |
| 1.7.5 | Tool catalog LLM merge | orchestrator/tests/test_tool_catalog_mcp_vs_llm.py | unit | ✅ | `test_mcp_and_llm_tool_name_sets_and_docstring_contract` in `orchestrator/tests/test_tool_catalog_mcp_vs_llm.py`; code audit |

### §1.8

| 1.8.1 | Chat ask + tool loop | orchestrator/tests/test_chat_memory_hint.py | unit | 🟡 | LUM-124, LUM-308; `test_memory_hint_appended_when_enabled` in `orchestrator/tests/test_chat_memory_hint.py`; plan partial — weak path/name match (LUM-428) |
| 1.8.2 | OpenAI-compatible `/v1/chat/completions` | orchestrator/tests/test_chat_route_llm_credential_errors.py | unit | ✅ | `test_chat_completions_424_on_missing_credential` in `orchestrator/tests/test_chat_route_llm_credential_errors.py`; code audit |
| 1.8.3 | Conversations history API (LUM-162) | orchestrator/tests/test_api_v1_conversations.py | unit | ✅ | LUM-162; `test_list_returns_only_visible_sessions` in `orchestrator/tests/test_api_v1_conversations.py`; plan `plan` (plan path) |
| 1.8.4 | Approvals pending API | orchestrator/tests/test_api_v1_approvals.py | unit | ✅ | LUM-44, LUM-76, LUM-123; `test_pending_returns_empty_when_no_data` in `orchestrator/tests/test_api_v1_approvals.py`; plan `plan` (plan path) |
| 1.8.5 | Action audit + reverse | orchestrator/tests/test_api_v1_audit.py | unit | ✅ | LUM-44; `test_list_audit_returns_rows` in `orchestrator/tests/test_api_v1_audit.py`; code audit |
| 1.8.6 | Action proposals atomic claim (LUM-123) | orchestrator/tests/test_proposal_queue.py | unit | ✅ | LUM-123; `test_claim_by_id_success` in `orchestrator/tests/test_proposal_queue.py`; plan `plan` (plan path) |

### §1.9

| 1.9.1 | Doctor CLI | orchestrator/tests/test_doctor_cli.py | unit | ✅ | LUM-199, LUM-319, LUM-320; `test_doctor_json_schema_version` in `orchestrator/tests/test_doctor_cli.py`; plan `plan` (plan path) |
| 1.9.2 | Admin diagnostics + stack status (LUM-178) | orchestrator/tests/test_stack_status_service.py | unit | ✅ | LUM-178; `test_stack_status_maps_compose_running_to_healthy` in `orchestrator/tests/test_stack_status_service.py`; plan `plan` (plan path) |
| 1.9.3 | OpenAPI snapshot + codegen gate | orchestrator/tests/test_api_v1_openapi_snapshot.py | unit | ✅ | LUM-44, LUM-76, LUM-123; `test_openapi_snapshot_exists` in `orchestrator/tests/test_api_v1_openapi_snapshot.py`; plan `plan` (plan path) |
| 1.9.4 | Public export hygiene (strip list, OpenAPI + Search overlay CI contracts) | orchestrator/tests/test_check_public_export_script.py | unit, integration | ✅ | LUM-199, LUM-303, **LUM-433**; `test_lum433_minimal_export_passes_with_search_workflow`, `test_export_tree_includes_search_overlay_workflow` in `orchestrator/tests/test_check_public_export_script.py` |
| 1.9.5 | Phase-3 grep security gate | orchestrator/tests/test_phase3_grep_gate.py | unit | 🟡 | `test_no_default_user_id_in_hot_paths` in `orchestrator/tests/test_phase3_grep_gate.py`; weak path/name match (LUM-428) |
| 1.9.6 | verify-public-rc release umbrella | — | release-rc | 🚫 | MS-TBD |
| 1.9.7 | GHCR publish smoke | — | release-rc | 🚫 | MS-TBD |
| 1.9.8 | LUM-101: Compose-test COMPOSE_FILE defaults + docker-compose.test.yml | stack-control/test_main.py | unit | ✅ | LUM-101; `test_health_ok` in `stack-control/test_main.py`; plan `.cursor/plans/archived/LUM-101-compose-test-compose-file-defaults.plan.md` (plan path) |
| 1.9.9 | LUM-199: make doctor v1 — read-only operator health CLI | orchestrator/tests/test_doctor_cli.py | unit | ✅ | LUM-199; `test_doctor_json_schema_version` in `orchestrator/tests/test_doctor_cli.py`; plan `.cursor/plans/archived/LUM-199-make-doctor-v1.plan.md` (plan path) |
| 1.9.10 | LUM-209: Sessions recency index (updated_at already shipped) | orchestrator/tests/test_mcp_tools.py | unit | ✅ | LUM-209; `test_recent_sessions_returns_empty_when_table_empty` in `orchestrator/tests/test_mcp_tools.py`; plan `.cursor/plans/archived/LUM-209-sessions-updated-at-index.plan.md` (plan path) |
| 1.9.11 | LUM-243: Per-request sid revocation lookup | orchestrator/tests/test_auth_phase1.py | unit | ✅ | LUM-243; `test_user_context_default_is_admin_in_dev_mode` in `orchestrator/tests/test_auth_phase1.py`; plan `.cursor/plans/archived/LUM-243-sid-revocation-lookup.plan.md` (plan path) |
| 1.9.12 | LUM-281: Paperless-ngx → Lumogis ingest (v0.1 Docker) | orchestrator/tests/test_caldav_connector_credentials.py | unit | ✅ | LUM-281; `test_caldav_is_registered` in `orchestrator/tests/test_caldav_connector_credentials.py`; plan `.cursor/plans/archived/LUM-281-paperless-ngx-v01-docker-ingest.plan.md` (plan path) |
| 1.9.13 | LUM-308: Document auto-RAG in chat | orchestrator/tests/test_auto_rag.py | unit | ✅ | LUM-308; `test_auto_rag_disabled_returns_empty` in `orchestrator/tests/test_auto_rag.py`; plan `.cursor/plans/archived/LUM-308-document-auto-rag-chat.plan.md` (plan path) |
| 1.9.14 | LUM-319: Doctor CI integration (lumogis-test) | orchestrator/tests/test_doctor_cli.py | unit | ✅ | LUM-319; `test_doctor_json_schema_version` in `orchestrator/tests/test_doctor_cli.py`; plan `.cursor/plans/archived/LUM-319-doctor-ci-integration.plan.md` (plan path) |
