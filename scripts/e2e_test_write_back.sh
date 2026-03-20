#!/usr/bin/env bash
# =============================================================
# E2E Test: write_back.py — Phase 2 validation
# Run from Docker_Projects/Salesforce_Backup/
#
# Usage:
#   ./scripts/e2e_test_write_back.sh
#
# Requires: .env with PG + SF creds, docker container sf_postgres
#           running, SF org authenticated.
# =============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

# ── Colours ───────────────────────────────────────────────────
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

PASS="${GREEN}PASS${NC}"
FAIL="${RED}FAIL${NC}"
INFO="${YELLOW}INFO${NC}"

FAILURES=0

pass() { echo -e "  [${PASS}] $1"; }
fail() { echo -e "  [${FAIL}] $1"; FAILURES=$((FAILURES + 1)); }
info() { echo -e "  [${INFO}] $1"; }
header() { echo -e "\n── $1 ──────────────────────────────────────────────"; }

# ── Load env ──────────────────────────────────────────────────
if [[ ! -f .env ]]; then
  echo -e "[${RED}ERROR${NC}] .env not found. Run from Docker_Projects/Salesforce_Backup/"
  exit 1
fi
# shellcheck disable=SC1091
source .env

PSQL="docker exec -i sf_postgres psql -U $POSTGRES_USER -d $POSTGRES_DB"
SF_ORG="your-sf-user@example.com"

# ── Scenario 1: Migration verification ────────────────────────
header "Scenario 1: Migration Verification"

SCHEMA=$($PSQL -c "\d experience" 2>&1)

if echo "$SCHEMA" | grep -q "salesforce_id.*character varying(18)"; then
  # salesforce_id should NOT have "not null" — check it's absent
  if echo "$SCHEMA" | grep "salesforce_id" | grep -q "not null"; then
    fail "salesforce_id still has NOT NULL constraint"
  else
    pass "salesforce_id is nullable"
  fi
else
  fail "salesforce_id column not found"
fi

if echo "$SCHEMA" | grep -q "github_url.*character varying(1024)"; then
  pass "github_url column exists (VARCHAR 1024)"
else
  fail "github_url column missing or wrong type"
fi

if echo "$SCHEMA" | grep -q "error_message.*text"; then
  pass "error_message column exists (TEXT)"
else
  fail "error_message column missing or wrong type"
fi

# ── Scenario 2: Successful write-back ─────────────────────────
header "Scenario 2: Successful Write-Back"

# Insert staging record
info "Inserting pending_push record..."
PG_ID=$($PSQL -At -c "
  INSERT INTO experience (
      salesforce_id, name, job, recordtypeid, start_date,
      description, skills, github_url, sync_status
  ) VALUES (
      NULL,
      'Test Write-Back Project',
      'a02al000000d197AAA',
      NULL,
      '2026-03-19',
      '<p>Testing the write-back pipeline.</p>',
      'Python;SQL;Docker',
      'https://github.com/wroachbarrette/test',
      'pending_push'
  ) RETURNING id;
" 2>&1 | grep -oE '^[0-9]+$' | head -1)

if [[ "$PG_ID" =~ ^[0-9]+$ ]]; then
  pass "Inserted staging record (PG id=$PG_ID)"
else
  fail "INSERT failed: $PG_ID"
  echo -e "\n${RED}Aborting — cannot proceed without a test record.${NC}"
  exit 1
fi

# Run write-back (POSTGRES_HOST=localhost — "postgres" hostname only resolves inside Docker)
info "Running write_back.py..."
WB_OUTPUT=$(POSTGRES_HOST=localhost POSTGRES_SSLMODE=disable PYTHONPATH=. python3 -m src.write_back 2>&1)
echo "$WB_OUTPUT" | sed 's/^/    /'

if echo "$WB_OUTPUT" | grep -q "1 pushed, 0 failed"; then
  pass "write_back.py reported 1 pushed, 0 failed"
else
  fail "write_back.py did not report success"
fi

# Verify PG row updated
PG_ROW=$($PSQL -t -c "SELECT salesforce_id, sync_status, error_message FROM experience WHERE id = $PG_ID;" 2>&1)
info "PG row: $PG_ROW"

SF_ID=$(echo "$PG_ROW" | awk -F'|' '{print $1}' | tr -d ' ')
PG_STATUS=$(echo "$PG_ROW" | awk -F'|' '{print $2}' | tr -d ' ')
PG_ERROR=$(echo "$PG_ROW" | awk -F'|' '{print $3}' | tr -d ' ')

if [[ ${#SF_ID} -eq 18 ]]; then
  pass "salesforce_id populated (${SF_ID})"
else
  fail "salesforce_id not populated (got: '${SF_ID}')"
fi

if [[ "$PG_STATUS" == "synced" ]]; then
  pass "sync_status = 'synced'"
else
  fail "sync_status = '${PG_STATUS}' (expected 'synced')"
fi

if [[ -z "$PG_ERROR" ]]; then
  pass "error_message is NULL"
else
  fail "error_message should be NULL, got: '${PG_ERROR}'"
fi

# Verify SF record
info "Querying Salesforce..."
SF_QUERY=$(sf data query \
  --query "SELECT Id, Name, Skills__c, GitHub_URL__c FROM Experience__c WHERE Name = 'Test Write-Back Project'" \
  --target-org "$SF_ORG" 2>&1)
echo "$SF_QUERY" | sed 's/^/    /'

if echo "$SF_QUERY" | grep -q "Test Write-Back Project"; then
  pass "Experience__c record visible in Salesforce"
else
  fail "Experience__c record NOT found in Salesforce"
fi

if echo "$SF_QUERY" | grep -q "Python"; then
  pass "Skills__c contains pushed skills"
else
  fail "Skills__c missing expected skills"
fi

if echo "$SF_QUERY" | grep -q "wroachbarrette/test"; then
  pass "GitHub_URL__c populated"
else
  fail "GitHub_URL__c missing or empty"
fi

# Verify ExperienceTrigger fired (Job__c.Skills__c updated)
info "Checking parent Job__c.Skills__c..."
JOB_QUERY=$(sf data query \
  --query "SELECT Id, Skills__c FROM Job__c WHERE Id = 'a02al000000d197AAA'" \
  --target-org "$SF_ORG" 2>&1)
echo "$JOB_QUERY" | sed 's/^/    /'

if echo "$JOB_QUERY" | grep -qi "Python"; then
  pass "ExperienceTrigger fired — Job__c.Skills__c contains 'Python'"
else
  fail "ExperienceTrigger may not have fired — Python not in Job__c.Skills__c"
fi

# Cleanup — delete SF record
info "Cleaning up Salesforce test record (${SF_ID})..."
if [[ ${#SF_ID} -eq 18 ]]; then
  sf data delete record \
    --sobject Experience__c \
    --record-id "$SF_ID" \
    --target-org "$SF_ORG" 2>&1 | sed 's/^/    /'
  pass "Deleted SF test record"
fi

# ── Scenario 3: Failure handling ──────────────────────────────
header "Scenario 3: Failure Handling (Invalid Job ID)"

# Insert record with invalid Job ID
info "Inserting pending_push record with invalid Job ID..."
FAIL_ID=$($PSQL -At -c "
  INSERT INTO experience (
      salesforce_id, name, job, start_date, skills, sync_status
  ) VALUES (
      NULL, 'Test Failure Handling', 'a02al000000INVALID', '2026-03-19', 'Python', 'pending_push'
  ) RETURNING id;
" 2>&1 | grep -oE '^[0-9]+$' | head -1)

if [[ "$FAIL_ID" =~ ^[0-9]+$ ]]; then
  pass "Inserted invalid staging record (PG id=$FAIL_ID)"
else
  fail "INSERT failed: $FAIL_ID"
fi

# Run write-back (expect failure — exit code 1 is OK)
info "Running write_back.py (expecting 1 failure)..."
FAIL_OUTPUT=$(POSTGRES_HOST=localhost POSTGRES_SSLMODE=disable PYTHONPATH=. python3 -m src.write_back 2>&1 || true)
echo "$FAIL_OUTPUT" | sed 's/^/    /'

if echo "$FAIL_OUTPUT" | grep -q "0 pushed, 1 failed"; then
  pass "write_back.py reported 0 pushed, 1 failed"
else
  fail "write_back.py did not report the expected failure"
fi

# Verify PG error state
FAIL_ROW=$($PSQL -t -c "SELECT sync_status, error_message FROM experience WHERE id = $FAIL_ID;" 2>&1)
info "PG row: $FAIL_ROW"

FAIL_STATUS=$(echo "$FAIL_ROW" | awk -F'|' '{print $1}' | tr -d ' ')
FAIL_MSG=$(echo "$FAIL_ROW" | awk -F'|' '{print $2}' | tr -d ' ')

if [[ "$FAIL_STATUS" == "failed" ]]; then
  pass "sync_status = 'failed'"
else
  fail "sync_status = '${FAIL_STATUS}' (expected 'failed')"
fi

if [[ -n "$FAIL_MSG" ]]; then
  pass "error_message populated: '${FAIL_MSG:0:80}...'"
else
  fail "error_message is empty — error should have been stored"
fi

# Cleanup
info "Cleaning up PG failure test record..."
$PSQL -c "DELETE FROM experience WHERE id = $FAIL_ID;" > /dev/null 2>&1
pass "Deleted PG failure test record"

# ── Summary ───────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════════════"
if [[ $FAILURES -eq 0 ]]; then
  echo -e "  ${GREEN}All E2E scenarios PASSED${NC}"
else
  echo -e "  ${RED}${FAILURES} check(s) FAILED — review output above${NC}"
fi
echo "════════════════════════════════════════════════════════"
echo ""

exit $FAILURES
