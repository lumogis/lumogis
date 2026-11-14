# ADR-210: Postgres connection pooling — one pool, not one connection
**Status:** Finalised
**Created:** 2026-08-09
**Last updated:** 2026-08-09
**Finalised copy:** `docs/decisions/210-lum-816-db-connection-pooling.md`
**Decided by:** Claude Opus 5 (Claude Code) via `/explore` — LUM-816

## Context

`config.get_metadata_store()` caches one `PostgresStore` per process, and that store holds one psycopg2 connection. `PostgresStore.transaction()` implements atomicity by toggling `autocommit` on that connection — shared mutable state.

The orchestrator serves **318 sync routes against 2 async**, so almost every request runs in Starlette's AnyIO threadpool (default 40 threads), alongside 12 APScheduler jobs on their own threads. Approximately forty threads contend for one connection guarded by a boolean.

Two failure modes, proved against a real Postgres in `tests/integration/test_postgres_store_concurrency_live.py`:

- **Loud** — the second concurrent `transaction()` raises `RuntimeError`. Deterministic, not a race.
- **Quiet** — an ordinary `execute()` on another thread is silently **enrolled** in an open transaction and discarded when that transaction rolls back. Nothing raises; committed-looking work vanishes.

The constraint has been rediscovered four times — LUM-358 (shipped a warning), LUM-186 (documented the mechanism and the cure, then routed around it), LUM-687 (restructured to avoid transactions), LUM-65 (walked in; four P0 defects). The guidance lived in archived plan prose, never in the code, so it could not reach the person who needed it.

Two further facts shaped the option space. **Six hand-rolled "give me a connection that is not the shared one" mechanisms** already exist across four tickets — three of them methods on `PostgresStore` itself, one (`share_lock.py`) reaching into the store's private `_dsn`. And **session-scoped advisory locks** (`consolidation_lock`, `share_lock`, `digest`, `triage_reminders`) require a connection held for the lock's full lifetime, which constrains any pooling design and rules out transaction-mode external poolers.

## Decision

Replace the single process-wide connection with an in-process **`psycopg2.pool.ThreadedConnectionPool`**, held behind an **unchanged `MetadataStore` port**, so a connection is checked out per unit of work and `autocommit` stops being shared state. The pool is configured by `LUMOGIS_PG_POOL_MIN` / `LUMOGIS_PG_POOL_MAX` (default 2 / 10, following the Postgres community `(cores × 2) + 1` guidance and leaving headroom under `max_connections=100`), and `config.get_metadata_store()` continues to cache one instance — now the pool holder rather than the connection holder. The six existing hand-rolled checkout mechanisms collapse onto it, with `checkout_connection()` retained as the explicit hold-for-lock-duration API that advisory locks require. As an interim guard shipping immediately and independently, `transaction()` gains a docstring stating the constraint.

## Alternatives Considered

Full detail: `.cursor/explorations/LUM-816-db-connection-pooling.md`.

- **psycopg3 + `psycopg_pool`** — the better destination (background health checks, `max_lifetime` recycling, ~20× faster acquisition, actively developed against psycopg2's maintenance mode). Deferred, not rejected: it welds an urgent concurrency fix to an unhurried driver migration across 15 files, 21 `RealDictCursor` sites and the container's boot-path migration scripts, where a regression would be indistinguishable from a pooling regression.
- **SQLAlchemy 2.0 Core `QueuePool`** — better-engineered than psycopg2's pool, but a large dependency for a codebase that deliberately has no ORM and hand-writes every parameterised query; buys little over the recommendation that psycopg3 would not buy better later.
- **PgBouncer sidecar** — rejected. It addresses many app instances exhausting `max_connections`, not threads inside one process sharing one client-side connection object; it would sit *behind* the shared `self._conn` and change nothing. Transaction-mode pooling would also silently break every `pg_advisory_lock` the codebase relies on, and it violates "no new Docker services unless justified."
- **Connection-per-request, no pool** — correct and simple (it is what LUM-65 did, generalised), but trades a correctness bug for unbounded connections under a 40-thread burst plus a TCP + auth handshake per request. Retained as the fallback if pooling hits an advisory-lock snag.
- **Status quo plus discipline** — rejected as a resolution, adopted as today's stop-gap. Four prior restatements did not prevent the fifth incident; a sixth would not either.

## Consequences

**Easier.** `transaction()` becomes safe from any route or scheduler job, removing the bug class rather than its instances. Six duplicated checkout mechanisms become one with a ceiling. The `SELECT 1`-before-every-query tax in `_ensure_conn()` disappears, along with its reconnect race. Future concurrent features — LUM-109's background consolidation agent, LUM-596's live collaborative conversation — stop needing bespoke workarounds. No new dependency, no new container, no image growth.

**Harder.** Connection count becomes a tunable with real failure modes at both ends: too small starves the threadpool under burst, too large approaches `max_connections`. psycopg2's pool does not reset connection state on `putconn` and has no background health checking, so the adapter must `rollback()` and normalise `autocommit` on return — a discipline that now lives in one place but must not be lost. Advisory-lock checkouts must be visibly distinct from ordinary ones, since a connection recycled mid-lock would release it silently.

**Future chunks must know.** The pool is **per process**. `uvicorn --workers N` means N pools and N × `pool_max` connections, which is where the sizing formula breaks and where PgBouncer would genuinely earn its place. LUM-407's startup-reclaim guard rests on the single-worker assumption and must be amended alongside any move to multi-worker. `transaction()` and `isolated_transaction()` converge in meaning once pooling lands — the LUM-65 distinction dissolves and should be retired deliberately rather than left as two names for one thing.


## Implementation amendments (2026-08-09, /verify-plan)

The decision held. Five details in the draft were wrong or incomplete, each
found by measurement rather than by reading, and each is now the shipped
behaviour.

**Pool defaults are 10/10, not 2/10 — and `min == max` is load-bearing.**
psycopg2's `_putconn` returns a connection to the free list only while
`len(_pool) < minconn` and **closes** it otherwise. A min below max therefore
degrades the pool into connect-per-use above `minconn` concurrent callers —
measured at ~12ms per statement versus ~0.15ms pooled, which is the entire
benefit this ADR was written to obtain. The draft's 2/10 would have delivered
pooling for two callers and connect-per-request for the next eight.

**An explicit `LUMOGIS_PG_POOL_MAX` wins over the default min.** Max is the
ceiling the database has slots for, so min yields to it. Clamping the other way
gave an operator who set only `MAX=4` ten connections, silently.

**The `SELECT 1` tax disappears; a replacement for its reconnect was still
required.** The draft's Consequences said the tax "disappears" and stopped
there, and the plan's error table then accepted the resulting gap outright
("connection broken between checkouts → the next borrower's statement fails —
**Accepted, and documented**"). That was wrong. Deleting `_ensure_conn` meant a
`docker compose restart postgres` left every pooled connection dead and 500ed up
to `pool_max` consecutive requests, where previously zero failed. The probe
cannot return — it is a round-trip per statement — and it cannot be replaced
cheaply either: measured, `conn.closed` is still `0` on a connection the server
has already dropped. **So the failure is the probe.** A statement that leaves
`conn.closed == 2` has its connection discarded and runs once more; an ordinary
SQL error leaves `0`, so this never fires on a deadlock. Never retried inside a
transaction, where the earlier statements died with the connection.

**`ping()` must go through the pool.** Probing off-pool reports healthy through
exactly the outage above — every pooled connection dead, a brand-new one fine —
so `/admin/health` stayed green while every request failed. A saturated pool
still reports healthy: busy is not broken.

**Pool exhaustion is 503 with `Retry-After`, not 500.** The draft assumed
"existing route error handling" would map it. Nothing did, so it reached clients
as a bare 500 after a ten-second wait.

**Two properties survive the `transaction()` / `isolated_transaction()`
convergence.** Nesting `transaction()` is a savepoint — but savepoint names now
carry their nesting depth, because Postgres resolves an unqualified `ROLLBACK
TO` to the *most recent* savepoint of a given name and does not destroy it, so a
shared literal made the outer rollback land on the inner mark (measured:
`{'base','between'}` survived a rollback that should have left `{'base'}`). And
`isolated_transaction()` still **refuses to nest**: its callers chose it for
independence, and `system_mail.drain_outbox`'s per-row claim must survive a
crash mid-send. Demoted to a savepoint it commits nothing of its own, and an
outer rollback would re-mail an already-delivered reset link.

**LUM-358's advisory-lock reconnect gap is closed structurally.** Every lock
site now holds an unpooled connection (`checkout_connection` /
`new_lock_connection`); there is no shared connection to reconnect. ADR-154
amended accordingly. The pooled successor — a lock taken through the pooled
surface being re-issued by the discard-and-retry path — is documented on
`PostgresStore._run`, and is why session-scoped state has no business on that
surface.

## Revisit conditions

- **`uvicorn --workers N` becomes a supported deployment shape**, or a second orchestrator process connects to the same database — client-side pooling stops being sufficient and PgBouncer (session mode, to preserve advisory locks) returns to the table.
- **psycopg2's `putconn` state-reset gap** ([#1022](https://github.com/psycopg/psycopg2/issues/1022)) produces a real incident despite the adapter's rollback-on-return — accelerate the psycopg3 migration rather than patching further.
- **An independent reason to move to psycopg3 arrives** (async routes, `COPY` performance, a psycopg2 security advisory) — fold the pool onto `psycopg_pool` at that point; this ADR's structure is intended to make that a drop-in.
- **Sustained pool exhaustion under normal household load** — the `(cores × 2) + 1` sizing assumption is wrong for this workload and needs measurement, not a larger default.
- **Postgres `max_connections` is lowered** below roughly 4× `LUMOGIS_PG_POOL_MAX` — the headroom for advisory-lock checkouts, boot migrations and admin `psql` no longer holds.

## Status history
- 2026-08-09: Draft created by /explore (LUM-816)
- 2026-08-09: Finalised by /verify-plan — implementation confirmed the decision; five details amended above. Finalised copy at `docs/decisions/210-lum-816-db-connection-pooling.md`.
