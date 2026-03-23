"""Tests for core.extraction_engine.sql_validator."""

import pytest
from pathlib import Path

from core.extraction_engine.sql_validator import (
    ParsedInsert,
    ValidationResult,
    load_schema_metadata,
    parse_insert,
    to_parameterized,
    validate_sql,
)

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "core" / "database" / "schema.sql"


# ---------------------------------------------------------------------------
# load_schema_metadata
# ---------------------------------------------------------------------------

def test_load_schema_metadata():
    meta = load_schema_metadata(SCHEMA_PATH)
    assert "accounts" in meta
    assert "statements" in meta
    assert "transactions" in meta
    assert "account_id" in meta["accounts"]
    assert "statement_id" in meta["statements"]
    assert "transaction_id" in meta["transactions"]


def test_schema_has_all_transaction_columns():
    meta = load_schema_metadata(SCHEMA_PATH)
    expected = {"transaction_id", "statement_id", "account_id", "date",
                "description_raw", "amount", "is_deposit", "type"}
    assert expected.issubset(set(meta["transactions"]))


# ---------------------------------------------------------------------------
# parse_insert
# ---------------------------------------------------------------------------

def test_parse_simple_insert():
    sql = "INSERT INTO accounts (account_id, bank_name) VALUES ('123', 'BoA')"
    p = parse_insert(sql)
    assert p.table == "accounts"
    assert p.columns == ["account_id", "bank_name"]
    assert p.values == ["123", "BoA"]
    assert p.on_conflict is None


def test_parse_insert_or_ignore():
    sql = "INSERT OR IGNORE INTO accounts (account_id, bank_name) VALUES ('123', 'BoA')"
    p = parse_insert(sql)
    assert p.table == "accounts"
    assert p.columns == ["account_id", "bank_name"]


def test_parse_insert_on_conflict():
    sql = (
        "INSERT INTO accounts (account_id, bank_name) VALUES ('123', 'BoA') "
        "ON CONFLICT(account_id) DO UPDATE SET bank_name = excluded.bank_name"
    )
    p = parse_insert(sql)
    assert p.table == "accounts"
    assert p.on_conflict is not None
    assert "DO UPDATE SET" in p.on_conflict


def test_parse_insert_with_null():
    sql = "INSERT INTO transactions (transaction_id, statement_id, account_id, date, description_raw, amount, is_deposit, type) VALUES ('t1', 's1', 'a1', '2023-01-15', NULL, -85.50, 0, 'ach_debit')"
    p = parse_insert(sql)
    assert p.values[4] is None
    assert p.values[5] == -85.50
    assert p.values[6] == 0


def test_parse_insert_with_escaped_quote():
    sql = "INSERT INTO transactions (transaction_id, statement_id, account_id, date, description_raw, amount, is_deposit, type) VALUES ('t1', 's1', 'a1', '2023-01-15', 'MCDONALD''S #1234', -12.50, 0, 'purchase')"
    p = parse_insert(sql)
    assert p.values[4] == "MCDONALD'S #1234"


def test_parse_not_insert_raises():
    with pytest.raises(ValueError, match="Not an INSERT"):
        parse_insert("DELETE FROM accounts WHERE 1=1")


# ---------------------------------------------------------------------------
# validate_sql
# ---------------------------------------------------------------------------

def test_validate_valid_sql():
    meta = load_schema_metadata(SCHEMA_PATH)
    sql = (
        "INSERT OR IGNORE INTO accounts (account_id, bank_name, account_type, holder_name) "
        "VALUES ('000123456789', 'Bank of America', 'checking', 'Alex Ho');\n"
        "INSERT INTO transactions (transaction_id, statement_id, account_id, date, "
        "description_raw, amount, is_deposit, type) "
        "VALUES ('stmt_2023-02-10_001', 'stmt_2023-02-10', '000123456789', "
        "'2023-01-15', 'ACH DEBIT VERIZON', -85.50, 0, 'ach_debit');"
    )
    result = validate_sql(sql, meta)
    assert result.is_fully_valid
    assert len(result.valid_statements) == 2


def test_validate_rejects_drop():
    meta = load_schema_metadata(SCHEMA_PATH)
    sql = "DROP TABLE accounts;"
    result = validate_sql(sql, meta)
    assert not result.is_fully_valid
    assert any("DROP" in e for e in result.errors)


def test_validate_rejects_delete():
    meta = load_schema_metadata(SCHEMA_PATH)
    sql = "DELETE FROM transactions WHERE 1=1;"
    result = validate_sql(sql, meta)
    assert not result.is_fully_valid


def test_validate_rejects_update():
    meta = load_schema_metadata(SCHEMA_PATH)
    sql = "UPDATE accounts SET bank_name = 'Evil Bank';"
    result = validate_sql(sql, meta)
    assert not result.is_fully_valid


def test_validate_rejects_unknown_table():
    meta = load_schema_metadata(SCHEMA_PATH)
    sql = "INSERT INTO users (name) VALUES ('test');"
    result = validate_sql(sql, meta)
    assert not result.is_fully_valid
    assert any("users" in e for e in result.errors)


def test_validate_rejects_unknown_column():
    meta = load_schema_metadata(SCHEMA_PATH)
    sql = "INSERT INTO accounts (account_id, fake_column) VALUES ('123', 'x');"
    result = validate_sql(sql, meta)
    assert not result.is_fully_valid
    assert any("fake_column" in e for e in result.errors)


def test_validate_rejects_column_value_mismatch():
    meta = load_schema_metadata(SCHEMA_PATH)
    sql = "INSERT INTO accounts (account_id, bank_name) VALUES ('123');"
    result = validate_sql(sql, meta)
    assert not result.is_fully_valid
    assert any("count" in e.lower() for e in result.errors)


def test_validate_allows_on_conflict():
    meta = load_schema_metadata(SCHEMA_PATH)
    sql = (
        "INSERT INTO accounts (account_id, bank_name, account_type, holder_name) "
        "VALUES ('123', 'BoA', 'checking', 'John') "
        "ON CONFLICT(account_id) DO UPDATE SET bank_name = excluded.bank_name, "
        "account_type = excluded.account_type, holder_name = excluded.holder_name;"
    )
    result = validate_sql(sql, meta)
    assert result.is_fully_valid


def test_validate_strips_markdown_fences():
    meta = load_schema_metadata(SCHEMA_PATH)
    sql = "```sql\nINSERT INTO accounts (account_id, bank_name) VALUES ('123', 'BoA');\n```"
    result = validate_sql(sql, meta)
    assert len(result.valid_statements) == 1


def test_validate_mixed_valid_and_invalid():
    meta = load_schema_metadata(SCHEMA_PATH)
    sql = (
        "INSERT INTO accounts (account_id, bank_name) VALUES ('123', 'BoA');\n"
        "DELETE FROM accounts;\n"
        "INSERT INTO accounts (account_id, bank_name) VALUES ('456', 'Chase');"
    )
    result = validate_sql(sql, meta)
    assert len(result.valid_statements) == 2
    assert len(result.rejected_statements) == 1
    assert not result.is_fully_valid


# ---------------------------------------------------------------------------
# to_parameterized
# ---------------------------------------------------------------------------

def test_to_parameterized_basic():
    p = ParsedInsert(
        table="accounts",
        columns=["account_id", "bank_name"],
        values=["123", "BoA"],
        raw_sql="INSERT INTO accounts ...",
    )
    sql, vals = to_parameterized(p)
    assert sql == "INSERT INTO accounts (account_id, bank_name) VALUES (?, ?)"
    assert vals == ("123", "BoA")


def test_to_parameterized_or_ignore():
    p = ParsedInsert(
        table="accounts",
        columns=["account_id"],
        values=["123"],
        raw_sql="INSERT OR IGNORE INTO accounts (account_id) VALUES ('123')",
    )
    sql, vals = to_parameterized(p)
    assert "OR IGNORE" in sql
    assert vals == ("123",)


def test_to_parameterized_on_conflict():
    p = ParsedInsert(
        table="accounts",
        columns=["account_id", "bank_name"],
        values=["123", "BoA"],
        raw_sql="INSERT INTO accounts ...",
        on_conflict="ON CONFLICT(account_id) DO UPDATE SET bank_name = excluded.bank_name",
    )
    sql, vals = to_parameterized(p)
    assert "ON CONFLICT" in sql
    assert vals == ("123", "BoA")
