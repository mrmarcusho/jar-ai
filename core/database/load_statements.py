#!/usr/bin/env python3
"""Load structured JSON statement files into the JAR SQLite database."""

import json
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "jar.db"
SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"

ACH_FIELDS = [
    "ach_company_name",
    "ach_description",
    "ach_individual_name",
    "ach_entry_class",
    "ach_company_id",
    "ach_trace_number",
    "ach_secondary_co_id",
    "ach_originator",
]


def init_db(conn: sqlite3.Connection) -> None:
    """Create tables from schema.sql if they don't exist."""
    schema_sql = SCHEMA_PATH.read_text()
    conn.executescript(schema_sql)


def make_statement_id(source_file: str) -> str:
    """Derive a statement_id from the source filename, e.g. 'stmt_2023-02-10'."""
    stem = Path(source_file).stem  # e.g. "eStmt_2023-02-10"
    return f"stmt_{stem.split('_', 1)[1]}" if "_" in stem else f"stmt_{stem}"


def load_one(conn: sqlite3.Connection, json_path: Path) -> dict:
    """Load a single structured JSON into the database. Returns stats dict."""
    data = json.loads(json_path.read_text())
    meta = data["metadata"]
    summary = data["account_summary"]
    validation = data["validation"]

    source_file = meta["source_file"]
    statement_id = make_statement_id(source_file)

    # Check for duplicate import
    existing = conn.execute(
        "SELECT 1 FROM statements WHERE source_file = ?", (source_file,)
    ).fetchone()
    if existing:
        print(f"  SKIP: {source_file} already loaded")
        return {"skipped": True, "source_file": source_file}

    # Upsert account
    acct = meta["account"]
    account_id = acct["account_number"]
    conn.execute(
        """INSERT INTO accounts (account_id, bank_name, account_type, holder_name)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(account_id) DO UPDATE SET
               bank_name = excluded.bank_name,
               account_type = excluded.account_type,
               holder_name = excluded.holder_name""",
        (
            account_id,
            meta["bank_entity"]["name"],
            acct.get("account_type"),
            acct.get("name"),
        ),
    )

    # Compute validation_passed: AND of all boolean validation fields
    validation_passed = all(
        v for k, v in validation.items() if isinstance(v, bool)
    )

    # Insert statement
    conn.execute(
        """INSERT INTO statements
           (statement_id, account_id, source_file, period_start, period_end,
            opening_balance, closing_balance, total_deposits, total_withdrawals,
            num_deposits, num_withdrawals, validation_passed)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            statement_id,
            account_id,
            source_file,
            meta["statement_period"]["start"],
            meta["statement_period"]["end"],
            summary["opening_balance"],
            summary["closing_balance"],
            summary["total_deposits_and_other_additions"],
            summary["total_withdrawals_and_other_subtractions"],
            summary.get("number_of_deposits"),
            summary.get("number_of_withdrawals"),
            int(validation_passed),
        ),
    )

    # Insert transactions
    txn_count = 0
    for is_deposit, txn_list in [(True, data.get("deposits", [])),
                                  (False, data.get("withdrawals", [])),
                                  (False, data.get("checks", []))]:
        for txn in txn_list:
            txn_id = f"{statement_id}_{txn['id']}"
            parsed = txn.get("parsed_fields", {}) or {}
            conn.execute(
                """INSERT INTO transactions
                   (transaction_id, statement_id, account_id, date,
                    description_raw, description_normalized, amount,
                    is_deposit, type, category, recurring,
                    ach_company_name, ach_description, ach_individual_name,
                    ach_entry_class, ach_company_id, ach_trace_number,
                    ach_secondary_co_id, ach_originator)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    txn_id,
                    statement_id,
                    account_id,
                    txn["date"],
                    txn.get("description_raw"),
                    txn.get("description_normalized"),
                    txn["amount"],
                    int(is_deposit),
                    txn.get("type"),
                    txn.get("category"),
                    int(txn["recurring"]) if txn.get("recurring") is not None else None,
                    parsed.get("ach_company_name"),
                    parsed.get("ach_description"),
                    parsed.get("ach_individual_name"),
                    parsed.get("ach_entry_class"),
                    parsed.get("ach_company_id"),
                    parsed.get("ach_trace_number"),
                    parsed.get("ach_secondary_co_id"),
                    parsed.get("ach_originator"),
                ),
            )
            txn_count += 1

    conn.commit()
    print(f"  Loaded {source_file}: {txn_count} transactions, validation={'PASS' if validation_passed else 'FAIL'}")
    return {
        "skipped": False,
        "source_file": source_file,
        "statement_id": statement_id,
        "txn_count": txn_count,
        "validation_passed": validation_passed,
    }


def run_verification(conn: sqlite3.Connection) -> None:
    """Run and print verification queries."""
    print("\n=== Verification ===")

    # Total statements
    (stmt_count,) = conn.execute("SELECT COUNT(*) FROM statements").fetchone()
    print(f"\nStatements loaded: {stmt_count}")

    # Total transactions
    (txn_count,) = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()
    print(f"Transactions loaded: {txn_count}")

    # Balance check per statement
    print("\nBalance check per statement:")
    rows = conn.execute("""
        SELECT s.statement_id, s.source_file,
               s.opening_balance, s.closing_balance,
               s.total_deposits, s.total_withdrawals,
               ROUND(s.opening_balance + s.total_deposits + s.total_withdrawals, 2) AS computed_closing,
               ROUND(SUM(CASE WHEN t.is_deposit = 1 THEN t.amount ELSE 0 END), 2) AS sum_deposits,
               ROUND(SUM(CASE WHEN t.is_deposit = 0 THEN t.amount ELSE 0 END), 2) AS sum_withdrawals
        FROM statements s
        LEFT JOIN transactions t ON t.statement_id = s.statement_id
        GROUP BY s.statement_id
    """).fetchall()
    for r in rows:
        stmt_id, src, opening, closing, dep, wd, computed, sum_dep, sum_wd = r
        balance_ok = abs(computed - closing) < 0.01
        dep_ok = abs(sum_dep - dep) < 0.01
        wd_ok = abs(sum_wd - wd) < 0.01
        status = "OK" if (balance_ok and dep_ok and wd_ok) else "MISMATCH"
        print(f"  {src}: opening={opening}, closing={closing}, computed={computed} [{status}]")
        print(f"    deposits: stated={dep}, summed={sum_dep}  |  withdrawals: stated={wd}, summed={sum_wd}")

    # Unique payees by type
    print("\nTransaction types and counts:")
    rows = conn.execute("""
        SELECT type, COUNT(*) as cnt, SUM(amount) as total
        FROM transactions
        GROUP BY type
        ORDER BY cnt DESC
    """).fetchall()
    for typ, cnt, total in rows:
        print(f"  {typ}: {cnt} transactions, total={total:.2f}")

    # Unique ACH company names
    print("\nUnique ACH payees:")
    rows = conn.execute("""
        SELECT DISTINCT ach_company_name
        FROM transactions
        WHERE ach_company_name IS NOT NULL
        ORDER BY ach_company_name
    """).fetchall()
    for (name,) in rows:
        print(f"  {name}")


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <file1.structured.json> [file2.structured.json ...]")
        sys.exit(1)

    json_paths = [Path(p) for p in sys.argv[1:]]
    for p in json_paths:
        if not p.exists():
            print(f"ERROR: File not found: {p}")
            sys.exit(1)

    print(f"Database: {DB_PATH}")
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    init_db(conn)

    total_txns = 0
    loaded_count = 0
    for p in json_paths:
        print(f"\nProcessing: {p.name}")
        result = load_one(conn, p)
        if not result["skipped"]:
            total_txns += result["txn_count"]
            loaded_count += 1

    print(f"\n--- Summary ---")
    print(f"Files processed: {len(json_paths)}")
    print(f"Statements loaded: {loaded_count}")
    print(f"Transactions inserted: {total_txns}")

    run_verification(conn)
    conn.close()


if __name__ == "__main__":
    main()
