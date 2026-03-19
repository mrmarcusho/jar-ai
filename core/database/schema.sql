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
    subcategory             TEXT,
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

-- User-created categorization rules (learning loop)
CREATE TABLE IF NOT EXISTS user_category_rules (
    rule_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword             TEXT NOT NULL,
    match_type          TEXT NOT NULL DEFAULT 'contains',
    category            TEXT NOT NULL,
    subcategory         TEXT,
    source_description  TEXT,
    source_transaction_id TEXT,
    times_matched       INTEGER DEFAULT 0,
    active              INTEGER DEFAULT 1,
    created_at          TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_user_rules_keyword ON user_category_rules(keyword);

-- Per-transaction user overrides
CREATE TABLE IF NOT EXISTS user_category_overrides (
    override_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id  TEXT NOT NULL UNIQUE REFERENCES transactions(transaction_id),
    category        TEXT NOT NULL,
    subcategory     TEXT,
    household_tag   TEXT,
    budget_type     TEXT,
    custom_tags     TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_overrides_txn ON user_category_overrides(transaction_id);
