"""Tests for core.extraction_engine.extract (with mocked Claude API)."""

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.extraction_engine.extract import (
    ExtractionResult,
    extract_statement,
    _execute_sql,
    _verify_balance,
    _categorize_transactions,
)
from core.extraction_engine.sql_validator import load_schema_metadata, validate_sql

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "core" / "database" / "schema.sql"

# Sample SQL that Claude would return for a small statement
SAMPLE_EXTRACTION_SQL = """\
INSERT INTO accounts (account_id, bank_name, account_type, holder_name) VALUES ('000123456789', 'Bank of America', 'checking', 'Alex Ho') ON CONFLICT(account_id) DO UPDATE SET bank_name = excluded.bank_name, account_type = excluded.account_type, holder_name = excluded.holder_name;
INSERT INTO statements (statement_id, account_id, source_file, period_start, period_end, opening_balance, closing_balance, total_deposits, total_withdrawals, num_deposits, num_withdrawals, validation_passed) VALUES ('stmt_2023-02-10', '000123456789', 'eStmt_2023-02-10.pdf', '2023-01-14', '2023-02-10', 1000.00, 950.00, 200.00, -250.00, 2, 3, 1);
INSERT INTO transactions (transaction_id, statement_id, account_id, date, description_raw, amount, is_deposit, type) VALUES ('stmt_2023-02-10_001', 'stmt_2023-02-10', '000123456789', '2023-01-15', 'DIRECT DEP ACME CORP PAYROLL', 100.00, 1, 'direct_deposit');
INSERT INTO transactions (transaction_id, statement_id, account_id, date, description_raw, amount, is_deposit, type) VALUES ('stmt_2023-02-10_002', 'stmt_2023-02-10', '000123456789', '2023-01-16', 'ZELLE PAYMENT TO JAMIE LEE', 100.00, 1, 'zelle_transfer');
INSERT INTO transactions (transaction_id, statement_id, account_id, date, description_raw, amount, is_deposit, type, ach_company_name) VALUES ('stmt_2023-02-10_003', 'stmt_2023-02-10', '000123456789', '2023-01-17', 'ACH DEBIT VERIZON WIRELESS PAYMENT', -85.50, 0, 'ach_debit', 'VERIZON WIRELESS');
INSERT INTO transactions (transaction_id, statement_id, account_id, date, description_raw, amount, is_deposit, type) VALUES ('stmt_2023-02-10_004', 'stmt_2023-02-10', '000123456789', '2023-01-20', 'ATM WITHDRAWAL 123 MAIN ST', -100.00, 0, 'atm_withdrawal');
INSERT INTO transactions (transaction_id, statement_id, account_id, date, description_raw, amount, is_deposit, type) VALUES ('stmt_2023-02-10_005', 'stmt_2023-02-10', '000123456789', '2023-01-25', 'CHECK #1042', -64.50, 0, 'check');
"""


@pytest.fixture
def test_db(tmp_path):
    """Create a fresh in-memory-style test database."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    schema_sql = SCHEMA_PATH.read_text()
    conn.executescript(schema_sql)
    conn.close()
    return db_path


@pytest.fixture
def test_pdf(tmp_path):
    """Create a minimal test PDF file."""
    pdf_path = tmp_path / "eStmt_2023-02-10.pdf"
    # Write minimal PDF header (enough for base64 encoding, not a real PDF)
    pdf_path.write_bytes(b"%PDF-1.4 fake test content")
    return pdf_path


def _mock_claude_response(text: str):
    """Create a mock Anthropic response object."""
    block = MagicMock()
    block.type = "text"
    block.text = text
    response = MagicMock()
    response.content = [block]
    return response


# ---------------------------------------------------------------------------
# validate sample SQL
# ---------------------------------------------------------------------------

def test_sample_sql_validates():
    """Verify our sample extraction SQL passes validation."""
    meta = load_schema_metadata(SCHEMA_PATH)
    result = validate_sql(SAMPLE_EXTRACTION_SQL, meta)
    assert result.is_fully_valid
    assert len(result.valid_statements) == 7  # 1 account + 1 statement + 5 transactions


# ---------------------------------------------------------------------------
# _execute_sql
# ---------------------------------------------------------------------------

def test_execute_sql(test_db):
    """Execute validated SQL and verify database state."""
    meta = load_schema_metadata(SCHEMA_PATH)
    validation = validate_sql(SAMPLE_EXTRACTION_SQL, meta)

    conn = sqlite3.connect(str(test_db))
    conn.execute("PRAGMA foreign_keys = ON")
    txn_count = _execute_sql(conn, validation)

    assert txn_count == 5

    # Verify account was created
    row = conn.execute("SELECT bank_name FROM accounts WHERE account_id = '000123456789'").fetchone()
    assert row[0] == "Bank of America"

    # Verify statement was created
    row = conn.execute("SELECT period_start, period_end FROM statements WHERE statement_id = 'stmt_2023-02-10'").fetchone()
    assert row[0] == "2023-01-14"
    assert row[1] == "2023-02-10"

    # Verify transactions
    rows = conn.execute("SELECT COUNT(*) FROM transactions WHERE statement_id = 'stmt_2023-02-10'").fetchone()
    assert rows[0] == 5

    # Verify a specific transaction
    row = conn.execute(
        "SELECT description_raw, amount, is_deposit, type FROM transactions WHERE transaction_id = 'stmt_2023-02-10_003'"
    ).fetchone()
    assert "VERIZON" in row[0]
    assert row[1] == -85.50
    assert row[2] == 0
    assert row[3] == "ach_debit"

    conn.close()


# ---------------------------------------------------------------------------
# _verify_balance
# ---------------------------------------------------------------------------

def test_verify_balance_pass(test_db):
    """Balance verification should pass when numbers add up."""
    meta = load_schema_metadata(SCHEMA_PATH)
    validation = validate_sql(SAMPLE_EXTRACTION_SQL, meta)

    conn = sqlite3.connect(str(test_db))
    conn.execute("PRAGMA foreign_keys = ON")
    _execute_sql(conn, validation)

    # 1000.00 + 200.00 + (-250.00) = 950.00 ✓
    passed = _verify_balance(conn, "stmt_2023-02-10")
    assert passed

    row = conn.execute("SELECT validation_passed FROM statements WHERE statement_id = 'stmt_2023-02-10'").fetchone()
    assert row[0] == 1
    conn.close()


def test_verify_balance_fail(test_db):
    """Balance verification should fail when numbers don't add up."""
    conn = sqlite3.connect(str(test_db))
    conn.execute("PRAGMA foreign_keys = ON")

    # Insert with mismatched totals
    conn.execute("INSERT INTO accounts (account_id, bank_name) VALUES ('test', 'Test Bank')")
    conn.execute(
        "INSERT INTO statements (statement_id, account_id, source_file, period_start, period_end, "
        "opening_balance, closing_balance, total_deposits, total_withdrawals, num_deposits, num_withdrawals, validation_passed) "
        "VALUES ('stmt_bad', 'test', 'bad.pdf', '2023-01-01', '2023-01-31', 1000.00, 500.00, 100.00, -100.00, 1, 1, 0)"
    )
    conn.commit()

    # 1000 + 100 + (-100) = 1000 ≠ 500
    passed = _verify_balance(conn, "stmt_bad")
    assert not passed
    conn.close()


# ---------------------------------------------------------------------------
# _categorize_transactions
# ---------------------------------------------------------------------------

def test_categorize_transactions(test_db):
    """Post-extraction categorization should tag some transactions."""
    meta = load_schema_metadata(SCHEMA_PATH)
    validation = validate_sql(SAMPLE_EXTRACTION_SQL, meta)

    conn = sqlite3.connect(str(test_db))
    conn.execute("PRAGMA foreign_keys = ON")
    _execute_sql(conn, validation)

    cat_count = _categorize_transactions(conn, "stmt_2023-02-10")
    # At least some transactions should be categorized (direct_deposit → income/salary, etc.)
    assert cat_count >= 1

    # Verify categorized values were written
    row = conn.execute(
        "SELECT category FROM transactions WHERE transaction_id = 'stmt_2023-02-10_001'"
    ).fetchone()
    # direct_deposit should map to income/salary via TYPE_FALLBACKS
    assert row[0] is not None
    conn.close()


# ---------------------------------------------------------------------------
# extract_statement (full flow with mocked API)
# ---------------------------------------------------------------------------

@patch("core.extraction_engine.extract._ocr_pdf", return_value="FAKE STATEMENT TEXT\nACCOUNT 000123456789\nBALANCE 950.00")
@patch("core.extraction_engine.extract.anthropic")
def test_extract_statement_success(mock_anthropic_mod, mock_ocr, test_db, test_pdf):
    """Full extraction flow with mocked Claude API."""
    mock_client = MagicMock()
    mock_anthropic_mod.Anthropic.return_value = mock_client
    mock_client.messages.create.return_value = _mock_claude_response(SAMPLE_EXTRACTION_SQL)

    result = extract_statement(test_pdf, test_db, api_key="test-key")

    assert result.status == "success"
    assert result.txn_count == 5
    assert result.statement_id == "stmt_2023-02-10"
    assert result.validation_passed is True
    assert result.categorized_count >= 1
    assert result.error is None

    # Verify data in database
    conn = sqlite3.connect(str(test_db))
    rows = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()
    assert rows[0] == 5
    conn.close()


@patch("core.extraction_engine.extract._ocr_pdf", return_value="FAKE STATEMENT TEXT\nACCOUNT 000123456789\nBALANCE 950.00")
@patch("core.extraction_engine.extract.anthropic")
def test_extract_statement_duplicate(mock_anthropic_mod, mock_ocr, test_db, test_pdf):
    """Duplicate import should return 'skipped' without calling API."""
    mock_client = MagicMock()
    mock_anthropic_mod.Anthropic.return_value = mock_client
    mock_client.messages.create.return_value = _mock_claude_response(SAMPLE_EXTRACTION_SQL)

    # First extraction
    result1 = extract_statement(test_pdf, test_db, api_key="test-key")
    assert result1.status == "success"

    # Second extraction of same file
    result2 = extract_statement(test_pdf, test_db, api_key="test-key")
    assert result2.status == "skipped"
    assert "already imported" in result2.error


def test_extract_statement_no_api_key(test_pdf, test_db):
    """Should return error when no API key is available."""
    with patch.dict("os.environ", {}, clear=True):
        result = extract_statement(test_pdf, test_db, api_key=None)
    assert result.status == "error"
    assert "API key" in result.error


def test_extract_statement_pdf_not_found(test_db, tmp_path):
    """Should return error when PDF doesn't exist."""
    result = extract_statement(tmp_path / "nonexistent.pdf", test_db, api_key="test-key")
    assert result.status == "error"
    assert "not found" in result.error


@patch("core.extraction_engine.extract._ocr_pdf", return_value="FAKE STATEMENT TEXT\nACCOUNT 000123456789\nBALANCE 950.00")
@patch("core.extraction_engine.extract.anthropic")
def test_extract_statement_malformed_sql_retries(mock_anthropic_mod, mock_ocr, test_db, test_pdf):
    """When first attempt returns garbage, should retry and succeed."""
    mock_client = MagicMock()
    mock_anthropic_mod.Anthropic.return_value = mock_client

    # First call returns garbage, second returns good SQL
    mock_client.messages.create.side_effect = [
        _mock_claude_response("This is not SQL at all, just random text."),
        _mock_claude_response(SAMPLE_EXTRACTION_SQL),
    ]

    result = extract_statement(test_pdf, test_db, api_key="test-key")
    assert result.status == "success"
    assert result.txn_count == 5
    assert len(result.warnings) > 0  # Should have a retry warning
