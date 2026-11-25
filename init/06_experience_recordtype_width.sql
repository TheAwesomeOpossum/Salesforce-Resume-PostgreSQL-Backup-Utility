-- document_project.py stages a RecordType DeveloperName (e.g. 'Personal_Experience', 19 chars)
-- in recordtypeid; write_back resolves it to an Id. VARCHAR(18) truncated it.
ALTER TABLE experience ALTER COLUMN recordtypeid TYPE VARCHAR(80);
