-- Migration 043: Cloud LLM privacy mode (LUM-194)
-- Per-user further restriction + instance policy in app_settings.

CREATE TABLE IF NOT EXISTS privacy_user_settings (
    user_id      TEXT NOT NULL DEFAULT 'default',
    restriction  TEXT NOT NULL DEFAULT 'inherit'
                 CHECK (restriction IN ('inherit', 'local_only')),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id)
);

-- Upgrade path: seed allow_cloud only when household already demonstrated cloud usage.
INSERT INTO app_settings (key, value)
SELECT 'privacy_mode', 'allow_cloud'
WHERE NOT EXISTS (SELECT 1 FROM app_settings WHERE key = 'privacy_mode')
  AND (
    EXISTS (
      SELECT 1 FROM user_connector_credentials ucc
      WHERE ucc.connector LIKE 'llm_%'
    )
    OR EXISTS (
      SELECT 1 FROM app_settings s
      WHERE s.key IN (
        'ANTHROPIC_API_KEY', 'OPENAI_API_KEY', 'XAI_API_KEY',
        'PERPLEXITY_API_KEY', 'GEMINI_API_KEY', 'MISTRAL_API_KEY'
      ) AND COALESCE(TRIM(s.value), '') <> ''
    )
    OR EXISTS (
      SELECT 1 FROM app_settings s
      WHERE s.key LIKE 'optional_%'
        AND LOWER(TRIM(s.value)) IN ('true', '1', 'yes')
    )
    OR EXISTS (
      SELECT 1 FROM app_settings s
      WHERE s.key = 'default_model'
        AND COALESCE(TRIM(s.value), '') NOT IN ('', 'llama')
    )
  );

-- Rollback: DROP TABLE privacy_user_settings; DELETE FROM app_settings WHERE key IN ('privacy_mode', 'privacy_mode_locked');
