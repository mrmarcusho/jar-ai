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
    cmap: dict = {}
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
