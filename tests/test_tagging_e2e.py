"""End-to-end API test for the transaction tagging flow.

Simulates the full user journey:
1. Seed DB with test transactions (some uncategorized)
2. GET /api/transactions/uncategorized → verify they appear
3. GET /api/categories → verify taxonomy loads
4. POST /api/transactions/{id}/tag → tag one, create rule
5. Verify retroactive apply updated similar transactions
6. Verify uncategorized count decreased
7. Verify category badge data in GET /api/transactions
8. GET /api/category-rules/user → verify rule exists
9. DELETE rule, verify it's gone
"""

import sqlite3
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "core" / "database" / "schema.sql"


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Create a TestClient with a temporary database."""
    db_path = tmp_path / "test.db"

    # Patch DB_PATH before importing app
    import app.server as server_mod
    monkeypatch.setattr(server_mod, "DB_PATH", db_path)

    # Initialize DB and seed test data
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA_PATH.read_text())

    # Seed: account, statement, transactions
    conn.execute(
        "INSERT INTO accounts (account_id, bank_name) VALUES ('acct1', 'TestBank')"
    )
    conn.execute(
        "INSERT INTO statements (statement_id, account_id, source_file, "
        "period_start, period_end, opening_balance, closing_balance) "
        "VALUES ('stmt1', 'acct1', 'test.json', '2024-01-01', '2024-01-31', 5000, 4500)"
    )

    # Mix of categorized and uncategorized transactions
    txns = [
        ("stmt1_001", "NETFLIX MONTHLY CHARGE", -15.99, "uncategorized", None, None),
        ("stmt1_002", "NETFLIX ANNUAL PLAN", -149.99, "uncategorized", None, None),
        ("stmt1_003", "SHOPRITE OF PARSIPPANY", -87.50, "groceries", "supermarket", None),
        ("stmt1_004", "ACME CORP UNKNOWN", -200.00, "uncategorized", None, None),
        ("stmt1_005", "ACME CORP REFUND", 50.00, "uncategorized", None, "ACME CORP"),
        ("stmt1_006", "COSTCO WHOLESALE #1234", -156.78, "groceries", "wholesale", None),
        ("stmt1_007", "XYZZY STRANGE PAYMENT", -42.00, None, None, None),
    ]
    for txn_id, desc, amount, cat, subcat, ach in txns:
        conn.execute(
            "INSERT INTO transactions (transaction_id, statement_id, account_id, "
            "date, description_raw, amount, is_deposit, category, subcategory, ach_company_name) "
            "VALUES (?, 'stmt1', 'acct1', '2024-01-15', ?, ?, ?, ?, ?, ?)",
            (txn_id, desc, amount, 1 if amount > 0 else 0, cat, subcat, ach),
        )
    conn.commit()
    conn.close()

    return TestClient(server_mod.app)


class TestTaggingE2E:

    def test_uncategorized_list(self, client):
        """Step 1: Uncategorized transactions are returned with suggested keywords."""
        resp = client.get("/api/transactions/uncategorized")
        assert resp.status_code == 200
        data = resp.json()

        # 4 uncategorized: NETFLIX x2, ACME x2 (one has null category, which also counts)
        # Actually: stmt1_001 (uncategorized), stmt1_002 (uncategorized),
        # stmt1_004 (uncategorized), stmt1_005 (uncategorized), stmt1_007 (null → uncategorized)
        assert data["count"] == 5
        assert len(data["transactions"]) == 5

        # Each should have a suggested_keyword
        for txn in data["transactions"]:
            assert "suggested_keyword" in txn

    def test_categories_endpoint(self, client):
        """Step 2: Taxonomy loads with all categories."""
        resp = client.get("/api/categories")
        assert resp.status_code == 200
        cats = resp.json()["categories"]

        assert "housing" in cats
        assert "groceries" in cats
        assert "subscriptions" in cats
        assert "Streaming Services" in cats["subscriptions"]["subcategories"].values()

    def test_tag_with_rule_and_retroactive(self, client):
        """Step 3: Tag a transaction, create rule, verify retroactive apply."""
        # Tag NETFLIX MONTHLY → subscriptions/streaming, create rule for "netflix"
        resp = client.post("/api/transactions/stmt1_001/tag", json={
            "category": "subscriptions",
            "subcategory": "streaming",
            "household_tag": "shared",
            "budget_type": "recurring",
            "create_rule": True,
            "keyword": "netflix",
        })
        assert resp.status_code == 200
        result = resp.json()

        assert result["status"] == "ok"
        assert result["rule_created"] is True
        assert result["keyword"] == "netflix"
        # stmt1_002 (NETFLIX ANNUAL PLAN) should have been retroactively updated
        assert result["similar_updated"] >= 1

    def test_uncategorized_count_decreased(self, client):
        """Step 4: After tagging NETFLIX + retroactive, count should drop."""
        # First tag to set up state
        client.post("/api/transactions/stmt1_001/tag", json={
            "category": "subscriptions",
            "subcategory": "streaming",
            "create_rule": True,
            "keyword": "netflix",
        })

        resp = client.get("/api/transactions/uncategorized")
        data = resp.json()

        # Originally 5 uncategorized. After tagging:
        # stmt1_001 → tagged directly (override + update)
        # stmt1_002 → retroactively updated (NETFLIX ANNUAL PLAN matches "netflix")
        # Remaining: stmt1_004, stmt1_005, stmt1_007 = 3
        assert data["count"] == 3

    def test_tagged_transaction_in_list(self, client):
        """Step 5: Tagged transaction shows correct category in transaction list."""
        client.post("/api/transactions/stmt1_001/tag", json={
            "category": "subscriptions",
            "subcategory": "streaming",
            "create_rule": True,
            "keyword": "netflix",
        })

        resp = client.get("/api/transactions?search=NETFLIX")
        txns = resp.json()["transactions"]

        for txn in txns:
            assert txn["category"] == "subscriptions"
            assert txn["subcategory"] == "streaming"

    def test_user_rules_listed(self, client):
        """Step 6: Created rule appears in user rules list."""
        client.post("/api/transactions/stmt1_001/tag", json={
            "category": "subscriptions",
            "subcategory": "streaming",
            "create_rule": True,
            "keyword": "netflix",
        })

        resp = client.get("/api/category-rules/user")
        rules = resp.json()["rules"]

        assert len(rules) >= 1
        netflix_rule = [r for r in rules if r["keyword"] == "netflix"]
        assert len(netflix_rule) == 1
        assert netflix_rule[0]["category"] == "subscriptions"
        assert netflix_rule[0]["times_matched"] >= 1

    def test_delete_rule(self, client):
        """Step 7: Delete a rule."""
        client.post("/api/transactions/stmt1_001/tag", json={
            "category": "subscriptions",
            "subcategory": "streaming",
            "create_rule": True,
            "keyword": "netflix",
        })

        rules = client.get("/api/category-rules/user").json()["rules"]
        rule_id = rules[0]["rule_id"]

        resp = client.delete(f"/api/category-rules/user/{rule_id}")
        assert resp.status_code == 200

        rules_after = client.get("/api/category-rules/user").json()["rules"]
        assert len(rules_after) == 0

    def test_batch_tag(self, client):
        """Step 8: Batch tag multiple transactions."""
        resp = client.post("/api/transactions/batch-tag", json={
            "transaction_ids": ["stmt1_004", "stmt1_005"],
            "category": "shopping",
            "subcategory": "online",
        })
        assert resp.status_code == 200
        assert resp.json()["updated"] == 2

        # Verify they're no longer uncategorized
        uncat = client.get("/api/transactions/uncategorized").json()
        uncat_ids = [t["transaction_id"] for t in uncat["transactions"]]
        assert "stmt1_004" not in uncat_ids
        assert "stmt1_005" not in uncat_ids

    def test_tag_without_rule(self, client):
        """Step 9: Tag without creating a rule (one-off override)."""
        resp = client.post("/api/transactions/stmt1_007/tag", json={
            "category": "entertainment",
            "subcategory": "games",
            "create_rule": False,
        })
        assert resp.status_code == 200
        result = resp.json()
        assert result["rule_created"] is False
        assert result["similar_updated"] == 0

    def test_tag_requires_category(self, client):
        """Step 10: Tagging without a category returns 400."""
        resp = client.post("/api/transactions/stmt1_001/tag", json={})
        assert resp.status_code == 400

    def test_spending_by_category_reflects_tags(self, client):
        """Step 11: Donut chart data updates after tagging."""
        # Tag netflix
        client.post("/api/transactions/stmt1_001/tag", json={
            "category": "subscriptions",
            "subcategory": "streaming",
            "create_rule": True,
            "keyword": "netflix",
        })

        resp = client.get("/api/dashboard/spending-by-category")
        cats = {c["category"]: c["total"] for c in resp.json()["categories"]}

        assert "subscriptions" in cats

    def test_update_rule(self, client):
        """Step 12: Update a rule's keyword."""
        client.post("/api/transactions/stmt1_001/tag", json={
            "category": "subscriptions",
            "subcategory": "streaming",
            "create_rule": True,
            "keyword": "netflix",
        })

        rules = client.get("/api/category-rules/user").json()["rules"]
        rule_id = rules[0]["rule_id"]

        resp = client.put(f"/api/category-rules/user/{rule_id}", json={
            "keyword": "netflix inc",
        })
        assert resp.status_code == 200

        updated = client.get("/api/category-rules/user").json()["rules"]
        assert updated[0]["keyword"] == "netflix inc"
