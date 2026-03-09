"""Load and validate configuration from environment variables."""

import os
from dotenv import load_dotenv

load_dotenv()


def _require(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise EnvironmentError(f"Required environment variable '{key}' is not set.")
    return value


# Salesforce
SF_USERNAME = _require("SF_USERNAME")
SF_PASSWORD = _require("SF_PASSWORD")
SF_SECURITY_TOKEN = _require("SF_SECURITY_TOKEN")
SF_CONSUMER_KEY = os.getenv("SF_CONSUMER_KEY")
SF_CONSUMER_SECRET = os.getenv("SF_CONSUMER_SECRET")
SF_INSTANCE_URL = os.getenv("SF_INSTANCE_URL")
SF_API_VERSION = os.getenv("SF_API_VERSION", "63.0")

# PostgreSQL
PG_HOST = os.getenv("POSTGRES_HOST", "localhost")
PG_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
PG_DB = _require("POSTGRES_DB")
PG_USER = _require("POSTGRES_USER")
PG_PASSWORD = _require("POSTGRES_PASSWORD")
PG_SSLMODE = os.getenv("POSTGRES_SSLMODE", "require")

# Sync
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
SYNC_INTERVAL_SECONDS = int(os.getenv("SYNC_INTERVAL_SECONDS", "86400"))  # default: 24 hours
