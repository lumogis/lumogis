-- Lumogis Postgres schema
-- Runs once on first container start (docker-entrypoint-initdb.d/).
-- Add new tables here; never alter existing columns without a migration.
-- For existing installations: see postgres/migrations/ for ALTER TABLE scripts.

-- Tracks every ingested document: path, content hash, chunk count, OCR flag.
-- file_hash enables re-ingest skip: if hash unchanged, skip chunking + embedding.
CREATE TABLE IF NOT EXISTS file_index (
    id              SERIAL PRIMARY KEY,
    file_path       TEXT NOT NULL,
    file_hash       TEXT NOT NULL,
    file_type       TEXT NOT NULL,
    chunk_count     INTEGER NOT NULL DEFAULT 0,
    ocr_used        BOOLEAN NOT NULL DEFAULT FALSE,
    user_id         TEXT NOT NULL DEFAULT 'default',
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Memory-scopes (migration 013): see
    -- .cursor/plans/personal_shared_system_memory_scopes.plan.md.
    -- `scope` partitions visibility into personal/shared/system; `published_from`
    -- back-references the personal source row when this row is a projection.
    scope           TEXT NOT NULL DEFAULT 'personal'
                    CHECK (scope IN ('personal','shared','system')),
    published_from  INTEGER REFERENCES file_index(id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX IF NOT EXISTS file_index_published_from_scope_uniq
    ON file_index (published_from, scope) WHERE published_from IS NOT NULL;
CREATE INDEX IF NOT EXISTS file_index_user_scope_idx ON file_index (user_id, scope);

-- Per-user uniqueness: two users can independently ingest the same absolute
-- path; (user_id, file_path) is the canonical "file identity" tuple. Also
-- serves as the read-side index for SELECT/UPSERT lookups against
-- (user_id, file_path); no separate non-unique helper index is needed.
-- See postgres/migrations/011-per-user-file-index.sql.
CREATE UNIQUE INDEX IF NOT EXISTS file_index_user_path_uniq
    ON file_index (user_id, file_path);

-- Extracted entities: people, organisations, projects, concepts.
-- context_tags drive entity resolution: overlap >= 2 tags -> merge.
-- aliases accumulates alternative names seen across sessions/documents.
-- Note: if upgrading from an earlier schema, apply:
--   ALTER TABLE entities ADD COLUMN IF NOT EXISTS user_id TEXT NOT NULL DEFAULT 'default';
-- extraction_quality: heuristic score [0,1] assigned at extraction time (NULL = pre-Pass-1 rows).
-- is_staged: TRUE = entity is quarantined; excluded from graph projection and queries.
CREATE TABLE IF NOT EXISTS entities (
    entity_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name               TEXT NOT NULL,
    entity_type        TEXT NOT NULL,        -- PERSON | ORG | PROJECT | CONCEPT | FILE
    aliases            TEXT[] NOT NULL DEFAULT '{}',
    context_tags       TEXT[] NOT NULL DEFAULT '{}',
    mention_count      INTEGER NOT NULL DEFAULT 1,
    user_id            TEXT NOT NULL DEFAULT 'default',
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    extraction_quality DOUBLE PRECISION,
    is_staged          BOOLEAN NOT NULL DEFAULT FALSE,
    -- Memory-scopes (migration 013): see plan personal_shared_system_memory_scopes.
    scope              TEXT NOT NULL DEFAULT 'personal'
                       CHECK (scope IN ('personal','shared','system')),
    published_from     UUID REFERENCES entities(entity_id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX IF NOT EXISTS entities_published_from_scope_uniq
    ON entities (published_from, scope) WHERE published_from IS NOT NULL;
CREATE INDEX IF NOT EXISTS entities_user_scope_idx ON entities (user_id, scope);

-- Provenance edges: where was each entity seen?
-- relation_type: MENTIONED_IN_SESSION | MENTIONED_IN_DOCUMENT | RELATED_TO
-- evidence_type: SESSION | DOCUMENT
-- evidence_id:   session UUID or file_path
-- evidence_granularity: 'sentence' | 'paragraph' | 'document' (default)
CREATE TABLE IF NOT EXISTS entity_relations (
    id                   SERIAL PRIMARY KEY,
    source_id            UUID NOT NULL REFERENCES entities(entity_id) ON DELETE CASCADE,
    relation_type        TEXT NOT NULL,
    evidence_type        TEXT NOT NULL,
    evidence_id          TEXT NOT NULL,
    user_id              TEXT NOT NULL DEFAULT 'default',
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    evidence_granularity TEXT NOT NULL DEFAULT 'document'
);

-- Per-evidence dedup contract: every (source_id, evidence_id, relation_type)
-- triple per user is a single durable claim, not a re-ingest-frequency
-- multiplier. Writer (services/entities.py) uses ON CONFLICT DO NOTHING.
-- Restore path (routes/admin.py) uses generic ON CONFLICT DO NOTHING (no
-- inference target) and is forward-compatible with this index automatically.
-- See postgres/migrations/012-entity-relations-evidence-uniq.sql.
CREATE UNIQUE INDEX IF NOT EXISTS entity_relations_evidence_uniq
    ON entity_relations (source_id, evidence_id, relation_type, user_id);

-- Partial index: fast lookup of staged entities per user (reconcile + promotion queries).
CREATE INDEX IF NOT EXISTS idx_entities_staged ON entities (user_id, is_staged) WHERE is_staged = TRUE;

-- Ambiguous entity merge candidates flagged for manual review.
-- Inspect and resolve via GET /review-queue.
CREATE TABLE IF NOT EXISTS review_queue (
    id              SERIAL PRIMARY KEY,
    candidate_a_id  UUID REFERENCES entities(entity_id) ON DELETE CASCADE,
    candidate_b_id  UUID REFERENCES entities(entity_id) ON DELETE CASCADE,
    reason          TEXT NOT NULL,
    user_id         TEXT NOT NULL DEFAULT 'default',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Memory-scopes (migration 013): forward-compatibility scaffolding only.
    -- No v1 writer ever produces shared/system rows on review_queue / action_log
    -- / audit_log; reads go through admin-bypass per §8 of the plan. If a future
    -- chunk activates household-visible review, it must add `published_from` and
    -- a publish path consistent with §7.
    scope           TEXT NOT NULL DEFAULT 'personal'
                    CHECK (scope IN ('personal','shared','system'))
);
CREATE INDEX IF NOT EXISTS review_queue_user_scope_idx ON review_queue (user_id, scope);

-- Ask/Do permission model: per-connector, per-action-type enforcement.
-- Every MCP connector starts in ASK mode. DO mode is explicitly enabled
-- per connector. routine_do tracks approval counts for Ask/Do++ elevation.
CREATE TABLE IF NOT EXISTS connector_permissions (
    id              SERIAL PRIMARY KEY,
    connector       TEXT NOT NULL,         -- e.g. 'filesystem-mcp', 'email-mcp'
    mode            TEXT NOT NULL DEFAULT 'ASK',  -- ASK | DO
    user_id         TEXT NOT NULL DEFAULT 'default',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(connector)
);

-- Ask/Do++ routine automation: tracks per-action-type approval history.
-- When approval_count reaches threshold (default 15) without edits,
-- Lumogis can prompt the user to auto-approve that action type.
-- Used by routine elevation automation when approval threshold is reached.
CREATE TABLE IF NOT EXISTS routine_do_tracking (
    id              SERIAL PRIMARY KEY,
    connector       TEXT NOT NULL,
    action_type     TEXT NOT NULL,         -- e.g. 'reply_email', 'tag_photo'
    approval_count  INTEGER NOT NULL DEFAULT 0,
    edit_count      INTEGER NOT NULL DEFAULT 0,
    auto_approved   BOOLEAN NOT NULL DEFAULT FALSE,
    granted_at      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(connector, action_type)
);

-- Action audit log: every read (ASK) and write (DO) action is logged.
-- Reversible actions store the reverse_action for undo capability.
CREATE TABLE IF NOT EXISTS action_log (
    id              SERIAL PRIMARY KEY,
    connector       TEXT NOT NULL,
    action_type     TEXT NOT NULL,
    mode            TEXT NOT NULL,         -- ASK | DO | ROUTINE_DO
    allowed         BOOLEAN NOT NULL DEFAULT TRUE,
    input_summary   TEXT,
    result_summary  TEXT,
    reverse_action  JSONB,                 -- null if irreversible
    user_id         TEXT NOT NULL DEFAULT 'default',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Memory-scopes (migration 013): forward-compat scaffolding (see review_queue note).
    scope           TEXT NOT NULL DEFAULT 'personal'
                    CHECK (scope IN ('personal','shared','system'))
);
CREATE INDEX IF NOT EXISTS action_log_user_scope_idx ON action_log (user_id, scope);

-- ==========================================================================
-- Signal infrastructure
-- ==========================================================================

-- Source registry: RSS feeds, monitored pages, CalDAV calendars.
-- source_type: rss | page | playwright | caldav
-- extraction_method: feedparser | trafilatura | playwright | caldav
CREATE TABLE IF NOT EXISTS sources (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                 TEXT NOT NULL DEFAULT 'default',
    name                    TEXT NOT NULL,
    source_type             TEXT NOT NULL DEFAULT 'rss',
    url                     TEXT NOT NULL,
    category                TEXT NOT NULL DEFAULT 'general',
    active                  BOOLEAN NOT NULL DEFAULT TRUE,
    poll_interval           INTEGER NOT NULL DEFAULT 3600,  -- seconds
    extraction_method       TEXT NOT NULL DEFAULT 'feedparser',
    css_selector_override   TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_polled_at          TIMESTAMPTZ,
    last_signal_at          TIMESTAMPTZ,
    UNIQUE(user_id, url)
);

-- Processed signals: structured + queryable. Postgres is the source of truth.
-- Qdrant signals collection holds embedded summaries for semantic dedup only.
-- raw_content is NOT stored here — it is transient (LLM processing only).
CREATE TABLE IF NOT EXISTS signals (
    signal_id       UUID PRIMARY KEY,
    user_id         TEXT NOT NULL DEFAULT 'default',
    source_id       TEXT NOT NULL,   -- references sources.id or '__system__'
    title           TEXT NOT NULL,
    url             TEXT NOT NULL DEFAULT '',
    published_at    TIMESTAMPTZ,
    content_summary TEXT NOT NULL DEFAULT '',
    entities        JSONB NOT NULL DEFAULT '[]',
    topics          JSONB NOT NULL DEFAULT '[]',
    importance_score FLOAT NOT NULL DEFAULT 0.0,
    relevance_score  FLOAT NOT NULL DEFAULT 0.0,
    notified        BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Memory-scopes (migration 013): see plan personal_shared_system_memory_scopes.
    -- `source_url` / `source_label` denormalize sources(url, name) so shared/system
    -- signals are renderable without joining `sources` (which is intentionally NOT
    -- in the scope-bearing set per §2.12 of the plan).
    scope           TEXT NOT NULL DEFAULT 'personal'
                    CHECK (scope IN ('personal','shared','system')),
    published_from  UUID REFERENCES signals(signal_id) ON DELETE CASCADE,
    source_url      TEXT,
    source_label    TEXT
);
CREATE INDEX IF NOT EXISTS idx_signals_user_created ON signals (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_signals_relevance ON signals (user_id, relevance_score DESC);
CREATE INDEX IF NOT EXISTS idx_signals_url ON signals (url);
CREATE UNIQUE INDEX IF NOT EXISTS signals_published_from_scope_uniq
    ON signals (published_from, scope) WHERE published_from IS NOT NULL;
CREATE INDEX IF NOT EXISTS signals_user_scope_idx ON signals (user_id, scope);

-- Relevance profiles: per-user topic/location/entity/keyword tracking.
CREATE TABLE IF NOT EXISTS relevance_profiles (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             TEXT NOT NULL DEFAULT 'default',
    tracked_locations   JSONB NOT NULL DEFAULT '[]',
    tracked_topics      JSONB NOT NULL DEFAULT '[]',
    tracked_entities    JSONB NOT NULL DEFAULT '[]',
    tracked_keywords    JSONB NOT NULL DEFAULT '[]',
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(user_id)
);

-- Feedback log: explicit (thumbs up/down) and implicit (opens, dismissals).
-- positive NULL means implicit feedback; positive TRUE/FALSE means explicit.
CREATE TABLE IF NOT EXISTS feedback_log (
    id          SERIAL PRIMARY KEY,
    user_id     TEXT NOT NULL DEFAULT 'default',
    item_type   TEXT NOT NULL,          -- signal | entity | briefing_item
    item_id     TEXT NOT NULL,
    positive    BOOLEAN,                -- NULL for implicit events
    event_type  TEXT,                   -- item_opened | signal_dismissed | briefing_item_expanded
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_feedback_item ON feedback_log (item_type, item_id);

-- ==========================================================================
-- Pre-configured example sources (opt-in by region — uncomment to activate)
-- User copies relevant INSERT statements and runs them, or uncomments + rebuilds.
-- ==========================================================================

-- ---- Australia (10 sources) -----------------------------------------------
-- INSERT INTO sources (name, source_type, url, category, poll_interval, extraction_method) VALUES
--   ('RBA – Reserve Bank of Australia',    'rss',  'https://www.rba.gov.au/rss/',                               'finance',    3600,  'feedparser'),
--   ('ABS – Australian Bureau of Statistics', 'rss', 'https://www.abs.gov.au/rss.xml',                         'statistics', 7200,  'feedparser'),
--   ('Queensland Planning',                'page', 'https://www.planning.qld.gov.au/planning-framework/news',  'planning',   14400, 'trafilatura'),
--   ('Domain – Property News',             'rss',  'https://www.domain.com.au/news/feed/',                     'property',   3600,  'feedparser'),
--   ('CoreLogic Research',                 'rss',  'https://www.corelogic.com.au/news-research/feed',          'property',   7200,  'feedparser'),
--   ('ABC News Australia',                 'rss',  'https://www.abc.net.au/news/feed/51120/rss.xml',           'news',       1800,  'feedparser'),
--   ('AFR – Australian Financial Review',  'rss',  'https://www.afr.com/rss/latest',                          'finance',    3600,  'feedparser'),
--   ('AWS Blog',                           'rss',  'https://aws.amazon.com/blogs/aws/feed/',                   'tech',       7200,  'feedparser'),
--   ('REIA – Real Estate Institute',       'rss',  'https://reia.asn.au/feed/',                               'property',   14400, 'feedparser'),
--   ('CoreLogic Daily',                    'rss',  'https://www.corelogic.com.au/news-research/daily-home-value-index/feed', 'property', 86400, 'feedparser');

-- ---- EU (5 sources) --------------------------------------------------------
-- INSERT INTO sources (name, source_type, url, category, poll_interval, extraction_method) VALUES
--   ('ECB – European Central Bank',        'rss',  'https://www.ecb.europa.eu/rss/press.html',                 'finance',    7200,  'feedparser'),
--   ('Eurostat News',                      'rss',  'https://ec.europa.eu/eurostat/en/rss-feed',               'statistics', 7200,  'feedparser'),
--   ('Reuters EU',                         'rss',  'https://feeds.reuters.com/reuters/EuropeanNewsHeadlines',  'news',       1800,  'feedparser'),
--   ('EuroNews',                           'rss',  'https://www.euronews.com/rss?format=mrss&level=theme&name=news', 'news', 1800, 'feedparser'),
--   ('GDPR Enforcement Tracker',           'page', 'https://www.enforcementtracker.com/',                     'legal',      86400, 'trafilatura');

-- ---- Global / Tech (5 sources) ---------------------------------------------
-- INSERT INTO sources (name, source_type, url, category, poll_interval, extraction_method) VALUES
--   ('Hacker News',                        'rss',  'https://news.ycombinator.com/rss',                        'tech',       1800,  'feedparser'),
--   ('arXiv CS',                           'rss',  'https://rss.arxiv.org/rss/cs',                            'research',   14400, 'feedparser'),
--   ('GitHub Trending',                    'page', 'https://github.com/trending',                              'tech',       86400, 'trafilatura'),
--   ('TechCrunch',                         'rss',  'https://techcrunch.com/feed/',                            'tech',       3600,  'feedparser'),
--   ('The Verge',                          'rss',  'https://www.theverge.com/rss/index.xml',                  'tech',       3600,  'feedparser');

-- ---- US / Global (5 sources) ------------------------------------------------
-- INSERT INTO sources (name, source_type, url, category, poll_interval, extraction_method) VALUES
--   ('WSJ World News',                     'rss',  'https://feeds.a.dj.com/rss/RSSWorldNews.xml',             'news',       3600,  'feedparser'),
--   ('NPR News',                           'rss',  'https://feeds.npr.org/1001/rss.xml',                      'news',       3600,  'feedparser'),
--   ('SEC EDGAR Filings',                  'rss',  'https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K&dateb=&owner=include&count=40&output=atom', 'finance', 3600, 'feedparser'),
--   ('GitHub Blog',                        'rss',  'https://github.blog/feed/',                               'tech',       7200,  'feedparser'),
--   ('arXiv AI',                           'rss',  'https://rss.arxiv.org/rss/cs.AI',                         'research',   14400, 'feedparser');

-- ==========================================================================
-- Actions foundation
-- ==========================================================================

-- Audit log for action executions. Distinct from action_log (permission checks).
-- reverse_token: UUID returned in ActionResult; used by POST /audit/{token}/reverse.
-- reverse_action: JSONB descriptor for undo — {action_name, input}.
-- reversed_at: set when reversal completes successfully.
CREATE TABLE IF NOT EXISTS audit_log (
    id              SERIAL PRIMARY KEY,
    user_id         TEXT NOT NULL DEFAULT 'default',
    action_name     TEXT NOT NULL,
    connector       TEXT NOT NULL,
    mode            TEXT NOT NULL,          -- ASK | DO
    input_summary   TEXT,
    result_summary  TEXT,
    reverse_token   UUID,                   -- NULL if not reversible
    reverse_action  JSONB,                  -- NULL if not reversible
    executed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reversed_at     TIMESTAMPTZ,            -- set on successful reversal
    -- Memory-scopes (migration 013): forward-compat scaffolding (see review_queue note).
    scope           TEXT NOT NULL DEFAULT 'personal'
                    CHECK (scope IN ('personal','shared','system'))
);
CREATE INDEX IF NOT EXISTS idx_audit_user_time ON audit_log (user_id, executed_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_reverse_token ON audit_log (reverse_token) WHERE reverse_token IS NOT NULL;
CREATE INDEX IF NOT EXISTS audit_log_user_scope_idx ON audit_log (user_id, scope);

-- Scheduled routines registry. schedule_cron follows APScheduler CronTrigger format.
-- steps: JSONB array of {action_name, input} objects.
-- requires_approval: weekly_review needs explicit approve; inbox_digest is auto-approved.
CREATE TABLE IF NOT EXISTS routines (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             TEXT NOT NULL DEFAULT 'default',
    name                TEXT NOT NULL,
    description         TEXT NOT NULL DEFAULT '',
    schedule_cron       TEXT NOT NULL,      -- "min hour dom mon dow"
    steps               JSONB NOT NULL DEFAULT '[]',
    requires_approval   BOOLEAN NOT NULL DEFAULT TRUE,
    approved_at         TIMESTAMPTZ,        -- NULL = not approved
    last_run_at         TIMESTAMPTZ,
    enabled             BOOLEAN NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(name, user_id)
);

-- Dashboard settings overrides (filesystem_root, API key env vars, default_model)
CREATE TABLE IF NOT EXISTS app_settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ==========================================================================
-- KG Quality Pipeline — Pass 2
-- ==========================================================================

-- Constraint violations: data quality rule violations detected at ingestion time.
-- severity: CRITICAL = data integrity issue; WARNING = quality concern; INFO = completeness hint.
-- resolved_at: set when the condition no longer holds (auto-resolved on next constraint check).
CREATE TABLE IF NOT EXISTS constraint_violations (
    violation_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         TEXT NOT NULL DEFAULT 'default',
    entity_id       UUID REFERENCES entities(entity_id) ON DELETE CASCADE,
    rule_name       TEXT NOT NULL,
    severity        TEXT NOT NULL CHECK (severity IN ('CRITICAL', 'WARNING', 'INFO')),
    detail          TEXT,
    detected_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_constraint_violations_open
    ON constraint_violations (user_id, severity, detected_at DESC)
    WHERE resolved_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_constraint_violations_entity
    ON constraint_violations (entity_id)
    WHERE resolved_at IS NULL;

-- Pass 3: PPMI-based edge quality scores between co-occurring entity pairs.
-- entity_id_a < entity_id_b enforces canonical direction, matching writer.py ordering.
-- NOT in backup tables — recomputable from entity_relations by the weekly quality job.
CREATE TABLE IF NOT EXISTS edge_scores (
    id                BIGSERIAL PRIMARY KEY,
    user_id           TEXT NOT NULL DEFAULT 'default',
    entity_id_a       UUID NOT NULL REFERENCES entities(entity_id) ON DELETE CASCADE,
    entity_id_b       UUID NOT NULL REFERENCES entities(entity_id) ON DELETE CASCADE,
    ppmi_score        DOUBLE PRECISION,
    edge_quality      DOUBLE PRECISION,
    decay_factor      DOUBLE PRECISION,
    last_evidence_at  TIMESTAMPTZ,
    computed_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, entity_id_a, entity_id_b),
    CHECK (entity_id_a < entity_id_b)
);

CREATE INDEX IF NOT EXISTS idx_edge_scores_user ON edge_scores (user_id);

-- Pass 4a: operator-confirmed distinct entity pairs (suppresses future merge candidates)
CREATE TABLE IF NOT EXISTS known_distinct_entity_pairs (
    user_id         TEXT NOT NULL DEFAULT 'default',
    entity_id_a     UUID NOT NULL REFERENCES entities(entity_id) ON DELETE CASCADE,
    entity_id_b     UUID NOT NULL REFERENCES entities(entity_id) ON DELETE CASCADE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, entity_id_a, entity_id_b),
    CHECK (entity_id_a < entity_id_b)
);

-- Pass 4a: audit trail for all operator review queue decisions
CREATE TABLE IF NOT EXISTS review_decisions (
    decision_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         TEXT NOT NULL DEFAULT 'default',
    item_type       TEXT NOT NULL,
    item_id         TEXT NOT NULL,
    action          TEXT NOT NULL,
    payload         JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_review_decisions_user_time
    ON review_decisions (user_id, created_at DESC);

-- Pass 4b: lifecycle record for each automated Splink deduplication job
CREATE TABLE IF NOT EXISTS deduplication_runs (
    run_id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             TEXT NOT NULL DEFAULT 'default',
    started_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at         TIMESTAMPTZ,
    candidate_count     INT,
    auto_merged         INT,
    queued_for_review   INT,
    known_distinct      INT,
    error_message       TEXT
);

-- Pass 4b: Splink-scored candidate pairs; rows >= 0.50 enter review_queue for human decision
CREATE TABLE IF NOT EXISTS dedup_candidates (
    id                  BIGSERIAL PRIMARY KEY,
    run_id              UUID NOT NULL REFERENCES deduplication_runs(run_id) ON DELETE CASCADE,
    user_id             TEXT NOT NULL DEFAULT 'default',
    entity_id_a         UUID NOT NULL REFERENCES entities(entity_id) ON DELETE CASCADE,
    entity_id_b         UUID NOT NULL REFERENCES entities(entity_id) ON DELETE CASCADE,
    match_probability   DOUBLE PRECISION NOT NULL,
    features            JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (entity_id_a < entity_id_b)
);

CREATE INDEX IF NOT EXISTS idx_dedup_candidates_run
    ON dedup_candidates (run_id, match_probability DESC);

CREATE INDEX IF NOT EXISTS idx_dedup_candidates_user
    ON dedup_candidates (user_id, match_probability DESC)
    WHERE match_probability >= 0.5;

-- Hot-reload KG settings: tunable graph/quality parameters with DB-first lookup.
-- Config getters check this table first (TTL-cached) and fall back to env vars.
-- This table IS in _BACKUP_TABLES — settings are user data and must survive restore.
CREATE TABLE IF NOT EXISTS kg_settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE OR REPLACE FUNCTION update_kg_settings_updated_at()
RETURNS TRIGGER AS $$
BEGIN NEW.updated_at = NOW(); RETURN NEW; END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE TRIGGER kg_settings_updated_at
    BEFORE UPDATE ON kg_settings
    FOR EACH ROW EXECUTE FUNCTION update_kg_settings_updated_at();
