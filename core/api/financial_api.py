"""Layer 2 API: Queryable financial intelligence over the JAR SQLite database."""

import sqlite3
from pathlib import Path


class FinancialAPI:
    def __init__(self, db_path: str):
        self.db_path = str(Path(db_path).resolve())
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys=ON")

    def close(self):
        self._conn.close()

    # -- Function 1 ----------------------------------------------------------

    def get_transactions(
        self,
        start_date: str = None,
        end_date: str = None,
        type: str = None,
        min_amount: float = None,
        max_amount: float = None,
        payee: str = None,
        is_deposit: bool = None,
        sort_by: str = "date",
        sort_dir: str = "desc",
        limit: int = 50,
    ) -> dict:
        """Query transactions with optional filters."""
        clauses = []
        params = []

        if start_date:
            clauses.append("t.date >= ?")
            params.append(start_date)
        if end_date:
            clauses.append("t.date <= ?")
            params.append(end_date)
        if type:
            clauses.append("t.type = ?")
            params.append(type)
        if min_amount is not None:
            clauses.append("t.amount >= ?")
            params.append(min_amount)
        if max_amount is not None:
            clauses.append("t.amount <= ?")
            params.append(max_amount)
        if payee:
            clauses.append(
                "(t.description_raw LIKE ? OR t.ach_company_name LIKE ?)"
            )
            params.extend([f"%{payee}%", f"%{payee}%"])
        if is_deposit is not None:
            clauses.append("t.is_deposit = ?")
            params.append(int(is_deposit))

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

        # Validate sort column
        allowed_sorts = {"date": "t.date", "amount": "ABS(t.amount)"}
        sort_col = allowed_sorts.get(sort_by, "t.date")
        sort_direction = "ASC" if sort_dir.lower() == "asc" else "DESC"

        # Get total count and sum for the full filtered set
        agg = self._conn.execute(
            f"SELECT COUNT(*) as cnt, COALESCE(SUM(amount), 0) as total "
            f"FROM transactions t {where}",
            params,
        ).fetchone()

        rows = self._conn.execute(
            f"SELECT t.transaction_id, t.date, t.description_raw, t.amount, "
            f"t.is_deposit, t.type, t.category, t.ach_company_name, "
            f"t.ach_description, t.ach_individual_name "
            f"FROM transactions t {where} "
            f"ORDER BY {sort_col} {sort_direction} LIMIT ?",
            params + [limit],
        ).fetchall()

        return {
            "function": "get_transactions",
            "transactions": [dict(r) for r in rows],
            "count": agg["cnt"],
            "total_amount": round(agg["total"], 2),
        }

    # -- Function 2 ----------------------------------------------------------

    def get_spending_by_category(
        self, period_start: str, period_end: str
    ) -> dict:
        """Group withdrawals by category and by type for a date range."""
        base_where = "WHERE t.is_deposit = 0 AND t.date >= ? AND t.date <= ?"
        base_params = [period_start, period_end]

        by_category = self._conn.execute(
            f"SELECT t.category, COUNT(*) as count, "
            f"ROUND(SUM(t.amount), 2) as total "
            f"FROM transactions t {base_where} "
            f"GROUP BY t.category ORDER BY total",
            base_params,
        ).fetchall()

        by_type = self._conn.execute(
            f"SELECT t.type, COUNT(*) as count, "
            f"ROUND(SUM(t.amount), 2) as total "
            f"FROM transactions t {base_where} "
            f"GROUP BY t.type ORDER BY total",
            base_params,
        ).fetchall()

        total = self._conn.execute(
            f"SELECT COALESCE(SUM(t.amount), 0) "
            f"FROM transactions t {base_where}",
            base_params,
        ).fetchone()[0]

        return {
            "function": "get_spending_by_category",
            "by_category": [dict(r) for r in by_category],
            "by_type": [dict(r) for r in by_type],
            "total_spent": round(total, 2),
            "period": {"start": period_start, "end": period_end},
        }

    # -- Function 3 ----------------------------------------------------------

    def get_recurring_bills(self) -> dict:
        """Find payees appearing in more than one statement."""
        # ACH-based recurring
        ach_rows = self._conn.execute("""
            SELECT ach_company_name as payee,
                   COUNT(*) as occurrences,
                   COUNT(DISTINCT statement_id) as stmt_count,
                   ROUND(AVG(amount), 2) as avg_amount,
                   MAX(date) as last_paid,
                   GROUP_CONCAT(amount) as amounts_csv
            FROM transactions
            WHERE ach_company_name IS NOT NULL AND is_deposit = 0
            GROUP BY ach_company_name
            HAVING COUNT(DISTINCT statement_id) > 1
            ORDER BY payee
        """).fetchall()

        # Bill payment recurring (match on normalized description)
        bp_rows = self._conn.execute("""
            SELECT description_raw as payee,
                   COUNT(*) as occurrences,
                   COUNT(DISTINCT statement_id) as stmt_count,
                   ROUND(AVG(amount), 2) as avg_amount,
                   MAX(date) as last_paid,
                   GROUP_CONCAT(amount) as amounts_csv
            FROM transactions
            WHERE type = 'bill_payment' AND is_deposit = 0
            GROUP BY LOWER(description_raw)
            HAVING COUNT(DISTINCT statement_id) > 1
            ORDER BY payee
        """).fetchall()

        bills = []
        for row in list(ach_rows) + list(bp_rows):
            amounts = [float(a) for a in row["amounts_csv"].split(",")]
            bills.append({
                "payee": row["payee"],
                "avg_amount": row["avg_amount"],
                "frequency": "monthly",
                "last_paid": row["last_paid"],
                "occurrences": row["occurrences"],
                "amounts": amounts,
            })

        return {
            "function": "get_recurring_bills",
            "recurring_bills": bills,
        }

    # -- Function 4 ----------------------------------------------------------

    def get_balance_summary(
        self, period_start: str = None, period_end: str = None
    ) -> dict:
        """Overview of account balances and cash flow."""
        clauses = []
        params = []
        if period_start:
            clauses.append("s.period_end >= ?")
            params.append(period_start)
        if period_end:
            clauses.append("s.period_start <= ?")
            params.append(period_end)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

        stmts = self._conn.execute(
            f"SELECT source_file, period_start, period_end, "
            f"opening_balance, closing_balance, total_deposits, "
            f"total_withdrawals FROM statements s {where} "
            f"ORDER BY s.period_start",
            params,
        ).fetchall()

        if not stmts:
            return {
                "function": "get_balance_summary",
                "opening_balance": None,
                "closing_balance": None,
                "total_income": 0.0,
                "total_spending": 0.0,
                "net_change": 0.0,
                "statements_in_range": [],
            }

        total_income = round(sum(s["total_deposits"] for s in stmts), 2)
        total_spending = round(sum(s["total_withdrawals"] for s in stmts), 2)

        return {
            "function": "get_balance_summary",
            "opening_balance": stmts[0]["opening_balance"],
            "closing_balance": stmts[-1]["closing_balance"],
            "total_income": total_income,
            "total_spending": total_spending,
            "net_change": round(total_income + total_spending, 2),
            "statements_in_range": [dict(s) for s in stmts],
        }

    # -- Function 5 ----------------------------------------------------------

    def get_spending_comparison(
        self,
        period1_start: str,
        period1_end: str,
        period2_start: str,
        period2_end: str,
    ) -> dict:
        """Compare spending between two periods."""

        def _period_stats(start, end):
            rows = self._conn.execute(
                "SELECT type, COUNT(*) as count, ROUND(SUM(amount), 2) as total "
                "FROM transactions "
                "WHERE is_deposit = 0 AND date >= ? AND date <= ? "
                "GROUP BY type ORDER BY total",
                (start, end),
            ).fetchall()
            total = sum(r["total"] for r in rows) if rows else 0.0
            return {
                "total_spent": round(total, 2),
                "by_type": [dict(r) for r in rows],
                "count": sum(r["count"] for r in rows),
            }

        def _payees(start, end):
            rows = self._conn.execute(
                "SELECT DISTINCT COALESCE(ach_company_name, description_raw) as payee "
                "FROM transactions "
                "WHERE is_deposit = 0 AND date >= ? AND date <= ?",
                (start, end),
            ).fetchall()
            return {r["payee"] for r in rows}

        p1 = _period_stats(period1_start, period1_end)
        p2 = _period_stats(period2_start, period2_end)
        payees1 = _payees(period1_start, period1_end)
        payees2 = _payees(period2_start, period2_end)

        diff = round(p2["total_spent"] - p1["total_spent"], 2)
        pct = round((diff / abs(p1["total_spent"])) * 100, 2) if p1["total_spent"] else 0.0

        return {
            "function": "get_spending_comparison",
            "period1": {"start": period1_start, "end": period1_end, **p1},
            "period2": {"start": period2_start, "end": period2_end, **p2},
            "difference": diff,
            "percent_change": pct,
            "new_payees_in_period2": sorted(payees2 - payees1),
            "missing_payees_from_period1": sorted(payees1 - payees2),
        }

    # -- Function 6 ----------------------------------------------------------

    def get_balance_history(self) -> dict:
        """Time-series of balance and cash flow by statement period."""
        rows = self._conn.execute(
            "SELECT period_end, closing_balance, total_deposits, total_withdrawals "
            "FROM statements ORDER BY period_end"
        ).fetchall()
        return {
            "function": "get_balance_history",
            "history": [dict(r) for r in rows],
        }

    # -- Function 7 ----------------------------------------------------------

    def get_top_merchants(self, limit: int = 10) -> dict:
        """Top merchants by total spend."""
        rows = self._conn.execute(
            "SELECT COALESCE(ach_company_name, description_raw) as merchant, "
            "COUNT(*) as count, ROUND(SUM(ABS(amount)), 2) as total_spent "
            "FROM transactions WHERE is_deposit = 0 "
            "GROUP BY merchant ORDER BY total_spent DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return {
            "function": "get_top_merchants",
            "merchants": [dict(r) for r in rows],
        }

    # -- Function 8 ----------------------------------------------------------

    def get_monthly_trends(self) -> dict:
        """Monthly income vs spending for bar chart."""
        rows = self._conn.execute(
            "SELECT strftime('%Y-%m', date) as month, "
            "ROUND(SUM(CASE WHEN is_deposit = 1 THEN amount ELSE 0 END), 2) as income, "
            "ROUND(SUM(CASE WHEN is_deposit = 0 THEN ABS(amount) ELSE 0 END), 2) as spending "
            "FROM transactions GROUP BY month ORDER BY month"
        ).fetchall()
        return {
            "function": "get_monthly_trends",
            "months": [dict(r) for r in rows],
        }

    # -- Function 9 ----------------------------------------------------------

    def search_transactions(self, keyword: str, limit: int = 20) -> dict:
        """Free text search across description and ACH fields."""
        pattern = f"%{keyword}%"
        rows = self._conn.execute(
            "SELECT transaction_id, date, description_raw, amount, "
            "is_deposit, type, ach_company_name, ach_individual_name "
            "FROM transactions "
            "WHERE description_raw LIKE ? COLLATE NOCASE "
            "   OR ach_company_name LIKE ? COLLATE NOCASE "
            "   OR ach_individual_name LIKE ? COLLATE NOCASE "
            "ORDER BY date DESC LIMIT ?",
            (pattern, pattern, pattern, limit),
        ).fetchall()

        return {
            "function": "search_transactions",
            "results": [dict(r) for r in rows],
            "count": len(rows),
            "keyword": keyword,
        }
