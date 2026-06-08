"""Incremental email sync: embed only what arrived since the last run.

The vector store (LanceDB) holds the emails; this module only tracks a per-account
cursor in the small ``email_sync_state`` SQLite table. A sync reads the account's
``last_imap_date``, runs the same fetch→embed→store path as a full scan via
``IMAP SEARCH SINCE``, then records the newest message date seen so the next sync
starts there.
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
        "SELECT username, last_sync_at, last_imap_date, last_uid, uidvalidity "
        "FROM email_sync_state WHERE username = ?",
        (username,),
    ).fetchone()
    if row is None:
        return None
    return {
        "username": row[0], "last_sync_at": row[1], "last_imap_date": row[2],
        "last_uid": row[3], "uidvalidity": row[4],
    }


def upsert_state(
    conn: sqlite3.Connection,
    username: str,
    last_imap_date: Optional[str],
    last_uid: Optional[int] = None,
    uidvalidity: Optional[str] = None,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO email_sync_state (username, last_sync_at, last_imap_date, last_uid, uidvalidity)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(username) DO UPDATE SET
            last_sync_at   = excluded.last_sync_at,
            last_imap_date = COALESCE(excluded.last_imap_date, email_sync_state.last_imap_date),
            last_uid       = COALESCE(excluded.last_uid, email_sync_state.last_uid),
            uidvalidity    = COALESCE(excluded.uidvalidity, email_sync_state.uidvalidity)
        """,
        (username, now, last_imap_date, last_uid, uidvalidity),
    )
    conn.commit()


def incremental_sync(
    progress_cb=None, db_path: Optional[Path] = None, db_dir: Optional[Path] = None
) -> dict:
    """Scan only messages newer than the last recorded sync point.

    Returns the run summary plus a ``new_emails`` alias for the API.
    """
    db_path = db_path or DB_PATH
    account = imap_client.load_account()
    if account is None:
        return {"error": "no saved IMAP account", "new_emails": 0}
    username = account["username"]

    conn = sqlite3.connect(str(db_path))
    ensure_schema(conn)
    state = get_state(conn, username)
    conn.close()

    # Prefer exact UID cursor; fall back to date, then days_back for first sync.
    if state and state.get("last_uid") is not None:
        summary = pipeline.run_scan(
            last_uid=state["last_uid"],
            uidvalidity=state.get("uidvalidity"),
            progress_cb=progress_cb, db_dir=db_dir,
        )
    elif state and state.get("last_imap_date"):
        summary = pipeline.run_scan(
            since=state["last_imap_date"],
            progress_cb=progress_cb, db_dir=db_dir,
        )
    else:
        summary = pipeline.run_scan(
            days_back=SYNC_FALLBACK_DAYS, progress_cb=progress_cb, db_dir=db_dir,
        )

    conn = sqlite3.connect(str(db_path))
    upsert_state(conn, username,
        last_imap_date=summary.get("last_imap_date"),
        last_uid=summary.get("last_uid"),
        uidvalidity=summary.get("uidvalidity"),
    )
    conn.close()

    summary["new_emails"] = summary.get("stored", 0)
    return summary
