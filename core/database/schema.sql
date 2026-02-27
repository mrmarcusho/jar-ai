-- JAR Layer 2: SQLite schema for bank statement data
-- Three tables: accounts, statements, transactions

CREATE TABLE IF NOT EXISTS accounts (
    account_id      TEXT PRIMARY KEY,   -- account number (e.g. "000123456789")
    bank_name       TEXT,
    account_type    TEXT,
    holder_name     TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS statements (
    statement_id        TEXT PRIMARY KEY,   -- derived from source_file
    account_id          TEXT NOT NULL REFERENCES accounts(account_id),
    source_file         TEXT NOT NULL UNIQUE,
    period_start        TEXT NOT NULL,
    period_end          TEXT NOT NULL,
    opening_balance     REAL NOT NULL,
    closing_balance     REAL NOT NULL,
    total_deposits      REAL,
    total_withdrawals   REAL,
    num_deposits        INTEGER,
    num_withdrawals     INTEGER,
    validation_passed   INTEGER NOT NULL DEFAULT 0,  -- boolean: 1 if all checks pass
    loaded_at           TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS transactions (
    transaction_id          TEXT PRIMARY KEY,   -- "{statement_id}_{txn.id}"
    statement_id            TEXT NOT NULL REFERENCES statements(statement_id),
    account_id              TEXT NOT NULL REFERENCES accounts(account_id),
    date                    TEXT NOT NULL,
    description_raw         TEXT,
    description_normalized  TEXT,
    amount                  REAL NOT NULL,
    is_deposit              INTEGER NOT NULL,   -- 1 = deposit, 0 = withdrawal
    type                    TEXT,
    category                TEXT,
    recurring               INTEGER,            -- nullable boolean
    ach_company_name        TEXT,
    ach_description         TEXT,
    ach_individual_name     TEXT,
    ach_entry_class         TEXT,
    ach_company_id          TEXT,
    ach_trace_number        TEXT,
    ach_secondary_co_id     TEXT,
    ach_originator          TEXT
);

-- Indexes for common query patterns
CREATE INDEX IF NOT EXISTS idx_transactions_date       ON transactions(date);
CREATE INDEX IF NOT EXISTS idx_transactions_account_id ON transactions(account_id);
CREATE INDEX IF NOT EXISTS idx_transactions_type       ON transactions(type);
CREATE INDEX IF NOT EXISTS idx_transactions_category   ON transactions(category);
CREATE INDEX IF NOT EXISTS idx_transactions_amount     ON transactions(amount);
CREATE INDEX IF NOT EXISTS idx_statements_account_id   ON statements(account_id);
