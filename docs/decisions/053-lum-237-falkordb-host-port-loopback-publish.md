# ADR-053: FalkorDB loopback host publish for `make m1-compat-with-retry` (LUM-237)

**Status:** Accepted
**Created:** 2026-05-21
**Last updated:** 2026-05-21
**Decided by:** `/explore --headless` LUM-237 (draft); **`/verify-plan --headless`** finalisation
**Linear:** [LUM-237](https://linear.app/lumogis/issue/LUM-237/falkordb-not-port-mapped-to-host-make-m1-compat-with-retry-requires)

## Context

The `make m1-compat-with-retry` target runs the live `TestFalkorDBCompatGate` against a real FalkorDB instance from the **host**, but the optional `docker-compose.falkordb.yml` overlay did not publish a host port. Operators had to discover the container IP and pass it via `FALKORDB_URL`, which changes on restart. Reusing host `6379` collides with common local Redis services.

The stack already uses a parameterised host-port convention for Qdrant (`"${QDRANT_HOST_PORT:-6334}:6333"`); FalkorDB uses the same *env-default* pattern but binds **loopback only** because the Redis wire is unauthenticated.

## Decision

Publish FalkorDB on the **host loopback** at a non-`6379` port, parameterised with default **6380**:

```yaml
# docker-compose.falkordb.yml (falkordb service)
ports:
  - "127.0.0.1:${FALKORDB_HOST_PORT:-6380}:6379"
```

Canonical host URL for compat-gate runs: **`redis://127.0.0.1:6380`**. Document **`FALKORDB_HOST_PORT`** and the host **`FALKORDB_URL`** in **`.env.example`** and cross-reference **`Makefile`** `m1-compat-with-retry`. Internal traffic continues to use **`redis://falkordb:6379`**; **`docker-compose.premium.yml`** and **`docker-compose.public-rc-stack.yml`** are unchanged for this chunk.

## Alternatives Considered

- **Hard-coded `127.0.0.1:6380:6379`** — rejected; no override hatch vs `QDRANT_HOST_PORT` convention.
- **Separate `docker-compose.falkordb-host.yml` overlay** — rejected (operator friction).
- **Makefile auto-discovers container IP** — rejected (brittle across Docker setups).
- **Publish on `0.0.0.0`** — rejected; unauthenticated Redis must not be LAN-reachable.

Full comparison: `.cursor/explorations/LUM-237-falkordb-host-port.md`.

## Consequences

**Easier:**

- `FALKORDB_URL=redis://127.0.0.1:6380 RUN_M1_COMPAT=1 make m1-compat-with-retry` works across container restarts when the overlay is up and the default port is free.
- Operators override **`FALKORDB_HOST_PORT`** (and matching URL) when **6380** is taken.

**Harder / closed off:**

- Operators with bespoke host binds must align with the new default once.
- This decision does **not** add FalkorDB `requirepass`; LAN exposure still requires a follow-up ADR.

**Touchpoints (as shipped):**

- `docker-compose.falkordb.yml` — `ports` + header comments.
- `.env.example` — Graph / FalkorDB block.
- `Makefile` — `m1-compat-with-retry` help line.
- `orchestrator/tests/premium/test_graph_writer.py` — module + `TestFalkorDBCompatGate` docstrings (examples only).

**Policy verification:** `make compose-policy-check` plus merged `python3 scripts/check_compose_policy.py --project-directory . -f docker-compose.yml -f docker-compose.falkordb.yml` (default CI compose set does not merge the FalkorDB overlay alone).

No changes to: **`docker-compose.premium.yml`**, **`docker-compose.public-rc-stack.yml`**, **`docker-compose.yml`**, or orchestrator **runtime** graph writer code paths.

## Revisit conditions

- FalkorDB gains authentication and operators want non-loopback access — re-evaluate bind and URL scheme.
- Default **6380** proves too collision-prone — re-pick default in a follow-up.
- RC stack ever needs host probes from the same convention — extend there explicitly.

## Status history

- **2026-05-21:** Finalised by **`/verify-plan --headless`** — `make test` **1744** passed / **51** skipped (orchestrator) + stack-control **11** passed (**`PYTHON=.venv/bin/python`**); **`make compose-policy-check`** exit **0**; merged **`check_compose_policy.py`** (base + `docker-compose.falkordb.yml`) exit **0**; **`FALKORDB_URL=redis://127.0.0.1:6380 RUN_M1_COMPAT=1 make m1-compat-with-retry`** green after **`docker compose … up -d falkordb`** (first pytest attempt flaked **`test_ping`**, Makefile retry path passed).
- **2026-05-21:** Draft created by **`/explore --headless`** LUM-237; **`/review-plan --self`** aligned § Touchpoints with orchestrator docstrings.
