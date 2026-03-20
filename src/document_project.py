"""
Staging script: validate an Experience record payload and insert it into
PostgreSQL with sync_status = 'pending_push' for subsequent write-back to Salesforce.

Usage:
    python -m src.document_project --json '<payload_json>'

Required payload fields:
    name        (str)   — Experience record name
    start_date  (str)   — ISO date YYYY-MM-DD
    skills      (list)  — List of skill strings; all must exist in the Skills global value set

Optional payload fields:
    end_date    (str|null) — ISO date YYYY-MM-DD, or null for ongoing
    description (str)      — HTML description text
    github_url  (str)      — Public GitHub repository URL
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
                sync_status
            ) VALUES (
                %s, %s, %s, %s,
                %s, %s, %s,
                'pending_push'
            )
            RETURNING id
            """,
            (
                payload["name"],
                PERSONAL_EXPERIENCE_JOB_ID,
                payload["start_date"],
                payload.get("end_date"),
                payload.get("description"),
                skills_str,
                payload.get("github_url"),
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

    # Validate skills against the live SF global value set
    sf = sf_client.connect()
    valid_skills = get_valid_skills(sf)
    validate_skills(payload["skills"], valid_skills)

    # Stage to PostgreSQL
    conn = db.connect()
    try:
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
