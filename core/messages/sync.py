"""Incremental message sync. Cursor = highest ROWID ingested, validated by a DB
fingerprint and a guid anchor so a VACUUM or device migration forces a full rescan."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from core.database.migrate import ensure_schema
from core.messages import chat_db, pipeline

DB_PATH = pipeline.DB_PATH


def get_state(conn: sqlite3.Connection) -> Optional[dict]:
    try:
        row = conn.execute(
            "SELECT last_rowid, last_guid, db_fingerprint, last_sync_at "
            "FROM message_sync_state WHERE id = 1"
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    if row is None:
        return None
    return {"last_rowid": row[0], "last_guid": row[1],
            "db_fingerprint": row[2], "last_sync_at": row[3]}


def upsert_state(conn: sqlite3.Connection, last_rowid: Optional[int],
                 last_guid: Optional[str], fingerprint: Optional[str]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO message_sync_state
               (id, last_rowid, last_guid, db_fingerprint, last_sync_at)
           VALUES (1, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET
             last_rowid = excluded.last_rowid,
             last_guid = excluded.last_guid,
             db_fingerprint = excluded.db_fingerprint,
             last_sync_at = excluded.last_sync_at""",
        (last_rowid, last_guid, fingerprint, now),
    )
    conn.commit()


def _cursor_valid(state: Optional[dict], chat_db_path: Path) -> bool:
    """True only if every cursor field is present, the fingerprint matches, and the
    row at last_rowid still has last_guid (guards VACUUM/migration/first-run NULLs)."""
    if not state or state["last_rowid"] is None or state["last_guid"] is None:
        return False
    if state["db_fingerprint"] != chat_db.fingerprint(chat_db_path):
        return False
    conn = chat_db.open_readonly(chat_db_path)
    try:
        row = conn.execute(
            "SELECT guid FROM message WHERE ROWID = ?", (state["last_rowid"],)
        ).fetchone()
    finally:
        conn.close()
    return bool(row) and row[0] == state["last_guid"]


def incremental_sync(chat_db_path: Path = chat_db.DEFAULT_CHAT_DB,
                     db_path: Optional[Path] = None,
                     db_dir: Optional[Path] = None,
                     progress_cb=None) -> dict:
    db_path = db_path or DB_PATH
    conn = sqlite3.connect(str(db_path))
    ensure_schema(conn)
    state = get_state(conn)
    conn.close()

    valid = _cursor_valid(state, chat_db_path)
    since = state["last_rowid"] if valid else None
    summary = pipeline.run_full_scan(
        chat_db_path=chat_db_path, since_rowid=since,
        progress_cb=progress_cb, db_dir=db_dir,
    )

    conn = sqlite3.connect(str(db_path))
    if summary["last_rowid"] is not None:
        upsert_state(conn, summary["last_rowid"], summary["last_guid"],
                     summary["fingerprint"])
    conn.close()

    summary["new_messages"] = summary["stored"]
    summary["full_rescan"] = not valid
    return summary
