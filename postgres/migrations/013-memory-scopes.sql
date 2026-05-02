-- Migration 013: personal / shared / system memory scopes.
--
-- Adds a first-class visibility dimension `scope ∈ {personal, shared, system}`
-- across 9 memory surfaces (Postgres side), and a nullable `published_from`
-- self-FK on the 6 publishable surfaces so a publish call creates a separate
-- shared/system "projection row" linked back to the personal source. The
-- personal row is never mutated by share/unshare; unpublish deletes the
-- projection only.
--
-- Sequencing: assumes 011-per-user-file-index.sql and
-- 012-entity-relations-evidence-uniq.sql are already live. Idempotent &
-- re-runnable: every ADD COLUMN uses IF NOT EXISTS, every CREATE INDEX uses
-- IF NOT EXISTS, every backfill UPDATE has a WHERE that's a no-op on the
-- second pass.
--
-- Greenfield posture: NO row-level pre-cleanup. All existing rows fall through
-- the column default `'personal'`. The one writer-side semantic backfill is
-- `signals.source_id='__system__' → scope='system'` (the only system-scoped
-- writer that exists pre-013).
--
-- See: .cursor/plans/personal_shared_system_memory_scopes.plan.md
--      .cursor/adrs/personal_shared_system_memory_scopes.md
--      .cursor/explorations/personal_shared_system_memory_scopes.md

BEGIN;

-- ──────────────────────────────────────────────────────────────────────────
-- 1. Add `scope` to the 9 memory-bearing tables.
-- ──────────────────────────────────────────────────────────────────────────
-- All default `'personal'`. CHECK constrains the cardinality at the storage
-- layer so a buggy writer cannot smuggle in `'global'`/`'public'`/etc.
-- ARBITRATE-R1-ADDENDUM (#3): the bottom three (review_queue / action_log /
-- audit_log) receive `scope` for schema uniformity but have NO writer that
-- ever produces `scope='shared'` or `scope='system'` rows in v1 — no publish
-- API, no signal/system seed, no dedup-promotion path. Their visible_filter
-- union arm `scope IN ('shared','system')` will always return zero rows in
-- v1. Reads on these tables go through admin-bypass (`# ADMIN-BYPASS:` per
-- §8 + acceptance #4) anyway. The column is forward-compatibility scaffolding
-- — reserved for a future chunk that activates household-visible audit /
-- household review queue. If you find yourself implementing a writer that
-- produces shared/system rows on these tables, you ARE that future chunk and
-- must add (a) the `published_from` column + partial unique index and (b) a
-- publish path consistent with §7 of the plan.

ALTER TABLE notes        ADD COLUMN IF NOT EXISTS scope TEXT NOT NULL DEFAULT 'personal'
    CHECK (scope IN ('personal','shared','system'));
ALTER TABLE audio_memos  ADD COLUMN IF NOT EXISTS scope TEXT NOT NULL DEFAULT 'personal'
    CHECK (scope IN ('personal','shared','system'));
ALTER TABLE sessions     ADD COLUMN IF NOT EXISTS scope TEXT NOT NULL DEFAULT 'personal'
    CHECK (scope IN ('personal','shared','system'));
ALTER TABLE file_index   ADD COLUMN IF NOT EXISTS scope TEXT NOT NULL DEFAULT 'personal'
    CHECK (scope IN ('personal','shared','system'));
ALTER TABLE entities     ADD COLUMN IF NOT EXISTS scope TEXT NOT NULL DEFAULT 'personal'
    CHECK (scope IN ('personal','shared','system'));
ALTER TABLE signals      ADD COLUMN IF NOT EXISTS scope TEXT NOT NULL DEFAULT 'personal'
    CHECK (scope IN ('personal','shared','system'));
ALTER TABLE review_queue ADD COLUMN IF NOT EXISTS scope TEXT NOT NULL DEFAULT 'personal'
    CHECK (scope IN ('personal','shared','system'));
ALTER TABLE action_log   ADD COLUMN IF NOT EXISTS scope TEXT NOT NULL DEFAULT 'personal'
    CHECK (scope IN ('personal','shared','system'));
ALTER TABLE audit_log    ADD COLUMN IF NOT EXISTS scope TEXT NOT NULL DEFAULT 'personal'
    CHECK (scope IN ('personal','shared','system'));

-- ──────────────────────────────────────────────────────────────────────────
-- 2. Add `published_from` to the 6 publishable surfaces.
-- ──────────────────────────────────────────────────────────────────────────
-- v1 PK-type set is closed at {UUID, INTEGER} per plan §2.4:
--   notes(note_id), audio_memos(audio_id), sessions(session_id),
--   entities(entity_id), signals(signal_id) → all UUID
--   file_index(id) → SERIAL/INTEGER (the only INTEGER case; verified
--                    against postgres/init.sql:8-18, NOT BIGINT)
-- ON DELETE CASCADE so deleting a personal source automatically removes its
-- shared/system projection — preserves the projection-as-derived-data invariant.

ALTER TABLE notes        ADD COLUMN IF NOT EXISTS published_from UUID
    REFERENCES notes(note_id)        ON DELETE CASCADE;
ALTER TABLE audio_memos  ADD COLUMN IF NOT EXISTS published_from UUID
    REFERENCES audio_memos(audio_id) ON DELETE CASCADE;
ALTER TABLE sessions     ADD COLUMN IF NOT EXISTS published_from UUID
    REFERENCES sessions(session_id)  ON DELETE CASCADE;
ALTER TABLE file_index   ADD COLUMN IF NOT EXISTS published_from INTEGER
    REFERENCES file_index(id)        ON DELETE CASCADE;
ALTER TABLE entities     ADD COLUMN IF NOT EXISTS published_from UUID
    REFERENCES entities(entity_id)   ON DELETE CASCADE;
ALTER TABLE signals      ADD COLUMN IF NOT EXISTS published_from UUID
    REFERENCES signals(signal_id)    ON DELETE CASCADE;

-- ──────────────────────────────────────────────────────────────────────────
-- 3. Idempotency unique indexes.
-- ──────────────────────────────────────────────────────────────────────────
-- Partial UNIQUE on (published_from, scope) WHERE published_from IS NOT NULL
-- guarantees concurrent publish calls collapse to one projection per
-- (source, scope). Personal rows (published_from IS NULL) are excluded so
-- the partial index never collides on legitimate personal data.
-- Constraint names are pinned per plan §7 step 3 — projection writers
-- target these by name in their ON CONFLICT clauses.

CREATE UNIQUE INDEX IF NOT EXISTS notes_published_from_scope_uniq
    ON notes        (published_from, scope) WHERE published_from IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS audio_memos_published_from_scope_uniq
    ON audio_memos  (published_from, scope) WHERE published_from IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS sessions_published_from_scope_uniq
    ON sessions     (published_from, scope) WHERE published_from IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS file_index_published_from_scope_uniq
    ON file_index   (published_from, scope) WHERE published_from IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS entities_published_from_scope_uniq
    ON entities     (published_from, scope) WHERE published_from IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS signals_published_from_scope_uniq
    ON signals      (published_from, scope) WHERE published_from IS NOT NULL;

-- ──────────────────────────────────────────────────────────────────────────
-- 4. Read-side composite indexes — every scoped table.
-- ──────────────────────────────────────────────────────────────────────────
-- Matches the visible_filter union path:
--   (scope='personal' AND user_id=$me) OR scope IN ('shared','system')
-- The personal arm is the most selective; (user_id, scope) accelerates it.
-- These are additive on top of the existing per-table user_id indexes
-- (audio_memos, file_index, review_queue, action_log, audit_log already had
-- single-column user_id indexes from earlier migrations).

CREATE INDEX IF NOT EXISTS notes_user_scope_idx        ON notes        (user_id, scope);
CREATE INDEX IF NOT EXISTS audio_memos_user_scope_idx  ON audio_memos  (user_id, scope);
CREATE INDEX IF NOT EXISTS sessions_user_scope_idx     ON sessions     (user_id, scope);
CREATE INDEX IF NOT EXISTS file_index_user_scope_idx   ON file_index   (user_id, scope);
CREATE INDEX IF NOT EXISTS entities_user_scope_idx     ON entities     (user_id, scope);
CREATE INDEX IF NOT EXISTS signals_user_scope_idx      ON signals      (user_id, scope);
CREATE INDEX IF NOT EXISTS review_queue_user_scope_idx ON review_queue (user_id, scope);
CREATE INDEX IF NOT EXISTS action_log_user_scope_idx   ON action_log   (user_id, scope);
CREATE INDEX IF NOT EXISTS audit_log_user_scope_idx    ON audit_log    (user_id, scope);

-- ──────────────────────────────────────────────────────────────────────────
-- 5. Backfill: signals.source_id='__system__' rows are system-scoped.
-- ──────────────────────────────────────────────────────────────────────────
-- The only writer that produces a non-personal scope at v1 is
-- orchestrator/signals/system_monitor.py, which uses the literal
-- '__system__' as its source_id sentinel. Promote those rows to scope='system'
-- so the visible_filter union arm includes them for every household user.
-- Idempotent: rows already at scope='system' aren't touched.

UPDATE signals
   SET scope = 'system'
 WHERE source_id = '__system__'
   AND scope <> 'system';

-- ──────────────────────────────────────────────────────────────────────────
-- 6. Denormalized source columns on `signals` (sources-exclusion invariant).
-- ──────────────────────────────────────────────────────────────────────────
-- `sources` is intentionally NOT in the scope-bearing set (per §2.12 of the
-- plan). Therefore signals that may be projected to shared/system MUST carry
-- enough denormalized source metadata so a household-scope reader who has no
-- visibility into the publisher's personal `sources` row can still render the
-- signal correctly.
--
-- Verified against postgres/init.sql:140-155 — `sources.id UUID PRIMARY KEY`
-- and the human-display column is `name TEXT` (NOT `label`).
-- `signals.source_id TEXT` may also hold the literal '__system__' which has
-- no row in `sources`; the INNER JOIN in the backfill skips those rows.
--
-- ARBITRATE-R1-ADDENDUM (#2): write-path audit confirms `signals.source_id`
-- is ALWAYS either (a) str(UUID) of `sources.id` — set by
--   signal_processor.py:82 ← RawSignal.source_id ← _config.id from
--   rss_source.py:87, calendar_adapter.py:134, playwright_fetcher.py:76,
--   page_scraper.py:56, page_monitor.py:112; or
--   `str(uuid.uuid4())` from routes/signals.py:89; or
-- (b) the literal '__system__' from system_monitor.py:190 (no `sources`
-- row; correctly skipped by INNER JOIN).
-- The `src.id::text` cast is therefore safe for every production write-path.
-- If a future signal adapter writes `signals.source_id` as a non-UUID short
-- name or URL-derived id, this backfill will silently leave
-- `source_url`/`source_label` NULL on those rows — the new adapter MUST
-- either insert a corresponding `sources` row keyed by UUID, or stop being
-- eligible for shared/system projection (per §2.12 the denormalized columns
-- are mandatory for renderability without a sources join).

ALTER TABLE signals ADD COLUMN IF NOT EXISTS source_url   TEXT;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS source_label TEXT;

UPDATE signals s
   SET source_url   = src.url,
       source_label = src.name
  FROM sources src
 WHERE s.source_id = src.id::text
   AND s.source_url IS NULL;

COMMIT;
