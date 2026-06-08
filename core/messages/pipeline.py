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
    batch: list = []

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
