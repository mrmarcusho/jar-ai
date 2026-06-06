"""Stage 6 — DB writer: route an extracted email to the right table.

The hybrid data model:

- **Settled money movement** (financial bucket, a real amount, a real date,
  status ``settled``) → existing ``transactions`` table with ``source='email'``
  and ``statement_id=NULL``. This makes email spending show up in every finance
  view (category/merchant/trend/search) automatically, since no transaction
  query inner-joins ``statements``.
- **Everything else** (forward obligations, appointments, prose notices, and any
  financial item missing an amount/date) → ``documents`` table.

Privacy: only ``source_id = sha256(Message-ID)`` and redacted text are persisted.
The raw IMAP id and raw body never reach SQLite. Dedup is by a content hash so
re-syncing the same mailbox does not double-insert.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Optional

from core.database.migrate import EMAIL_ACCOUNT_ID
from core.email.bucket_classifier import BUCKET_FINANCIAL

SETTLED_TYPES = {
    "transaction", "order", "subscription", "receipt",
    "charge", "payment", "bill", "invoice",
}


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def source_id_for(message_id: str) -> str:
    """Stable, non-reversible id for an email. Raw Message-ID never stored."""
    return _sha256(message_id or "")


def _txn_dedupe_hash(source_id: str, date, amount, merchant) -> str:
    return _sha256(f"{source_id}{date}{amount}{merchant}")


def _doc_dedupe_hash(source_id: str, date, summary) -> str:
    return _sha256(f"{source_id}{date}{summary}")


def _hash_exists(conn: sqlite3.Connection, table: str, dedupe_hash: str) -> bool:
    row = conn.execute(
        f"SELECT 1 FROM {table} WHERE dedupe_hash = ? LIMIT 1", (dedupe_hash,)
    ).fetchone()
    return row is not None


def _is_settled_transaction(result: dict) -> bool:
    """A financial item we can put in the ledger needs a type, amount, and date."""
    if result.get("bucket") != BUCKET_FINANCIAL:
        return False
    if result.get("status") == "forward":
        return False
    if (result.get("type") or "").strip().lower() not in SETTLED_TYPES:
        return False
    return result.get("amount") is not None and bool(result.get("date"))


def write_transaction(
    conn: sqlite3.Connection, result: dict, source_id: str
) -> Optional[str]:
    """Insert a settled email money event into ``transactions``.

    Returns the transaction_id, or None if it was a duplicate.
    """
    dedupe_hash = _txn_dedupe_hash(
        source_id, result.get("date"), result.get("amount"), result.get("merchant")
    )
    if _hash_exists(conn, "transactions", dedupe_hash):
        return None

    transaction_id = f"email_{dedupe_hash}"
    is_deposit = 1 if result.get("direction") == "credit" else 0
    merchant = result.get("merchant") or result.get("sender_domain") or "Email"

    conn.execute(
        """
        INSERT INTO transactions (
            transaction_id, statement_id, account_id, date,
            description_raw, description_normalized, amount, is_deposit,
            type, category, source, dedupe_hash
        ) VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, 'email', ?)
        """,
        (
            transaction_id,
            EMAIL_ACCOUNT_ID,
            result.get("date"),
            merchant,
            merchant,
            float(result["amount"]),
            is_deposit,
            result.get("type"),
            result.get("category"),
            dedupe_hash,
        ),
    )
    return transaction_id


def write_document(
    conn: sqlite3.Connection, result: dict, source_id: str
) -> Optional[str]:
    """Insert a prose / forward-obligation email into ``documents``.

    Returns the document_id, or None if it was a duplicate.
    """
    summary = result.get("summary") or ""
    event_date = result.get("date")
    dedupe_hash = _doc_dedupe_hash(source_id, event_date, summary)
    if _hash_exists(conn, "documents", dedupe_hash):
        return None

    document_id = f"email_{dedupe_hash}"
    metadata = result.get("metadata") or {}

    conn.execute(
        """
        INSERT INTO documents (
            document_id, source, source_id, category, event_date,
            sender_domain, summary, action_required, deadline, amount,
            body_redacted, metadata, dedupe_hash
        ) VALUES (?, 'email', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            document_id,
            source_id,
            result.get("category"),
            event_date,
            result.get("sender_domain"),
            summary,
            1 if result.get("action_required") else 0,
            result.get("deadline"),
            result.get("amount"),
            result.get("_redacted_body"),
            json.dumps(metadata) if metadata else None,
            dedupe_hash,
        ),
    )
    return document_id


def route_and_write(
    conn: sqlite3.Connection, result: dict, message_id: str
) -> str:
    """Route an extracted email to the right table and write it.

    Returns: ``'transaction'``, ``'document'``, or ``'duplicate'``.
    """
    source_id = source_id_for(message_id)

    if _is_settled_transaction(result):
        if write_transaction(conn, result, source_id) is not None:
            return "transaction"
        return "duplicate"

    if write_document(conn, result, source_id) is not None:
        return "document"
    return "duplicate"
