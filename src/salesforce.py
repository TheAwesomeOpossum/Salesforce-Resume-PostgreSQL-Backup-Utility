"""Salesforce connection and data retrieval via simple-salesforce."""

import logging
import time
from simple_salesforce import Salesforce, SalesforceExpiredSession
from src import config

logger = logging.getLogger(__name__)

# SOQL IN clause max values per chunk (Salesforce limit is 1000)
SOQL_IN_CHUNK_SIZE = 999


def connect() -> Salesforce:
    """Authenticate to Salesforce and return a session."""
    logger.info("Connecting to Salesforce as %s", config.SF_USERNAME)
    sf = Salesforce(
        username=config.SF_USERNAME,
        password=config.SF_PASSWORD,
        security_token=config.SF_SECURITY_TOKEN,
        consumer_key=config.SF_CONSUMER_KEY,
        consumer_secret=config.SF_CONSUMER_SECRET,
        instance_url=config.SF_INSTANCE_URL,
        version=config.SF_API_VERSION,
    )
    logger.info("Connected. Instance: %s", sf.base_url)
    return sf


def describe_object(sf: Salesforce, sobject_name: str) -> dict:
    """Return the full describe metadata for a Salesforce object."""
    return getattr(sf, sobject_name).describe()


def get_queryable_fields(sf: Salesforce, sobject_name: str) -> list[str]:
    """Return a list of all queryable, non-compound field API names for an object."""
    meta = describe_object(sf, sobject_name)
    fields = []
    for f in meta["fields"]:
        if not f.get("queryable", True):
            continue
        # Skip compound address/location parent fields — query their sub-fields instead
        if f["type"] in ("address", "location"):
            continue
        fields.append(f["name"])
    return fields


def query_records(sf: Salesforce, sobject_name: str, fields: list[str], where: str = "") -> list[dict]:
    """
    Fetch records for a Salesforce object using query_all (auto-paginated).
    Optionally apply a WHERE clause.
    """
    field_str = ", ".join(fields)
    soql = f"SELECT {field_str} FROM {sobject_name}"
    if where:
        soql += f" WHERE {where}"

    logger.info("Querying: %s", soql[:200])
    result = _with_backoff(lambda: sf.query_all(soql))
    records = result["records"]
    for r in records:
        r.pop("attributes", None)
    logger.info("  → %d records", len(records))
    return records


def query_accounts_filtered(sf: Salesforce, fields: list[str]) -> list[dict]:
    """Fetch only Account records with Type = 'Resume' or 'Job Application'."""
    return query_records(sf, "Account", fields, where="Type IN ('Resume', 'Job Application')")


def query_contacts_for_accounts(sf: Salesforce, fields: list[str], account_ids: list[str]) -> list[dict]:
    """
    Fetch Contact records whose AccountId is in the provided account_ids list.
    Chunks the IN clause to stay within Salesforce's 1000-value limit.
    """
    if not account_ids:
        logger.info("No account IDs provided — skipping Contact sync")
        return []

    all_records: list[dict] = []
    chunks = _chunk(account_ids, SOQL_IN_CHUNK_SIZE)
    logger.info("Fetching Contacts for %d accounts in %d chunk(s)", len(account_ids), len(chunks))

    for i, chunk in enumerate(chunks, 1):
        id_list = ", ".join(f"'{aid}'" for aid in chunk)
        where = f"AccountId IN ({id_list})"
        records = query_records(sf, "Contact", fields, where=where)
        all_records.extend(records)
        logger.info("  Chunk %d/%d: %d contacts", i, len(chunks), len(records))

    logger.info("Total contacts fetched: %d", len(all_records))
    return all_records


def _chunk(lst: list, size: int) -> list[list]:
    """Split a list into chunks of at most `size` items."""
    return [lst[i:i + size] for i in range(0, len(lst), size)]


def _with_backoff(fn, max_retries: int = 5):
    """Retry a callable with exponential backoff on transient errors."""
    for attempt in range(max_retries):
        try:
            return fn()
        except SalesforceExpiredSession:
            raise
        except Exception as exc:
            if attempt == max_retries - 1:
                raise
            wait = 2 ** attempt
            logger.warning("Attempt %d failed: %s. Retrying in %ds...", attempt + 1, exc, wait)
            time.sleep(wait)
