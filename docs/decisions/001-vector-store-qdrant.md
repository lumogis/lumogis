# ADR-001: Qdrant as default vector store

## Context

lumogis needs a vector database for document chunks, session summaries, entity embeddings, and signal deduplication. Candidates included Chroma, Milvus, Weaviate, and Qdrant.

## Decision

Use **Qdrant** as the default vector store, implemented in `adapters/qdrant_store.py`.

## Consequences

- **Performance:** Qdrant offers strong latency on local and self-hosted deployments; hybrid dense + sparse (BM25) search with RRF fits our two-stage retrieval model.
- **Filtering:** Payload filters per user and collection are first-class; required for multi-tenant-style isolation on a single instance.
- **Self-hosted:** Official Docker image, no cloud dependency; aligns with “data never leaves your machine.”
- **gRPC / HTTP:** Both APIs supported; we use HTTP from Python for simplicity.
- **Community adapters:** Contributors may add Chroma/Milvus adapters implementing the same `VectorStore` port; they must accept `sparse_query` on `search()` even if unused.
