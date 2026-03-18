# ADR-003: Nomic Embed (via Ollama) as default embedder

## Context

Embeddings must run fully locally. Alternatives: sentence-transformers models, OpenAI embeddings, other Ollama models.

## Decision

Default to **Nomic Embed Text** pulled through **Ollama** (`nomic-embed-text`), 768-dimensional vectors.

## Consequences

- **Multilingual:** Strong cross-lingual behaviour relative to size.
- **Small & local:** Runs on consumer GPUs and acceptable on CPU for evaluation tiers.
- **MTEB:** Competitive scores for its class; good default for personal RAG.
- **No API key:** Zero outbound calls for embedding in the default stack.
- **Swappable:** `EMBEDDER_BACKEND` and adapter pattern allow sentence-transformers or cloud embedders without changing services.
