-- Lumogis Postgres schema
-- Runs once on first container start (docker-entrypoint-initdb.d/).
-- Add new tables here; never alter existing columns without a migration.
-- For existing installations: see postgres/migrations/ for ALTER TABLE scripts.

-- Tracks every ingested document: path, content hash, chunk count, OCR flag.
-- file_hash enables re-ingest skip: if hash unchanged, skip chunking + embedding.
CREATE TABLE IF NOT EXISTS file_index (
    id          SERIAL PRIMARY KEY,
    file_path   TEXT UNIQUE NOT NULL,
    file_hash   TEXT NOT NULL,
    file_type   TEXT NOT NULL,
    chunk_count INTEGER NOT NULL DEFAULT 0,
    ocr_used    BOOLEAN NOT NULL DEFAULT FALSE,
    user_id     TEXT NOT NULL DEFAULT 'default',
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Extracted entities: people, organisations, projects, concepts.
-- context_tags drive entity resolution: overlap >= 2 tags -> merge.
-- aliases accumulates alternative names seen across sessions/documents.
-- Note: if upgrading from an earlier schema, apply:
--   ALTER TABLE entities ADD COLUMN IF NOT EXISTS user_id TEXT NOT NULL DEFAULT 'default';
CREATE TABLE IF NOT EXISTS entities (
    entity_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name         TEXT NOT NULL,
    entity_type  TEXT NOT NULL,        -- PERSON | ORG | PROJECT | CONCEPT | FILE
    aliases      TEXT[] NOT NULL DEFAULT '{}',
    context_tags TEXT[] NOT NULL DEFAULT '{}',
    mention_count INTEGER NOT NULL DEFAULT 1,
    user_id      TEXT NOT NULL DEFAULT 'default',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Provenance edges: where was each entity seen?
-- relation_type: MENTIONED_IN_SESSION | MENTIONED_IN_DOCUMENT | RELATED_TO
-- evidence_type: SESSION | DOCUMENT
-- evidence_id:   session UUID or file_path
CREATE TABLE IF NOT EXISTS entity_relations (
    id            SERIAL PRIMARY KEY,
    source_id     UUID NOT NULL REFERENCES entities(entity_id) ON DELETE CASCADE,
    relation_type TEXT NOT NULL,
    evidence_type TEXT NOT NULL,
    evidence_id   TEXT NOT NULL,
    user_id       TEXT NOT NULL DEFAULT 'default',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Ambiguous entity merge candidates flagged for manual review.
-- Inspect and resolve via GET /review-queue.
CREATE TABLE IF NOT EXISTS review_queue (
    id              SERIAL PRIMARY KEY,
    candidate_a_id  UUID REFERENCES entities(entity_id) ON DELETE CASCADE,
    candidate_b_id  UUID REFERENCES entities(entity_id) ON DELETE CASCADE,
    reason          TEXT NOT NULL,
    user_id         TEXT NOT NULL DEFAULT 'default',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

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
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

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
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_signals_user_created ON signals (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_signals_relevance ON signals (user_id, relevance_score DESC);
CREATE INDEX IF NOT EXISTS idx_signals_url ON signals (url);

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
    reversed_at     TIMESTAMPTZ             -- set on successful reversal
);
CREATE INDEX IF NOT EXISTS idx_audit_user_time ON audit_log (user_id, executed_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_reverse_token ON audit_log (reverse_token) WHERE reverse_token IS NOT NULL;

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
