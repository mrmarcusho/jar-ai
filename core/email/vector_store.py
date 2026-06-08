"""LanceDB vector store for embedded emails (``data/lancedb/``).

A single table, ``emails``, holds one row per message keyed by ``message_id``.
Each row carries the raw sender/subject/date/body plus a 1024-dim bge-m3 vector
for semantic search. Writes use ``merge_insert`` so re-scanning the mailbox
updates existing rows instead of duplicating them.

Everything here is local: the database lives on disk under ``data/`` and the
vectors are produced on-device, so no email content ever leaves the machine.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import lancedb
from lancedb.pydantic import LanceModel, Vector

from core.embeddings import EMBED_DIM

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
LANCEDB_DIR = PROJECT_ROOT / "data" / "lancedb"
TABLE_NAME = "emails"


class EmailRecord(LanceModel):
    """One embedded email. ``message_id`` is the dedup key."""

    message_id: str
    sender: str
    subject: str
    date: str
    body: str
    vector: Vector(EMBED_DIM)  # type: ignore[valid-type]


def connect(db_dir: Optional[Path] = None):
    """Open (creating if needed) the LanceDB database directory."""
    db_dir = Path(db_dir) if db_dir else LANCEDB_DIR
    db_dir.mkdir(parents=True, exist_ok=True)
    return lancedb.connect(str(db_dir))


def open_table(db=None, db_dir: Optional[Path] = None):
    """Open the ``emails`` table, creating it from the schema if absent."""
    db = db or connect(db_dir)
    if TABLE_NAME in db.list_tables().tables:
        return db.open_table(TABLE_NAME)
    return db.create_table(TABLE_NAME, schema=EmailRecord)


def upsert(records: list[dict], db_dir: Optional[Path] = None) -> int:
    """Insert/update rows keyed by ``message_id``. Returns rows processed."""
    if not records:
        return 0
    table = open_table(db_dir=db_dir)
    (
        table.merge_insert("message_id")
        .when_matched_update_all()
        .when_not_matched_insert_all()
        .execute(records)
    )
    return len(records)


def count(db_dir: Optional[Path] = None) -> int:
    """Number of stored emails (0 if the table does not exist yet)."""
    db = connect(db_dir)
    if TABLE_NAME not in db.list_tables().tables:
        return 0
    return db.open_table(TABLE_NAME).count_rows()


def search(query_vector: list[float], limit: int = 10, db_dir: Optional[Path] = None) -> list[dict]:
    """Cosine nearest-neighbour search; returns matching rows (minus the vector)."""
    db = connect(db_dir)
    if TABLE_NAME not in db.list_tables().tables:
        return []
    rows = (
        db.open_table(TABLE_NAME)
        .search(query_vector)
        .metric("cosine")
        .limit(limit)
        .to_list()
    )
    for row in rows:
        row.pop("vector", None)
    return rows
