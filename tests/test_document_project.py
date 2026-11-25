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

    The RecordType lookup returns one Id, and the UI API picklist call for that record type
    allows Python, Docker and SQL only: Apex is in the global value set but NOT enabled for
    the record type.
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
    mock.query.return_value = {"records": [{"Id": "012000000000001AAA"}]}
    mock.restful.return_value = {
        "values": [{"value": "Python"}, {"value": "Docker"}, {"value": "SQL"}]
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

    # Skills were checked against the Personal_Experience record type (the default)
    assert "DeveloperName = 'Personal_Experience'" in sf.query.call_args[0][0]
    assert sf.restful.call_args[0][0] == (
        "ui-api/object-info/Experience__c/picklist-values/012000000000001AAA/Skills__c"
    )
    # Staged as a new record with the default record type and no company
    sql, params = cursor.execute.call_args[0]
    assert "'pending_push'" in sql
    assert params[7] == "Personal_Experience"
    assert params[8] is None


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
    # (name[0], job[1], start_date[2], end_date[3], description[4], skills_str[5], github_url[6],
    #  record_type[7], company[8])
    insert_params = cursor.execute.call_args[0][1]
    assert len(insert_params) == 9
    assert insert_params[5] == "Python;Docker;SQL"


# ─── Test 7: Skill in the value set but not enabled for the record type ───────

def test_run_skill_not_enabled_for_record_type_raises(sf, conn_with_cursor):
    """
    'Apex' is in the Skills global value set but not in the record type's allowed values.
    run() raises ValueError naming the skill and the record type, and nothing is staged.
    """
    conn, cursor = conn_with_cursor
    with patch("src.document_project.sf_client.connect", return_value=sf), \
         patch("src.document_project.db.connect", return_value=conn) as mock_db:
        with pytest.raises(ValueError) as excinfo:
            document_project.run(_valid_payload(skills=["Python", "Apex"]))

    message = str(excinfo.value)
    assert "Apex" in message
    assert "Personal_Experience" in message
    assert "'Python'" not in message.split("record type")[1]  # only the missing skill is listed
    mock_db.assert_not_called()
    cursor.execute.assert_not_called()


# ─── Test 8: Work experience stages its job, record type and company ──────────

def test_run_work_experience_stages_job_record_type_and_company(sf, conn_with_cursor):
    """
    A payload with job_id, record_type and company validates skills against THAT record type
    and stores all three on the staged row.
    """
    conn, cursor = conn_with_cursor
    payload = _valid_payload(
        job_id="a02000000000001AAA",
        record_type="Work_Experience",
        company="Example Company A",
    )
    with patch("src.document_project.sf_client.connect", return_value=sf), \
         patch("src.document_project.db.connect", return_value=conn):
        document_project.run(payload)

    assert "DeveloperName = 'Work_Experience'" in sf.query.call_args[0][0]
    sql, params = cursor.execute.call_args[0]
    assert "'pending_push'" in sql
    assert params[1] == "a02000000000001AAA"
    assert params[7] == "Work_Experience"
    assert params[8] == "Example Company A"


# ─── Test 9: Without job_id the record links to the Personal Projects job ─────

def test_run_defaults_to_personal_projects_job(sf, conn_with_cursor):
    """A payload with no job_id is staged under the Personal Projects Job__c record."""
    conn, cursor = conn_with_cursor
    with patch("src.document_project.sf_client.connect", return_value=sf), \
         patch("src.document_project.db.connect", return_value=conn):
        document_project.run(_valid_payload())

    params = cursor.execute.call_args[0][1]
    assert params[1] == document_project.PERSONAL_EXPERIENCE_JOB_ID


# ─── Test 10: salesforce_id in the payload stages an update ───────────────────

def test_run_with_salesforce_id_stages_update(sf, conn_with_cursor, capsys):
    """
    A payload carrying salesforce_id is staged as 'pending_update' with that Id stored,
    and the confirmation says it is an update.
    """
    conn, cursor = conn_with_cursor
    with patch("src.document_project.sf_client.connect", return_value=sf), \
         patch("src.document_project.db.connect", return_value=conn):
        document_project.run(_valid_payload(salesforce_id="a03000000000009AAA"))

    sql, params = cursor.execute.call_args[0]
    assert "'pending_update'" in sql
    assert "'pending_push'" not in sql
    assert len(params) == 10
    assert params[9] == "a03000000000009AAA"
    out = capsys.readouterr().out
    assert "Salesforce update" in out
    assert "a03000000000009AAA" in out


# ─── Test 11: Unknown record type is rejected ─────────────────────────────────

def test_get_record_type_skills_unknown_record_type(sf):
    """A record type DeveloperName that does not exist on Experience__c raises ValueError."""
    sf.query.return_value = {"records": []}
    with pytest.raises(ValueError, match="No_Such_Type"):
        document_project.get_record_type_skills(sf, "No_Such_Type")
    sf.restful.assert_not_called()
