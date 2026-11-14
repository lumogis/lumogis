-- LUM-649: per-user, feedback-derived weight deltas layered additively on top of
-- the explicit tracked_* lists in relevance_profiles. Additive-only; NOT NULL
-- DEFAULT '{}' so every existing row (and every profile before its first
-- feedback) behaves exactly as before this migration.
--
-- learned_category_weights is keyed by the signal's classified `category` slug
-- (e.g. "newsletter", "insurance_renewal" — see LUM-629/630/631 taxonomy), NOT
-- free-text keyword extraction. This is deliberately distinct from the existing
-- `tracked_keywords` explicit substring-match list, which is untouched by this
-- migration and this chunk.
ALTER TABLE relevance_profiles
    ADD COLUMN IF NOT EXISTS learned_topic_weights JSONB NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS learned_entity_weights JSONB NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS learned_category_weights JSONB NOT NULL DEFAULT '{}';

-- Rollback:
-- ALTER TABLE relevance_profiles
--     DROP COLUMN learned_topic_weights,
--     DROP COLUMN learned_entity_weights,
--     DROP COLUMN learned_category_weights;
