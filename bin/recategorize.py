#!/usr/bin/env python3
"""Backfill category and subcategory for all transactions in the database.

Reads every transaction, runs the categorizer, and updates in-place.

Usage:
    python bin/recategorize.py
"""

import sqlite3
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.categorization.categorizer import CategoryStore, TransactionCategorizer

DB_PATH = PROJECT_ROOT / "data" / "jar.db"


def main():
    if not DB_PATH.exists():
        print(f"ERROR: Database not found at {DB_PATH}")
        sys.exit(1)

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    # Ensure subcategory column exists
    cols = [r[1] for r in conn.execute("PRAGMA table_info(transactions)").fetchall()]
    if "subcategory" not in cols:
        conn.execute("ALTER TABLE transactions ADD COLUMN subcategory TEXT")
        print("Added subcategory column to transactions table.")

    store = CategoryStore()
    categorizer = TransactionCategorizer(store)

    rows = conn.execute(
        "SELECT transaction_id, description_raw, type, "
        "ach_company_name, ach_description, ach_individual_name "
        "FROM transactions"
    ).fetchall()

    print(f"Processing {len(rows)} transactions...")

    categorized = 0
    uncategorized = 0
    category_counts = Counter()

    for row in rows:
        txn = {
            "description_raw": row["description_raw"],
            "type": row["type"],
            "parsed_fields": {
                "ach_company_name": row["ach_company_name"],
                "ach_description": row["ach_description"],
                "ach_individual_name": row["ach_individual_name"],
            },
        }
        result = categorizer.categorize(txn)

        conn.execute(
            "UPDATE transactions SET category = ?, subcategory = ? WHERE transaction_id = ?",
            (result["category"], result["subcategory"], row["transaction_id"]),
        )

        category_counts[f"{result['category']}/{result['subcategory']}"] += 1
        if result["category"] == "uncategorized":
            uncategorized += 1
        else:
            categorized += 1

    conn.commit()
    conn.close()

    print(f"\nDone: {categorized} categorized, {uncategorized} uncategorized")
    print(f"\nCategory breakdown:")
    for cat, count in category_counts.most_common():
        print(f"  {cat}: {count}")


if __name__ == "__main__":
    main()
