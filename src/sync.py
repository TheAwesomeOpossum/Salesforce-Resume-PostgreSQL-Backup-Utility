"""
Main entry point for the Salesforce → PostgreSQL sync service.

Phase 1: Full initial pull of all configured objects.

Sync order:
  1. Account           — filtered to Type IN ('Resume', 'Job Application')
  2. Contact           — filtered to AccountId IN (synced account IDs)
  3. Custom objects    — all records, no filter
  4. Interview__c      — all records
  5. InterviewContact__c — all records
"""

import logging
import sys
import time
from src import config
from src import salesforce as sf_client
from src import database as db

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("/app/logs/sync.log"),
    ],
)
logger = logging.getLogger(__name__)

# Standard objects with special handling (filtered queries)
FILTERED_OBJECTS = {"Account", "Contact"}

# Custom objects — all records pulled without filtering
CUSTOM_OBJECTS = [
    "AboutMe__c",
    "AnthropicSettings__c",
    "Award__c",
    "CoverLetter__c",
    "Education__c",
    "Experience__c",
    "Interview__c",
    "InterviewContact__c",
    "Job__c",
    "JobApplication__c",
    "Resume__c",
    "Resume_Award__c",
    "Resume_Education__c",
    "Resume_Experience__c",
    "Resume_Strength__c",
    "Strength__c",
]


def run_full_sync():
    """Pull all records for every configured object and upsert into PostgreSQL."""
    logger.info("=== Starting full Salesforce → PostgreSQL sync ===")

    sf = sf_client.connect()
    conn = db.connect()
    total_synced = 0

    # ── Step 1: Accounts (filtered by Type) ──────────────────────────────────
    logger.info("--- Step 1/3: Syncing Account (Type = Resume | Job Application) ---")
    try:
        account_fields = sf_client.get_queryable_fields(sf, "Account")
        account_records = sf_client.query_accounts_filtered(sf, account_fields)
        count = db.upsert_records(conn, "account", account_records)
        total_synced += count

        # Collect the Salesforce IDs of synced accounts for the Contact filter
        synced_account_ids = [r["Id"] for r in account_records]
    except Exception as exc:
        logger.error("Failed to sync Account: %s", exc, exc_info=True)
        conn.rollback()
        synced_account_ids = []

    # ── Step 2: Contacts (related to synced accounts only) ───────────────────
    logger.info("--- Step 2/3: Syncing Contact (related to filtered accounts) ---")
    try:
        contact_fields = sf_client.get_queryable_fields(sf, "Contact")
        contact_records = sf_client.query_contacts_for_accounts(sf, contact_fields, synced_account_ids)
        count = db.upsert_records(conn, "contact", contact_records)
        total_synced += count
    except Exception as exc:
        logger.error("Failed to sync Contact: %s", exc, exc_info=True)
        conn.rollback()

    # ── Step 3: Custom objects (all records) ─────────────────────────────────
    logger.info("--- Step 3/3: Syncing %d custom objects ---", len(CUSTOM_OBJECTS))
    for sobject_name in CUSTOM_OBJECTS:
        table_name = sobject_name.lower().replace("__c", "")
        logger.info("  Syncing %s → table '%s'", sobject_name, table_name)

        if not db.table_exists(conn, table_name):
            logger.warning(
                "Table '%s' not found in PostgreSQL. Skipping %s. "
                "Ensure init/02_custom_schema.sql was applied.",
                table_name,
                sobject_name,
            )
            continue

        try:
            fields = sf_client.get_queryable_fields(sf, sobject_name)
            records = sf_client.query_records(sf, sobject_name, fields)
            count = db.upsert_records(conn, table_name, records)
            total_synced += count
        except Exception as exc:
            logger.error("Failed to sync %s: %s", sobject_name, exc, exc_info=True)
            conn.rollback()

    logger.info("=== Sync complete. Total records synced: %d ===", total_synced)
    conn.close()


if __name__ == "__main__":
    while True:
        run_full_sync()
        logger.info("Sleeping %d seconds until next sync...", config.SYNC_INTERVAL_SECONDS)
        time.sleep(config.SYNC_INTERVAL_SECONDS)
