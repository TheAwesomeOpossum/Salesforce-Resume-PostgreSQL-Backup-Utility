-- ============================================================
-- CDC Replay State
--
-- Persists the last-seen CometD replay ID for the Salesforce
-- Change Data Capture streaming channel.
--
-- cdc_listener.py reads this on startup to resume from exactly
-- where it left off, replaying any missed events (Salesforce
-- retains CDC events for 72 hours).
--
-- One row per channel; updated after every successfully
-- processed CDC event.
-- ============================================================

CREATE TABLE IF NOT EXISTS cdc_replay_state (
    channel     VARCHAR(255)             PRIMARY KEY,
    replay_id   BIGINT                   NOT NULL,
    updated_at  TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
