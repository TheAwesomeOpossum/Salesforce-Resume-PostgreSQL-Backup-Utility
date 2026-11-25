"""
Write-back worker: pushes pending_push Experience records from PostgreSQL to Salesforce.

Usage (standalone):
    python -m src.write_back

Importable for testing:
    from src.write_back import push_pending, get_record_type_id
"""

import logging
import sys
from datetime import date
from typing import Optional

from simple_salesforce import SalesforceExpiredSession

from src import config, database as db, salesforce as sf_client

logger = logging.getLogger(__name__)

# Canonical "Personal Projects" Job record for Awesome Opossum.
# All Personal_Experience records created via write-back link to this Job.
# Documented in context/AwesomeOpossum/objects/Experience__c.md.
PERSONAL_EXPERIENCE_JOB_ID = "a02al000000d197AAA"

# Module-level cache: DeveloperName → Salesforce RecordType ID
_record_type_cache: dict[str, str] = {}


def get_record_type_id(sf, developer_name: str) -> str:
    """
    Resolve a RecordType Id by DeveloperName for Experience__c.
    Result is cached in-process — only queried once per run.
    """
    if developer_name in _record_type_cache:
        return _record_type_cache[developer_name]

    result = sf_client._with_backoff(
        lambda: sf.query(
            f"SELECT Id FROM RecordType "
            f"WHERE SObjectType = 'Experience__c' "
            f"AND DeveloperName = '{developer_name}'"
        )
    )
    records = result.get("records", [])
    if not records:
        raise ValueError(
            f"RecordType '{developer_name}' not found for Experience__c in org. "
            f"Verify the RecordType DeveloperName is correct."
        )

    record_type_id = records[0]["Id"]
    _record_type_cache[developer_name] = record_type_id
    logger.info("Resolved RecordType '%s' → %s", developer_name, record_type_id)
    return record_type_id


def push_pending(sf, conn) -> dict:
    """
    Query all pending_push Experience records from PostgreSQL and push each to Salesforce.

    Returns:
        dict with keys: pushed (int), failed (int), total (int)
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, name, job, recordtypeid, start_date, end_date,
                   description, skills, github_url, salesforce_id, sync_status,
                   company
            FROM experience
            WHERE sync_status IN ('pending_push', 'pending_update')
            ORDER BY id
        """)
        columns = [desc[0] for desc in cur.description]
        rows = cur.fetchall()

    pending = [dict(zip(columns, row)) for row in rows]
    logger.info("Found %d pending_push experience record(s)", len(pending))

    pushed = 0
    failed = 0

    for record in pending:
        try:
            is_update = record["sync_status"] == "pending_update"
            record_type_id = _resolve_record_type(sf, record)
            payload = _build_sf_payload(record, record_type_id, is_update=is_update)
            if is_update:
                sf_id = record["salesforce_id"]
                sf_client._with_backoff(
                    lambda p=payload, sid=sf_id: sf.Experience__c.update(sid, p)
                )
                _update_status(conn, record["id"], sf_id=sf_id, status="synced")
                logger.info("Updated '%s' → Salesforce ID %s", record["name"], sf_id)
            else:
                result = sf_client._with_backoff(
                    lambda p=payload: sf.Experience__c.create(p)
                )
                sf_id = result["id"]
                _update_status(conn, record["id"], sf_id=sf_id, status="synced")
                logger.info("Pushed '%s' → Salesforce ID %s", record["name"], sf_id)
            pushed += 1
        except SalesforceExpiredSession:
            logger.error("Salesforce session expired — aborting write-back. Re-authenticate and retry.")
            raise
        except Exception as exc:
            error_msg = str(exc)[:2000]
            _update_status(conn, record["id"], status="failed", error_message=error_msg)
            logger.error("Failed to push '%s': %s", record["name"], error_msg)
            failed += 1

    summary = {"pushed": pushed, "failed": failed, "total": len(pending)}
    logger.info("Write-back complete: %d pushed, %d failed (of %d total)", pushed, failed, len(pending))
    return summary


def _resolve_record_type(sf, record: dict) -> str:
    """
    The staged row's `recordtypeid` may hold a RecordType DeveloperName (staged by
    document_project.py), a real 012... Id (rows mirrored from Salesforce), or nothing
    (legacy rows → Personal_Experience).
    """
    value = (record.get("recordtypeid") or "").strip()
    if not value:
        return get_record_type_id(sf, "Personal_Experience")
    if value.startswith("012") and len(value) in (15, 18):
        return value
    return get_record_type_id(sf, value)


def _build_sf_payload(record: dict, record_type_id: str, is_update: bool = False) -> dict:
    """Build the Salesforce REST API payload from a PostgreSQL experience row."""
    payload = {
        "Name": record["name"],
        "Start_Date__c": _format_date(record["start_date"]),
        "Skills__c": record["skills"],
    }
    if not is_update:
        payload["Job__c"] = record["job"]
        payload["RecordTypeId"] = record_type_id
    if record.get("end_date"):
        payload["End_Date__c"] = _format_date(record["end_date"])
    if record.get("description"):
        payload["Description__c"] = record["description"]
    if record.get("github_url"):
        payload["GitHub_URL__c"] = record["github_url"]
    # NOTE: Experience__c.Company__c is a formula field (derived from the Job), so it cannot be
    # written. The staged `company` column is kept for reporting; the client company is carried
    # in the description instead (see import_project_docs.build_payload).
    return payload


def _format_date(d) -> Optional[str]:
    """Format a Python date object to YYYY-MM-DD string for Salesforce REST API."""
    if d is None:
        return None
    if isinstance(d, date):
        return d.isoformat()
    return str(d)


def _update_status(
    conn,
    record_id: int,
    sf_id: Optional[str] = None,
    status: str = "synced",
    error_message: Optional[str] = None,
) -> None:
    """Update sync lifecycle columns on a PostgreSQL experience row."""
    with conn.cursor() as cur:
        if sf_id:
            cur.execute(
                """
                UPDATE experience
                SET salesforce_id = %s,
                    sync_status   = %s,
                    error_message = NULL,
                    last_synced   = NOW()
                WHERE id = %s
                """,
                (sf_id, status, record_id),
            )
        else:
            cur.execute(
                """
                UPDATE experience
                SET sync_status   = %s,
                    error_message = %s,
                    last_synced   = NOW()
                WHERE id = %s
                """,
                (status, error_message, record_id),
            )
    conn.commit()


if __name__ == "__main__":
    logging.basicConfig(
        level=getattr(logging, config.LOG_LEVEL, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    sf = sf_client.connect()
    conn = db.connect()
    try:
        summary = push_pending(sf, conn)
        print(
            f"\nWrite-back complete: {summary['pushed']} pushed, "
            f"{summary['failed']} failed (of {summary['total']} total)"
        )
        sys.exit(1 if summary["failed"] > 0 else 0)
    finally:
        conn.close()
