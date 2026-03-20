-- ============================================================
-- Migration 04: Experience write-back staging support
-- Applied: 2026-03-19
-- Purpose: Allow staged Experience records (pending_push) that
--          don't yet have a Salesforce ID. Add github_url and
--          error_message columns for write-back lifecycle.
-- ============================================================

-- Allow new records staged from Claude (no SF ID yet)
ALTER TABLE experience ALTER COLUMN salesforce_id DROP NOT NULL;

-- Add GitHub URL for personal project links
ALTER TABLE experience ADD COLUMN IF NOT EXISTS github_url VARCHAR(1024);
CREATE INDEX IF NOT EXISTS idx_experience_github ON experience(github_url)
    WHERE github_url IS NOT NULL;

-- Add error_message for write-back failure tracking
ALTER TABLE experience ADD COLUMN IF NOT EXISTS error_message TEXT;
