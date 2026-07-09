# ADR-156: Two-user Qdrant semantic-search isolation integration test (LUM-307)

**Status:** Finalised
**Created:** 2026-07-06
**Last updated:** 2026-07-07
**Decided by:** /explore --headless LUM-307; finalised by /verify-plan (Composer 2.5)
**Plan:** `.cursor/plans/LUM-307-two-user-qdrant-isolation-test.plan.md`
**Exploration:** `.cursor/explorations/LUM-307-two-user-qdrant-isolation-test.md`
**Draft mirror:** `.cursor/adrs/LUM-307-two-user-qdrant-isolation-test.md`
**Linear:** [LUM-307](https://linear.app/lumogis/issue/LUM-307/two-user-qdrant-semantic-search-isolation-integration-test)

## Context

LUM-305 (ADR 057) fixed `QdrantStore._build_filter` to honour the top-level `should` household-union clause emitted by `visibility.visible_qdrant_filter`. Coverage before LUM-307 was unit-only (`test_qdrant_store_filter_build.py`) plus a MockVectorStore headline test that never exercised real Qdrant ANN. ADR 057 §Revisit and portfolio **FP-061** required a live two-user integration test proving semantic-search visibility filters prevent cross-user personal leakage and honour household-union shared scope.

## Decision

Add a **live integration test module** `orchestrator/tests/integration/test_two_user_qdrant_isolation_live.py` that:

1. Uses the LUM-157 `live_stores` pattern — override `config._instances` with real `QdrantStore` + `PostgresStore` after conftest autouse fakes; `pytest.skip` when the stack is unreachable.
2. Seeds Alice/Bob personal chunks (distinct `user_id`, `file_path`, synthetic one-hot vectors at a **per-run unique vector index**) plus one `scope='shared'` chunk (owner `user_id` = Alice).
3. Asserts `vs.search(..., filter=visible_qdrant_filter(ctx))` with **member** contexts (`role="user"`, `allows_shared=True`): positive controls (own `file_path` present), negative controls (other user's personal absent), and household-union shared visibility for both users.
4. Marks `pytest.mark.integration`; primary CI gate is targeted pytest inside the orchestrator compose container under **`make compose-test`** (not `compose-test-integration`, which collects repo-root `tests/integration/` only).
5. Cross-references the sibling live module from `test_two_user_isolation.py` module docstring; mock harness unchanged.

**Out of scope (v1):** product code changes; MockVectorStore fidelity; `services.search.semantic_search()` with live embedder; HTTP chat round-trip.

## Alternatives Considered

- **Extend `test_two_user_isolation.py` with a live function** — rejected; mixed fixture regimes (Option 2 in exploration).
- **Improve MockVectorStore `should` semantics** — rejected; proves the mock, not Qdrant (Option 3).
- **`testcontainers` QdrantContainer** — rejected; redundant with compose harness (Option 4).

Full detail: `.cursor/explorations/LUM-307-two-user-qdrant-isolation-test.md`.

## Consequences

**Positive:** Closes ADR 057 §Revisit / FP-061; de-risks LUM-205 (Web chat blocker) and supplies vector-layer evidence cited by LUM-473 household-exposure claims. Future `visible_qdrant_filter` shape changes must keep this test green.

**Limits:** Proves filter + real Qdrant ANN with synthetic vectors for direct `vs.search` tests; **`semantic_search()` e2e** with live embedder covered by **LUM-588** (`test_live_semantic_search_no_cross_user_leakage`). **`allows_shared=False` member scenario** covered by **LUM-587** (`test_live_personal_only_member_excludes_shared_chunks`). Shared `live_stores` conftest extraction deferred until a third live module lands.

## Revisit conditions

- `visibility.visible_qdrant_filter` dict shape changes — extend or re-baseline this test.
- `services.search.semantic_search` retrieval path changes such that direct `vs.search` no longer mirrors production — add embedder-gated e2e follow-up.
- Cross-user leak reported despite this test passing — add the missing scenario.

## Status history

- **2026-07-06:** Draft created by /explore --headless LUM-307.
- **2026-07-06:** Revised during /review-plan --arbitrate R1 — CI gate `make compose-test`; per-run vector index; member contexts; positive controls; shared-chunk `user_id`.
- **2026-07-07:** Finalised by /verify-plan — implementation confirmed; compose primary gate **2/2** passed (`QDRANT_HOST_PORT=6336`).
- **2026-07-07:** LUM-587 follow-up — `test_live_personal_only_member_excludes_shared_chunks` proves `allows_shared=False` members do not retrieve shared-scope chunks.
- **2026-07-07:** LUM-588 follow-up — `test_live_semantic_search_no_cross_user_leakage` proves production `semantic_search()` path with live embedder has no cross-user personal leakage.
