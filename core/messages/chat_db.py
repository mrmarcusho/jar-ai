"""Read-only reader for the macOS Messages database (~/Library/Messages/chat.db).

Opens the live DB in SQLite read-only + immutable mode (never a byte copy, which
could capture a torn write), decodes message bodies, and yields plain dicts. All
local; nothing here touches the network.
"""
from __future__ import annotations

import hashlib
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


def _access_hint(db_path: Path, exc: Exception) -> str:
    return (
        f"Cannot read {db_path}: {exc}. Grant Full Disk Access in "
        f"System Settings > Privacy & Security > Full Disk Access, then restart the app."
    )


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
    except sqlite3.Error as exc:
        # Missing Full Disk Access denies the open here as a DatabaseError
        # ("authorization denied"); a locked DB raises OperationalError. Both are
        # sqlite3.Error subclasses, so catch the base and surface the FDA hint.
        raise MessagesAccessError(_access_hint(db_path, exc)) from exc


def iter_messages(db_path: Path = DEFAULT_CHAT_DB,
                  since_rowid: Optional[int] = None,
                  since_apple_ns: Optional[int] = None) -> Iterator[dict]:
    """Yield message dicts ordered by ROWID. Skips rows with no body (tapbacks etc.).

    `since_rowid` filters by the incremental cursor; `since_apple_ns` filters by date
    (Apple-epoch nanoseconds) for a bounded full scan (e.g. "last 90 days").
    """
    conn = open_readonly(db_path)
    conn.row_factory = sqlite3.Row
    clauses: list = []
    params: list = []
    if since_rowid is not None:
        clauses.append("m.ROWID > ?")
        params.append(since_rowid)
    if since_apple_ns is not None:
        clauses.append("m.date > ?")
        params.append(since_apple_ns)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    skipped = 0
    try:
        # The Full Disk Access denial surfaces here as a sqlite3 error
        # ("authorization denied" → DatabaseError), not at connect time.
        try:
            cursor = conn.execute(_QUERY.format(where=where), params)
        except sqlite3.Error as exc:
            raise MessagesAccessError(_access_hint(db_path, exc)) from exc
        for r in cursor:
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


def has_access(db_path: Path = DEFAULT_CHAT_DB) -> bool:
    """True if chat.db exists and is actually readable (i.e. Full Disk Access granted).

    macOS denies the read until the user grants Full Disk Access; the denial surfaces
    either at connect or on the first query, so we probe with a trivial SELECT.
    """
    try:
        conn = open_readonly(db_path)
    except MessagesAccessError:
        return False
    try:
        conn.execute("SELECT 1 FROM message LIMIT 1")
        return True
    except sqlite3.Error:
        # macOS FDA denial raises DatabaseError ("authorization denied"); other
        # sqlite errors also mean we can't read it. Either way: no usable access.
        return False
    finally:
        try:
            conn.close()
        except Exception:  # pragma: no cover - defensive
            pass


def fingerprint(db_path: Path = DEFAULT_CHAT_DB) -> str:
    """Cheap hash that changes on DB replacement or VACUUM (ROWID renumber)."""
    conn = open_readonly(db_path)
    try:
        try:
            cnt, lo, hi = conn.execute(
                "SELECT COUNT(*), MIN(ROWID), MAX(ROWID) FROM message"
            ).fetchone()
        except sqlite3.Error as exc:
            raise MessagesAccessError(_access_hint(db_path, exc)) from exc
    finally:
        conn.close()
    return hashlib.sha1(f"{cnt}:{lo}:{hi}".encode()).hexdigest()
