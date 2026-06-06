"""Incremental email sync: scan only what arrived since the last run.

Bookkeeping lives in the ``email_sync_state`` table (one row per account). A
sync reads the account's ``last_imap_date``, runs the same per-email path as a
full scan via ``IMAP SEARCH SINCE``, then records the newest message date seen
so the next sync starts there.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from core.database.migrate import ensure_schema
from core.email import imap_client, pipeline

DB_PATH = pipeline.DB_PATH
SYNC_FALLBACK_DAYS = 30  # first sync with no recorded date looks back this far


def get_state(conn: sqlite3.Connection, username: str) -> Optional[dict]:
    row = conn.execute(
        "SELECT username, last_sync_at, last_imap_date "
        "FROM email_sync_state WHERE username = ?",
        (username,),
    ).fetchone()
    if row is None:
        return None
    return {"username": row[0], "last_sync_at": row[1], "last_imap_date": row[2]}


def upsert_state(
    conn: sqlite3.Connection, username: str, last_imap_date: Optional[str]
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO email_sync_state (username, last_sync_at, last_imap_date)
        VALUES (?, ?, ?)
        ON CONFLICT(username) DO UPDATE SET
            last_sync_at = excluded.last_sync_at,
            last_imap_date = COALESCE(excluded.last_imap_date, email_sync_state.last_imap_date)
        """,
        (username, now, last_imap_date),
    )
    conn.commit()


def incremental_sync(progress_cb=None, db_path: Optional[Path] = None) -> dict:
    """Scan only messages newer than the last recorded sync point.

    Returns the run summary plus ``new_events`` / ``new_documents`` aliases.
    """
    db_path = db_path or DB_PATH
    account = imap_client.load_account()
    if account is None:
        return {"error": "no saved IMAP account", "new_events": 0, "new_documents": 0}
    username = account["username"]

    conn = sqlite3.connect(str(db_path))
    ensure_schema(conn)
    state = get_state(conn, username)
    conn.close()

    since = state["last_imap_date"] if state and state.get("last_imap_date") else None
    if since:
        summary = pipeline.run_scan(since=since, progress_cb=progress_cb, db_path=db_path)
    else:
        summary = pipeline.run_scan(
            days_back=SYNC_FALLBACK_DAYS, progress_cb=progress_cb, db_path=db_path
        )

    conn = sqlite3.connect(str(db_path))
    upsert_state(conn, username, summary.get("last_imap_date"))
    conn.close()

    summary["new_events"] = summary.get("events_written", 0)
    summary["new_documents"] = summary.get("documents_written", 0)
    return summary
