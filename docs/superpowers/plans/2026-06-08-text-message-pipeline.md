# Text-Message Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the chat engine answer questions from the user's iMessage/SMS history by ingesting the local macOS Messages database, embedding messages on-device into LanceDB, and exposing a redacted `search_messages` tool.

**Architecture:** Mirrors the existing `core/email/` pipeline. A read-only `chat.db` reader (typedstream-decoding + Apple-epoch conversion) feeds contact-resolved messages through a shared bge-m3 embedder into a LanceDB `messages` table. A shared `cloud_boundary` redaction step scrubs PII from excerpts before they reach the cloud LLM at answer time (and is retrofitted onto the email path). Incremental sync uses a ROWID cursor validated by a DB fingerprint + guid anchor.

**Tech Stack:** Python 3.11+ (3.9 locally), SQLite, LanceDB, sentence-transformers (bge-m3), Presidio, FastAPI + APScheduler, pytest.

**Spec:** `docs/superpowers/specs/2026-06-08-text-message-pipeline-design.md`

---

## File Structure

**Create:**
- `core/embeddings.py` — shared bge-m3 embedder (lifted from `core/email/embedder.py`)
- `core/messages/__init__.py`
- `core/messages/chat_db.py` — read-only `chat.db` reader: typedstream decode, Apple-epoch, fingerprint, `MessagesAccessError`
- `core/messages/contacts.py` — AddressBook → `handle → contact_name` map (E.164)
- `core/messages/vector_store.py` — LanceDB `messages` table
- `core/messages/pipeline.py` — full-scan orchestrator
- `core/messages/sync.py` — incremental sync (ROWID cursor + fingerprint/guid validation)
- `core/privacy/cloud_boundary.py` — shared redact-excerpts-before-cloud helper
- `tests/test_chat_db.py`, `tests/test_message_contacts.py`, `tests/test_message_vector_store.py`, `tests/test_message_sync.py`, `tests/test_cloud_boundary.py`

**Modify:**
- `core/email/embedder.py` — becomes a thin re-export of `core/embeddings.py`
- `core/email/vector_store.py:19` — import `EMBED_DIM` from `core.embeddings`
- `core/privacy/redactor.py` — add `lowercase_for_ner` flag + `register_person()`
- `core/database/schema.sql` — add `message_sync_state` table
- `app/chat/chat_engine.py` — `search_messages` tool, session `RedactionEngine`, detokenize, email retrofit
- `app/server.py` — message scan/sync endpoints, nightly job extension, Presidio warm

---

## Task 1: Lift the embedder to a shared module

**Files:**
- Create: `core/embeddings.py`
- Modify: `core/email/embedder.py`, `core/email/vector_store.py:19`
- Test: `tests/test_shared_embeddings.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_shared_embeddings.py
def test_shared_embeddings_exports():
    from core import embeddings
    assert embeddings.EMBED_DIM == 1024
    assert callable(embeddings.embed_texts)
    assert embeddings.embed_texts([]) == []

def test_email_embedder_reexports_shared():
    # Back-compat: existing imports must keep working.
    from core.email import embedder
    from core import embeddings
    assert embedder.EMBED_DIM is embeddings.EMBED_DIM
    assert embedder.embed_texts is embeddings.embed_texts
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_shared_embeddings.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.embeddings'`

- [ ] **Step 3: Create `core/embeddings.py`**

Move the entire current body of `core/email/embedder.py` into `core/embeddings.py` verbatim (the module is self-contained: `MODEL_NAME`, `EMBED_DIM`, `DEFAULT_BATCH`, `MAX_SEQ_LENGTH`, `MAX_CHARS`, `_model`, `get_model()`, `embed_texts()`). Change the logger name to `"jar.embeddings"`.

- [ ] **Step 4: Replace `core/email/embedder.py` with a re-export**

```python
"""Backwards-compatible re-export. The embedder now lives in core.embeddings
so both the email and message pipelines share one model load and vector space."""
from core.embeddings import (  # noqa: F401
    MODEL_NAME, EMBED_DIM, DEFAULT_BATCH, MAX_SEQ_LENGTH, MAX_CHARS,
    get_model, embed_texts,
)

__all__ = [
    "MODEL_NAME", "EMBED_DIM", "DEFAULT_BATCH", "MAX_SEQ_LENGTH",
    "MAX_CHARS", "get_model", "embed_texts",
]
```

- [ ] **Step 5: Update `core/email/vector_store.py:19`**

Change `from core.email.embedder import EMBED_DIM` → `from core.embeddings import EMBED_DIM`.

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_shared_embeddings.py tests/ -k "email" -v`
Expected: PASS (email tests still green via the re-export).

- [ ] **Step 7: Commit**

```bash
git add core/embeddings.py core/email/embedder.py core/email/vector_store.py tests/test_shared_embeddings.py
git commit -m "refactor: lift bge-m3 embedder to shared core.embeddings"
```

---

## Task 2: Add `message_sync_state` schema

**Files:**
- Modify: `core/database/schema.sql`
- Test: `tests/test_message_sync_state_schema.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_message_sync_state_schema.py
import sqlite3
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
    import pytest
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO message_sync_state (id, last_rowid) VALUES (2, 20)")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_message_sync_state_schema.py -v`
Expected: FAIL — `sqlite3.OperationalError: no such table: message_sync_state`

- [ ] **Step 3: Append the table to `core/database/schema.sql`**

```sql
CREATE TABLE IF NOT EXISTS message_sync_state (
    id              INTEGER PRIMARY KEY CHECK (id = 1), -- single-row table
    last_rowid      INTEGER,  -- highest message.ROWID ingested; the sync cursor
    last_guid       TEXT,     -- guid of the message at last_rowid (cursor anchor)
    db_fingerprint  TEXT,     -- detects chat.db replacement / VACUUM
    last_sync_at    TEXT
);
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_message_sync_state_schema.py -v`
Expected: PASS (verify `ensure_schema` runs `schema.sql` via `executescript`; if it doesn't, add `conn.executescript(open(SCHEMA_PATH).read())` is already how it works — confirm by reading `core/database/migrate.py`).

- [ ] **Step 5: Commit**

```bash
git add core/database/schema.sql tests/test_message_sync_state_schema.py
git commit -m "feat: add message_sync_state table"
```

---

## Task 3: `chat_db` pure helpers — Apple epoch + attributedBody decode

**Files:**
- Create: `core/messages/__init__.py` (empty), `core/messages/chat_db.py`
- Test: `tests/test_chat_db.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_chat_db.py
from core.messages import chat_db

def test_apple_time_nanoseconds():
    # 2024-01-01T00:00:00Z in Apple ns: (1704067200 - 978307200) * 1e9
    ns = (1704067200 - 978307200) * 1_000_000_000
    assert chat_db.apple_time_to_iso(ns).startswith("2024-01-01T00:00:00")

def test_apple_time_seconds_legacy():
    secs = 1704067200 - 978307200  # legacy rows store seconds
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_chat_db.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.messages'`

- [ ] **Step 3: Implement the pure helpers in `core/messages/chat_db.py`**

```python
"""Read-only reader for the macOS Messages database (~/Library/Messages/chat.db).

Opens the live DB in SQLite read-only + immutable mode (never a byte copy, which
could capture a torn write), decodes message bodies, and yields plain dicts. All
local; nothing here touches the network.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

logger = logging.getLogger("jar.messages.chat_db")

DEFAULT_CHAT_DB = Path.home() / "Library" / "Messages" / "chat.db"
APPLE_EPOCH = 978307200  # seconds between 1970-01-01 and 2001-01-01 UTC
_NS_THRESHOLD = 1e11     # values larger than this are nanoseconds, else seconds


class MessagesAccessError(Exception):
    """Raised when chat.db is missing or unreadable (usually no Full Disk Access)."""


def apple_time_to_iso(raw: Optional[int]) -> Optional[str]:
    """Convert a Messages `date` value (Apple epoch, ns or legacy s) to ISO 8601."""
    if raw is None:
        return None
    seconds = raw / 1_000_000_000 if raw > _NS_THRESHOLD else float(raw)
    return datetime.fromtimestamp(APPLE_EPOCH + seconds, tz=timezone.utc).isoformat()


def decode_attributed_body(blob: Optional[bytes]) -> str:
    """Best-effort decode of message.attributedBody (NSAttributedString typedstream).

    Heuristic parser (no external dependency): the message text sits right after the
    `NSString` class marker, preceded by a 5-byte type tag and a length prefix
    (single byte, or 0x81 + 2-byte little-endian for longer strings).
    """
    if not blob:
        return ""
    idx = blob.find(b"NSString")
    if idx == -1:
        return ""
    seg = blob[idx + len("NSString") + 5:]  # skip class marker + 5-byte type tag
    if not seg:
        return ""
    marker = seg[0]
    if marker == 0x81:
        length = int.from_bytes(seg[1:3], "little")
        start = 3
    else:
        length = marker
        start = 1
    try:
        return seg[start:start + length].decode("utf-8", errors="replace")
    except Exception:  # pragma: no cover - defensive
        return ""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_chat_db.py -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add core/messages/__init__.py core/messages/chat_db.py tests/test_chat_db.py
git commit -m "feat: chat.db Apple-epoch + attributedBody decode helpers"
```

---

## Task 4: `chat_db` reader — open, query, fingerprint

**Files:**
- Modify: `core/messages/chat_db.py`
- Test: `tests/test_chat_db.py` (extend)

- [ ] **Step 1: Write the failing test (synthetic chat.db fixture)**

```python
# tests/test_chat_db.py  (append)
import sqlite3

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
    conn.commit(); conn.close()
    assert chat_db.fingerprint(db) != fp1

def test_missing_db_raises(tmp_path):
    import pytest
    with pytest.raises(chat_db.MessagesAccessError):
        list(chat_db.iter_messages(tmp_path / "nope.db"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_chat_db.py -k "read_messages or fingerprint or missing_db" -v`
Expected: FAIL — `AttributeError: module 'core.messages.chat_db' has no attribute 'iter_messages'`

- [ ] **Step 3: Add the reader functions to `core/messages/chat_db.py`**

```python
_QUERY = """
SELECT m.ROWID AS rowid, m.guid AS guid, m.text AS text,
       m.attributedBody AS abody, m.date AS date_raw,
       m.is_from_me AS is_from_me, h.id AS handle, c.style AS chat_style
FROM message m
LEFT JOIN handle h ON m.handle_id = h.ROWID
LEFT JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
LEFT JOIN chat c ON cmj.chat_id = c.ROWID
{where}
ORDER BY m.ROWID ASC
"""


def open_readonly(db_path: Path) -> sqlite3.Connection:
    """Open chat.db read-only + immutable (never locks Messages, never a byte copy)."""
    db_path = Path(db_path)
    if not db_path.exists():
        raise MessagesAccessError(f"No Messages database found at {db_path}")
    uri = f"file:{db_path}?mode=ro&immutable=1"
    try:
        return sqlite3.connect(uri, uri=True)
    except sqlite3.OperationalError as exc:  # locked / permission
        raise MessagesAccessError(
            f"Cannot open {db_path}: {exc}. Grant Full Disk Access in "
            f"System Settings > Privacy & Security."
        ) from exc


def iter_messages(db_path: Path = DEFAULT_CHAT_DB, since_rowid: Optional[int] = None
                  ) -> Iterator[dict]:
    """Yield message dicts ordered by ROWID. Skips rows with no body (tapbacks etc.)."""
    conn = open_readonly(db_path)
    where = "WHERE m.ROWID > ?" if since_rowid is not None else ""
    params = (since_rowid,) if since_rowid is not None else ()
    skipped = 0
    try:
        cur = conn.execute(_QUERY.format(where=where), params)
        for r in cur:
            text = r["text"] if isinstance(r, sqlite3.Row) else r[2]
    finally:
        conn.close()
    # NOTE: replaced below by row-factory implementation
```

Replace the body of `iter_messages` with a row-factory version (clearer):

```python
def iter_messages(db_path: Path = DEFAULT_CHAT_DB, since_rowid: Optional[int] = None
                  ) -> Iterator[dict]:
    conn = open_readonly(db_path)
    conn.row_factory = sqlite3.Row
    where = "WHERE m.ROWID > ?" if since_rowid is not None else ""
    params = (since_rowid,) if since_rowid is not None else ()
    skipped = 0
    try:
        for r in conn.execute(_QUERY.format(where=where), params):
            text = r["text"] or decode_attributed_body(r["abody"])
            if not text or not text.strip():
                skipped += 1
                continue
            yield {
                "rowid": r["rowid"],
                "guid": r["guid"],
                "text": text,
                "date": apple_time_to_iso(r["date_raw"]),
                "is_from_me": int(r["is_from_me"] or 0),
                "handle": r["handle"],
                "is_group": (r["chat_style"] == 43),
            }
    finally:
        conn.close()
    if skipped:
        logger.debug("Skipped %d body-less messages", skipped)


def fingerprint(db_path: Path = DEFAULT_CHAT_DB) -> str:
    """Cheap hash that changes on DB replacement or VACUUM (ROWID renumber)."""
    import hashlib
    conn = open_readonly(db_path)
    try:
        cnt, lo, hi = conn.execute(
            "SELECT COUNT(*), MIN(ROWID), MAX(ROWID) FROM message"
        ).fetchone()
    finally:
        conn.close()
    return hashlib.sha1(f"{cnt}:{lo}:{hi}".encode()).hexdigest()
```

(Delete the first placeholder `iter_messages` so only the row-factory version remains.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_chat_db.py -v`
Expected: PASS (all chat_db tests green).

- [ ] **Step 5: Commit**

```bash
git add core/messages/chat_db.py tests/test_chat_db.py
git commit -m "feat: read-only chat.db reader with group flag, skip, fingerprint"
```

---

## Task 5: Contact resolution

**Files:**
- Create: `core/messages/contacts.py`
- Test: `tests/test_message_contacts.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_message_contacts.py
from core.messages import contacts

def test_normalize_phone_us_10_digit():
    assert contacts.normalize_handle("(617) 555-0142") == "+16175550142"

def test_normalize_phone_11_digit_leading_1():
    assert contacts.normalize_handle("1-617-555-0142") == "+16175550142"

def test_normalize_keeps_plus_e164():
    assert contacts.normalize_handle("+16175550142") == "+16175550142"

def test_normalize_email_lowercased():
    assert contacts.normalize_handle("Dr.Smith@Clinic.COM") == "dr.smith@clinic.com"

def test_resolve_with_map():
    cmap = {"+16175550142": "Dr. Smith"}
    assert contacts.resolve("(617) 555-0142", cmap) == "Dr. Smith"
    assert contacts.resolve("+19998887777", cmap) == "+19998887777"  # fallback raw
    assert contacts.resolve(None, cmap) == "Me"  # outgoing
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_message_contacts.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.messages.contacts'`

- [ ] **Step 3: Implement `core/messages/contacts.py`**

```python
"""Resolve iMessage/SMS handles (phone/email) to contact names via macOS AddressBook.

Reads AddressBook SQLite stores read-only. Phone numbers are normalized to E.164 on
both sides so matching is robust to formatting differences.
"""
from __future__ import annotations

import logging
import re
import sqlite3
from pathlib import Path
from typing import Optional

logger = logging.getLogger("jar.messages.contacts")

_ADDRESSBOOK_GLOB = "Library/Application Support/AddressBook/Sources/*/AddressBook-v22.abcddb"


def normalize_handle(handle: str) -> str:
    """E.164-normalize phone numbers; lowercase emails; pass others through."""
    if not handle:
        return handle
    h = handle.strip()
    if "@" in h:
        return h.lower()
    digits = re.sub(r"\D", "", h)
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    if h.startswith("+"):
        return "+" + digits
    return "+" + digits if digits else h


def resolve(handle: Optional[str], contact_map: dict) -> str:
    """Map a handle to a contact name; 'Me' for outgoing, raw handle if unknown."""
    if not handle:
        return "Me"
    norm = normalize_handle(handle)
    return contact_map.get(norm, handle)


def build_contact_map(home: Optional[Path] = None) -> dict:
    """Build {normalized_handle: full_name} from all AddressBook stores.

    Best-effort: returns {} if AddressBook is absent/unreadable (logged, not raised) —
    handle resolution simply falls back to raw handles.
    """
    home = home or Path.home()
    cmap: dict[str, str] = {}
    for abcddb in home.glob(_ADDRESSBOOK_GLOB):
        try:
            conn = sqlite3.connect(f"file:{abcddb}?mode=ro&immutable=1", uri=True)
        except sqlite3.OperationalError as exc:
            logger.warning("Cannot open AddressBook %s: %s", abcddb, exc)
            continue
        try:
            _load_store(conn, cmap)
        except sqlite3.OperationalError as exc:
            logger.warning("AddressBook schema unexpected in %s: %s", abcddb, exc)
        finally:
            conn.close()
    return cmap


def _full_name(first: Optional[str], last: Optional[str], org: Optional[str]) -> str:
    name = " ".join(p for p in (first, last) if p).strip()
    return name or (org or "").strip()


def _load_store(conn: sqlite3.Connection, cmap: dict) -> None:
    """Join phone/email rows to their owning record and fill the map."""
    phones = conn.execute(
        "SELECT r.ZFIRSTNAME, r.ZLASTNAME, r.ZORGANIZATION, p.ZFULLNUMBER "
        "FROM ZABCDPHONENUMBER p JOIN ZABCDRECORD r ON p.ZOWNER = r.Z_PK"
    ).fetchall()
    for first, last, org, number in phones:
        if number:
            cmap.setdefault(normalize_handle(number), _full_name(first, last, org))
    emails = conn.execute(
        "SELECT r.ZFIRSTNAME, r.ZLASTNAME, r.ZORGANIZATION, e.ZADDRESS "
        "FROM ZABCDEMAILADDRESS e JOIN ZABCDRECORD r ON e.ZOWNER = r.Z_PK"
    ).fetchall()
    for first, last, org, addr in emails:
        if addr:
            cmap.setdefault(normalize_handle(addr), _full_name(first, last, org))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_message_contacts.py -v`
Expected: PASS (5 passed). (`build_contact_map`/`_load_store` are exercised at integration time; the pure functions are unit-tested here.)

- [ ] **Step 5: Add an AddressBook fixture test for `_load_store`**

```python
# tests/test_message_contacts.py  (append)
import sqlite3

def test_load_store_from_synthetic_abcddb(tmp_path):
    db = tmp_path / "AddressBook-v22.abcddb"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE ZABCDRECORD (Z_PK INTEGER PRIMARY KEY,
            ZFIRSTNAME TEXT, ZLASTNAME TEXT, ZORGANIZATION TEXT);
        CREATE TABLE ZABCDPHONENUMBER (Z_PK INTEGER PRIMARY KEY,
            ZOWNER INTEGER, ZFULLNUMBER TEXT);
        CREATE TABLE ZABCDEMAILADDRESS (Z_PK INTEGER PRIMARY KEY,
            ZOWNER INTEGER, ZADDRESS TEXT);
        """
    )
    conn.execute("INSERT INTO ZABCDRECORD VALUES (1,'Jane','Smith',NULL)")
    conn.execute("INSERT INTO ZABCDPHONENUMBER VALUES (1,1,'(617) 555-0142')")
    conn.execute("INSERT INTO ZABCDEMAILADDRESS VALUES (1,1,'jane@x.com')")
    conn.commit(); conn.close()
    cmap = {}
    c = sqlite3.connect(db)
    contacts._load_store(c, cmap); c.close()
    assert cmap["+16175550142"] == "Jane Smith"
    assert cmap["jane@x.com"] == "Jane Smith"
```

Run: `pytest tests/test_message_contacts.py -v` → Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add core/messages/contacts.py tests/test_message_contacts.py
git commit -m "feat: AddressBook contact resolution (E.164)"
```

---

## Task 6: Messages vector store

**Files:**
- Create: `core/messages/vector_store.py`
- Test: `tests/test_message_vector_store.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_message_vector_store.py
from core.messages import vector_store

def test_upsert_and_count_and_search(tmp_path):
    rows = [{
        "guid": "g1", "text": "dentist appt tuesday", "contact_name": "Dr. Smith",
        "is_from_me": 0, "is_group": False, "chat_id": 1, "date": "2024-01-01T00:00:00+00:00",
        "vector": [0.1] * vector_store_dim(),
    }]
    assert vector_store.upsert(rows, db_dir=tmp_path) == 1
    assert vector_store.count(db_dir=tmp_path) == 1
    # re-upsert same guid does not duplicate
    vector_store.upsert(rows, db_dir=tmp_path)
    assert vector_store.count(db_dir=tmp_path) == 1
    hits = vector_store.search([0.1] * vector_store_dim(), limit=5, db_dir=tmp_path)
    assert hits and hits[0]["guid"] == "g1" and "vector" not in hits[0]

def vector_store_dim():
    from core.embeddings import EMBED_DIM
    return EMBED_DIM
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_message_vector_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.messages.vector_store'`

- [ ] **Step 3: Implement `core/messages/vector_store.py`** (mirror of `core/email/vector_store.py`)

```python
"""LanceDB vector store for embedded text messages (data/lancedb/, table `messages`).

One row per message keyed by `guid`. Mirrors core/email/vector_store.py. Local-only.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import lancedb
from lancedb.pydantic import LanceModel, Vector

from core.embeddings import EMBED_DIM

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
LANCEDB_DIR = PROJECT_ROOT / "data" / "lancedb"
TABLE_NAME = "messages"


class MessageRecord(LanceModel):
    guid: str
    text: str
    contact_name: str
    is_from_me: int
    is_group: bool
    chat_id: int
    date: str
    vector: Vector(EMBED_DIM)  # type: ignore[valid-type]


def connect(db_dir: Optional[Path] = None):
    db_dir = Path(db_dir) if db_dir else LANCEDB_DIR
    db_dir.mkdir(parents=True, exist_ok=True)
    return lancedb.connect(str(db_dir))


def open_table(db=None, db_dir: Optional[Path] = None):
    db = db or connect(db_dir)
    if TABLE_NAME in db.list_tables().tables:
        return db.open_table(TABLE_NAME)
    return db.create_table(TABLE_NAME, schema=MessageRecord)


def upsert(records: list[dict], db_dir: Optional[Path] = None) -> int:
    if not records:
        return 0
    table = open_table(db_dir=db_dir)
    (table.merge_insert("guid")
        .when_matched_update_all()
        .when_not_matched_insert_all()
        .execute(records))
    return len(records)


def count(db_dir: Optional[Path] = None) -> int:
    db = connect(db_dir)
    if TABLE_NAME not in db.list_tables().tables:
        return 0
    return db.open_table(TABLE_NAME).count_rows()


def search(query_vector: list[float], limit: int = 5, db_dir: Optional[Path] = None) -> list[dict]:
    db = connect(db_dir)
    if TABLE_NAME not in db.list_tables().tables:
        return []
    rows = (db.open_table(TABLE_NAME).search(query_vector)
            .metric("cosine").limit(limit).to_list())
    for row in rows:
        row.pop("vector", None)
    return rows
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_message_vector_store.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core/messages/vector_store.py tests/test_message_vector_store.py
git commit -m "feat: LanceDB messages vector store"
```

---

## Task 7: Full-scan pipeline

**Files:**
- Create: `core/messages/pipeline.py`
- Test: `tests/test_message_pipeline.py`

- [ ] **Step 1: Write the failing test (embedder + contacts monkeypatched)**

```python
# tests/test_message_pipeline.py
from core.messages import pipeline, chat_db
from tests.test_chat_db import _make_chat_db  # reuse fixture builder

def test_run_full_scan(tmp_path, monkeypatch):
    db = tmp_path / "chat.db"
    _make_chat_db(db)
    monkeypatch.setattr(pipeline.contacts, "build_contact_map",
                        lambda home=None: {"+16175550142": "Dr. Smith"})
    monkeypatch.setattr(pipeline.embeddings, "embed_texts",
                        lambda texts, **k: [[0.1] * 1024 for _ in texts])
    summary = pipeline.run_full_scan(chat_db_path=db, db_dir=tmp_path)
    assert summary["stored"] == 2          # 2 bodies, tapback skipped
    assert summary["last_rowid"] == 2
    assert summary["last_guid"] == "g2"
    from core.messages import vector_store
    rows = vector_store.search([0.1] * 1024, limit=5, db_dir=tmp_path)
    names = {r["contact_name"] for r in rows}
    assert "Dr. Smith" in names and "Me" in names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_message_pipeline.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.messages.pipeline'`

- [ ] **Step 3: Implement `core/messages/pipeline.py`**

```python
"""Full-scan orchestrator: read chat.db -> resolve contacts -> embed -> upsert."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Optional

from core import embeddings
from core.messages import chat_db, contacts, vector_store

logger = logging.getLogger("jar.messages.pipeline")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH = PROJECT_ROOT / "data" / "jar.db"
EMBED_BATCH = 200  # messages per embed+upsert batch


def run_full_scan(chat_db_path: Path = chat_db.DEFAULT_CHAT_DB,
                  since_rowid: Optional[int] = None,
                  progress_cb: Optional[Callable] = None,
                  db_dir: Optional[Path] = None) -> dict:
    """Read messages, embed, and upsert into LanceDB. Returns a sync summary.

    Raises chat_db.MessagesAccessError if the DB is missing/unreadable.
    """
    contact_map = contacts.build_contact_map()
    stored = 0
    last_rowid: Optional[int] = since_rowid
    last_guid: Optional[str] = None
    batch: list[dict] = []

    def flush():
        nonlocal stored
        if not batch:
            return
        vectors = embeddings.embed_texts([b["text"] for b in batch])
        for row, vec in zip(batch, vectors):
            row["vector"] = vec
        vector_store.upsert(batch, db_dir=db_dir)
        stored += len(batch)
        if progress_cb:
            progress_cb({"stored": stored})
        batch.clear()

    for msg in chat_db.iter_messages(chat_db_path, since_rowid=since_rowid):
        batch.append({
            "guid": msg["guid"],
            "text": msg["text"],
            "contact_name": contacts.resolve(msg["handle"], contact_map),
            "is_from_me": msg["is_from_me"],
            "is_group": msg["is_group"],
            "chat_id": 0,
            "date": msg["date"] or "",
        })
        last_rowid = msg["rowid"]
        last_guid = msg["guid"]
        if len(batch) >= EMBED_BATCH:
            flush()
    flush()

    return {
        "stored": stored,
        "last_rowid": last_rowid,
        "last_guid": last_guid,
        "fingerprint": chat_db.fingerprint(chat_db_path),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_message_pipeline.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core/messages/pipeline.py tests/test_message_pipeline.py
git commit -m "feat: message full-scan pipeline"
```

---

## Task 8: Incremental sync (cursor + fingerprint/guid validation)

**Files:**
- Create: `core/messages/sync.py`
- Test: `tests/test_message_sync.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_message_sync.py
import sqlite3
from core.database.migrate import ensure_schema
from core.messages import sync, pipeline, chat_db
from tests.test_chat_db import _make_chat_db

def _patch(monkeypatch):
    monkeypatch.setattr(pipeline.contacts, "build_contact_map", lambda home=None: {})
    monkeypatch.setattr(pipeline.embeddings, "embed_texts",
                        lambda texts, **k: [[0.1] * 1024 for _ in texts])

def test_first_run_full_scan(tmp_path, monkeypatch):
    _patch(monkeypatch)
    jar = tmp_path / "jar.db"; conn = sqlite3.connect(jar); ensure_schema(conn); conn.close()
    chatdb = tmp_path / "chat.db"; _make_chat_db(chatdb)
    summary = sync.incremental_sync(chat_db_path=chatdb, db_path=jar, db_dir=tmp_path)
    assert summary["new_messages"] == 2
    state = sync.get_state(sqlite3.connect(jar))
    assert state["last_rowid"] == 2 and state["last_guid"] == "g2"

def test_second_run_no_new(tmp_path, monkeypatch):
    _patch(monkeypatch)
    jar = tmp_path / "jar.db"; conn = sqlite3.connect(jar); ensure_schema(conn); conn.close()
    chatdb = tmp_path / "chat.db"; _make_chat_db(chatdb)
    sync.incremental_sync(chat_db_path=chatdb, db_path=jar, db_dir=tmp_path)
    summary = sync.incremental_sync(chat_db_path=chatdb, db_path=jar, db_dir=tmp_path)
    assert summary["new_messages"] == 0  # cursor valid, nothing past rowid 2

def test_stale_cursor_forces_full(tmp_path, monkeypatch):
    _patch(monkeypatch)
    jar = tmp_path / "jar.db"; conn = sqlite3.connect(jar); ensure_schema(conn)
    # seed a bogus cursor whose guid anchor won't match
    sync.upsert_state(conn, last_rowid=1, last_guid="WRONG", fingerprint="stale")
    conn.close()
    chatdb = tmp_path / "chat.db"; _make_chat_db(chatdb)
    summary = sync.incremental_sync(chat_db_path=chatdb, db_path=jar, db_dir=tmp_path)
    assert summary["full_rescan"] is True and summary["new_messages"] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_message_sync.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.messages.sync'`

- [ ] **Step 3: Implement `core/messages/sync.py`**

```python
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


def upsert_state(conn, last_rowid, last_guid, fingerprint) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO message_sync_state (id, last_rowid, last_guid, db_fingerprint, last_sync_at)
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
    conn = sqlite3.connect(str(db_path)); ensure_schema(conn)
    state = get_state(conn); conn.close()

    valid = _cursor_valid(state, chat_db_path)
    since = state["last_rowid"] if valid else None
    summary = pipeline.run_full_scan(
        chat_db_path=chat_db_path, since_rowid=since,
        progress_cb=progress_cb, db_dir=db_dir,
    )

    conn = sqlite3.connect(str(db_path))
    if summary["last_rowid"] is not None:
        upsert_state(conn, summary["last_rowid"], summary["last_guid"], summary["fingerprint"])
    conn.close()

    summary["new_messages"] = summary["stored"]
    summary["full_rescan"] = not valid
    return summary
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_message_sync.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add core/messages/sync.py tests/test_message_sync.py
git commit -m "feat: incremental message sync with VACUUM-safe cursor"
```

---

## Task 9: RedactionEngine — `lowercase_for_ner` flag + `register_person`

**Files:**
- Modify: `core/privacy/redactor.py`
- Test: `tests/test_redactor_message_mode.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_redactor_message_mode.py
from core.privacy.redactor import RedactionEngine
from core.privacy.profile import HouseholdProfile

def test_register_person_tokenizes_known_name():
    eng = RedactionEngine(HouseholdProfile(), lowercase_for_ner=False)
    eng.register_person("Dr. Smith")
    out = eng.redact("Reminder from Dr. Smith about your appointment")
    assert "Dr. Smith" not in out
    assert eng.detokenize(out) == "Reminder from Dr. Smith about your appointment"

def test_lowercase_flag_defaults_true_backcompat():
    eng = RedactionEngine(HouseholdProfile())
    assert eng._lowercase_for_ner is True  # legacy bank behavior preserved
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_redactor_message_mode.py -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'lowercase_for_ner'`

- [ ] **Step 3: Modify `core/privacy/redactor.py`**

In `__init__`, change the signature and store the flag:

```python
def __init__(self, profile: HouseholdProfile, lowercase_for_ner: bool = True) -> None:
    self._profile = profile
    self._lowercase_for_ner = lowercase_for_ner
    self._store = TokenStore()
    # ... rest unchanged ...
```

In `_pass2_presidio`, replace the unconditional lowercase with the flag:

```python
        # Bank statements are ALL-CAPS (NER needs lowercase); conversational text
        # keeps its casing (NER needs the capitalization cues).
        analyzed_text = text.lower() if self._lowercase_for_ner else text
        try:
            results = self._analyzer.analyze(
                text=analyzed_text,
                language="en",
                entities=_PRESIDIO_ENTITIES,
                score_threshold=0.45,
            )
```

(The span-application code already reads from the original `text`, so positions stay valid for both branches since `.lower()` is a 1-to-1 map.)

Add a public method near `detokenize`:

```python
    def register_person(self, name: str) -> str:
        """Pre-register a known person name so it tokenizes deterministically.

        Used by the cloud-boundary helper to seed resolved contact names before
        redacting excerpts. Returns the assigned token.
        """
        name = (name or "").strip()
        if not name:
            return ""
        return self._store.get_or_create("PERSON", name)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_redactor_message_mode.py tests/test_redaction.py -v`
Expected: PASS (new tests pass; existing redaction tests still green — default `True` preserves behavior).

- [ ] **Step 5: Commit**

```bash
git add core/privacy/redactor.py tests/test_redactor_message_mode.py
git commit -m "feat: RedactionEngine lowercase_for_ner flag + register_person"
```

---

## Task 10: Shared cloud-boundary redaction helper

**Files:**
- Create: `core/privacy/cloud_boundary.py`
- Test: `tests/test_cloud_boundary.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cloud_boundary.py
from core.privacy.redactor import RedactionEngine
from core.privacy.profile import HouseholdProfile
from core.privacy import cloud_boundary

def test_redact_excerpts_round_trip():
    eng = RedactionEngine(HouseholdProfile(), lowercase_for_ner=False)
    rows = [{"contact_name": "Dr. Smith", "date": "2024-01-01T00:00:00+00:00",
             "text": "Dr. Smith here — call 617-555-0142 to confirm."}]
    out = cloud_boundary.redact_rows(
        eng, rows, text_fields=["text", "contact_name"], seed_names=["Dr. Smith"],
    )
    assert "Dr. Smith" not in out[0]["text"]
    assert "617-555-0142" not in out[0]["text"]
    assert out[0]["date"] == "2024-01-01T00:00:00+00:00"  # untouched
    # answer-time reversal
    answer = f"{out[0]['contact_name']} wants you to call back."
    assert eng.detokenize(answer) == "Dr. Smith wants you to call back."

def test_redact_rows_no_seed_names():
    eng = RedactionEngine(HouseholdProfile(), lowercase_for_ner=False)
    rows = [{"sender": "chase.com", "subject": "x", "date": "d",
             "body": "Email me at john@x.com"}]
    out = cloud_boundary.redact_rows(eng, rows, text_fields=["body"], seed_names=None)
    assert "john@x.com" not in out[0]["body"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cloud_boundary.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.privacy.cloud_boundary'`

- [ ] **Step 3: Implement `core/privacy/cloud_boundary.py`**

```python
"""Redact retrieved excerpts immediately before they cross the network to the cloud
LLM. Shared by the message and email search tools. Reversal happens at answer time
via RedactionEngine.detokenize() on the model's final text.
"""
from __future__ import annotations

from typing import Optional

from core.privacy.redactor import RedactionEngine


def redact_rows(engine: RedactionEngine, rows: list[dict], text_fields: list[str],
                seed_names: Optional[list[str]] = None) -> list[dict]:
    """Return copies of `rows` with `text_fields` redacted via `engine`.

    `seed_names` (e.g. resolved contact names) are pre-registered so they tokenize
    deterministically rather than relying on NER. Non-listed fields pass through
    unchanged. The engine's TokenStore is the reversal key — keep it for the session.
    """
    for name in seed_names or []:
        engine.register_person(name)
    redacted: list[dict] = []
    for row in rows:
        new_row = dict(row)
        for field in text_fields:
            if isinstance(new_row.get(field), str):
                new_row[field] = engine.redact(new_row[field])
        redacted.append(new_row)
    return redacted
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cloud_boundary.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core/privacy/cloud_boundary.py tests/test_cloud_boundary.py
git commit -m "feat: shared cloud-boundary excerpt redaction helper"
```

---

## Task 11: Chat-engine integration (search_messages + redaction + email retrofit)

**Files:**
- Modify: `app/chat/chat_engine.py`
- Test: `tests/test_chat_engine_messages.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_chat_engine_messages.py
import sqlite3
from app.chat.chat_engine import JarChat
from core.database.migrate import ensure_schema

def _engine(tmp_path, monkeypatch):
    db = tmp_path / "jar.db"; c = sqlite3.connect(db); ensure_schema(c); c.close()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    return JarChat(db_path=str(db))

def test_search_messages_redacts_excerpts(tmp_path, monkeypatch):
    jc = _engine(tmp_path, monkeypatch)
    monkeypatch.setattr("core.embeddings.embed_texts", lambda t, **k: [[0.1] * 1024])
    monkeypatch.setattr("core.messages.vector_store.search",
        lambda v, limit=5, db_dir=None: [{
            "guid": "g1", "contact_name": "Dr. Smith", "is_from_me": 0,
            "is_group": False, "date": "2024-01-01T00:00:00+00:00",
            "text": "Dr. Smith: appt is Tuesday, call 617-555-0142",
        }])
    result = jc._search_messages("dentist", limit=5)
    body = result["messages"][0]["text"]
    assert "Dr. Smith" not in body and "617-555-0142" not in body
    # token reverses through the session engine
    assert jc.redaction.detokenize(result["messages"][0]["contact_name"]) == "Dr. Smith"

def test_email_path_now_redacts(tmp_path, monkeypatch):
    jc = _engine(tmp_path, monkeypatch)
    monkeypatch.setattr("core.embeddings.embed_texts", lambda t, **k: [[0.1] * 1024])
    monkeypatch.setattr("core.email.vector_store.search",
        lambda v, limit=5, db_dir=None: [{
            "sender": "clinic.com", "subject": "appt", "date": "d",
            "body": "Reach me at john@x.com",
        }])
    result = jc._search_emails("appt", limit=5)
    assert "john@x.com" not in result["emails"][0]["body"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_chat_engine_messages.py -v`
Expected: FAIL — `AttributeError: 'JarChat' object has no attribute '_search_messages'` (and no `redaction`).

- [ ] **Step 3: Wire the chat engine**

In `JarChat.__init__` (after `self.system_prompt = ...`), build the session redaction engine:

```python
        from core.privacy.profile import HouseholdProfile
        from core.privacy.redactor import RedactionEngine
        _conn = sqlite3.connect(self.db_path)
        try:
            _profile = HouseholdProfile.load(_conn)
        finally:
            _conn.close()
        # lowercase_for_ner=False: chat excerpts keep their natural casing for NER.
        self.redaction = RedactionEngine(_profile, lowercase_for_ner=False)
        if not _profile.is_configured():
            import logging
            logging.getLogger("jar.chat").warning(
                "Household profile not configured — redaction relies on NER only."
            )
```

(Add `import sqlite3` at the top of the file if not already present.)

Add the constants and the new tool method (mirrors `_search_emails`):

```python
    MAX_MESSAGE_RESULTS = 5
    MAX_MESSAGE_BODY_CHARS = 600

    def _search_messages(self, query: str, limit: int = 5) -> dict:
        """Semantic search over the synced message vector store, redacted for cloud."""
        from core import embeddings
        from core.messages import vector_store
        from core.privacy import cloud_boundary

        query = (query or "").strip()
        if not query:
            return {"error": "empty query"}
        limit = min(limit or self.MAX_MESSAGE_RESULTS, self.MAX_MESSAGE_RESULTS)
        try:
            vector = embeddings.embed_texts([query])[0]
            rows = vector_store.search(vector, limit=limit)
        except Exception as exc:
            return {"error": f"message search failed: {exc}"}

        trimmed = [{
            "contact_name": r.get("contact_name", ""),
            "direction": "me" if r.get("is_from_me") else "them",
            "date": r.get("date", ""),
            "text": (r.get("text") or "")[:self.MAX_MESSAGE_BODY_CHARS],
        } for r in rows]
        seeds = [r.get("contact_name", "") for r in rows]
        redacted = cloud_boundary.redact_rows(
            self.redaction, trimmed, text_fields=["text", "contact_name"], seed_names=seeds,
        )
        return {"count": len(redacted), "messages": redacted}
```

Retrofit `_search_emails` — after building the `emails` list, redact before returning:

```python
        from core.privacy import cloud_boundary
        emails = cloud_boundary.redact_rows(
            self.redaction, emails, text_fields=["body", "subject"], seed_names=None,
        )
        return {"count": len(emails), "emails": emails}
```

Route the new tool in `_execute_tool` (next to the `search_emails` branch):

```python
        if name == "search_messages":
            return self._search_messages(params.get("query", ""), params.get("limit", 5))
```

Detokenize the final answer in `chat()` — change the return:

```python
        self.conversation_history.append({"role": "assistant", "content": response.content})
        final_text = self.redaction.detokenize(final_text)
        import re
        if re.search(r"\[[A-Z_]+(?:_\d+)?\]", final_text):
            import logging
            logging.getLogger("jar.chat").warning("Undetokenized token survived in answer")
        return final_text
```

Add the `search_messages` tool schema to the `TOOLS` list (after `search_emails`):

```python
    {
        "name": "search_messages",
        "description": (
            "Semantic search over the user's synced text messages (iMessage/SMS). "
            "Use for ANY question about texts — appointments, orders, confirmations, "
            "people, plans. Returns the most relevant messages (contact name, "
            "direction, date, text)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to look for, in natural language."},
                "limit": {"type": "integer", "description": "Max messages, default 5 (capped at 5)."},
            },
            "required": ["query"],
        },
    },
```

Update the system prompt in `_build_system_prompt` to mention `search_messages` for text-message questions (one sentence mirroring the email guidance).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_chat_engine_messages.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Run the full suite**

Run: `pytest tests/ -q`
Expected: all green (existing email chat tests still pass with redaction now applied; if any asserts on exact unredacted body text, update it to assert on the redacted/detokenized round-trip).

- [ ] **Step 6: Commit**

```bash
git add app/chat/chat_engine.py tests/test_chat_engine_messages.py
git commit -m "feat: search_messages tool + cloud-boundary redaction (email retrofit)"
```

---

## Task 12: Server endpoints, nightly job, Presidio warm

**Files:**
- Modify: `app/server.py`
- Test: `tests/test_server_messages.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_server_messages.py
from fastapi.testclient import TestClient
import app.server as server

def test_message_sync_endpoint(monkeypatch):
    monkeypatch.setattr("core.messages.sync.incremental_sync",
        lambda **k: {"new_messages": 3, "full_rescan": False, "stored": 3})
    client = TestClient(server.app)
    resp = client.post("/api/messages/sync")
    assert resp.status_code == 200
    assert resp.json()["new_messages"] == 3

def test_message_sync_reports_access_error(monkeypatch):
    from core.messages.chat_db import MessagesAccessError
    def boom(**k):
        raise MessagesAccessError("Grant Full Disk Access")
    monkeypatch.setattr("core.messages.sync.incremental_sync", boom)
    client = TestClient(server.app)
    resp = client.post("/api/messages/sync")
    assert resp.status_code == 400
    assert "Full Disk Access" in resp.json()["detail"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_server_messages.py -v`
Expected: FAIL — 404 (endpoint not defined).

- [ ] **Step 3: Add endpoints + scheduler extension to `app/server.py`**

Add endpoints (place near the email sync endpoint around line 1231):

```python
@app.post("/api/messages/scan")
async def messages_scan():
    from core.messages import pipeline
    from core.messages.chat_db import MessagesAccessError
    loop = asyncio.get_event_loop()
    try:
        summary = await loop.run_in_executor(None, pipeline.run_full_scan)
        return summary
    except MessagesAccessError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/messages/sync")
async def messages_sync():
    from core.messages import sync as msg_sync
    from core.messages.chat_db import MessagesAccessError
    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(None, msg_sync.incremental_sync)
    except MessagesAccessError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
```

(Ensure `from fastapi import HTTPException` is imported.)

Extend `_nightly_sync` so the two syncs are isolated (one failing never blocks the other):

```python
    async def _nightly_sync() -> None:
        loop = asyncio.get_event_loop()
        # Email (existing) — isolated
        try:
            from core.email import imap_client, sync as email_sync
            if imap_client.load_account() is not None:
                result = await loop.run_in_executor(None, email_sync.incremental_sync)
                _scheduler_logger.info("Nightly email sync completed: %s", result)
        except Exception:
            _scheduler_logger.exception("Nightly email sync failed")
        # Messages (new) — isolated
        try:
            from core.messages import sync as msg_sync
            result = await loop.run_in_executor(None, msg_sync.incremental_sync)
            _scheduler_logger.info("Nightly message sync completed: %s", result)
        except Exception:
            _scheduler_logger.exception("Nightly message sync failed")
```

Warm Presidio at startup (in the existing startup handler that starts the scheduler):

```python
    try:
        from core.privacy.redactor import _get_shared_presidio
        analyzer, _ = _get_shared_presidio()
        if analyzer is None:
            _scheduler_logger.warning("Presidio unavailable — chat redaction is NER-degraded.")
    except Exception:
        _scheduler_logger.exception("Presidio warm-up failed")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_server_messages.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add app/server.py tests/test_server_messages.py
git commit -m "feat: message scan/sync endpoints, nightly job, Presidio warm"
```

---

## Task 13: Docs + requirements

**Files:**
- Modify: `requirements.txt`, `CLAUDE.md`
- Create: `doc/message-pipeline-architecture.md`

- [ ] **Step 1: requirements.txt**

No new hard dependency is required (the `attributedBody` decoder is pure-Python heuristic). Add a comment group documenting the optional enhancement:

```
# Text-message ingestion (macOS chat.db). The attributedBody decoder is a built-in
# pure-Python heuristic; no extra runtime dependency is required.
```

- [ ] **Step 2: Architecture doc**

Create `doc/message-pipeline-architecture.md` mirroring `doc/email-pipeline-architecture.md`: overview diagram, component table, full-scan/incremental/query flows, storage table, privacy model (redaction at the cloud boundary), and the Full Disk Access requirement.

- [ ] **Step 3: CLAUDE.md**

Add a short "Text-message scanning (macOS)" section under the Google Drive section: the `chat.db` source, the Full Disk Access requirement, and the `search_messages` chat tool.

- [ ] **Step 4: Run the full suite + commit**

Run: `pytest tests/ -q`
Expected: all green.

```bash
git add requirements.txt doc/message-pipeline-architecture.md CLAUDE.md
git commit -m "docs: message pipeline architecture + Full Disk Access notes"
```

---

## Self-Review notes (author)

- **Spec coverage:** §3 reader → Tasks 3–5; §4 components → Tasks 1,3–8,10; §5 storage → Tasks 2,6; §6 flows → Tasks 7,8,12; §7 redaction → Tasks 9–11; §8 chat → Task 11; §9 server → Task 12; §11 tests → every task; §12 defaults (3yr/group/shared embedder) → `days_back` default in `run_full_scan` caller (set the API/CLI default to 1095), `is_group` in Task 4, Task 1. **Action:** when wiring the scan entry point/UI, pass `days_back=1095`; `iter_messages` currently reads all rows — add an optional `since_date` filter when a 3-year cap is desired (follow-up, not blocking first scan).
- **Placeholder scan:** none — all code shown.
- **Type consistency:** message dict keys (`rowid/guid/text/date/is_from_me/handle/is_group`) are consistent across `chat_db` → `pipeline`; vector rows (`guid/text/contact_name/is_from_me/is_group/chat_id/date/vector`) match `MessageRecord`; `redact_rows(engine, rows, text_fields, seed_names)` signature consistent in Tasks 10–11.
