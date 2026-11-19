-- Migration 082: per-member name rules (LUM-967).
--
-- A member's correction of what kind an entry is, keyed by the entry's
-- canonical name (normalised: whitespace collapsed, lower-cased) and the kind
-- extraction read it as. store_entities rewrites the kind before storing
-- (extraction path only). Keyed by name, not entity id, so a rule survives the
-- entry being renamed, merged or deleted. source_entity_id is informational
-- only (no FK: the rule deliberately outlives the entry, and a FK would order
-- the per-user import). LUM-966 widens entity_name_rules_action to 'block'.
-- Invariant kept by services/entity_name_rules.record_retype: for one
-- (user_id, name_norm) no rule's to_type is another rule's from_type, so one
-- lookup always gives the final kind (no chains, no cycles).

CREATE TABLE IF NOT EXISTS entity_name_rules (
    user_id          TEXT        NOT NULL DEFAULT 'default',
    name_norm        TEXT        NOT NULL,
    from_type        TEXT        NOT NULL,
    action           TEXT        NOT NULL,
    to_type          TEXT,
    source_entity_id UUID,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, name_norm, from_type),
    CONSTRAINT entity_name_rules_name_nonempty CHECK (name_norm <> ''),
    CONSTRAINT entity_name_rules_action CHECK (action IN ('retype')),
    CONSTRAINT entity_name_rules_retype_target CHECK ((action = 'retype') = (to_type IS NOT NULL)),
    CONSTRAINT entity_name_rules_no_self CHECK (to_type IS NULL OR to_type <> from_type)
);
