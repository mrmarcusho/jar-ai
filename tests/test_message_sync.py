import sqlite3

from core.database.migrate import ensure_schema
from core.messages import pipeline, sync
from tests.test_chat_db import _make_chat_db


def _patch(monkeypatch):
    monkeypatch.setattr(pipeline.contacts, "build_contact_map", lambda home=None: {})
    monkeypatch.setattr(pipeline.embeddings, "embed_texts",
                        lambda texts, **k: [[0.1] * 1024 for _ in texts])


def test_first_run_full_scan(tmp_path, monkeypatch):
    _patch(monkeypatch)
    jar = tmp_path / "jar.db"
    conn = sqlite3.connect(jar)
    ensure_schema(conn)
    conn.close()
    chatdb = tmp_path / "chat.db"
    _make_chat_db(chatdb)
    summary = sync.incremental_sync(chat_db_path=chatdb, db_path=jar, db_dir=tmp_path)
    assert summary["new_messages"] == 2
    assert summary["full_rescan"] is True
    state = sync.get_state(sqlite3.connect(jar))
    assert state["last_rowid"] == 2 and state["last_guid"] == "g2"


def test_second_run_no_new(tmp_path, monkeypatch):
    _patch(monkeypatch)
    jar = tmp_path / "jar.db"
    conn = sqlite3.connect(jar)
    ensure_schema(conn)
    conn.close()
    chatdb = tmp_path / "chat.db"
    _make_chat_db(chatdb)
    sync.incremental_sync(chat_db_path=chatdb, db_path=jar, db_dir=tmp_path)
    summary = sync.incremental_sync(chat_db_path=chatdb, db_path=jar, db_dir=tmp_path)
    assert summary["new_messages"] == 0  # cursor valid, nothing past rowid 2
    assert summary["full_rescan"] is False


def test_stale_cursor_forces_full(tmp_path, monkeypatch):
    _patch(monkeypatch)
    jar = tmp_path / "jar.db"
    conn = sqlite3.connect(jar)
    ensure_schema(conn)
    # seed a bogus cursor whose guid anchor won't match
    sync.upsert_state(conn, last_rowid=1, last_guid="WRONG", fingerprint="stale")
    conn.close()
    chatdb = tmp_path / "chat.db"
    _make_chat_db(chatdb)
    summary = sync.incremental_sync(chat_db_path=chatdb, db_path=jar, db_dir=tmp_path)
    assert summary["full_rescan"] is True and summary["new_messages"] == 2
