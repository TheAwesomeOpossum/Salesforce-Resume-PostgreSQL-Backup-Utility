"""
Staging script: validate an Experience record payload and insert it into
PostgreSQL for subsequent write-back to Salesforce.

Usage:
    python -m src.document_project --json '<payload_json>'

Required payload fields:
    name        (str)   — Experience record name
    start_date  (str)   — ISO date YYYY-MM-DD
    skills      (list)  — List of skill strings; all must exist in the Skills global value set

Optional payload fields:
    end_date       (str|null) — ISO date YYYY-MM-DD, or null for ongoing
    description    (str)      — HTML description text
    github_url     (str)      — Public GitHub repository URL
    salesforce_id  (str)      — Existing Salesforce record ID; if present, stages an update
                                (sync_status='pending_update') instead of a new create
    job_id         (str)      — Job__c record to link to. Defaults to the "Personal Projects" job.
    record_type    (str)      — Experience__c RecordType DeveloperName (Personal_Experience,
                                Work_Experience, Internship). Defaults to Personal_Experience.
    company        (str)      — Company__c value, e.g. the client company for work done through
                                an employer ("Example Company A").
"""

import argparse
import json
import logging
import sys
from typing import Optional

from src import config, database as db, salesforce as sf_client
from src.write_back import PERSONAL_EXPERIENCE_JOB_ID

logger = logging.getLogger(__name__)


def get_valid_skills(sf) -> set:
    """
    Return the set of active Skills picklist values from the Salesforce org.
    Uses the Experience__c.Skills__c field describe result — no Tooling API needed.
    """
    result = sf_client._with_backoff(lambda: sf.Experience__c.describe())
    for field in result["fields"]:
        if field["name"] == "Skills__c":
            return {pv["value"] for pv in field["picklistValues"] if pv["active"]}
    raise ValueError(
        "Skills__c field not found on Experience__c describe result. "
        "Verify the field exists and the org connection is correct."
    )


def get_record_type_skills(sf, record_type_developer_name: str) -> set:
    """
    Return the Skills__c values allowed for ONE Experience__c record type, via the UI API.
    A value can exist in the global value set but be missing from a record type's allowed
    list; Salesforce rejects those with INVALID_OR_NULL_FOR_RESTRICTED_PICKLIST.
    """
    rt = sf_client._with_backoff(lambda: sf.query(
        "SELECT Id FROM RecordType WHERE SObjectType = 'Experience__c' "
        f"AND DeveloperName = '{record_type_developer_name}'"
    ))
    records = rt.get("records", [])
    if not records:
        raise ValueError(f"RecordType '{record_type_developer_name}' not found on Experience__c.")
    rt_id = records[0]["Id"]
    result = sf_client._with_backoff(lambda: sf.restful(
        f"ui-api/object-info/Experience__c/picklist-values/{rt_id}/Skills__c"
    ))
    return {v["value"] for v in result["values"]}


def validate_skills(submitted: list, valid_skills: set) -> None:
    """
    Raise ValueError if any skill in `submitted` is not in `valid_skills`.
    Matching is exact and case-sensitive (mirrors Salesforce restricted picklist enforcement).
    """
    invalid = [s for s in submitted if s not in valid_skills]
    if invalid:
        raise ValueError(
            f"Invalid skill(s) not in Skills global value set: {invalid}\n"
            f"Check the full list in "
            f"LWRPortfolioWebsite/force-app/main/default/globalValueSets/Skills.globalValueSet-meta.xml"
        )


def stage_experience(conn, payload: dict) -> int:
    """
    Insert a staged Experience record into PostgreSQL with sync_status = 'pending_push'.
    Returns the new row id.
    """
    skills_str = ";".join(payload["skills"])
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO experience (
                name, job, start_date, end_date,
                description, skills, github_url,
                recordtypeid, company,
                sync_status
            ) VALUES (
                %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s,
                'pending_push'
            )
            RETURNING id
            """,
            (
                payload["name"],
                payload.get("job_id") or PERSONAL_EXPERIENCE_JOB_ID,
                payload["start_date"],
                payload.get("end_date"),
                payload.get("description"),
                skills_str,
                payload.get("github_url"),
                payload.get("record_type") or "Personal_Experience",
                payload.get("company"),
            ),
        )
        row_id = cur.fetchone()[0]
    conn.commit()
    return row_id


def stage_experience_update(conn, payload: dict) -> int:
    """
    Insert a staged Experience update row into PostgreSQL with sync_status = 'pending_update'.
    The salesforce_id is stored so write_back knows which SF record to PATCH.
    Returns the new PostgreSQL row id.
    """
    skills_str = ";".join(payload["skills"])
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO experience (
                name, job, start_date, end_date,
                description, skills, github_url,
                recordtypeid, company,
                salesforce_id, sync_status
            ) VALUES (
                %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s,
                %s, 'pending_update'
            )
            RETURNING id
            """,
            (
                payload["name"],
                payload.get("job_id") or PERSONAL_EXPERIENCE_JOB_ID,
                payload["start_date"],
                payload.get("end_date"),
                payload.get("description"),
                skills_str,
                payload.get("github_url"),
                payload.get("record_type") or "Personal_Experience",
                payload.get("company"),
                payload["salesforce_id"],
            ),
        )
        row_id = cur.fetchone()[0]
    conn.commit()
    return row_id


def run(payload: dict) -> None:
    """
    Full pipeline: validate payload → validate skills against SF → stage to PostgreSQL.
    Raises ValueError on any validation failure (no DB action taken before validation passes).
    """
    # Validate required fields before making any network connections
    for required in ("name", "start_date", "skills"):
        if not payload.get(required):
            raise ValueError(f"Required field missing or empty in payload: '{required}'")

    if not isinstance(payload["skills"], list):
        raise ValueError("'skills' must be a JSON array of strings.")

    # Validate skills against the live SF global value set, then against the record type
    sf = sf_client.connect()
    valid_skills = get_valid_skills(sf)
    validate_skills(payload["skills"], valid_skills)
    record_type = payload.get("record_type") or "Personal_Experience"
    rt_skills = get_record_type_skills(sf, record_type)
    missing_from_rt = [sk for sk in payload["skills"] if sk not in rt_skills]
    if missing_from_rt:
        raise ValueError(
            f"Skill(s) exist in the Skills value set but are not enabled for record type "
            f"'{record_type}': {missing_from_rt}\n"
            f"Add them with: python3 scripts/add_skills_to_recordtype.py {record_type} "
            + " ".join(repr(sk) for sk in missing_from_rt)
        )

    # Stage to PostgreSQL
    conn = db.connect()
    try:
        if payload.get("salesforce_id"):
            row_id = stage_experience_update(conn, payload)
            print(
                f"Staged experience '{payload['name']}' (PG id={row_id}) "
                f"for Salesforce update (SF ID: {payload['salesforce_id']})."
            )
            logger.info(
                "Staged experience update '%s' with %d skill(s) → PG id=%d, SF id=%s",
                payload["name"],
                len(payload["skills"]),
                row_id,
                payload["salesforce_id"],
            )
        else:
            row_id = stage_experience(conn, payload)
            print(f"Staged experience '{payload['name']}' (PG id={row_id}) for Salesforce sync.")
            logger.info(
                "Staged experience '%s' with %d skill(s) → PG id=%d",
                payload["name"],
                len(payload["skills"]),
                row_id,
            )
    finally:
        conn.close()


if __name__ == "__main__":
    logging.basicConfig(
        level=getattr(logging, config.LOG_LEVEL, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    parser = argparse.ArgumentParser(
        description="Stage a new Experience record into PostgreSQL for Salesforce write-back."
    )
    parser.add_argument(
        "--json",
        required=True,
        dest="payload_json",
        metavar="PAYLOAD",
        help=(
            'JSON payload, e.g. \'{"name":"My Project","start_date":"2026-01-15",'
            '"skills":["Python","Docker"]}\''
        ),
    )
    args = parser.parse_args()

    try:
        payload = json.loads(args.payload_json)
    except json.JSONDecodeError as exc:
        print(f"Error: Invalid JSON payload: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        run(payload)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
