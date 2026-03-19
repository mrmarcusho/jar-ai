"""Tests for user-created categorization rules and tagging."""

import sqlite3
from pathlib import Path

import pytest

from core.categorization.user_rules import UserRulesStore
from core.categorization.categorizer import CategoryStore, TransactionCategorizer


SCHEMA_PATH = Path(__file__).resolve().parent.parent / "core" / "database" / "schema.sql"


@pytest.fixture
def db():
    """In-memory SQLite DB with full schema."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA_PATH.read_text())
    # Insert a test account and statement so FK constraints pass
    conn.execute(
        "INSERT INTO accounts (account_id, bank_name) VALUES ('acct1', 'TestBank')"
    )
    conn.execute(
        "INSERT INTO statements (statement_id, account_id, source_file, "
        "period_start, period_end, opening_balance, closing_balance) "
        "VALUES ('stmt1', 'acct1', 'test.json', '2024-01-01', '2024-01-31', 1000, 900)"
    )
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def store(db):
    return UserRulesStore(db)


def _insert_txn(db, txn_id, desc, ach=None, category=None):
    db.execute(
        "INSERT INTO transactions (transaction_id, statement_id, account_id, "
        "date, description_raw, amount, is_deposit, category, ach_company_name) "
        "VALUES (?, 'stmt1', 'acct1', '2024-01-15', ?, -50.0, 0, ?, ?)",
        (txn_id, desc, category, ach),
    )
    db.commit()


# ── extract_keyword tests ────────────────────────────────────────

class TestExtractKeyword:
    def test_ach_priority(self):
        """ACH company name is used when available."""
        result = UserRulesStore.extract_keyword(
            "Bill Payment ACH Debit PSEG 01/15 DES:ONLINE", "PSEG - PUBLIC SERV"
        )
        assert result == "PSEG - PUBLIC SERV"

    def test_strips_noise(self):
        """Bill Payment prefix, dates, IDs are stripped."""
        result = UserRulesStore.extract_keyword(
            "Bill Payment ACH Debit SHOPRITE 01/15/2024 DES:PURCHASE ID:12345678901"
        )
        assert "SHOPRITE" in result
        assert "Bill Payment" not in result
        assert "01/15/2024" not in result
        assert "12345678901" not in result

    def test_empty(self):
        assert UserRulesStore.extract_keyword("") == ""
        assert UserRulesStore.extract_keyword(None) == ""

    def test_ach_whitespace(self):
        """Empty ACH falls through to description."""
        result = UserRulesStore.extract_keyword("COSTCO WHOLESALE", "  ")
        assert "COSTCO" in result

    def test_strips_ach_codes(self):
        result = UserRulesStore.extract_keyword(
            "ACH Debit VERIZON PPD WEB ID:9876543210"
        )
        assert "VERIZON" in result
        assert "PPD" not in result
        assert "WEB" not in result


# ── Rule CRUD tests ──────────────────────────────────────────────

class TestRuleCRUD:
    def test_add_and_get_rules(self, store):
        rid = store.add_rule("costco", "groceries", "wholesale")
        rules = store.get_rules()
        assert len(rules) == 1
        assert rules[0]["keyword"] == "costco"
        assert rules[0]["rule_id"] == rid

    def test_disable_rule(self, store):
        rid = store.add_rule("target", "shopping", "clothing")
        store.disable_rule(rid)
        active = store.get_rules(active_only=True)
        all_rules = store.get_rules(active_only=False)
        assert len(active) == 0
        assert len(all_rules) == 1

    def test_delete_rule(self, store):
        rid = store.add_rule("test", "shopping")
        store.delete_rule(rid)
        assert len(store.get_rules(active_only=False)) == 0

    def test_update_rule(self, store):
        rid = store.add_rule("old", "shopping")
        store.update_rule(rid, keyword="new", category="groceries")
        rules = store.get_rules()
        assert rules[0]["keyword"] == "new"
        assert rules[0]["category"] == "groceries"


# ── Override tests ───────────────────────────────────────────────

class TestOverrides:
    def test_add_and_get_override(self, store, db):
        _insert_txn(db, "txn1", "SOME PURCHASE")
        store.add_override("txn1", "dining", "restaurants")
        ov = store.get_override("txn1")
        assert ov is not None
        assert ov["category"] == "dining"

    def test_upsert_override(self, store, db):
        _insert_txn(db, "txn2", "SOME PURCHASE")
        store.add_override("txn2", "shopping")
        store.add_override("txn2", "groceries", "supermarket")
        ov = store.get_override("txn2")
        assert ov["category"] == "groceries"
        assert ov["subcategory"] == "supermarket"

    def test_no_override(self, store):
        assert store.get_override("nonexistent") is None


# ── Categorizer integration tests ────────────────────────────────

class TestCategorizerWithUserRules:
    def test_add_rule_and_match(self, db, store):
        """User rule gets picked up by the categorizer."""
        store.add_rule("xyzzy corp", "entertainment", "games")
        cat_store = CategoryStore()
        categorizer = TransactionCategorizer(cat_store, user_rules=store)

        result = categorizer.categorize({"description_raw": "XYZZY CORP PAYMENT"})
        assert result["category"] == "entertainment"
        assert result["subcategory"] == "games"
        assert result["confidence"] == 0.95

    def test_user_rule_priority_over_system(self, db, store):
        """User rule for COSTCO overrides system rule."""
        store.add_rule("costco", "shopping", "home_goods")
        cat_store = CategoryStore()
        categorizer = TransactionCategorizer(cat_store, user_rules=store)

        result = categorizer.categorize({"description_raw": "COSTCO WHOLESALE #1234"})
        assert result["category"] == "shopping"
        assert result["subcategory"] == "home_goods"
        assert result["confidence"] == 0.95

    def test_override_priority_over_user_rule(self, db, store):
        """Override beats user rule."""
        _insert_txn(db, "txn_ov", "COSTCO WHOLESALE")
        store.add_rule("costco", "shopping", "home_goods")
        store.add_override("txn_ov", "dining", "restaurants")

        cat_store = CategoryStore()
        categorizer = TransactionCategorizer(cat_store, user_rules=store)

        result = categorizer.categorize({
            "transaction_id": "txn_ov",
            "description_raw": "COSTCO WHOLESALE",
        })
        assert result["category"] == "dining"
        assert result["subcategory"] == "restaurants"
        assert result["confidence"] == 1.0

    def test_backward_compatible(self):
        """Without user_rules, categorizer works as before."""
        cat_store = CategoryStore()
        categorizer = TransactionCategorizer(cat_store)
        result = categorizer.categorize({"description_raw": "SHOPRITE"})
        assert result["category"] == "groceries"
        assert result["confidence"] == 0.8


# ── Retroactive apply tests ─────────────────────────────────────

class TestRetroactiveApply:
    def test_retroactive_apply(self, db, store):
        """Creating a rule updates existing uncategorized transactions."""
        _insert_txn(db, "txn_a", "ACME CORP PAYMENT", category="uncategorized")
        _insert_txn(db, "txn_b", "ACME CORP REFUND", category="uncategorized")
        _insert_txn(db, "txn_c", "SHOPRITE GROCERY", category="groceries")  # already categorized

        rid = store.add_rule("acme corp", "shopping", "online")
        count = store.apply_rule_retroactively(rid)

        assert count == 2

        # Verify the transactions were updated
        row_a = db.execute("SELECT category FROM transactions WHERE transaction_id = 'txn_a'").fetchone()
        row_b = db.execute("SELECT category FROM transactions WHERE transaction_id = 'txn_b'").fetchone()
        row_c = db.execute("SELECT category FROM transactions WHERE transaction_id = 'txn_c'").fetchone()

        assert row_a["category"] == "shopping"
        assert row_b["category"] == "shopping"
        assert row_c["category"] == "groceries"  # untouched

    def test_retroactive_apply_ach(self, db, store):
        """Rule matches on ACH company name too."""
        _insert_txn(db, "txn_ach", "SOME DESC", ach="ACME CORP", category="uncategorized")

        rid = store.add_rule("acme corp", "entertainment")
        count = store.apply_rule_retroactively(rid)
        assert count == 1


# ── API integration test ─────────────────────────────────────────

class TestTagEndpoint:
    def test_tag_endpoint(self, db, store):
        """Simulate what the tag endpoint does."""
        _insert_txn(db, "txn_tag", "NETFLIX MONTHLY", category="uncategorized")

        # 1. Save override
        store.add_override("txn_tag", "subscriptions", "streaming")

        # 2. Update transaction
        db.execute(
            "UPDATE transactions SET category = 'subscriptions', subcategory = 'streaming' "
            "WHERE transaction_id = 'txn_tag'"
        )

        # 3. Create rule + retroactive
        _insert_txn(db, "txn_tag2", "NETFLIX ANNUAL", category="uncategorized")
        rid = store.add_rule("netflix", "subscriptions", "streaming",
                             source_description="NETFLIX MONTHLY",
                             source_transaction_id="txn_tag")
        updated = store.apply_rule_retroactively(rid)
        assert updated == 1

        row = db.execute("SELECT category FROM transactions WHERE transaction_id = 'txn_tag2'").fetchone()
        assert row["category"] == "subscriptions"
