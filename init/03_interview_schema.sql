-- ============================================================
-- Interview table additions for Obsidian vault sync
--
-- The `interview` table was created in 02_custom_schema.sql.
-- This file adds the columns needed by the obsidian-sync service
-- using IF NOT EXISTS guards so it is safe to re-run.
-- ============================================================

-- SF: Study_Guide__c — long-form markdown study guide content
ALTER TABLE interview
    ADD COLUMN IF NOT EXISTS study_guide TEXT;

-- Path to the corresponding .md file in the Obsidian vault
ALTER TABLE interview
    ADD COLUMN IF NOT EXISTS vault_path VARCHAR(1024);

-- Timestamp of the last successful vault↔DB sync for this record
ALTER TABLE interview
    ADD COLUMN IF NOT EXISTS last_synced_at TIMESTAMP WITH TIME ZONE;

-- Index on vault_path so the sync service can look up records by file path quickly
CREATE INDEX IF NOT EXISTS idx_interview_vault_path ON interview(vault_path);
