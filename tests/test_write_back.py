"""Unit tests for src/write_back.py."""

from datetime import date
from unittest.mock import MagicMock

import pytest

from src import write_back


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def clear_record_type_cache():
    """Clear the module-level RecordType cache before each test."""
    write_back._record_type_cache.clear()
    yield
    write_back._record_type_cache.clear()


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    """Replace _with_backoff with a direct call to avoid retry delays in tests."""
    monkeypatch.setattr(write_back.sf_client, "_with_backoff", lambda fn: fn())


@pytest.fixture
def sf():
    """Mock Salesforce connection. Default: RecordType query returns a valid ID."""
    mock = MagicMock()
    mock.query.return_value = {"records": [{"Id": "012000000000001AAA"}]}
    return mock


@pytest.fixture
def conn_with_cursor():
    """
    Mock psycopg2 connection with a context-manager cursor.
    Returns (conn, cursor) tuple.
    """
    cursor = MagicMock()
    cursor.description = [
        ("id",), ("name",), ("job",), ("recordtypeid",), ("start_date",),
        ("end_date",), ("description",), ("skills",), ("github_url",),
        ("salesforce_id",), ("sync_status",), ("company",),
    ]
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cursor
    conn.cursor.return_value.__exit__.return_value = False
    return conn, cursor


def _make_row(
    id=1,
    name="My Project",
    job="a02al000000d197AAA",
    recordtypeid="012000000000001AAA",
    start_date=date(2026, 1, 1),
    end_date=None,
    description="<p>Description</p>",
    skills="Python;Docker",
    github_url="https://github.com/user/repo",
    salesforce_id=None,
    sync_status="pending_push",
    company=None,
):
    """Build a fake PostgreSQL experience row tuple (column order matches the SELECT)."""
    return (
        id, name, job, recordtypeid, start_date, end_date, description, skills, github_url,
        salesforce_id, sync_status, company,
    )


# ─── Test 1: RecordType resolved by DeveloperName ────────────────────────────

def test_get_record_type_id_resolves_by_developer_name(sf):
    """RecordType ID is queried by DeveloperName and returned."""
    result = write_back.get_record_type_id(sf, "Personal_Experience")

    assert result == "012000000000001AAA"
    sf.query.assert_called_once()
    query_arg = sf.query.call_args[0][0]
    assert "DeveloperName = 'Personal_Experience'" in query_arg
    assert "SObjectType = 'Experience__c'" in query_arg


# ─── Test 2: RecordType result is cached ─────────────────────────────────────

def test_get_record_type_id_caches_result(sf):
    """RecordType ID is only queried once; subsequent calls use the in-process cache."""
    write_back.get_record_type_id(sf, "Personal_Experience")
    write_back.get_record_type_id(sf, "Personal_Experience")

    assert sf.query.call_count == 1


# ─── Test 3: Successful push ─────────────────────────────────────────────────

def test_push_pending_success(sf, conn_with_cursor):
    """
    A pending_push record is pushed to Salesforce and the PG row is updated
    with salesforce_id + sync_status = 'synced'.
    """
    conn, cursor = conn_with_cursor
    cursor.fetchall.return_value = [_make_row()]
    sf.Experience__c.create.return_value = {"id": "a03000000000001AAA", "success": True}

    summary = write_back.push_pending(sf, conn)

    assert summary["pushed"] == 1
    assert summary["failed"] == 0
    assert summary["total"] == 1

    # Verify SF payload content
    sf.Experience__c.create.assert_called_once()
    payload = sf.Experience__c.create.call_args[0][0]
    assert payload["Name"] == "My Project"
    assert payload["Skills__c"] == "Python;Docker"
    assert payload["GitHub_URL__c"] == "https://github.com/user/repo"
    assert payload["Start_Date__c"] == "2026-01-01"
    assert "End_Date__c" not in payload  # end_date was None
    # A new record is linked to its Job and given its RecordType
    assert payload["Job__c"] == "a02al000000d197AAA"
    assert payload["RecordTypeId"] == "012000000000001AAA"
    sf.Experience__c.update.assert_not_called()

    # Verify PG row was updated to synced
    conn.commit.assert_called()


# ─── Test 4: Salesforce API failure ──────────────────────────────────────────

def test_push_pending_sf_api_failure(sf, conn_with_cursor):
    """
    When the Salesforce API raises an exception, the PG row is updated to
    sync_status = 'failed' with an error_message. Other records continue processing.
    """
    conn, cursor = conn_with_cursor
    cursor.fetchall.return_value = [_make_row(id=1), _make_row(id=2, name="Second Project")]
    sf.Experience__c.create.side_effect = [
        Exception("REQUIRED_FIELD_MISSING: Missing required field"),
        {"id": "a03000000000002AAA", "success": True},
    ]

    summary = write_back.push_pending(sf, conn)

    assert summary["failed"] == 1
    assert summary["pushed"] == 1
    assert summary["total"] == 2

    # The failed row is marked 'failed' with the error text; the second row is marked 'synced'
    status_updates = [c.args[1] for c in cursor.execute.call_args_list if "UPDATE experience" in c.args[0]]
    assert ("failed", "REQUIRED_FIELD_MISSING: Missing required field", 1) in status_updates
    assert ("a03000000000002AAA", "synced", 2) in status_updates


# ─── Test 5: Optional fields omitted when None ───────────────────────────────

def test_push_pending_optional_fields_omitted_when_null(sf, conn_with_cursor):
    """
    GitHub_URL__c, End_Date__c, and Description__c are NOT included in the SF
    payload when the corresponding PG columns are None.
    """
    conn, cursor = conn_with_cursor
    cursor.fetchall.return_value = [
        _make_row(end_date=None, description=None, github_url=None)
    ]
    sf.Experience__c.create.return_value = {"id": "a03000000000003AAA", "success": True}

    write_back.push_pending(sf, conn)

    payload = sf.Experience__c.create.call_args[0][0]
    assert "GitHub_URL__c" not in payload
    assert "End_Date__c" not in payload
    assert "Description__c" not in payload
    # Required fields always present
    assert "Name" in payload
    assert "Skills__c" in payload
    assert "Job__c" in payload
    assert "RecordTypeId" in payload
    assert "Start_Date__c" in payload


# ─── Test 6: pending_update rows update the existing Salesforce record ───────

def test_push_pending_update_patches_existing_record(sf, conn_with_cursor):
    """
    A pending_update row calls Experience__c.update on its salesforce_id (never create),
    and the update payload leaves Job__c and RecordTypeId alone.
    """
    conn, cursor = conn_with_cursor
    cursor.fetchall.return_value = [
        _make_row(salesforce_id="a03000000000009AAA", sync_status="pending_update")
    ]

    summary = write_back.push_pending(sf, conn)

    assert summary == {"pushed": 1, "failed": 0, "total": 1}
    sf.Experience__c.create.assert_not_called()
    sf.Experience__c.update.assert_called_once()
    sf_id, payload = sf.Experience__c.update.call_args[0]
    assert sf_id == "a03000000000009AAA"
    assert payload["Name"] == "My Project"
    assert payload["Skills__c"] == "Python;Docker"
    assert "Job__c" not in payload
    assert "RecordTypeId" not in payload

    status_updates = [c.args[1] for c in cursor.execute.call_args_list if "UPDATE experience" in c.args[0]]
    assert ("a03000000000009AAA", "synced", 1) in status_updates


# ─── Test 7: RecordType DeveloperName staged by document_project is resolved ──

def test_push_pending_resolves_record_type_developer_name(sf, conn_with_cursor):
    """
    A row staged with a RecordType DeveloperName (e.g. 'Work_Experience') is resolved to the
    RecordType Id by query, and that Id is sent to Salesforce.
    """
    conn, cursor = conn_with_cursor
    cursor.fetchall.return_value = [_make_row(recordtypeid="Work_Experience")]
    sf.query.return_value = {"records": [{"Id": "012000000000002AAA"}]}
    sf.Experience__c.create.return_value = {"id": "a03000000000004AAA", "success": True}

    write_back.push_pending(sf, conn)

    assert "DeveloperName = 'Work_Experience'" in sf.query.call_args[0][0]
    payload = sf.Experience__c.create.call_args[0][0]
    assert payload["RecordTypeId"] == "012000000000002AAA"


# ─── Test 8: A real RecordType Id is used as-is, with no lookup ──────────────

def test_push_pending_uses_real_record_type_id_without_query(sf, conn_with_cursor):
    """A row that already holds an 012... RecordType Id is sent unchanged; no query is made."""
    conn, cursor = conn_with_cursor
    cursor.fetchall.return_value = [_make_row(recordtypeid="012000000000007AAA")]
    sf.Experience__c.create.return_value = {"id": "a03000000000005AAA", "success": True}

    write_back.push_pending(sf, conn)

    sf.query.assert_not_called()
    payload = sf.Experience__c.create.call_args[0][0]
    assert payload["RecordTypeId"] == "012000000000007AAA"


# ─── Test 9: Legacy rows with no record type default to Personal_Experience ──

@pytest.mark.parametrize("blank", [None, "", "   "])
def test_push_pending_blank_record_type_defaults_to_personal(sf, conn_with_cursor, blank):
    """Rows with an empty recordtypeid are pushed as Personal_Experience."""
    conn, cursor = conn_with_cursor
    cursor.fetchall.return_value = [_make_row(recordtypeid=blank)]
    sf.Experience__c.create.return_value = {"id": "a03000000000006AAA", "success": True}

    write_back.push_pending(sf, conn)

    assert "DeveloperName = 'Personal_Experience'" in sf.query.call_args[0][0]
    payload = sf.Experience__c.create.call_args[0][0]
    assert payload["RecordTypeId"] == "012000000000001AAA"


# ─── Test 10: The staged company is never written to Salesforce ──────────────

def test_push_pending_company_not_sent_to_salesforce(sf, conn_with_cursor):
    """
    Experience__c.Company__c is a formula field, so the staged `company` column must not be
    included in the payload (Salesforce would reject the write).
    """
    conn, cursor = conn_with_cursor
    cursor.fetchall.return_value = [_make_row(company="Example Company A")]
    sf.Experience__c.create.return_value = {"id": "a03000000000008AAA", "success": True}

    summary = write_back.push_pending(sf, conn)

    assert summary["pushed"] == 1
    payload = sf.Experience__c.create.call_args[0][0]
    assert "Company__c" not in payload
    assert "Example Company A" not in payload.values()
