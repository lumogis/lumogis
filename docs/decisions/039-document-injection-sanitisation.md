# ADR 039: Document injection sanitisation — layered defence for ingested content

**Status:** Finalised
**Created:** 2026-05-14
**Last updated:** 2026-05-14
**Linear:** [LUM-127](https://linear.app/lumogis/issue/LUM-127/document-injection-sanitisation-filter-prompt-injections-from-ingested)
**Exploration:** `.cursor/explorations/LUM-127-document_injection_sanitisation.md`

## Context

Lumogis ingests documents (PDF, DOCX, OCR, markdown, scraped pages, RSS, audio transcripts) and embeds extracted chunks into Qdrant. On every chat request, retrieved chunks reach the LLM as either tool-result content (`services/tools.py::_search_files`) or as a synthetic `user` message via `routes/chat.py::_inject_context`. Historically there was **no explicit origin scaffolding** distinguishing corpus from privileged instructions inside those strings, **no ingest-time sanitisation**, **no compaction prompt rule**, and **no per-request parallel tool-chain cap**. The Claude Code source leak illustrated how compaction can launder corpus-borne directives into enduring session summaries — aligning with Lumogis's compaction roadmap (**LUM-122**).

## Decision

Adopt **native, in-process layering** shipped with **LUM-127**:

1. **`services/injection_sanitiser.py`** + checked-in **`orchestrator/data/injection_patterns.yaml`** run at ingest, persisting **`origin`** metadata (**`pattern_hits`**, timestamps, **`injection_flagged`**) alongside existing Qdrant payload fields.
2. **Spotlight wrappers:** canonical **`<retrieved_chunk …>`** tags wrap corpus fragments emitted from retrieval + `_inject_context` assembly + tool JSON payloads. **`Event.CONTEXT_BUILDING` subscribers (graph + future)** continue to mutate a shared **`list[str]`** list of **plaintext** fragments; tagging occurs **after** synchronous hook completion **and KG merge**, inside **`routes/chat.py`**, coordinated via **`apply_retrieved_chunk_markup` + parallel `ResolvedOrigin | None` hints**.
3. **Outer scaffolding:** concatenate tagged fragments beneath **`<lumogis_injected_context request_nonce='…'>`** instead of forgeable ASCII banners. Assistant scaffolding lines embed unguessable nonce tails per request.
4. **Compaction hygiene:** prepend **`memory.py`** summarise instructions (**`_SUMMARIZE_PROMPT`**) so `<retrieved_chunk>` bodies remain **untrusted corpus**, and scaffold tags plus nonce scaffolding are never restated as authenticated user prefs (**LUM-122** preserves wording verbatim downstream).
5. **Tool-chain cap:** **`TOOL_CHAIN_CAP`** (**default 10**) enforces pessimistic increments per **`run_tool` dispatch**, returning structured **`lumogis_blocked`** JSON (incl **`blocked_tool`**) rather than escalating Ask/Do in v1 (**LUM-131** owns UX escalation).
6. **Audit:** **`Event.INJECTION_FLAGGED`** (background) plus synchronous structured **`logging`** for **`block_ingest` high severity** drops. Payload includes **`user_id`, `file_path`, `chunk_index|null`, severity, pattern_hits, action, sanitised_at, stage (ingest|context|tool_result)`**.
7. **`InjectionScanner` Protocol + `NullInjectionScanner` + factory** mirrors **`get_notifier`**: default path stays dependency-free while enabling future scanners (LLM Guard / Pytector).

## Alternatives considered

- **LLM Guard as primary scanner** — model weight / false negatives → opt-in Protocol adapter only.
- **RAG Firewall, Pytector, ProtectRAG, NeMo Guardrails, StruQ** — dismissed or deferred as non-default.
- **`contextvars`-only budgets** across FastAPI **`StreamingResponse` thread iteration** — **Rejected** in favour of lexical **per-invocation `ToolChainBudget`** objects (**`loop.py`**).

## Consequences

- Qdrant payload gains **`origin: {trusted,scope,source,ingested,pattern_hits,...}`**. Legacy vectors default **`trusted=false, source='legacy', scope interpreted as personal-equivalence with WARN`** when unknown.
- Synthetic chat context becomes **nonce scaffold + `<retrieved_chunk>` blobs** rather than legacy plaintext banners.
- **Malformed patterns YAML** raises during **`lifespan`** with **`INJECTION_SANITISER_ENABLED=false` remediation**.
- **Accepted residual risks (documented mitigations):** stdlib **`re`** offers no universal timeout → pattern lint + optional gated `regex` dependency later; shared **`hooks` ThreadPoolExecutor** saturation under ingest storms → synchronous logs on destructive ingest paths + backlog coordination on **LUM-125**.

## Status history

- 2026-05-14: Draft created by `/explore --headless LUM-127`.
- 2026-05-14: **Draft (revised)** via `/review-plan --arbitrate` Round 1.
- 2026-05-14: **Finalised** by `/verify-plan --headless` — implementation aligned with Decision; devtools linkage registries updated for **`--require-classified`**.
