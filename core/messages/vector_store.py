"""LanceDB vector store for embedded text messages (data/lancedb/, table `messages`).

One row per message keyed by `guid`. Mirrors core/email/vector_store.py. Local-only:
the database lives on disk under data/ and vectors are produced on-device.
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
    """One embedded message. ``guid`` is the dedup key."""

    guid: str
    text: str
    contact_name: str
    is_from_me: int
    is_group: bool
    chat_id: int
    date: str
    vector: Vector(EMBED_DIM)  # type: ignore[valid-type]


def connect(db_dir: Optional[Path] = None):
    """Open (creating if needed) the LanceDB database directory."""
    db_dir = Path(db_dir) if db_dir else LANCEDB_DIR
    db_dir.mkdir(parents=True, exist_ok=True)
    return lancedb.connect(str(db_dir))


def open_table(db=None, db_dir: Optional[Path] = None):
    """Open the ``messages`` table, creating it from the schema if absent."""
    db = db or connect(db_dir)
    if TABLE_NAME in db.list_tables().tables:
        return db.open_table(TABLE_NAME)
    return db.create_table(TABLE_NAME, schema=MessageRecord)


def upsert(records: list, db_dir: Optional[Path] = None) -> int:
    """Insert/update rows keyed by ``guid``. Returns rows processed."""
    if not records:
        return 0
    table = open_table(db_dir=db_dir)
    (
        table.merge_insert("guid")
        .when_matched_update_all()
        .when_not_matched_insert_all()
        .execute(records)
    )
    return len(records)


def count(db_dir: Optional[Path] = None) -> int:
    """Number of stored messages (0 if the table does not exist yet)."""
    db = connect(db_dir)
    if TABLE_NAME not in db.list_tables().tables:
        return 0
    return db.open_table(TABLE_NAME).count_rows()


def search(query_vector: list, limit: int = 5, db_dir: Optional[Path] = None) -> list:
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
