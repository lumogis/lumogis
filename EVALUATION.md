# Evaluating your Lumogis instance

This guide helps household operators answer: **“Is Lumogis actually learning from my data, and is retrieval good enough to trust?”**

Run these checks after you have used Lumogis for several days (ingest, chat, and search). A fresh install with no documents will not pass the search-quality baselines — that is expected.

**Prerequisites:** Docker Compose stack running (`docker compose up -d`), documents indexed under your configured ingest roots, and at least one chat or search session. See [`docs/deployment/quickstart.md`](docs/deployment/quickstart.md).

When **`AUTH_ENABLED=true`**, browser UI checks use your logged-in session. Raw `curl` calls to `/health` and `/graph/health` need the same credentials (session cookie or bearer token) unless you use the **System status** panel in Lumogis Web, which calls the admin diagnostics surface for you.

---

## Section 1 — Search quality baseline (five test queries)

Use **Lumogis Web** search or chat (recommended). Each query should return **specific** excerpts with source paths or session references — not vague platitudes.

| # | Query (adapt names/topics to your household) | What good looks like |
|---|-----------------------------------------------|----------------------|
| 1 | “What projects am I currently working on?” | Lists active projects or themes; cites recent documents or sessions. |
| 2 | “Who is **[name you mention often]**?” | Returns an entity or person context with relationship hints and recent mentions (when the knowledge graph has extracted entities). |
| 3 | “What did I decide about **[topic from last month]**?” | Retrieves a specific note, session, or document chunk and dates the decision. |
| 4 | “What connects **[concept A]** and **[concept B]**?” | Shows related chunks or entities — ideally a non-obvious link, not two unrelated hits. |
| 5 | “Summarize what you know about **[upcoming meeting or recurring topic]**.” | Produces a concise brief from recent sessions and indexed files (full meeting-brief automation may arrive in a later release; this query still validates retrieval breadth). |

**If results are weak:**

- Confirm files appear in your ingest folder and `last_ingest` is recent (Section 2).
- Wait for embedding/indexing to finish after large imports (`embedding_ready` in `/healthz`).
- Try narrower queries with exact filenames or phrases you know exist in the corpus.

Optional API check (authenticated): `GET /api/v1/memory/search?q=...` — same semantics as the Web search box.

---

## Section 2 — Memory and index health indicators

### Signs the system is healthy

- **`file_index_count`** and **`total_chunks_indexed`** in `/health` grow after you add documents.
- **`last_ingest`** in `/health` updates within hours of new files in watched folders.
- **`chunk_drift_pct`** in `/health` stays below **5%** (Qdrant vectors vs Postgres chunk counts).
- **`postgres_ok`** is `true` and `/health` returns HTTP **200**.
- Search answers cite **specific sources** (paths, dates) rather than generic statements.
- When entities exist, **`entity_count`** in `/health` increases over time and **`mean_entity_completeness`** in `/graph/health` trends upward (not stuck at zero).

### Signs something is degraded

- `/health` returns **503** or `postgres_ok` is `false`.
- **`last_ingest`** is null or days old while you added files recently.
- **`chunk_drift_pct`** above **5%** — vector index may be out of sync with the file index.
- **`orphan_entity_pct`** in `/graph/health` is high (many entities with no relations).
- **`constraint_violation_counts.CRITICAL`** in `/graph/health` is non-zero.
- Answers contradict text you indexed this week, or search returns empty while `file_index_count` is large.

Use Lumogis Web **System status** for a curated view, or inspect raw JSON from the commands in Section 3.

---

## Section 3 — Minimal self-evaluation commands

Replace `http://localhost` with your deployment origin if different (LAN IP, Tailscale hostname, etc.).

```bash
# Liveness — no authentication required
curl -fsS http://localhost/healthz

# Indexing counts, drift, and Postgres status
# (authenticate when AUTH_ENABLED=true — or use System status in the Web UI)
curl -fsS http://localhost/health | python3 -m json.tool

# Knowledge-graph quality metrics (Postgres-backed; available even when FalkorDB is off)
curl -fsS http://localhost/graph/health | python3 -m json.tool

# Host-side stack and config checks (read-only)
make doctor
make doctor ARGS="--json"    # requires jq; see scripts/doctor/README.md
```

**Reading `/health` (indexing):** watch `file_index_count`, `total_chunks_indexed`, `entity_count`, `last_ingest`, `chunk_drift_pct`, and `postgres_ok`.

**Reading `/graph/health` (graph hygiene):** watch `orphan_entity_pct`, `mean_entity_completeness`, `duplicate_candidate_count`, `constraint_violation_counts`, and `temporal_freshness` (recent entity activity buckets).

For a curated operator summary (stores, capabilities, warnings), authenticated admins can use `GET /api/v1/admin/diagnostics` from the API or the in-product diagnostics panel.

---

## Section 4 — Contributor benchmarks (stub)

These definitions are for **contributors** measuring retrieval and extraction improvements — not required for household operators at install time.

| Benchmark | Intent | Status |
|-----------|--------|--------|
| Hybrid retrieval F1 | 50-question household-scenario set covering search + chat grounding | **Stub** — corpus and harness to be published post-launch |
| Entity extraction precision/recall | 20-document labelled corpus for ingest → entity pipeline | **Stub** — depends on public KG evaluation fixtures |
| Meeting-brief completeness | Rubric 1–5 for attendee coverage, topics, and action items | **Stub** — blocked on meeting-brief product feature |

Contributors: open a GitHub issue on **`lumogis/lumogis`** with the label query for evaluation work, or extend tests under `orchestrator/tests/` and `tests/integration/` following [`CONTRIBUTING.md`](CONTRIBUTING.md).

---

## Related operator docs

- [`docs/deployment/quickstart.md`](docs/deployment/quickstart.md) — first-run setup
- [`docs/LUMOGIS_REFERENCE_MANUAL.md`](docs/LUMOGIS_REFERENCE_MANUAL.md) — operator reference and `make doctor` detail
- [`docs/guides/troubleshooting.md`](docs/guides/troubleshooting.md) — common failures
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — verification commands for contributors
