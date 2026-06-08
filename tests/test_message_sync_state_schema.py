import sqlite3

import pytest

from core.database.migrate import ensure_schema


def test_message_sync_state_table_created(tmp_path):
    db = tmp_path / "jar.db"
    conn = sqlite3.connect(db)
    ensure_schema(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(message_sync_state)")}
    assert cols == {"id", "last_rowid", "last_guid", "db_fingerprint", "last_sync_at"}


def test_message_sync_state_single_row_check(tmp_path):
    db = tmp_path / "jar.db"
    conn = sqlite3.connect(db)
    ensure_schema(conn)
    conn.execute("INSERT INTO message_sync_state (id, last_rowid) VALUES (1, 10)")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO message_sync_state (id, last_rowid) VALUES (2, 20)")
