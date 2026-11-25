-- Adds the Company__c mirror column so write-back can set the client company
-- (e.g. "Example Company A") on Work_Experience records created from project docs.
ALTER TABLE experience ADD COLUMN IF NOT EXISTS company VARCHAR(255);
