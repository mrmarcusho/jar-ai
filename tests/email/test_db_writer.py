"""Tests for db_writer: routing, dedup, and unified finance-view visibility."""
import json

from core.api.financial_api import FinancialAPI
from core.email.bucket_classifier import BUCKET_DOCUMENT, BUCKET_FINANCIAL
from core.email.db_writer import route_and_write


def _financial(**over):
    base = {
        "bucket": BUCKET_FINANCIAL, "type": "transaction",
        "date": "2026-01-14", "due_date": None, "amount": 86.42,
        "merchant": "Whole Foods Market", "category": "groceries",
        "direction": "debit", "status": "settled", "action_required": False,
        "deadline": None, "summary": "groceries", "metadata": {},
        "sender_domain": "chase.com", "_redacted_body": "redacted body text",
    }
    base.update(over)
    return base


def _document(**over):
    base = {
        "bucket": BUCKET_DOCUMENT, "category": "government",
        "date": "2026-01-19", "amount": 1240.0, "action_required": True,
        "deadline": "2026-02-28", "summary": "IRS notice CP2000",
        "metadata": {"notice": "CP2000"}, "sender_domain": "irs.gov",
        "_redacted_body": "Dear [ACCT_HOLDER], this is a notice.",
    }
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# Migration / setup
# ---------------------------------------------------------------------------

def test_email_account_exists_after_migration(db):
    conn, _ = db
    row = conn.execute(
        "SELECT account_id FROM accounts WHERE account_id = 'email'"
    ).fetchone()
    assert row is not None


def test_transactions_has_email_columns(db):
    conn, _ = db
    cols = {r[1] for r in conn.execute("PRAGMA table_info(transactions)")}
    assert "source" in cols and "dedupe_hash" in cols


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

def test_settled_financial_goes_to_transactions(db):
    conn, _ = db
    assert route_and_write(conn, _financial(), "<m1@x>") == "transaction"
    count = conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE source = 'email'"
    ).fetchone()[0]
    assert count == 1


def test_forward_financial_goes_to_documents(db):
    conn, _ = db
    result = _financial(status="forward")
    assert route_and_write(conn, result, "<m2@x>") == "document"


def test_document_goes_to_documents(db):
    conn, _ = db
    assert route_and_write(conn, _document(), "<m3@x>") == "document"


def test_financial_without_amount_goes_to_documents(db):
    conn, _ = db
    assert route_and_write(conn, _financial(amount=None), "<m4@x>") == "document"


# ---------------------------------------------------------------------------
# Dedup
# ---------------------------------------------------------------------------

def test_duplicate_transaction_not_inserted_twice(db):
    conn, _ = db
    assert route_and_write(conn, _financial(), "<dup@x>") == "transaction"
    assert route_and_write(conn, _financial(), "<dup@x>") == "duplicate"
    count = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    assert count == 1


def test_duplicate_document_not_inserted_twice(db):
    conn, _ = db
    assert route_and_write(conn, _document(), "<dup2@x>") == "document"
    assert route_and_write(conn, _document(), "<dup2@x>") == "duplicate"
    count = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    assert count == 1


# ---------------------------------------------------------------------------
# Unified finance view — the whole point of the hybrid model
# ---------------------------------------------------------------------------

def test_email_transaction_visible_in_category_and_merchant_views(db):
    conn, db_path = db
    route_and_write(conn, _financial(), "<m5@x>")
    conn.commit()

    api = FinancialAPI(db_path)
    try:
        cats = api.get_spending_by_category("2026-01-01", "2026-01-31")
        category_names = {c["category"] for c in cats["by_category"]}
        assert "groceries" in category_names

        merchants = api.get_top_merchants()
        # description_raw=merchant makes the email txn surface in merchant ranking
        assert "Whole Foods Market" in json.dumps(merchants)
    finally:
        api.close()


def test_document_stores_redacted_body_only(db):
    conn, _ = db
    route_and_write(conn, _document(), "<m6@x>")
    row = conn.execute(
        "SELECT body_redacted FROM documents WHERE source_id IS NOT NULL"
    ).fetchone()
    assert row is not None
    assert "[ACCT_HOLDER]" in row[0]
