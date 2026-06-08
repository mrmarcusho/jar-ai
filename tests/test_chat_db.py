import sqlite3

import pytest

from core.messages import chat_db


def test_apple_time_nanoseconds():
    ns = (1704067200 - 978307200) * 1_000_000_000  # 2024-01-01T00:00:00Z
    assert chat_db.apple_time_to_iso(ns).startswith("2024-01-01T00:00:00")


def test_apple_time_seconds_legacy():
    secs = 1704067200 - 978307200
    assert chat_db.apple_time_to_iso(secs).startswith("2024-01-01T00:00:00")


def test_apple_time_none():
    assert chat_db.apple_time_to_iso(None) is None


def test_decode_attributed_body_short():
    text = "Dinner at 7 with Dr. Smith"
    blob = b"streamtyped...." + b"NSString" + b"\x01\x94\x84\x01+" \
           + bytes([len(text)]) + text.encode("utf-8")
    assert chat_db.decode_attributed_body(blob) == text


def test_decode_attributed_body_empty():
    assert chat_db.decode_attributed_body(b"") == ""
    assert chat_db.decode_attributed_body(None) == ""


def test_decode_attributed_body_no_marker():
    assert chat_db.decode_attributed_body(b"no string here") == ""


def _make_chat_db(path):
    """Build a minimal chat.db with message/handle/chat/join tables."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT);
        CREATE TABLE chat (ROWID INTEGER PRIMARY KEY, style INTEGER);
        CREATE TABLE message (
            ROWID INTEGER PRIMARY KEY, guid TEXT, text TEXT,
            attributedBody BLOB, date INTEGER, is_from_me INTEGER, handle_id INTEGER
        );
        CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER);
        """
    )
    conn.execute("INSERT INTO handle VALUES (1, '+16175550142')")
    conn.execute("INSERT INTO chat VALUES (1, 45)")   # 45 = direct, 43 = group
    conn.execute("INSERT INTO chat VALUES (2, 43)")
    ns = (1704067200 - 978307200) * 1_000_000_000
    # row 1: plain text, incoming, 1:1
    conn.execute("INSERT INTO message VALUES (1,'g1','Hi there',NULL,?,0,1)", (ns,))
    # row 2: attributedBody only, outgoing (handle_id 0), group chat
    txt = "See you at 7"
    blob = b"x" + b"NSString" + b"\x01\x94\x84\x01+" + bytes([len(txt)]) + txt.encode()
    conn.execute("INSERT INTO message VALUES (2,'g2',NULL,?,?,1,0)", (blob, ns))
    # row 3: reaction/tapback — no text, no body → must be skipped
    conn.execute("INSERT INTO message VALUES (3,'g3',NULL,NULL,?,0,1)", (ns,))
    conn.executemany("INSERT INTO chat_message_join VALUES (?,?)",
                     [(1, 1), (2, 2), (1, 3)])
    conn.commit()
    conn.close()


def test_read_messages_decodes_and_skips(tmp_path):
    db = tmp_path / "chat.db"
    _make_chat_db(db)
    rows = list(chat_db.iter_messages(db))
    bodies = {r["text"] for r in rows}
    assert bodies == {"Hi there", "See you at 7"}      # row 3 skipped
    g2 = next(r for r in rows if r["guid"] == "g2")
    assert g2["is_from_me"] == 1 and g2["is_group"] is True
    g1 = next(r for r in rows if r["guid"] == "g1")
    assert g1["handle"] == "+16175550142" and g1["is_group"] is False


def test_read_messages_since_rowid(tmp_path):
    db = tmp_path / "chat.db"
    _make_chat_db(db)
    rows = list(chat_db.iter_messages(db, since_rowid=1))
    assert {r["guid"] for r in rows} == {"g2"}  # only rowid > 1, row 3 skipped


def test_fingerprint_changes_on_vacuum_like_renumber(tmp_path):
    db = tmp_path / "chat.db"
    _make_chat_db(db)
    fp1 = chat_db.fingerprint(db)
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM message WHERE ROWID = 1")  # changes count + min_rowid
    conn.commit()
    conn.close()
    assert chat_db.fingerprint(db) != fp1


def test_missing_db_raises(tmp_path):
    with pytest.raises(chat_db.MessagesAccessError):
        list(chat_db.iter_messages(tmp_path / "nope.db"))


def test_iter_messages_since_apple_ns(tmp_path):
    db = tmp_path / "chat.db"
    _make_chat_db(db)
    ns = (1704067200 - 978307200) * 1_000_000_000  # all fixture rows at this date
    assert len(list(chat_db.iter_messages(db, since_apple_ns=ns - 1))) == 2  # included
    assert len(list(chat_db.iter_messages(db, since_apple_ns=ns + 1))) == 0  # excluded
