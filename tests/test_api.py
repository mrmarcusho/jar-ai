#!/usr/bin/env python3
"""Test all 6 FinancialAPI functions against jar.db."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from core.api.financial_api import FinancialAPI

DB = Path(__file__).resolve().parent.parent.parent / "data" / "jar.db"


def pp(data: dict) -> None:
    print(json.dumps(data, indent=2, default=str))


def main():
    api = FinancialAPI(str(DB))

    print("=" * 60)
    print("1. All transactions in January 2023")
    print("=" * 60)
    pp(api.get_transactions(start_date="2023-01-01", end_date="2023-01-31"))

    print("\n" + "=" * 60)
    print("2. Spending by category — Feb statement period")
    print("=" * 60)
    pp(api.get_spending_by_category("2023-01-14", "2023-02-10"))

    print("\n" + "=" * 60)
    print("3. Recurring bills")
    print("=" * 60)
    pp(api.get_recurring_bills())

    print("\n" + "=" * 60)
    print("4. Balance summary (all statements)")
    print("=" * 60)
    pp(api.get_balance_summary())

    print("\n" + "=" * 60)
    print("5. Compare spending: Feb period vs Dec period")
    print("=" * 60)
    pp(api.get_spending_comparison(
        "2023-01-14", "2023-02-10",
        "2023-11-15", "2023-12-13",
    ))

    print("\n" + "=" * 60)
    print("6. Search: PSEG")
    print("=" * 60)
    pp(api.search_transactions("PSEG"))

    print("\n" + "=" * 60)
    print("7. Search: JAMIE")
    print("=" * 60)
    pp(api.search_transactions("JAMIE"))

    api.close()


if __name__ == "__main__":
    main()
