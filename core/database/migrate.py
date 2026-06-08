"""Idempotent schema migrations for the JAR SQLite database.

The project has no migrations framework — schema.sql is applied with
`executescript` and `CREATE TABLE IF NOT EXISTS`. That covers new tables, but it
cannot evolve an existing `transactions` table (SQLite can't ALTER a column from
NOT NULL to nullable). `ensure_schema()` bridges that gap:

1. Rebuild `transactions` once to make `statement_id` nullable and add the
   `source` / `dedupe_hash` columns the email pipeline needs (guarded so it runs
   at most once).
2. Apply schema.sql to create any missing tables/indexes (documents,
   email_sync_state, ...).
3. Ensure the single real `accounts` row that email-sourced transactions hang
   off (statement_id is NULL for them, but account_id is still NOT NULL).

Safe to call on every server start and at the top of the email pipeline.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"
DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "jar.db"

EMAIL_ACCOUNT_ID = "email"

# Columns carried over verbatim from the pre-migration transactions table.
_LEGACY_TXN_COLUMNS = [
    "transaction_id", "statement_id", "account_id", "date",
    "description_raw", "description_normalized", "amount", "is_deposit",
    "type", "category", "subcategory", "recurring",
    "ach_company_name", "ach_description", "ach_individual_name",
    "ach_entry_class", "ach_company_id", "ach_trace_number",
    "ach_secondary_co_id", "ach_originator",
]


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _transactions_needs_rebuild(conn: sqlite3.Connection) -> bool:
    cols = _table_columns(conn, "transactions")
    if not cols:
        return False  # fresh DB — schema.sql will create the new shape directly
    return "source" not in cols


def _rebuild_transactions(conn: sqlite3.Connection) -> None:
    """Recreate `transactions` with nullable statement_id + source/dedupe_hash.

    Standard SQLite table-rebuild: copy into a new table, drop, rename, reindex.
    FKs are toggled off for the swap (user_category_overrides references this
    table by name, so it re-binds to the new table after rename).
    """
    carried = ", ".join(_LEGACY_TXN_COLUMNS)
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.executescript(
        f"""
        BEGIN;
        CREATE TABLE transactions_new (
            transaction_id          TEXT PRIMARY KEY,
            statement_id            TEXT REFERENCES statements(statement_id),
            account_id              TEXT NOT NULL REFERENCES accounts(account_id),
            date                    TEXT NOT NULL,
            description_raw         TEXT,
            description_normalized  TEXT,
            amount                  REAL NOT NULL,
            is_deposit              INTEGER NOT NULL,
            type                    TEXT,
            category                TEXT,
            subcategory             TEXT,
            recurring               INTEGER,
            source                  TEXT NOT NULL DEFAULT 'bank_statement',
            dedupe_hash             TEXT,
            ach_company_name        TEXT,
            ach_description         TEXT,
            ach_individual_name     TEXT,
            ach_entry_class         TEXT,
            ach_company_id          TEXT,
            ach_trace_number        TEXT,
            ach_secondary_co_id     TEXT,
            ach_originator          TEXT
        );
        INSERT INTO transactions_new ({carried})
            SELECT {carried} FROM transactions;
        DROP TABLE transactions;
        ALTER TABLE transactions_new RENAME TO transactions;
        CREATE INDEX IF NOT EXISTS idx_transactions_date       ON transactions(date);
        CREATE INDEX IF NOT EXISTS idx_transactions_account_id ON transactions(account_id);
        CREATE INDEX IF NOT EXISTS idx_transactions_type       ON transactions(type);
        CREATE INDEX IF NOT EXISTS idx_transactions_category   ON transactions(category);
        CREATE INDEX IF NOT EXISTS idx_transactions_amount     ON transactions(amount);
        CREATE INDEX IF NOT EXISTS idx_transactions_dedupe     ON transactions(dedupe_hash);
        COMMIT;
        """
    )
    conn.execute("PRAGMA foreign_keys=ON")


def _ensure_email_account(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO accounts (account_id, bank_name, account_type, holder_name) "
        "VALUES (?, 'Email', 'email', 'Email')",
        (EMAIL_ACCOUNT_ID,),
    )


def _ensure_email_sync_columns(conn: sqlite3.Connection) -> None:
    """Add last_uid / uidvalidity to email_sync_state on existing DBs."""
    cols = _table_columns(conn, "email_sync_state")
    if "last_uid" not in cols:
        conn.execute("ALTER TABLE email_sync_state ADD COLUMN last_uid INTEGER")
    if "uidvalidity" not in cols:
        conn.execute("ALTER TABLE email_sync_state ADD COLUMN uidvalidity TEXT")
    conn.commit()


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Bring `conn`'s database up to the current schema. Idempotent."""
    if _transactions_needs_rebuild(conn):
        _rebuild_transactions(conn)

    conn.executescript(SCHEMA_PATH.read_text())
    _ensure_email_sync_columns(conn)  # must run after executescript so table exists
    _ensure_email_account(conn)
    conn.commit()


if __name__ == "__main__":
    _conn = sqlite3.connect(str(DB_PATH))
    try:
        ensure_schema(_conn)
        cols = sorted(_table_columns(_conn, "transactions"))
        tables = [r[0] for r in _conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )]
        print(f"transactions columns: {cols}")
        print(f"tables: {tables}")
    finally:
        _conn.close()
