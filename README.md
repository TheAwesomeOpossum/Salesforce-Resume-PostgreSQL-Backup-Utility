# Salesforce Backup & Sync

**Status:** Phase 1 - In Development | **Stack:** Python · PostgreSQL · Docker

A Dockerized service for bi-directional data synchronization between a Salesforce org and a PostgreSQL database.

**Phase 1 (current):** Full initial data pull from Salesforce into PostgreSQL via Bulk API 2.0.
**Phase 2 (roadmap):** Real-time ongoing sync using Change Data Capture (CDC) and write-back from PostgreSQL to Salesforce.

---

## Table of Contents

1. [Architecture](#architecture)
2. [Salesforce Integration Overview](#salesforce-integration-overview)
3. [Prerequisites](#prerequisites)
4. [Environment Configuration](#environment-configuration)
5. [Docker Setup](#docker-setup)
6. [Database Schema Design](#database-schema-design)
7. [Phase 1: Initial Data Pull](#phase-1-initial-data-pull)
8. [Phase 2: Bidirectional Sync (Roadmap)](#phase-2-bidirectional-sync-roadmap)
9. [Key Libraries](#key-libraries)
10. [Rate Limiting & Error Handling](#rate-limiting--error-handling)
11. [PostgreSQL Indexing Strategy](#postgresql-indexing-strategy)
12. [Security Notes](#security-notes)
13. [Troubleshooting](#troubleshooting)
14. [Roadmap](#roadmap)

---

## Architecture

```
                         PHASE 1 (Pull)
  +------------------+   Bulk API 2.0   +-------------------+   psycopg2   +------------+
  |  Salesforce Org  | ---------------> | Python Sync Svc   | -----------> | PostgreSQL |
  |                  |   REST API       |  (simple-sf)      |              |            |
  |  - Accounts      | ---------------> |                   |              |  - accounts|
  |  - Contacts      |                  |  - SOQL queries   |              |  - contacts|
  |  - Opportunities |                  |  - type mapping   |              |  - opps    |
  |  - Custom Objs   |                  |  - upsert logic   |              |  - ...     |
  +------------------+                  +-------------------+              +------------+

                         PHASE 2 (Bidirectional)
  +------------------+   CDC / Pub-Sub   +-------------------+
  |  Salesforce Org  | <--------------> | Python Sync Svc   |
  |  (event stream)  |   REST API upsert |  (conflict mgmt)  |
  +------------------+                  +-------------------+
```

---

## Salesforce Integration Overview

### APIs Used

| API | Use Case | When to Use |
|-----|----------|-------------|
| **Bulk API 2.0** | Full initial data loads, large backfills | >2,000 records; Phase 1 primary method |
| **REST API** | Single-record CRUD, SOQL queries, metadata | Targeted queries, small updates |
| **Change Data Capture (CDC)** | Real-time event streaming on record changes | Phase 2 ongoing sync |
| **Pub/Sub API** | Subscribe to CDC event channels | Phase 2 inbound stream |

### Authentication: OAuth 2.0 via Connected App

Salesforce requires a **Connected App** for all API integrations. This provides:
- A unique identity per application (all API calls logged under the app's identity)
- OAuth 2.0 token-based auth (no plaintext credentials in API calls)
- Granular OAuth scope control

**Supported OAuth flows for server-to-server integration:**
- **Username-Password Flow** — Simple, good for development. Requires `SF_SECURITY_TOKEN`.
- **JWT Bearer Flow** — Recommended for production. No interactive login; uses a certificate.

### Rate Limits

| Limit | Value |
|-------|-------|
| Daily API requests (base) | 100,000 / 24hr (scales with licenses) |
| Concurrent long-running requests (>20s) | 25 in production, 5 in sandbox |
| Bulk API 2.0 jobs | Separate quota from REST API calls |
| Max SOQL queries per sync transaction | 100 (synchronous) |

**Always implement exponential backoff** for retries: `2s → 4s → 8s → 16s`.

### SOQL Basics

Salesforce Object Query Language (SOQL) is used to retrieve data from Salesforce objects:

```sql
-- Fetch all Accounts (paginated automatically by simple-salesforce query_all)
SELECT Id, Name, Industry, AnnualRevenue, CreatedDate, LastModifiedDate
FROM Account
WHERE LastModifiedDate > 2024-01-01T00:00:00Z

-- Relationship query (Contacts with their parent Account)
SELECT Id, FirstName, LastName, Email, Account.Name
FROM Contact
WHERE IsDeleted = false
```

---

## Prerequisites

1. **Salesforce org** — Developer Edition (free) or Sandbox org
   - Sign up: https://developer.salesforce.com/signup

2. **Connected App** — Create one in Salesforce to get OAuth credentials:
   - Navigate to: Setup > App Manager > New Connected App
   - Enable OAuth Settings
   - Set Callback URL: `http://localhost:8080/callback` (or any valid URL for server flows)
   - Add OAuth Scopes: `Full access (full)` or at minimum `Access and manage your data (api)`
   - Save — note your **Consumer Key** and **Consumer Secret**
   - If using Username-Password flow: also retrieve your **Security Token** (My Settings > Reset My Security Token)

3. **Docker & Docker Compose** v2.x+

4. **Python 3.11+** (only needed for local development outside Docker)

---

## Environment Configuration

Copy `.env.example` to `.env` and fill in your values. Never commit `.env` to version control.

```bash
cp .env.example .env
```

**`.env.example`:**
```dotenv
# Salesforce Credentials
SF_USERNAME=your.email@example.com
SF_PASSWORD=yourSalesforcePassword
SF_SECURITY_TOKEN=yourSecurityToken
SF_CONSUMER_KEY=your_connected_app_consumer_key
SF_CONSUMER_SECRET=your_connected_app_consumer_secret
SF_INSTANCE_URL=https://yourorg.my.salesforce.com
SF_API_VERSION=59.0

# PostgreSQL
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
POSTGRES_DB=salesforce_backup
POSTGRES_USER=sfuser
POSTGRES_PASSWORD=changeme_strong_password

# Sync Configuration
SYNC_BATCH_SIZE=10000
SYNC_LOG_LEVEL=INFO
```

---

## Docker Setup

**`docker-compose.yml`** (to be created in Phase 1 implementation):

```yaml
services:
  postgres:
    image: postgres:16
    container_name: sf_postgres
    restart: unless-stopped
    environment:
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: ${POSTGRES_DB}
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./init:/docker-entrypoint-initdb.d
    ports:
      - "5432:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER} -d ${POSTGRES_DB}"]
      interval: 5s
      timeout: 5s
      retries: 5

  sync-service:
    build: .
    container_name: sf_sync
    restart: unless-stopped
    env_file: .env
    depends_on:
      postgres:
        condition: service_healthy
    volumes:
      - ./logs:/app/logs

volumes:
  postgres_data:
```

**Key Docker practices applied:**
- Health check on PostgreSQL before sync service starts (`service_healthy`)
- Named volume for data persistence across container restarts
- Non-root user defined in `Dockerfile`
- Credentials loaded via `env_file`, never hardcoded

---

## Database Schema Design

### Conventions

Every table maps to one Salesforce object and includes these standard columns:

| Column | Type | Purpose |
|--------|------|---------|
| `id` | `BIGSERIAL PRIMARY KEY` | Internal surrogate key |
| `salesforce_id` | `VARCHAR(18) UNIQUE NOT NULL` | Native Salesforce 18-char ID |
| `created_at` | `TIMESTAMP WITH TIME ZONE` | Record creation in Salesforce |
| `updated_at` | `TIMESTAMP WITH TIME ZONE` | Last modified in Salesforce |
| `last_synced` | `TIMESTAMP WITH TIME ZONE` | Timestamp of last successful sync |
| `sync_status` | `VARCHAR(50) DEFAULT 'pending'` | `pending`, `synced`, `conflict`, `failed` |

### Salesforce → PostgreSQL Type Mapping

| Salesforce Type | PostgreSQL Type | Notes |
|----------------|-----------------|-------|
| `id` (18-char) | `VARCHAR(18)` | Always index with UNIQUE constraint |
| `string` / `text` | `VARCHAR(255)` | Adjust length to SF field max |
| `textarea` / `longtextarea` | `TEXT` | No length limit |
| `boolean` / `checkbox` | `BOOLEAN` | |
| `integer` | `INTEGER` | |
| `double` / `percent` | `NUMERIC(18,6)` | Preserve precision |
| `currency` | `NUMERIC(18,2)` | Two decimal places |
| `date` | `DATE` | |
| `datetime` | `TIMESTAMP WITH TIME ZONE` | Salesforce returns UTC |
| `picklist` | `VARCHAR(255)` | Store raw value |
| `multipicklist` | `TEXT` | Semicolon-separated values from SF |
| `reference` (lookup) | `VARCHAR(18)` | Store related record's SF ID |
| `base64` / `blob` | `TEXT` | Store as base64 string or handle separately |

### Example Table: Accounts

```sql
CREATE TABLE accounts (
    id              BIGSERIAL PRIMARY KEY,
    salesforce_id   VARCHAR(18)              NOT NULL UNIQUE,
    name            VARCHAR(255)             NOT NULL,
    industry        VARCHAR(255),
    annual_revenue  NUMERIC(18,2),
    billing_city    VARCHAR(100),
    billing_country VARCHAR(100),
    owner_id        VARCHAR(18),             -- reference to SF User
    created_at      TIMESTAMP WITH TIME ZONE,
    updated_at      TIMESTAMP WITH TIME ZONE,
    last_synced     TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    sync_status     VARCHAR(50)              DEFAULT 'pending'
);

CREATE UNIQUE INDEX idx_accounts_sf_id  ON accounts(salesforce_id);
CREATE INDEX        idx_accounts_sync   ON accounts(sync_status, last_synced);
CREATE INDEX        idx_accounts_owner  ON accounts(owner_id);
```

---

## Phase 1: Initial Data Pull

### Strategy

1. Authenticate to Salesforce via OAuth 2.0 (Username-Password flow for dev)
2. For each Salesforce object to sync:
   - Build SOQL query selecting all required fields
   - Use `simple-salesforce`'s `query_all()` (handles pagination automatically)
   - For large objects (>2,000 records): use **Bulk API 2.0** job instead
3. Map and transform each record's fields to PostgreSQL types
4. **Upsert** into PostgreSQL on `salesforce_id` — safe to re-run without duplicates
5. Update `last_synced` and `sync_status = 'synced'` on success

### Upsert Pattern (PostgreSQL)

```sql
INSERT INTO accounts (salesforce_id, name, industry, annual_revenue, created_at, updated_at, last_synced, sync_status)
VALUES (%s, %s, %s, %s, %s, %s, NOW(), 'synced')
ON CONFLICT (salesforce_id)
DO UPDATE SET
    name           = EXCLUDED.name,
    industry       = EXCLUDED.industry,
    annual_revenue = EXCLUDED.annual_revenue,
    updated_at     = EXCLUDED.updated_at,
    last_synced    = NOW(),
    sync_status    = 'synced';
```

### Objects to Sync (configurable)

- Account
- Contact
- Opportunity
- OpportunityLineItem
- Lead
- Case
- Task / Event
- User
- Custom Objects (`__c` suffix)

---

## Phase 2: Bidirectional Sync (Roadmap)

### Inbound: Salesforce → PostgreSQL (Ongoing)

Replace the full-pull polling with **Change Data Capture (CDC)**:
- Enable CDC for each object in Salesforce Setup > Change Data Capture
- Subscribe to the `/data/<ObjectName>ChangeEvent` channel via Pub/Sub API
- On each event, upsert the changed record into PostgreSQL

### Outbound: PostgreSQL → Salesforce

- Query PostgreSQL rows where `sync_status = 'pending_push'`
- Use Salesforce REST API `PATCH /sobjects/<Object>/<salesforce_id>` to update
- Or use `upsert` with an External ID field for new record creation
- Mark `sync_status = 'synced'` on success, `'failed'` on error

### Conflict Resolution

True bidirectional sync creates conflicts when both sides change the same record. Strategy:

1. **Last-write-wins** — Compare `LastModifiedDate` from Salesforce vs `updated_at` in PostgreSQL. Most recent value wins.
2. **Salesforce authoritative** — Salesforce always wins for specific fields.
3. **Manual review queue** — Conflicting records set to `sync_status = 'conflict'` for human review.

Implementation uses separate field sets per direction:
- `sf_*` prefixed columns store the last-known Salesforce value
- Application columns store the local modified value
- A reconciliation function calculates the final merged value

---

## Key Libraries

| Library | Purpose | Install |
|---------|---------|---------|
| `simple-salesforce` | Salesforce REST + Bulk API 2.0 Python client | `pip install simple-salesforce` |
| `psycopg2-binary` | PostgreSQL driver for Python | `pip install psycopg2-binary` |
| `python-dotenv` | Load `.env` file into environment | `pip install python-dotenv` |
| `pandas` | Optional — data transformation and batch processing | `pip install pandas` |

**Basic authentication with simple-salesforce:**

```python
from simple_salesforce import Salesforce
import os

sf = Salesforce(
    username=os.getenv("SF_USERNAME"),
    password=os.getenv("SF_PASSWORD"),
    security_token=os.getenv("SF_SECURITY_TOKEN"),
    consumer_key=os.getenv("SF_CONSUMER_KEY"),
    consumer_secret=os.getenv("SF_CONSUMER_SECRET"),
    version=os.getenv("SF_API_VERSION", "59.0"),
)

# Query all accounts (auto-paginated)
results = sf.query_all("SELECT Id, Name, Industry FROM Account")
records = results["records"]
```

---

## Rate Limiting & Error Handling

```python
import time

def with_backoff(fn, max_retries=5):
    """Exponential backoff retry wrapper."""
    for attempt in range(max_retries):
        try:
            return fn()
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            wait = 2 ** attempt  # 1s, 2s, 4s, 8s, 16s
            print(f"Attempt {attempt+1} failed: {e}. Retrying in {wait}s...")
            time.sleep(wait)
```

**Best practices:**
- Use Bulk API 2.0 for any object with >2,000 records to avoid REST quota burn
- Cache Salesforce metadata (field definitions) — they rarely change
- Log API usage and alert when approaching daily limits
- Use `query_all()` instead of `query()` to avoid manually handling pagination

---

## PostgreSQL Indexing Strategy

```sql
-- Always: unique index on salesforce_id (lookup during upsert)
CREATE UNIQUE INDEX idx_<table>_sf_id ON <table>(salesforce_id);

-- Always: composite index for sync queries
CREATE INDEX idx_<table>_sync ON <table>(sync_status, last_synced);

-- For relationship tables: index foreign SF ID columns
CREATE INDEX idx_contacts_account ON contacts(account_id);

-- For date-range queries during incremental sync
CREATE INDEX idx_accounts_updated ON accounts(updated_at);
```

**Avoid** using random UUID v4 as primary keys — they degrade B-tree index performance through random page splits. Use `BIGSERIAL` or time-ordered UUID v7 if UUIDs are required.

---

## Security Notes

- Never commit `.env` to git — add it to `.gitignore`
- Use `.env.example` with placeholder values for documentation
- In production, use **Docker secrets** or a secrets manager (Vault, AWS Secrets Manager) instead of `.env`
- All Salesforce API calls use HTTPS — never downgrade to HTTP
- Request only the **minimum necessary OAuth scopes** for your Connected App
- Rotate the Salesforce Security Token after any suspected exposure (My Settings > Reset My Security Token)
- Run the sync service container as a **non-root user** (defined in Dockerfile)

---

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| `INVALID_SESSION_ID` | Expired or invalid OAuth token | Re-authenticate; check credentials |
| `REQUEST_LIMIT_EXCEEDED` | Daily API quota hit | Switch to Bulk API; wait for reset |
| `QUERY_TIMEOUT` | SOQL query too broad | Add `WHERE` filters; use Bulk API |
| `psycopg2.OperationalError` on startup | PostgreSQL not ready | Health check ensures readiness; add retry logic |
| Duplicate key violations | Re-running without upsert | Use `ON CONFLICT DO UPDATE` pattern |
| Missing fields in query results | SOQL doesn't return null fields by default | Explicitly list all fields; use `coalesce` in mapping |
| Sandbox vs Production auth error | Wrong instance URL | Set `SF_INSTANCE_URL` correctly; use `test.salesforce.com` for sandbox |

---

## Roadmap

- [ ] **Phase 1** — Full initial data pull via Bulk API 2.0
- [ ] **Phase 1** — Docker Compose + Dockerfile implementation
- [ ] **Phase 1** — Schema init scripts for core SF objects
- [ ] **Phase 1** — Configurable object list (env or YAML config)
- [ ] **Phase 2** — CDC / Pub-Sub API inbound real-time sync
- [ ] **Phase 2** — Outbound write-back from PostgreSQL to Salesforce
- [ ] **Phase 2** — Conflict detection and resolution queue
- [ ] **Phase 3** — Monitoring dashboard (sync status, API usage, error rates)
- [ ] **Phase 3** — Web UI for manual conflict resolution

---

## References

- [Salesforce Bulk API 2.0 Developer Guide](https://developer.salesforce.com/docs/atlas.en-us.api_asynch.meta/api_asynch/asynch_api_intro.htm)
- [Salesforce REST API Developer Guide](https://developer.salesforce.com/docs/atlas.en-us.api_rest.meta/api_rest/intro_rest.htm)
- [Change Data Capture Developer Guide](https://developer.salesforce.com/docs/atlas.en-us.change_data_capture.meta/change_data_capture/cdc_intro.htm)
- [OAuth 2.0 and Connected Apps](https://developer.salesforce.com/docs/atlas.en-us.api_rest.meta/api_rest/intro_oauth_and_connected_apps.htm)
- [simple-salesforce Documentation](https://simple-salesforce.readthedocs.io/)
- [PostgreSQL Documentation](https://www.postgresql.org/docs/)
- [Docker Compose Reference](https://docs.docker.com/compose/compose-file/)
