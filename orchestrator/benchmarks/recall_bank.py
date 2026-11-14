# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Deterministic synthetic memory bank for the recall scale benchmark (LUM-542).

``generate_bank(n, seed=...)`` returns ``n`` memories for one ``(user_id, bank)``
plus the entities and ``entity_edges`` that the recall graph leg reads, and a
precomputed embedding for every memory. ``generate_queries(bank, count)``
returns a seeded mix of query shapes with their own precomputed vectors.

Same ``(n, seed, stream, user_id, bank)`` gives byte-identical output, so the
10k bank is regenerated on every run instead of being committed.

What makes it realistic enough to time:

* **Lengths** are log-normal (median ~28 words, clipped to 6..320), like
  hand-written agent memories: mostly a sentence or two, a long tail of notes.
* **Topics** are Zipf-weighted (a few dominate), and each memory mixes common
  English with its topic's vocabulary, so ``websearch_to_tsquery`` AND-matching
  behaves as on real text: short keyword queries hit tens of rows, long
  questions hit few.
* **Identifiers** (``LUM-1234``, ``orchestrator/services/x_y.py``) appear in a
  fraction of rows, for exact-identifier queries.
* **Vectors** are at the real embedding dimension (768, ``nomic-embed-text``)
  and clustered: topic centroid + sub-topic + noise, unit-normalised. Qdrant's
  HNSW behaves differently on clustered data than on uniform noise.
* **Graph**: entities scale with the bank (memory-linked ones plus unlinked
  "document" entities the seed ``ILIKE`` still has to scan); each memory names
  0-3 of its topic's entities and gets ``entity_edges`` with that memory as
  evidence, deduplicated on the table's UNIQUE key.
* **Validity**: ~4% of memories (and their edges) are archived, so hydration's
  ``valid_until`` filter drops real rows.

The vectors are not semantically tied to the text. The benchmark measures
search, fusion, hydration and rerank cost, not retrieval quality (that is
LUM-532's manual validation).
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from datetime import timedelta
from datetime import timezone

import numpy as np

#: ``nomic-embed-text`` (the default ``EMBEDDING_MODEL``) produces 768-dim vectors.
EMBED_DIM = 768
DEFAULT_SEED = 542
DEFAULT_USER_ID = "bench-recall"
DEFAULT_BANK = "coding"

_NS = uuid.UUID("5b0d2c3e-6a42-4f8e-9d54-0542a1b2c3d4")
#: Fixed anchor so valid_from timestamps do not depend on the wall clock.
_ANCHOR = datetime(2026, 6, 1, tzinfo=timezone.utc)
_ARCHIVED_FRACTION = 0.04
_SUBTOPICS = 6


@dataclass(frozen=True)
class Topic:
    name: str
    vocab: tuple[str, ...]
    entities: tuple[tuple[str, str], ...]  # (name, entity_type)


TOPICS: tuple[Topic, ...] = (
    Topic(
        "postgres",
        (
            "migration",
            "index",
            "vacuum",
            "tsvector",
            "query",
            "planner",
            "transaction",
            "schema",
            "pool",
            "replica",
            "jsonb",
            "constraint",
            "deadlock",
            "advisory",
            "lock",
            "column",
        ),
        (("Postgres", "LIBRARY"), ("psycopg2", "LIBRARY"), ("pgvector", "LIBRARY")),
    ),
    Topic(
        "qdrant",
        (
            "vector",
            "collection",
            "payload",
            "filter",
            "hnsw",
            "embedding",
            "cosine",
            "snapshot",
            "segment",
            "optimizer",
            "tenant",
            "shard",
            "quantization",
            "recall",
            "similarity",
            "point",
        ),
        (("Qdrant", "LIBRARY"), ("nomic-embed-text", "CONCEPT"), ("HNSW", "CONCEPT")),
    ),
    Topic(
        "mcp",
        (
            "tool",
            "server",
            "transport",
            "stdio",
            "annotation",
            "scope",
            "token",
            "bank",
            "remember",
            "forget",
            "recall",
            "client",
            "session",
            "schema",
            "jsonrpc",
            "bridge",
        ),
        (("FastMCP", "LIBRARY"), ("Cursor", "PROJECT"), ("Claude Desktop", "PROJECT")),
    ),
    Topic(
        "auth",
        (
            "session",
            "cookie",
            "refresh",
            "jwt",
            "password",
            "argon2",
            "csrf",
            "origin",
            "role",
            "admin",
            "household",
            "member",
            "rotation",
            "expiry",
            "login",
            "logout",
        ),
        (("Argon2", "LIBRARY"), ("JWT", "CONCEPT"), ("Caddy", "LIBRARY")),
    ),
    Topic(
        "docker",
        (
            "compose",
            "image",
            "volume",
            "network",
            "healthcheck",
            "build",
            "layer",
            "cache",
            "overlay",
            "container",
            "restart",
            "entrypoint",
            "tmpfs",
            "registry",
            "tag",
            "buildkit",
        ),
        (("Docker", "LIBRARY"), ("Compose", "LIBRARY"), ("GHCR", "ORG")),
    ),
    Topic(
        "graph",
        (
            "entity",
            "edge",
            "relation",
            "merge",
            "alias",
            "extraction",
            "hop",
            "traversal",
            "cypher",
            "node",
            "duplicate",
            "constraint",
            "evidence",
            "reconcile",
            "projection",
            "orphan",
        ),
        (("FalkorDB", "LIBRARY"), ("Cypher", "CONCEPT"), ("RedisGraph", "LIBRARY")),
    ),
    Topic(
        "frontend",
        (
            "react",
            "component",
            "route",
            "hook",
            "state",
            "render",
            "vite",
            "bundle",
            "codegen",
            "openapi",
            "form",
            "modal",
            "theme",
            "layout",
            "accessibility",
            "pwa",
        ),
        (("React", "LIBRARY"), ("Vite", "LIBRARY"), ("Vitest", "LIBRARY")),
    ),
    Topic(
        "testing",
        (
            "pytest",
            "fixture",
            "mock",
            "integration",
            "flaky",
            "coverage",
            "assert",
            "harness",
            "skip",
            "marker",
            "seed",
            "deterministic",
            "regression",
            "gate",
            "parametrize",
            "conftest",
        ),
        (("pytest", "LIBRARY"), ("Playwright", "LIBRARY"), ("TestClient", "CONCEPT")),
    ),
    Topic(
        "email",
        (
            "imap",
            "mailbox",
            "attachment",
            "thread",
            "header",
            "sender",
            "unsubscribe",
            "digest",
            "inbox",
            "folder",
            "quote",
            "signature",
            "smtp",
            "draft",
            "reply",
            "bounce",
        ),
        (("IMAP", "CONCEPT"), ("Gmail", "ORG"), ("Fastmail", "ORG")),
    ),
    Topic(
        "release",
        (
            "changelog",
            "export",
            "branch",
            "promotion",
            "tag",
            "public",
            "private",
            "preflight",
            "drift",
            "hygiene",
            "snapshot",
            "candidate",
            "rollback",
            "version",
            "license",
            "agpl",
        ),
        (("GitHub", "ORG"), ("AGPL", "CONCEPT"), ("Linear", "PROJECT")),
    ),
    Topic(
        "llm",
        (
            "model",
            "prompt",
            "context",
            "token",
            "temperature",
            "rerank",
            "cross-encoder",
            "ollama",
            "provider",
            "streaming",
            "tool-call",
            "budget",
            "judge",
            "summary",
            "extraction",
            "privacy",
        ),
        (("Ollama", "PROJECT"), ("llama3.2", "CONCEPT"), ("MiniLM", "CONCEPT")),
    ),
    Topic(
        "backup",
        (
            "snapshot",
            "restore",
            "manifest",
            "retention",
            "rsync",
            "hardlink",
            "schedule",
            "bgsave",
            "archive",
            "verify",
            "encryption",
            "disk",
            "workspace",
            "rotation",
            "prune",
            "checksum",
        ),
        (("rsync", "LIBRARY"), ("restic", "LIBRARY"), ("Redis", "LIBRARY")),
    ),
    Topic(
        "household",
        (
            "calendar",
            "school",
            "dentist",
            "groceries",
            "birthday",
            "holiday",
            "insurance",
            "garden",
            "recipe",
            "invoice",
            "appointment",
            "car",
            "renovation",
            "plumber",
            "flight",
            "hotel",
        ),
        (("Paperless", "PROJECT"), ("Immich", "PROJECT"), ("CalDAV", "CONCEPT")),
    ),
    Topic(
        "security",
        (
            "egress",
            "allowlist",
            "proxy",
            "sni",
            "secret",
            "scanner",
            "injection",
            "sandbox",
            "audit",
            "cve",
            "header",
            "tls",
            "certificate",
            "firewall",
            "rate-limit",
            "csp",
        ),
        (("Squid", "LIBRARY"), ("Bandit", "LIBRARY"), ("Tailscale", "PROJECT")),
    ),
)

_COMMON = (
    "the",
    "we",
    "because",
    "when",
    "use",
    "should",
    "always",
    "never",
    "after",
    "before",
    "this",
    "that",
    "with",
    "for",
    "from",
    "into",
    "keeps",
    "makes",
    "needs",
    "returns",
    "fails",
    "works",
    "slow",
    "fast",
    "small",
    "large",
    "first",
    "last",
    "again",
    "only",
    "every",
    "each",
    "one",
    "two",
    "three",
    "default",
    "config",
    "value",
    "path",
    "file",
    "user",
    "request",
    "response",
    "error",
    "retry",
    "timeout",
    "limit",
    "check",
    "test",
    "run",
    "start",
    "stop",
    "change",
    "update",
    "write",
    "read",
    "load",
    "save",
    "local",
    "remote",
    "decision",
    "reason",
    "problem",
    "fix",
    "note",
    "later",
    "today",
    "week",
    "month",
    "simple",
    "clear",
    "safe",
    "risky",
    "old",
    "new",
    "same",
    "different",
    "instead",
    "also",
    "still",
)
_VERBS = (
    "use",
    "adopt",
    "drop",
    "replace",
    "pin",
    "cache",
    "split",
    "merge",
    "index",
    "batch",
    "retry",
    "gate",
)
_KINDS = ("decision", "convention", "bug", "fact", "preference", "todo")
_KIND_P = (0.25, 0.15, 0.2, 0.2, 0.1, 0.1)
_SOURCES = ("cursor", "claude-code", "claude-desktop", "web")
_PROJECTS = ("lumogis-app", "lumogis-devtools", "lumogis-search", "household")
_DIRS = ("services", "adapters", "routes", "models", "tests", "scripts")
_RELATIONS = ("USES", "DEPENDS_ON", "RELATES_TO", "REPLACES", "PART_OF")
_SYLLABLES = ("ra", "vel", "mo", "sk", "tor", "lin", "ka", "dex", "ber", "nu", "qi", "zan")


@dataclass
class Memory:
    id: str
    user_id: str
    bank: str
    content: str
    tags: list[str]
    metadata: dict
    valid_from: datetime
    valid_until: datetime | None
    topic: int
    entity_ids: list[str]
    identifiers: list[str]


@dataclass
class Entity:
    entity_id: str
    user_id: str
    name: str
    entity_type: str
    mention_count: int
    topic: int


@dataclass
class Edge:
    id: str
    user_id: str
    bank: str
    src_entity_id: str
    dst_entity_id: str
    relation_type: str
    evidence_id: str
    valid_from: datetime
    valid_until: datetime | None


@dataclass
class Bank:
    user_id: str
    bank: str
    seed: int
    memories: list[Memory]
    vectors: np.ndarray  # (n, EMBED_DIM) float32, row i belongs to memories[i]
    entities: list[Entity] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)

    def fingerprint(self) -> str:
        """SHA-256 over every generated field: equal fingerprints = identical bank."""
        h = hashlib.sha256()
        for m in self.memories:
            h.update(f"{m.id}|{m.content}|{m.tags}|{sorted(m.metadata.items())}|".encode())
            h.update(f"{m.valid_from.isoformat()}|{m.valid_until}|{m.entity_ids}".encode())
        h.update(self.vectors.tobytes())
        for e in self.entities:
            h.update(f"{e.entity_id}|{e.name}|{e.entity_type}|{e.mention_count}".encode())
        for ed in self.edges:
            h.update(f"{ed.id}|{ed.src_entity_id}|{ed.dst_entity_id}|{ed.evidence_id}".encode())
        return h.hexdigest()


@dataclass(frozen=True)
class Query:
    text: str
    shape: str  # keyword | entity | identifier | conceptual | long
    vector: np.ndarray


def _uid(*parts: object) -> str:
    return uuid.uuid5(_NS, "::".join(str(p) for p in parts)).hex


def _uuid_str(*parts: object) -> str:
    """Hyphenated form: ``entities.entity_id`` is a UUID column and recall's graph
    leg compares ``str(entity_id)`` against the TEXT ``entity_edges`` columns."""
    return str(uuid.uuid5(_NS, "::".join(str(p) for p in parts)))


def _unit(v: np.ndarray) -> np.ndarray:
    return (v / np.linalg.norm(v)).astype(np.float32)


def _topic_weights() -> np.ndarray:
    w = 1.0 / np.arange(1, len(TOPICS) + 1) ** 0.8
    return w / w.sum()


def _centroids(seed: int) -> tuple[np.ndarray, np.ndarray]:
    """Topic centroids (T, D) and sub-topic directions (T, S, D); depend on ``seed`` only."""
    rng = np.random.default_rng([seed, 0xC0FFEE])
    cent = rng.standard_normal((len(TOPICS), EMBED_DIM))
    cent /= np.linalg.norm(cent, axis=1, keepdims=True)
    sub = rng.standard_normal((len(TOPICS), _SUBTOPICS, EMBED_DIM))
    sub /= np.linalg.norm(sub, axis=2, keepdims=True)
    return cent, sub


def _vector(rng: np.random.Generator, cent, sub, topic: int) -> np.ndarray:
    s = int(rng.integers(0, _SUBTOPICS))
    noise = rng.standard_normal(EMBED_DIM)
    noise /= np.linalg.norm(noise)
    return _unit(0.6 * cent[topic] + 0.4 * sub[topic, s] + 0.55 * noise)


def _synthetic_name(rng: np.random.Generator) -> str:
    n = int(rng.integers(2, 4))
    return "".join(str(rng.choice(_SYLLABLES)) for _ in range(n)).capitalize()


def _generate_entities(
    *, n: int, seed: int, stream: int, user_id: str
) -> tuple[list[Entity], dict[int, list[Entity]]]:
    """Entities for ``user_id``: topic seed names + synthetic names, ~0.3·n rows in all.

    Returns (all entities, linkable entities per topic). About a third of the
    rows are linkable (named in memories); the rest stand in for
    document-extracted entities, which the graph seed's ILIKE scans too.
    """
    rng = np.random.default_rng([seed, stream, 0xE7])
    total = max(len(TOPICS) * 4, int(0.3 * n))
    linkable_per_topic = max(4, total // (3 * len(TOPICS)))
    types = ("PROJECT", "LIBRARY", "CONCEPT", "PERSON", "ORG", "FILE")
    out: list[Entity] = []
    by_topic: dict[int, list[Entity]] = {t: [] for t in range(len(TOPICS))}
    seen: set[str] = set()

    def add(name: str, etype: str, topic: int, linkable: bool) -> None:
        if name.lower() in seen:
            return
        seen.add(name.lower())
        mentions = int(min(500, rng.zipf(1.6)))
        e = Entity(
            _uuid_str("entity", seed, stream, user_id, name), user_id, name, etype, mentions, topic
        )
        out.append(e)
        if linkable:
            by_topic[topic].append(e)

    for t, topic in enumerate(TOPICS):
        for name, etype in topic.entities:
            add(name, etype, t, True)
        while len(by_topic[t]) < linkable_per_topic:
            add(_synthetic_name(rng), str(rng.choice(types)), t, True)
    guard = 0
    while len(out) < total and guard < total * 20:
        guard += 1
        t = int(rng.integers(0, len(TOPICS)))
        add(f"{_synthetic_name(rng)} {_synthetic_name(rng)}", str(rng.choice(types)), t, False)
    return out, by_topic


def _content(
    rng: np.random.Generator,
    *,
    topic: Topic,
    kind: str,
    names: list[str],
    identifiers: list[str],
) -> str:
    target = int(np.clip(rng.lognormal(np.log(28), 0.75), 6, 320))
    w1, w2 = (str(x) for x in rng.choice(topic.vocab, 2, replace=False))
    verb = str(rng.choice(_VERBS))
    ent = names[0] if names else str(rng.choice(topic.vocab))
    ident = identifiers[0] if identifiers else f"the {w1} path"
    head = {
        "decision": f"Decided to {verb} {ent} for {w1} {w2}",
        "convention": f"Convention: always {verb} {w1} {w2} when touching {ent}",
        "bug": f"Bug in {ent}: {w1} {w2} fails under load, fixed in {ident}",
        "fact": f"{ent} handles {w1} and {w2} for the {topic.name} layer",
        "preference": f"Prefer {w1} over {w2} for {ent}",
        "todo": f"TODO {ident}: {verb} {w1} {w2} in {ent}",
    }[kind]
    words = head.split()
    extra = names[1:] + identifiers[1:]
    while len(words) < target:
        if extra and rng.random() < 0.08:
            words.append(extra.pop(0))
        elif rng.random() < 0.4:
            words.append(str(rng.choice(topic.vocab)))
        else:
            words.append(str(rng.choice(_COMMON)))
    words.extend(extra)
    # Sentence breaks every ~12 words, so the text reads like prose to the parser.
    out: list[str] = []
    for i, w in enumerate(words):
        out.append(w)
        if i and i % 12 == 0:
            out[-1] = out[-1] + "."
    return " ".join(out).rstrip(".") + "."


def generate_bank(
    n: int,
    *,
    seed: int = DEFAULT_SEED,
    stream: int = 0,
    user_id: str = DEFAULT_USER_ID,
    bank: str = DEFAULT_BANK,
    graph: bool = True,
) -> Bank:
    """Generate ``n`` memories (+ entities and edges when ``graph``) deterministically.

    ``stream`` separates independent banks sharing one ``seed`` (other users,
    other banks): they reuse the topic centroids, so their vectors compete with
    the benchmarked bank in the same embedding space, as real banks do.
    """
    cent, sub = _centroids(seed)
    weights = _topic_weights()
    entities: list[Entity] = []
    by_topic: dict[int, list[Entity]] = {}
    if graph:
        entities, by_topic = _generate_entities(n=n, seed=seed, stream=stream, user_id=user_id)

    memories: list[Memory] = []
    vectors = np.empty((n, EMBED_DIM), dtype=np.float32)
    edges: list[Edge] = []
    edge_keys: set[tuple[str, str, str]] = set()

    for i in range(n):
        rng = np.random.default_rng([seed, stream, i])
        t = int(rng.choice(len(TOPICS), p=weights))
        topic = TOPICS[t]
        kind = str(rng.choice(_KINDS, p=_KIND_P))

        linked: list[Entity] = []
        if graph and by_topic.get(t):
            pool = by_topic[t]
            k = int(rng.choice(4, p=(0.3, 0.35, 0.25, 0.1)))
            # Zipf-ish preference for the topic's first (best-known) entities.
            p = 1.0 / np.arange(1, len(pool) + 1) ** 1.1
            idx = rng.choice(len(pool), size=min(k, len(pool)), replace=False, p=p / p.sum())
            linked = [pool[j] for j in idx]

        identifiers: list[str] = []
        if rng.random() < 0.18:
            identifiers.append(f"LUM-{int(rng.integers(100, 1000))}")
        if rng.random() < 0.10:
            a, b = (str(x) for x in rng.choice(topic.vocab, 2, replace=False))
            safe = f"{a}_{b}".replace("-", "_")
            identifiers.append(f"orchestrator/{rng.choice(_DIRS)}/{safe}.py")

        content = _content(
            rng,
            topic=topic,
            kind=kind,
            names=[e.name for e in linked],
            identifiers=identifiers,
        )
        valid_from = _ANCHOR - timedelta(minutes=int(rng.integers(0, 730 * 24 * 60)))
        valid_until = None
        if rng.random() < _ARCHIVED_FRACTION:
            valid_until = valid_from + timedelta(days=int(rng.integers(1, 120)))
            valid_until = min(valid_until, _ANCHOR)
        mid = _uid("memory", seed, stream, user_id, bank, i)
        tags = [topic.name, kind]
        if rng.random() < 0.3:
            tags.append(str(rng.choice(_PROJECTS)))
        metadata = {
            "source": str(rng.choice(_SOURCES)),
            "kind": kind,
            "topic": topic.name,
            "session_id": _uid("session", seed, stream, int(rng.integers(0, max(1, n // 8)))),
        }
        memories.append(
            Memory(
                id=mid,
                user_id=user_id,
                bank=bank,
                content=content,
                tags=tags,
                metadata=metadata,
                valid_from=valid_from,
                valid_until=valid_until,
                topic=t,
                entity_ids=[e.entity_id for e in linked],
                identifiers=identifiers,
            )
        )
        vectors[i] = _vector(rng, cent, sub, t)

        if graph and linked:
            hub = by_topic[t][0]
            pairs = list(zip(linked, linked[1:]))
            if len(linked) == 1 and linked[0].entity_id != hub.entity_id:
                pairs = [(linked[0], hub)]
            for src, dst in pairs:
                rel = str(rng.choice(_RELATIONS))
                key = (src.entity_id, dst.entity_id, rel)
                if key in edge_keys:
                    continue  # UNIQUE (user_id, bank, src, dst, relation_type)
                edge_keys.add(key)
                edges.append(
                    Edge(
                        id=_uid("edge", seed, stream, user_id, bank, *key),
                        user_id=user_id,
                        bank=bank,
                        src_entity_id=src.entity_id,
                        dst_entity_id=dst.entity_id,
                        relation_type=rel,
                        evidence_id=mid,
                        valid_from=valid_from,
                        valid_until=valid_until,
                    )
                )

    return Bank(user_id, bank, seed, memories, vectors, entities, edges)


#: Query-shape mix. ``identifier`` queries take a real identifier from the bank.
QUERY_MIX: tuple[tuple[str, float], ...] = (
    ("keyword", 0.30),
    ("entity", 0.20),
    ("identifier", 0.15),
    ("conceptual", 0.25),
    ("long", 0.10),
)

_CONCEPTUAL = (
    "why did we choose {a} for {b}",
    "how do we handle {a} {b}",
    "what was the decision about {a}",
    "what is our convention for {a} and {b}",
    "which {a} problem did we fix last month",
)


def generate_queries(bank: Bank, count: int, *, seed: int = DEFAULT_SEED) -> list[Query]:
    """A seeded mix of ``count`` queries against ``bank`` (see ``QUERY_MIX``)."""
    rng = np.random.default_rng([seed, 0x9E7])
    cent, sub = _centroids(bank.seed)
    weights = _topic_weights()
    shapes = [s for s, _ in QUERY_MIX]
    probs = [p for _, p in QUERY_MIX]
    with_ident = [i for i, m in enumerate(bank.memories) if m.identifiers]
    named = {eid for m in bank.memories for eid in m.entity_ids}
    linkable = [e for e in bank.entities if e.entity_id in named]
    out: list[Query] = []
    for _ in range(count):
        shape = str(rng.choice(shapes, p=probs))
        t = int(rng.choice(len(TOPICS), p=weights))
        topic = TOPICS[t]
        vec = _vector(rng, cent, sub, t)
        if shape == "identifier" and with_ident:
            i = int(rng.choice(with_ident))
            text = bank.memories[i].identifiers[0]
            noise = rng.standard_normal(EMBED_DIM)
            vec = _unit(bank.vectors[i] + 0.3 * noise / np.linalg.norm(noise))
        elif shape == "entity" and linkable:
            e = linkable[int(rng.integers(0, len(linkable)))]
            text = f"{e.name} {rng.choice(TOPICS[e.topic].vocab)}"
            vec = _vector(rng, cent, sub, e.topic)
        elif shape == "conceptual":
            a, b = (str(x) for x in rng.choice(topic.vocab, 2, replace=False))
            text = str(rng.choice(_CONCEPTUAL)).format(a=a, b=b)
        elif shape == "long":
            n_words = int(rng.integers(12, 21))
            words = [
                str(rng.choice(topic.vocab)) if rng.random() < 0.4 else str(rng.choice(_COMMON))
                for _ in range(n_words)
            ]
            text = " ".join(words)
        else:
            shape = "keyword"
            k = int(rng.integers(1, 4))
            text = " ".join(str(x) for x in rng.choice(topic.vocab, k, replace=False))
        out.append(Query(text=text, shape=shape, vector=vec))
    return out
