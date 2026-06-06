-- Migration 027: web chat transcript persistence (LUM-162 slice 2)

CREATE TABLE IF NOT EXISTS web_conversations (
    conversation_id   UUID PRIMARY KEY,
    user_id           TEXT NOT NULL DEFAULT 'default',
    title             TEXT NOT NULL DEFAULT '',
    model             TEXT NOT NULL DEFAULT '',
    scope             TEXT NOT NULL DEFAULT 'personal',
    message_count     INTEGER NOT NULL DEFAULT 0,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_web_conversations_user_updated
    ON web_conversations (user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS web_messages (
    message_id        UUID PRIMARY KEY,
    conversation_id   UUID NOT NULL REFERENCES web_conversations(conversation_id) ON DELETE CASCADE,
    user_id           TEXT NOT NULL DEFAULT 'default',
    role              TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content           TEXT NOT NULL,
    model             TEXT,
    token_count       INTEGER,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_web_messages_conversation_created
    ON web_messages (conversation_id, created_at ASC);

CREATE OR REPLACE TRIGGER set_updated_at BEFORE UPDATE ON web_conversations
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE OR REPLACE FUNCTION web_messages_bump_count() RETURNS TRIGGER AS $$
BEGIN
    UPDATE web_conversations SET message_count = message_count + 1, updated_at = NOW()
    WHERE conversation_id = NEW.conversation_id;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE TRIGGER web_messages_count AFTER INSERT ON web_messages
    FOR EACH ROW EXECUTE FUNCTION web_messages_bump_count();
