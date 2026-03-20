"""Unit tests for src/document_project.py."""

from unittest.mock import MagicMock, patch

import pytest

from src import document_project, salesforce as sf_client


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    """Replace _with_backoff with a direct call to avoid retry delays in tests."""
    monkeypatch.setattr(document_project.sf_client, "_with_backoff", lambda fn: fn())


@pytest.fixture
def sf():
    """
    Mock Salesforce connection. Skills__c describe returns a small valid set:
    Python, Docker, SQL, Apex.
    """
    mock = MagicMock()
    mock.Experience__c.describe.return_value = {
        "fields": [
            {
                "name": "Skills__c",
                "picklistValues": [
                    {"value": "Python", "active": True},
                    {"value": "Docker", "active": True},
                    {"value": "SQL", "active": True},
                    {"value": "Apex", "active": True},
                    {"value": "Deprecated Skill", "active": False},
                ],
            }
        ]
    }
    return mock


@pytest.fixture
def conn_with_cursor():
    """Mock psycopg2 connection returning row id=42 from INSERT RETURNING."""
    cursor = MagicMock()
    cursor.fetchone.return_value = (42,)
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cursor
    conn.cursor.return_value.__exit__.return_value = False
    return conn, cursor


def _valid_payload(**overrides):
    """Build a valid Experience payload with sensible defaults."""
    payload = {
        "name": "My Project",
        "start_date": "2026-01-15",
        "skills": ["Python", "Docker"],
        "end_date": None,
        "description": "<p>Test description</p>",
        "github_url": "https://github.com/user/repo",
    }
    payload.update(overrides)
    return payload


# ─── Test 1: Valid skills pass validation ─────────────────────────────────────

def test_validate_skills_all_valid(sf):
    """All skills present in the SF value set — no exception raised."""
    valid = document_project.get_valid_skills(sf)
    # Should not raise
    document_project.validate_skills(["Python", "Docker", "SQL"], valid)


# ─── Test 2: Invalid skill raises ValueError naming the bad skill ─────────────

def test_validate_skills_invalid_skill(sf):
    """A skill not in the SF value set raises ValueError that names the invalid value."""
    valid = document_project.get_valid_skills(sf)
    with pytest.raises(ValueError, match="PostgreSQL"):
        document_project.validate_skills(["Python", "PostgreSQL"], valid)


# ─── Test 3: Inactive skills are excluded from the valid set ──────────────────

def test_inactive_skills_are_excluded(sf):
    """Skills with active=False are not in the valid set and will fail validation."""
    valid = document_project.get_valid_skills(sf)
    assert "Deprecated Skill" not in valid
    with pytest.raises(ValueError, match="Deprecated Skill"):
        document_project.validate_skills(["Python", "Deprecated Skill"], valid)


# ─── Test 4: Full run() stages record and prints confirmation ─────────────────

def test_run_success(sf, conn_with_cursor, capsys):
    """
    Valid payload: SF connection validates skills, PG insert succeeds,
    confirmation message is printed with the record name.
    """
    conn, cursor = conn_with_cursor
    with patch("src.document_project.sf_client.connect", return_value=sf), \
         patch("src.document_project.db.connect", return_value=conn):
        document_project.run(_valid_payload())

    captured = capsys.readouterr()
    assert "My Project" in captured.out
    assert "Staged experience" in captured.out
    conn.commit.assert_called_once()


# ─── Test 5: Missing required field raises ValueError before connections ───────

def test_run_missing_required_field_no_connections():
    """
    Payload missing 'name' raises ValueError immediately.
    Neither SF nor PG connections are made.
    """
    payload = _valid_payload()
    del payload["name"]

    with patch("src.document_project.sf_client.connect") as mock_sf, \
         patch("src.document_project.db.connect") as mock_db:
        with pytest.raises(ValueError, match="name"):
            document_project.run(payload)

    mock_sf.assert_not_called()
    mock_db.assert_not_called()


# ─── Test 6: Skills list is semicolon-joined in the PG INSERT ─────────────────

def test_run_skills_semicolon_format(sf, conn_with_cursor):
    """
    Skills list ['Python', 'Docker', 'SQL'] is stored as 'Python;Docker;SQL'
    in the PostgreSQL INSERT.
    """
    conn, cursor = conn_with_cursor
    with patch("src.document_project.sf_client.connect", return_value=sf), \
         patch("src.document_project.db.connect", return_value=conn):
        document_project.run(_valid_payload(skills=["Python", "Docker", "SQL"]))

    # INSERT params tuple:
    # (name[0], job[1], start_date[2], end_date[3], description[4], skills_str[5], github_url[6])
    insert_params = cursor.execute.call_args[0][1]
    assert insert_params[5] == "Python;Docker;SQL"
