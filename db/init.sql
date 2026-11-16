-- Ініціалізація бази даних для «Мовного фіду»
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS video_actions (
    action_id BIGSERIAL PRIMARY KEY,
    "timestamp" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    "user" TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('suggested', 'hid')),
    video_url TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS video_actions_user_idx ON video_actions ("user", "timestamp" DESC);
CREATE INDEX IF NOT EXISTS video_actions_action_idx ON video_actions (action, "timestamp" DESC);
