"""
CDC Listener: near-real-time Salesforce → PostgreSQL sync via Change Data Capture.

Opens an outbound CometD long-poll connection to Salesforce's Streaming API
and subscribes to /data/ChangeEvents. No inbound connections are required —
this service connects OUT to Salesforce, so it works behind NAT/firewalls
without any port exposure.

Flow per CDC event:
  1. Event arrives announcing a CREATE / UPDATE / DELETE / UNDELETE
  2. For CREATE / UPDATE / UNDELETE: fetch the full record via SOQL and upsert
     into PostgreSQL using the existing upsert_records() function (Option B)
  3. For DELETE: mark isdeleted = TRUE in PostgreSQL
  4. Persist the CometD replay ID so restarts resume from the right position

Salesforce retains CDC events for 72 hours, so any events missed during a
service outage are automatically replayed on the next startup.

Usage:
    python -m src.cdc_listener
"""

import json
import logging
import sys
import time
import uuid

import requests
from simple_salesforce import SalesforceExpiredSession

from src import config, database as db, salesforce as sf_client

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("/app/logs/cdc_listener.log"),
    ],
)
logger = logging.getLogger(__name__)

# The single CDC channel that receives events for all CDC-enabled objects.
CDC_CHANNEL = "/data/ChangeEvents"

# Salesforce's server-side long-poll timeout is ~40 seconds.
# Our HTTP timeout is set higher so normal empty-response polls don't raise.
LONG_POLL_TIMEOUT_SECONDS = 110

# Seconds to wait before attempting a full reconnect after an error.
RECONNECT_DELAY_SECONDS = 30

# SF object API name → PostgreSQL table name for CDC-enabled objects only.
# These 5 objects get near-real-time updates via CDC.
# All other objects continue to be pulled by the nightly sync.py full sync.
OBJECT_TABLE_MAP = {
    "Account":              "account",
    "Contact":              "contact",
    "Interview__c":         "interview",
    "InterviewContact__c":  "interviewcontact",
    "JobApplication__c":    "jobapplication",
}


# ── Replay ID persistence ────────────────────────────────────────────────────

def _load_replay_id(conn) -> int:
    """
    Return the last persisted CometD replay ID for CDC_CHANNEL.
    Returns -1 (replay all available events, up to 72 hours) if no ID is stored.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT replay_id FROM cdc_replay_state WHERE channel = %s",
            (CDC_CHANNEL,),
        )
        row = cur.fetchone()
    replay_id = row[0] if row else -1
    logger.info("Loaded replay ID: %s", replay_id)
    return replay_id


def _save_replay_id(conn, replay_id: int) -> None:
    """Upsert the latest replay ID so we resume here on the next restart."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO cdc_replay_state (channel, replay_id, updated_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (channel) DO UPDATE
                SET replay_id  = EXCLUDED.replay_id,
                    updated_at = NOW()
            """,
            (CDC_CHANNEL, replay_id),
        )
    conn.commit()


# ── Event processing ─────────────────────────────────────────────────────────

def _mark_deleted(conn, table_name: str, salesforce_ids: list[str]) -> None:
    """Set isdeleted = TRUE for records that were deleted in Salesforce."""
    with conn.cursor() as cur:
        for sf_id in salesforce_ids:
            cur.execute(
                f"UPDATE {table_name} SET isdeleted = TRUE, last_synced = NOW() "
                "WHERE salesforce_id = %s",
                (sf_id,),
            )
    conn.commit()
    logger.info("Marked %d record(s) deleted in '%s'", len(salesforce_ids), table_name)


def _handle_event(sf, conn, message: dict) -> None:
    """
    Process one CDC message.

    For CREATE / UPDATE / UNDELETE: query the full record from Salesforce via
    SOQL and upsert it into PostgreSQL. Fetching the full record (Option B)
    avoids having to map partial CDC payloads to DB columns, and reuses the
    existing upsert_records() / get_queryable_fields() functions unchanged.

    For DELETE: mark the row isdeleted = TRUE without touching Salesforce.
    """
    data        = message.get("data", {})
    payload     = data.get("payload", {})
    header      = payload.get("ChangeEventHeader", {})

    entity_name = header.get("entityName", "")
    record_ids  = header.get("recordIds", [])
    change_type = header.get("changeType", "")

    table_name = OBJECT_TABLE_MAP.get(entity_name)
    if not table_name:
        logger.debug("No table mapping for '%s' — skipping", entity_name)
        return

    logger.info(
        "CDC %-10s  %s → '%s'  (%d record(s))",
        change_type, entity_name, table_name, len(record_ids),
    )

    if change_type == "DELETE":
        _mark_deleted(conn, table_name, record_ids)
        return

    # CREATE / UPDATE / UNDELETE
    try:
        fields  = sf_client.get_queryable_fields(sf, entity_name)
        id_list = ", ".join(f"'{rid}'" for rid in record_ids)
        records = sf_client.query_records(
            sf, entity_name, fields, where=f"Id IN ({id_list})"
        )
        if records:
            db.upsert_records(conn, table_name, records)
        else:
            logger.warning(
                "SOQL returned 0 records for %s IDs %s — deleted concurrently?",
                entity_name, record_ids,
            )
    except Exception as exc:
        logger.error(
            "Failed to process %s on %s %s: %s",
            change_type, entity_name, record_ids, exc,
            exc_info=True,
        )


# ── CometD client ────────────────────────────────────────────────────────────

class CometDClient:
    """
    Minimal synchronous CometD long-poll client for Salesforce Streaming API.

    Protocol flow:
        handshake()  — negotiate a clientId with Salesforce
        subscribe()  — subscribe to a channel, passing the replay ID
        connect()    — one long-poll request; blocks up to ~40s then returns
                       with any pending events (empty list is normal)

    The caller is responsible for looping on connect() indefinitely.
    """

    def __init__(self, sf, replay_id: int):
        self._client_id: str | None = None
        self._replay_id = replay_id
        self._cometd_url = f"{sf.instance_url}/cometd/{config.SF_API_VERSION}"
        self._http = requests.Session()
        self._http.headers.update({
            "Authorization": f"Bearer {sf.session_id}",
            "Content-Type": "application/json",
        })

    def _post(self, messages: list[dict]) -> list[dict]:
        resp = self._http.post(
            self._cometd_url,
            data=json.dumps(messages),
            timeout=LONG_POLL_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        return resp.json()

    def handshake(self) -> None:
        response = self._post([{
            "id":                      str(uuid.uuid4()),
            "version":                 "1.0",
            "minimumVersion":          "1.0",
            "channel":                 "/meta/handshake",
            "supportedConnectionTypes": ["long-polling"],
            "advice":                  {"timeout": 60000, "interval": 0},
        }])
        result = response[0]
        if not result.get("successful"):
            raise RuntimeError(f"CometD handshake failed: {result}")
        self._client_id = result["clientId"]
        logger.info("CometD handshake OK — clientId=%s", self._client_id)

    def subscribe(self, channel: str) -> None:
        response = self._post([{
            "id":           str(uuid.uuid4()),
            "channel":      "/meta/subscribe",
            "clientId":     self._client_id,
            "subscription": channel,
            "ext":          {"replay": {channel: self._replay_id}},
        }])
        result = response[0]
        if not result.get("successful"):
            raise RuntimeError(f"CometD subscribe failed: {result}")
        logger.info("Subscribed to %s (replayId=%s)", channel, self._replay_id)

    def connect(self) -> list[dict]:
        """
        Issue one long-poll connect request.
        Returns data messages (non-meta). An empty list is normal when no
        events occurred during the poll window.
        """
        response = self._post([{
            "id":             str(uuid.uuid4()),
            "channel":        "/meta/connect",
            "clientId":       self._client_id,
            "connectionType": "long-polling",
        }])
        events = []
        for msg in response:
            if msg.get("channel", "").startswith("/meta/"):
                if not msg.get("successful", True):
                    logger.warning("CometD meta: %s", msg)
            else:
                events.append(msg)
        return events

    def update_auth(self, sf) -> None:
        """Refresh the Authorization header after a Salesforce re-authentication."""
        self._http.headers["Authorization"] = f"Bearer {sf.session_id}"

    def close(self) -> None:
        try:
            self._post([{
                "id":       str(uuid.uuid4()),
                "channel":  "/meta/disconnect",
                "clientId": self._client_id,
            }])
        except Exception:
            pass
        self._http.close()


# ── Main loop ────────────────────────────────────────────────────────────────

def run_listener() -> None:
    """Establish a CDC connection and process events until an unhandled error."""
    sf        = sf_client.connect()
    conn      = db.connect()
    replay_id = _load_replay_id(conn)

    cometd = CometDClient(sf, replay_id)
    cometd.handshake()
    cometd.subscribe(CDC_CHANNEL)
    logger.info("=== Listening for CDC events ===")

    while True:
        try:
            events = cometd.connect()
        except requests.exceptions.Timeout:
            # HTTP timeout with no response at all — unusual but non-fatal.
            # (Normal empty long-polls return a 200 with no data messages.)
            logger.warning("HTTP timeout on long-poll — reconnecting")
            raise
        except Exception as exc:
            logger.error("CometD connect error: %s", exc, exc_info=True)
            raise

        for message in events:
            event_replay_id = message.get("data", {}).get("event", {}).get("replayId")
            try:
                _handle_event(sf, conn, message)
            except SalesforceExpiredSession:
                logger.warning("Salesforce session expired — re-authenticating")
                sf = sf_client.connect()
                cometd.update_auth(sf)
                _handle_event(sf, conn, message)

            if event_replay_id is not None:
                _save_replay_id(conn, event_replay_id)


def main() -> None:
    """Outer reconnect loop — restarts run_listener() on any unhandled error."""
    logger.info("=== CDC Listener starting ===")
    while True:
        try:
            run_listener()
        except Exception as exc:
            logger.error(
                "Listener crashed: %s — reconnecting in %ds",
                exc, RECONNECT_DELAY_SECONDS,
                exc_info=True,
            )
            time.sleep(RECONNECT_DELAY_SECONDS)


if __name__ == "__main__":
    main()
