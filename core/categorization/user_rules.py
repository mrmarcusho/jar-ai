"""User-created categorization rules and per-transaction overrides.

Provides a learning loop: user tags a transaction → keyword extracted →
reusable rule created → retroactively applied to similar transactions.
"""

import re
import sqlite3


class UserRulesStore:
    """CRUD for user_category_rules and user_category_overrides tables."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # ── Rules ────────────────────────────────────────────────────

    def add_rule(
        self,
        keyword: str,
        category: str,
        subcategory: str = None,
        source_description: str = None,
        source_transaction_id: str = None,
    ) -> int:
        cur = self.conn.execute(
            """INSERT INTO user_category_rules
               (keyword, category, subcategory, source_description, source_transaction_id)
               VALUES (?, ?, ?, ?, ?)""",
            (keyword.lower(), category, subcategory, source_description, source_transaction_id),
        )
        self.conn.commit()
        return cur.lastrowid

    def get_rules(self, active_only: bool = True) -> list[dict]:
        where = "WHERE active = 1" if active_only else ""
        rows = self.conn.execute(
            f"SELECT * FROM user_category_rules {where} ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def increment_match_count(self, rule_id: int, count: int = 1):
        self.conn.execute(
            "UPDATE user_category_rules SET times_matched = times_matched + ? WHERE rule_id = ?",
            (count, rule_id),
        )
        self.conn.commit()

    def disable_rule(self, rule_id: int):
        self.conn.execute(
            "UPDATE user_category_rules SET active = 0 WHERE rule_id = ?", (rule_id,)
        )
        self.conn.commit()

    def delete_rule(self, rule_id: int):
        self.conn.execute(
            "DELETE FROM user_category_rules WHERE rule_id = ?", (rule_id,)
        )
        self.conn.commit()

    def update_rule(self, rule_id: int, **fields):
        allowed = {"keyword", "category", "subcategory", "match_type", "active"}
        sets = []
        vals = []
        for k, v in fields.items():
            if k in allowed:
                sets.append(f"{k} = ?")
                vals.append(v)
        if not sets:
            return
        vals.append(rule_id)
        self.conn.execute(
            f"UPDATE user_category_rules SET {', '.join(sets)} WHERE rule_id = ?",
            vals,
        )
        self.conn.commit()

    # ── Overrides ────────────────────────────────────────────────

    def add_override(
        self,
        transaction_id: str,
        category: str,
        subcategory: str = None,
        household_tag: str = None,
        budget_type: str = None,
        custom_tags: str = None,
    ):
        self.conn.execute(
            """INSERT INTO user_category_overrides
               (transaction_id, category, subcategory, household_tag, budget_type, custom_tags)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(transaction_id) DO UPDATE SET
                   category = excluded.category,
                   subcategory = excluded.subcategory,
                   household_tag = excluded.household_tag,
                   budget_type = excluded.budget_type,
                   custom_tags = excluded.custom_tags,
                   created_at = datetime('now')""",
            (transaction_id, category, subcategory, household_tag, budget_type, custom_tags),
        )
        self.conn.commit()

    def get_override(self, transaction_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM user_category_overrides WHERE transaction_id = ?",
            (transaction_id,),
        ).fetchone()
        return dict(row) if row else None

    # ── Keyword extraction ───────────────────────────────────────

    @staticmethod
    def extract_keyword(description_raw: str, ach_company_name: str = None) -> str:
        """Extract a reusable keyword from a transaction description.

        Priority: ACH company name if present, else clean up description_raw.
        """
        if ach_company_name and ach_company_name.strip():
            return ach_company_name.strip()

        if not description_raw:
            return ""

        text = description_raw.strip()

        # Remove common prefixes
        prefixes = [
            r"^Bill Payment\s+",
            r"^Online Payment\s+",
            r"^ACH Debit\s+",
            r"^ACH Credit\s+",
            r"^Online Transfer\s+",
            r"^Recurring Payment\s+",
            r"^Pre-Authorized Debit\s+",
        ]
        for pat in prefixes:
            text = re.sub(pat, "", text, flags=re.IGNORECASE)

        # Remove ACH field codes: DES:xxx, ID:xxx, INDN:xxx, CO ID:xxx, PPD, WEB, CCD, TEL
        text = re.sub(r"\b(DES|ID|INDN|CO ID|SEC|TRN|ORIG)\s*:\s*\S+", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\b(PPD|WEB|CCD|TEL)\b", "", text, flags=re.IGNORECASE)

        # Remove dates (various formats)
        text = re.sub(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", "", text)
        text = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", "", text)

        # Remove dollar amounts
        text = re.sub(r"\$[\d,]+\.?\d*", "", text)

        # Remove long numbers (8+ digits — trace numbers, account refs)
        text = re.sub(r"\b\d{8,}\b", "", text)

        # Collapse whitespace and strip
        text = re.sub(r"\s+", " ", text).strip()

        # Return first meaningful token(s) — up to first 3 words
        words = text.split()
        if not words:
            return ""
        # Take up to 3 words, but stop at very short noise tokens
        result_words = []
        for w in words[:3]:
            if len(w) <= 1:
                break
            result_words.append(w)
        return " ".join(result_words) if result_words else (words[0] if words else "")

    # ── Retroactive apply ────────────────────────────────────────

    def apply_rule_retroactively(self, rule_id: int) -> int:
        """Apply a rule to existing uncategorized transactions. Returns count updated."""
        rule = self.conn.execute(
            "SELECT keyword, category, subcategory FROM user_category_rules WHERE rule_id = ?",
            (rule_id,),
        ).fetchone()
        if not rule:
            return 0

        keyword, category, subcategory = rule["keyword"], rule["category"], rule["subcategory"]
        pattern = f"%{keyword}%"

        cur = self.conn.execute(
            """UPDATE transactions SET category = ?, subcategory = ?
               WHERE (category IS NULL OR category = 'uncategorized')
               AND (description_raw LIKE ? COLLATE NOCASE
                    OR ach_company_name LIKE ? COLLATE NOCASE)""",
            (category, subcategory, pattern, pattern),
        )
        count = cur.rowcount
        if count > 0:
            self.increment_match_count(rule_id, count)
        self.conn.commit()
        return count
