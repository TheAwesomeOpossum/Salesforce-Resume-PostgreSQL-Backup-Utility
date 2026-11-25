-- ============================================================
-- Job Search Config (Job_Search_Config__c)
--
-- Local mirror of the Salesforce Job_Search_Config__c object.
-- Kept in sync via CDC (cdc_listener.py) using the standard
-- upsert_records() pattern — conflicts resolved on salesforce_id.
--
-- This table is the backup-side read target for the JobFinder
-- scheduler: it can query here instead of hitting the SF API
-- on every startup, giving resilience when SF is unreachable.
-- ============================================================

CREATE TABLE IF NOT EXISTS job_search_config (
    id                BIGSERIAL PRIMARY KEY,
    salesforce_id     VARCHAR(18)              NOT NULL UNIQUE,  -- SF: Id
    ownerid           VARCHAR(18),                               -- SF: OwnerId
    name              VARCHAR(255),                              -- SF: Name
    keywords          TEXT,                                      -- SF: Keywords__c
    required_skills   TEXT,                                      -- SF: Required_Skills__c (semicolon-delimited)
    bonus_skills      TEXT,                                      -- SF: Bonus_Skills__c (semicolon-delimited)
    daily_limit       INTEGER,                                   -- SF: Daily_Limit__c
    min_annual_salary NUMERIC(12, 0),                           -- SF: Min_Annual_Salary__c
    schedule_hour     INTEGER,                                   -- SF: Schedule_Hour__c (0–23 UTC)
    schedule_minute   INTEGER,                                   -- SF: Schedule_Minute__c (0–59)
    days_back         INTEGER,                                   -- SF: Days_Back__c
    is_active         BOOLEAN,                                   -- SF: Is_Active__c
    last_run_at       TIMESTAMP WITH TIME ZONE,                  -- SF: Last_Run_At__c
    last_run_summary  TEXT,                                      -- SF: Last_Run_Summary__c (JSON)
    isdeleted         BOOLEAN          DEFAULT FALSE,
    createddate       TIMESTAMP WITH TIME ZONE,
    createdbyid       VARCHAR(18),
    lastmodifieddate  TIMESTAMP WITH TIME ZONE,
    lastmodifiedbyid  VARCHAR(18),
    systemmodstamp    TIMESTAMP WITH TIME ZONE,
    last_synced       TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    sync_status       VARCHAR(50)              DEFAULT 'pending'
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_job_search_config_sf_id    ON job_search_config(salesforce_id);
CREATE INDEX        IF NOT EXISTS idx_job_search_config_sync      ON job_search_config(sync_status, last_synced);
CREATE INDEX        IF NOT EXISTS idx_job_search_config_is_active ON job_search_config(is_active);
