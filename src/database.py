"""PostgreSQL connection and upsert logic."""

import logging
import psycopg2
import psycopg2.extras
from src import config

logger = logging.getLogger(__name__)


def connect():
    """Return a psycopg2 connection to PostgreSQL."""
    conn = psycopg2.connect(
        host=config.PG_HOST,
        port=config.PG_PORT,
        dbname=config.PG_DB,
        user=config.PG_USER,
        password=config.PG_PASSWORD,
        sslmode=config.PG_SSLMODE,
    )
    conn.autocommit = False
    return conn


def table_exists(conn, table_name: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = %s)",
            (table_name.lower(),),
        )
        return cur.fetchone()[0]


def _get_table_columns(conn, table_name: str) -> set:
    """Return the set of column names that exist in the given table."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
            (table_name.lower(),),
        )
        return {row[0] for row in cur.fetchall()}


def upsert_records(conn, table_name: str, records: list[dict], sf_id_field: str = "Id") -> int:
    """
    Upsert a list of Salesforce records into a PostgreSQL table.
    Matches on salesforce_id (the Salesforce Id field).
    Returns the number of rows affected.
    """
    if not records:
        return 0

    table = table_name.lower()
    table_cols = _get_table_columns(conn, table)

    # Build column list from first record, filtered to only columns that exist in the table
    sample = records[0]
    columns = [
        sf_col for sf_col in sample.keys()
        if _sf_to_pg_col(sf_col) in table_cols
    ]

    if not columns:
        logger.warning("No matching columns found for table %s — skipping", table)
        return 0

    # Map Salesforce field names to PostgreSQL column names (lowercase, no __c suffix noise)
    pg_columns = [_sf_to_pg_col(c) for c in columns]

    # Build the upsert SQL
    col_list = ", ".join(pg_columns)
    placeholder_list = ", ".join(["%s"] * len(pg_columns))
    update_set = ", ".join(
        f"{col} = EXCLUDED.{col}"
        for col in pg_columns
        if col != "salesforce_id"
    )

    sql = f"""
        INSERT INTO {table} ({col_list}, last_synced, sync_status)
        VALUES ({placeholder_list}, NOW(), 'synced')
        ON CONFLICT (salesforce_id)
        DO UPDATE SET {update_set}, last_synced = NOW(), sync_status = 'synced'
    """

    rows = [
        tuple(_coerce(r.get(sf_col)) for sf_col in columns)
        for r in records
    ]

    with conn.cursor() as cur:
        psycopg2.extras.execute_batch(cur, sql, rows, page_size=500)

    conn.commit()
    logger.info("Upserted %d records into %s", len(rows), table)
    return len(rows)


def _sf_to_pg_col(sf_name: str) -> str:
    """Convert a Salesforce field API name to a PostgreSQL column name."""
    # Id → salesforce_id; everything else lowercase, strip __c
    if sf_name == "Id":
        return "salesforce_id"
    return sf_name.lower().rstrip("_c").rstrip("_") if sf_name.endswith("__c") else sf_name.lower()


def _coerce(value):
    """Coerce Salesforce field values to PostgreSQL-compatible types."""
    # Nested objects (e.g. relationship fields) are stored as None for now
    if isinstance(value, dict):
        return None
    return value
