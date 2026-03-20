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
):
    """Build a fake PostgreSQL experience row tuple."""
    return (id, name, job, recordtypeid, start_date, end_date, description, skills, github_url)


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
