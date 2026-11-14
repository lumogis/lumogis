# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Recall latency benchmark at bank scale (LUM-542). Run with ``make bench-recall``.

Loads a synthetic bank (``benchmarks.recall_bank``) into a throwaway Postgres +
Qdrant, then times ``services.recall.recall`` — the function the MCP ``recall``
tool calls — with all four legs (semantic, bm25, graph, temporal), RRF fusion,
Postgres hydration and the tool's JSON serialisation. The MCP JSON-RPC/HTTP
transport is not included; the LUM-299 gate measures it on the 50-memory bank.

No network model is used. Three modes:

``fusion``
    Query embedding stubbed (precomputed 768-dim vector, a dict lookup), no
    rerank. Search + fusion + hydration only: the number LUM-542 asks for.
``rerank``
    ``fusion`` plus the production rerank step (top 20 through
    ``adapters.bge_reranker.BGEReranker.rerank``) on a cross-encoder with the
    exact shape of ``cross-encoder/ms-marco-MiniLM-L-6-v2`` (6 layers, 384
    hidden) and random seeded weights. Same compute, no download; the scores
    are meaningless, the latency is not.
``full``
    ``rerank`` plus a query-embedding estimate: a 12-layer, 768-hidden BERT
    (the shape of ``nomic-embed-text``) on CPU torch. Production embeds through
    Ollama (llama.cpp, maybe on a GPU) over HTTP, which this cannot measure;
    treat ``full`` as a CPU-only upper estimate.

Each size is a fresh load. Every mode gets ``--warmup`` untimed queries, then
``--queries`` timed ones from a seeded mix. A profile pass then times each
stage serially (embed, each leg, RRF, hydrate, rerank, serialise) to show where
the time goes.

Writes: TRUNCATEs ``memories`` / ``entity_edges``, deletes bench entities and
recreates the Qdrant ``memories`` collection. It refuses to run outside
``scripts/test-env.sh`` tier 2 unless ``--allow-wipe`` is given.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import sys
import tempfile
import time
import uuid
from collections.abc import Callable
from datetime import datetime
from datetime import timezone
from pathlib import Path

_ORCH = Path(__file__).resolve().parent.parent
if str(_ORCH) not in sys.path:
    sys.path.insert(0, str(_ORCH))

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")

from benchmarks import recall_bank as rb  # noqa: E402

MODES = ("fusion", "rerank", "full")
STRATEGIES = ["semantic", "bm25", "graph", "temporal"]
NOISE_FRACTION = 0.25
OTHER_USER = "bench-recall-other"


# ---------------------------------------------------------------------------
# Stats + host info
# ---------------------------------------------------------------------------


def pct(samples: list[float], q: float) -> float:
    """Nearest-rank-interpolated percentile (``statistics.quantiles`` inclusive)."""
    if len(samples) == 1:
        return samples[0]
    return statistics.quantiles(samples, n=1000, method="inclusive")[int(q * 10) - 1]


def summarise(samples: list[float]) -> dict:
    return {
        "n": len(samples),
        "p50": round(pct(samples, 50), 2),
        "p95": round(pct(samples, 95), 2),
        "p99": round(pct(samples, 99), 2),
        "max": round(max(samples), 2),
        "mean": round(statistics.fmean(samples), 2),
    }


def host_info(ms, qdrant_url: str) -> dict:
    cpu = platform.processor() or "unknown"
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                cpu = line.split(":", 1)[1].strip()
                break
    except OSError:
        pass
    mem_gb = None
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal"):
                mem_gb = round(int(line.split()[1]) / 1024 / 1024, 1)
    except OSError:
        pass
    import httpx
    import torch

    try:
        qv = httpx.get(qdrant_url.rstrip("/") + "/", timeout=5).json().get("version")
    except Exception:  # noqa: BLE001
        qv = "unknown"
    row = ms.fetch_one("SELECT version() AS v")
    return {
        "cpu": cpu,
        "logical_cpus": os.cpu_count(),
        "mem_total_gb": mem_gb,
        "kernel": platform.release(),
        "python": platform.python_version(),
        "torch": torch.__version__,
        "torch_threads": torch.get_num_threads(),
        "postgres": (row or {}).get("v", "unknown").split(" on ")[0],
        "qdrant": qv,
        "note": "Postgres and Qdrant run in Docker on the same host, tmpfs storage",
    }


# ---------------------------------------------------------------------------
# Embedder / reranker seams
# ---------------------------------------------------------------------------


class StubEmbedder:
    """Returns the precomputed vector for a known query (the ``Embedder`` port)."""

    def __init__(self, vectors: dict[str, list[float]]):
        self._v = vectors

    @property
    def vector_size(self) -> int:
        return rb.EMBED_DIM

    def embed(self, text: str) -> list[float]:
        return self._v[text]


class ShapeEmbedder(StubEmbedder):
    """Runs a BERT-base-shape forward pass (real CPU cost), returns the stub vector."""

    def __init__(self, vectors, model, tokenizer):
        super().__init__(vectors)
        self._model = model
        self._tok = tokenizer

    def embed(self, text: str) -> list[float]:
        import torch

        with torch.inference_mode():
            enc = self._tok(text, return_tensors="pt", truncation=True, max_length=512)
            out = self._model(**enc).last_hidden_state
            out.mean(dim=1)
        return self._v[text]


def _shape_vocab() -> list[str]:
    words: set[str] = set(rb._COMMON) | set(rb._VERBS)
    for t in rb.TOPICS:
        words.update(w.lower() for w in t.vocab)
    for tmpl in rb._CONCEPTUAL:
        words.update(w for w in tmpl.split() if "{" not in w)
    chars = [chr(c) for c in range(33, 127) if not chr(c).isupper()]
    vocab = ["[PAD]", "[UNK]", "[CLS]", "[SEP]", "[MASK]"] + chars + ["##" + c for c in chars]
    vocab += sorted(words - set(vocab))
    vocab += [f"[unused{i}]" for i in range(30522 - len(vocab))]
    return vocab


def build_shape_models(seed: int, workdir: Path):
    """Seeded random-weight models with production shapes; no download.

    Returns ``(reranker, embed_model, tokenizer)``. The reranker is the real
    ``BGEReranker`` adapter wrapping a sentence-transformers ``CrossEncoder``
    loaded from a local dir, so ``rerank()`` runs the production code path.
    """
    import torch
    from adapters.bge_reranker import BGEReranker
    from sentence_transformers import CrossEncoder
    from transformers import BertConfig
    from transformers import BertForSequenceClassification
    from transformers import BertModel
    from transformers import BertTokenizerFast
    from transformers.utils import logging as hf_logging

    hf_logging.set_verbosity_error()
    hf_logging.disable_progress_bar()
    vocab_file = workdir / "vocab.txt"
    vocab_file.write_text("\n".join(_shape_vocab()) + "\n")
    tok = BertTokenizerFast(vocab_file=str(vocab_file), do_lower_case=True, model_max_length=512)

    torch.manual_seed(seed)
    # cross-encoder/ms-marco-MiniLM-L-6-v2: BertForSequenceClassification, 6x384, 12 heads.
    ce_cfg = BertConfig(
        vocab_size=30522,
        hidden_size=384,
        num_hidden_layers=6,
        num_attention_heads=12,
        intermediate_size=1536,
        max_position_embeddings=512,
        num_labels=1,
    )
    ce_dir = workdir / "cross-encoder-shape"
    BertForSequenceClassification(ce_cfg).eval().save_pretrained(ce_dir)
    tok.save_pretrained(ce_dir)
    ce = CrossEncoder(str(ce_dir), device="cpu", max_length=512)
    reranker = BGEReranker.__new__(BGEReranker)
    reranker._model = ce

    # nomic-embed-text is a 12-layer, 768-hidden BERT variant (137M params).
    emb_cfg = BertConfig(
        vocab_size=30522,
        hidden_size=768,
        num_hidden_layers=12,
        num_attention_heads=12,
        intermediate_size=3072,
        max_position_embeddings=512,
    )
    embed_model = BertModel(emb_cfg).eval()
    return reranker, embed_model, tok


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def _pg_connect():
    import psycopg2

    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        user=os.environ.get("POSTGRES_USER", "lumogis"),
        password=os.environ.get("POSTGRES_PASSWORD", "lumogis-dev"),
        dbname=os.environ.get("POSTGRES_DB", "lumogis"),
    )


def reset_stores(vs) -> None:
    import config

    conn = _pg_connect()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("TRUNCATE memories, entity_edges")
        cur.execute("DELETE FROM entities WHERE user_id LIKE 'bench-recall%'")
        cur.execute("VACUUM ANALYZE entities")
    conn.close()
    client = vs._client
    if any(c.name == "memories" for c in client.get_collections().collections):
        client.delete_collection("memories")
    vs.create_collection("memories", rb.EMBED_DIM)
    config._memories_qdrant_indexes_ensured = False
    config.ensure_memories_qdrant_indexes(vs)


def load_bank(bank: rb.Bank, vs, *, batch: int = 512) -> None:
    from psycopg2.extras import execute_values
    from qdrant_client.models import PointStruct

    conn = _pg_connect()
    with conn, conn.cursor() as cur:
        execute_values(
            cur,
            "INSERT INTO memories (id, user_id, bank, content, tags, metadata, "
            "valid_from, valid_until) VALUES %s",
            [
                (
                    m.id,
                    m.user_id,
                    m.bank,
                    m.content,
                    m.tags,
                    json.dumps(m.metadata),
                    m.valid_from,
                    m.valid_until,
                )
                for m in bank.memories
            ],
            template="(%s, %s, %s, %s, %s, %s::jsonb, %s, %s)",
            page_size=1000,
        )
        if bank.entities:
            execute_values(
                cur,
                "INSERT INTO entities (entity_id, name, entity_type, mention_count, user_id, "
                "context_tags) VALUES %s",
                [
                    (
                        e.entity_id,
                        e.name,
                        e.entity_type,
                        e.mention_count,
                        e.user_id,
                        [rb.TOPICS[e.topic].name],
                    )
                    for e in bank.entities
                ],
                template="(%s::uuid, %s, %s, %s, %s, %s)",
                page_size=1000,
            )
        if bank.edges:
            execute_values(
                cur,
                "INSERT INTO entity_edges (id, user_id, bank, src_entity_id, dst_entity_id, "
                "relation_type, evidence_id, valid_from, valid_until) VALUES %s",
                [
                    (
                        ed.id,
                        ed.user_id,
                        ed.bank,
                        ed.src_entity_id,
                        ed.dst_entity_id,
                        ed.relation_type,
                        ed.evidence_id,
                        ed.valid_from,
                        ed.valid_until,
                    )
                    for ed in bank.edges
                ],
                page_size=1000,
            )
    conn.close()

    client = vs._client
    for start in range(0, len(bank.memories), batch):
        points = [
            PointStruct(
                # Same point id + payload as services.memories.store_memory.
                id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"memory::{m.user_id}::{m.id}")),
                vector=bank.vectors[start + j].tolist(),
                payload={"memory_id": m.id, "user_id": m.user_id, "bank": m.bank},
            )
            for j, m in enumerate(bank.memories[start : start + batch])
        ]
        client.upsert(collection_name="memories", points=points, wait=True)


def finish_load(vs, *, timeout_s: float = 600.0) -> dict:
    """ANALYZE Postgres and wait until Qdrant's optimizer has settled."""
    conn = _pg_connect()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("VACUUM ANALYZE memories")
        cur.execute("VACUUM ANALYZE entity_edges")
        cur.execute("VACUUM ANALYZE entities")
    conn.close()
    deadline = time.monotonic() + timeout_s
    info = None
    while time.monotonic() < deadline:
        info = vs._client.get_collection("memories")
        status = str(getattr(info.status, "value", info.status))
        opt = info.optimizer_status
        opt_ok = str(getattr(opt, "value", opt)).lower() == "ok" or getattr(opt, "ok", False)
        if status == "green" and opt_ok:
            break
        time.sleep(0.5)
    return {
        "points": info.points_count,
        "indexed_vectors": info.indexed_vectors_count,
        "segments": info.segments_count,
        "status": str(getattr(info.status, "value", info.status)),
    }


# ---------------------------------------------------------------------------
# Measurement
# ---------------------------------------------------------------------------


def _timed(fn: Callable[[], object]) -> tuple[float, object]:
    t0 = time.perf_counter()
    out = fn()
    return (time.perf_counter() - t0) * 1000.0, out


def run_mode(
    mode: str,
    queries: list[rb.Query],
    *,
    warmup: int,
    user_id: str,
    bank: str,
    ms,
    vs,
    stub: StubEmbedder,
    shape_embedder: StubEmbedder | None,
    reranker,
) -> dict:
    from services.recall import recall

    embedder = shape_embedder if mode == "full" else stub
    use_rerank = mode in ("rerank", "full")

    def one(q: rb.Query) -> list[dict]:
        out = recall(
            user_id=user_id,
            bank=bank,
            query=q.text,
            limit=10,
            retrieval_strategies=STRATEGIES,
            rerank=use_rerank,
            ms=ms,
            embedder=embedder,
            vs=vs,
            reranker=reranker if use_rerank else None,
        )
        # What mcp_server.recall_tool returns to the client.
        return [m.model_dump(mode="json") for m in out]

    for i in range(warmup):
        one(queries[i % len(queries)])
    samples: list[float] = []
    by_shape: dict[str, list[float]] = {}
    hits: list[int] = []
    for q in queries:
        ms_, res = _timed(lambda q=q: one(q))
        samples.append(ms_)
        by_shape.setdefault(q.shape, []).append(ms_)
        hits.append(len(res))
    return {
        **summarise(samples),
        "by_shape": {k: summarise(v) for k, v in sorted(by_shape.items())},
        "mean_results": round(statistics.fmean(hits), 2),
    }


def profile_stages(
    queries: list[rb.Query],
    *,
    user_id: str,
    bank: str,
    ms,
    vs,
    stub: StubEmbedder,
    shape_embedder: StubEmbedder | None,
    reranker,
) -> dict:
    """Time each recall stage serially, per query (same SQL / search calls as recall)."""
    from services import entities as entities_svc
    from services import entity_edges
    from services import recall as r

    as_of = datetime.now(timezone.utc)
    stages: dict[str, list[float]] = {}
    leg_sizes: dict[str, list[int]] = {}
    rerank_words: list[int] = []

    def rec(name: str, fn):
        t, out = _timed(fn)
        stages.setdefault(name, []).append(t)
        return out

    for q in queries:
        rec("embed_stub", lambda: stub.embed(q.text))
        if shape_embedder is not None:
            rec("embed_bert_base_cpu", lambda: shape_embedder.embed(q.text))
        sem = rec(
            "leg_semantic_qdrant",
            lambda: r._leg_semantic(q.text, user_id=user_id, bank=bank, embedder=stub, vs=vs),
        )
        bm = rec(
            "leg_bm25",
            lambda: r._leg_bm25(q.text, user_id=user_id, bank=bank, as_of=as_of, ms=ms),
        )
        seeds = rec(
            "leg_graph_seed_ilike",
            lambda: entities_svc.entity_ids_for_query(q.text, user_id=user_id, limit=10, ms=ms),
        )
        gr = rec(
            "leg_graph_edges",
            lambda: entity_edges.memories_for_entities(
                seeds, user_id=user_id, bank=bank, as_of=as_of, hops=1, ms=ms
            ),
        )
        tm = rec(
            "leg_temporal",
            lambda: r._leg_temporal(user_id=user_id, bank=bank, as_of=as_of, ms=ms),
        )
        ranked = {"semantic": sem, "bm25": bm, "graph": gr, "temporal": tm}
        for k, v in ranked.items():
            leg_sizes.setdefault(k, []).append(len(v))
        fused = rec("rrf", lambda: r._rrf(ranked))
        ids = [mid for mid, _s, _src in fused]
        hyd = rec(
            "hydrate",
            lambda: r._hydrate(ids, user_id=user_id, bank=bank, as_of=as_of, ms=ms),
        )
        if reranker is not None:
            cands = [{"text": hyd[mid]["content"], "memory_id": mid} for mid in ids if mid in hyd][
                : r._RERANK_FANIN
            ]
            rec("rerank_minilm_cpu", lambda: reranker.rerank(q.text, cands, 10))
            # The batch is padded to its longest pair, so the longest candidate sets the cost.
            rerank_words.append(max((len(c["text"].split()) for c in cands), default=0))
        rec("thread_pool_4", lambda: _pool_overhead())
    return {
        "stages_ms": {k: summarise(v) for k, v in stages.items()},
        "mean_leg_hits": {k: round(statistics.fmean(v), 1) for k, v in leg_sizes.items()},
        "fused_candidates_note": "hydrate reads every fused id (up to 4 x 50)",
        "rerank_longest_candidate_words": summarise(rerank_words) if rerank_words else None,
    }


def _pool_overhead() -> None:
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="recall-leg") as ex:
        for f in [ex.submit(lambda: None) for _ in range(4)]:
            f.result()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--sizes", default="1000,5000,10000", help="comma-separated bank sizes")
    p.add_argument("--queries", type=int, default=200, help="timed queries per mode (>=200)")
    p.add_argument("--warmup", type=int, default=30)
    p.add_argument("--seed", type=int, default=rb.DEFAULT_SEED)
    p.add_argument("--modes", default=",".join(MODES), help=f"subset of {','.join(MODES)}")
    p.add_argument("--profile-queries", type=int, default=100, help="0 disables the profile")
    p.add_argument(
        "--max-p95-ms",
        type=float,
        default=None,
        help="exit 1 if any gated mode's p95 exceeds this at any size",
    )
    p.add_argument(
        "--gate-modes",
        default=None,
        help="modes the --max-p95-ms gate applies to (default: all measured modes)",
    )
    p.add_argument("--json-out", type=Path, default=None)
    p.add_argument(
        "--allow-wipe",
        action="store_true",
        help="run outside test-env tier 2: TRUNCATEs memories and drops the Qdrant collection",
    )
    a = p.parse_args(argv)
    a.sizes = [int(s) for s in a.sizes.split(",") if s.strip()]
    a.modes = [m for m in a.modes.split(",") if m.strip()]
    bad = [m for m in a.modes if m not in MODES]
    if bad:
        p.error(f"unknown mode(s) {bad}; expected {MODES}")
    a.gate_modes = [m for m in (a.gate_modes or ",".join(a.modes)).split(",") if m.strip()]
    if a.queries < 200:
        p.error("--queries must be >= 200 (LUM-542)")
    return a


def _print_table(results: list[dict], modes: list[str]) -> None:
    print("\n| bank size | mode | p50 ms | p95 ms | p99 ms | max ms | mean results |")
    print("|---:|---|---:|---:|---:|---:|---:|")
    for r in results:
        for m in modes:
            s = r["modes"][m]
            print(
                f"| {r['size']:,} | {m} | {s['p50']} | {s['p95']} | {s['p99']} "
                f"| {s['max']} | {s['mean_results']} |"
            )
    for r in results:
        prof = r.get("profile")
        if not prof:
            continue
        print(f"\nStage profile, {r['size']:,} memories (serial, ms):")
        print("| stage | p50 | p95 | p99 |")
        print("|---|---:|---:|---:|")
        for k, s in prof["stages_ms"].items():
            print(f"| {k} | {s['p50']} | {s['p95']} | {s['p99']} |")
        print(f"mean leg hits: {prof['mean_leg_hits']}")
        if prof.get("rerank_longest_candidate_words"):
            print(f"longest rerank candidate (words): {prof['rerank_longest_candidate_words']}")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if os.environ.get("LUMOGIS_TEST_ENV_TIER") != "2" and not args.allow_wipe:
        print(
            "bench-recall: refusing to run outside scripts/test-env.sh tier 2 "
            "(it truncates memories and drops the Qdrant memories collection). "
            "Use `make bench-recall`, or pass --allow-wipe against a throwaway stack.",
            file=sys.stderr,
        )
        return 2

    import config

    ms = config.get_metadata_store()
    vs = config.get_vector_store()
    qdrant_url = os.environ.get("QDRANT_URL", "http://qdrant:6333")
    info = host_info(ms, qdrant_url)
    print(f"host: {json.dumps(info)}", flush=True)

    reranker = embed_model = tok = None
    tmp = tempfile.TemporaryDirectory(prefix="bench-recall-")
    if any(m in args.modes for m in ("rerank", "full")) or args.profile_queries:
        reranker, embed_model, tok = build_shape_models(args.seed, Path(tmp.name))

    results: list[dict] = []
    for size in args.sizes:
        t0 = time.perf_counter()
        bank = rb.generate_bank(size, seed=args.seed)
        noise = [
            rb.generate_bank(
                int(size * NOISE_FRACTION), seed=args.seed, stream=1, user_id=OTHER_USER
            ),
            rb.generate_bank(
                int(size * NOISE_FRACTION),
                seed=args.seed,
                stream=2,
                user_id=rb.DEFAULT_USER_ID,
                bank="personal",
                graph=False,
            ),
        ]
        gen_s = time.perf_counter() - t0
        t0 = time.perf_counter()
        reset_stores(vs)
        for b in (bank, *noise):
            load_bank(b, vs)
        qinfo = finish_load(vs)
        load_s = time.perf_counter() - t0
        print(
            f"\n[{size:,}] generated in {gen_s:.1f}s (fingerprint {bank.fingerprint()[:12]}); "
            f"loaded {len(bank.memories)} + {sum(len(b.memories) for b in noise)} noise memories, "
            f"{len(bank.entities) + len(noise[0].entities)} entities, "
            f"{len(bank.edges) + len(noise[0].edges)} edges in {load_s:.1f}s; qdrant {qinfo}",
            flush=True,
        )

        queries = rb.generate_queries(bank, args.queries, seed=args.seed)
        vectors = {q.text: q.vector.tolist() for q in queries}
        stub = StubEmbedder(vectors)
        shape_emb = ShapeEmbedder(vectors, embed_model, tok) if embed_model is not None else None

        entry: dict = {
            "size": size,
            "noise_memories": sum(len(b.memories) for b in noise),
            "entities": len(bank.entities),
            "edges": len(bank.edges),
            "archived": sum(1 for m in bank.memories if m.valid_until is not None),
            "qdrant": qinfo,
            "load_s": round(load_s, 1),
            "fingerprint": bank.fingerprint(),
            "query_mix": {s: sum(1 for q in queries if q.shape == s) for s, _ in rb.QUERY_MIX},
            "modes": {},
        }
        for mode in args.modes:
            entry["modes"][mode] = run_mode(
                mode,
                queries,
                warmup=args.warmup,
                user_id=rb.DEFAULT_USER_ID,
                bank=rb.DEFAULT_BANK,
                ms=ms,
                vs=vs,
                stub=stub,
                shape_embedder=shape_emb,
                reranker=reranker,
            )
            s = entry["modes"][mode]
            print(
                f"[{size:,}] {mode:<6} p50={s['p50']}ms p95={s['p95']}ms p99={s['p99']}ms "
                f"max={s['max']}ms (n={s['n']})",
                flush=True,
            )
        if args.profile_queries:
            entry["profile"] = profile_stages(
                queries[: args.profile_queries],
                user_id=rb.DEFAULT_USER_ID,
                bank=rb.DEFAULT_BANK,
                ms=ms,
                vs=vs,
                stub=stub,
                shape_embedder=shape_emb,
                reranker=reranker,
            )
        results.append(entry)

    tmp.cleanup()
    _print_table(results, args.modes)

    failures = []
    if args.max_p95_ms is not None:
        for r in results:
            for m in args.gate_modes:
                if m in r["modes"] and r["modes"][m]["p95"] > args.max_p95_ms:
                    failures.append(f"{r['size']}/{m} p95 {r['modes'][m]['p95']}ms")
    report = {
        "benchmark": "recall (LUM-542)",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "seed": args.seed,
        "queries": args.queries,
        "warmup": args.warmup,
        "strategies": STRATEGIES,
        "host": info,
        "results": results,
        "gate": {
            "max_p95_ms": args.max_p95_ms,
            "modes": args.gate_modes,
            "failures": failures,
        },
    }
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, indent=2, default=str))
        print(f"\nwrote {args.json_out}")
    if failures:
        print(f"\nFAIL: p95 above {args.max_p95_ms}ms: {', '.join(failures)}", file=sys.stderr)
        return 1
    if args.max_p95_ms is not None:
        print(f"\nPASS: every gated p95 <= {args.max_p95_ms}ms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
