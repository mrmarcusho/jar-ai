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
    transaction_id          TEXT PRIMARY KEY,   -- "{statement_id}_{txn.id}" or "email_{hash}"
    statement_id            TEXT REFERENCES statements(statement_id),  -- NULL for email-sourced rows
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
    source                  TEXT NOT NULL DEFAULT 'bank_statement',  -- 'bank_statement' | 'email'
    dedupe_hash             TEXT,               -- cross-sync dedup key (email rows)
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
CREATE INDEX IF NOT EXISTS idx_transactions_dedupe     ON transactions(dedupe_hash);
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

-- HSM (Holy Shit Moment) findings detected after extraction
CREATE TABLE IF NOT EXISTS findings (
    finding_id      TEXT PRIMARY KEY,
    statement_id    TEXT NOT NULL REFERENCES statements(statement_id),
    hsm_id          TEXT NOT NULL,       -- e.g. "A03", "C02"
    domain          TEXT NOT NULL,       -- e.g. "subscriptions", "banking"
    title           TEXT NOT NULL,
    description     TEXT NOT NULL,
    amount_impact   REAL,                -- estimated annual/total dollar impact
    severity        TEXT NOT NULL DEFAULT 'warning',  -- critical, warning, info
    merchant        TEXT,
    dismissed       INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_findings_statement ON findings(statement_id);
CREATE INDEX IF NOT EXISTS idx_findings_dismissed ON findings(dismissed);

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

-- Household profile — user identity for Pass 1 PII redaction
-- Loaded once per extraction session; never sent to the cloud.
CREATE TABLE IF NOT EXISTS household_profile (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    field_type  TEXT NOT NULL,   -- 'holder_first' | 'holder_last' | 'holder_alias'
                                 -- | 'partner_first' | 'partner_last' | 'street' | 'city'
    value       TEXT NOT NULL,
    created_at  TEXT DEFAULT (datetime('now'))
);

-- Email-sourced prose / non-ledger items: legal, insurance, tax, medical,
-- housing, employment, government notices, and appointments. Financial line
-- items from email go into transactions (source='email') instead.
CREATE TABLE IF NOT EXISTS documents (
    document_id     TEXT PRIMARY KEY,         -- "email_" + dedupe_hash
    source          TEXT NOT NULL,            -- 'email'
    source_id       TEXT,                     -- sha256(Message-ID); never the raw IMAP id
    category        TEXT,                     -- legal|insurance|tax|medical|housing|
                                              -- employment|government|appointment|other
    event_date      TEXT,                     -- ISO 8601
    sender_domain   TEXT,
    summary         TEXT NOT NULL,            -- AI-generated one-sentence summary
    action_required INTEGER NOT NULL DEFAULT 0,
    deadline        TEXT,
    amount          REAL,
    body_redacted   TEXT,                     -- Presidio-cleaned body; local only
    metadata        TEXT,                     -- JSON overflow (appt time, provider_type, ...)
    dedupe_hash     TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_documents_date   ON documents(event_date);
CREATE INDEX IF NOT EXISTS idx_documents_action ON documents(action_required);
CREATE INDEX IF NOT EXISTS idx_documents_dedupe ON documents(dedupe_hash);

-- Incremental email sync bookkeeping: where each account's last scan stopped.
CREATE TABLE IF NOT EXISTS email_sync_state (
    username        TEXT PRIMARY KEY,
    last_sync_at    TEXT,
    last_imap_date  TEXT,     -- most recent email date seen (RFC 2822 / IMAP date)
    last_uid        INTEGER,  -- highest IMAP UID seen; used as the sync cursor
    uidvalidity     TEXT      -- IMAP UIDVALIDITY; mismatch triggers full rescan
);

CREATE TABLE IF NOT EXISTS message_sync_state (
    id              INTEGER PRIMARY KEY CHECK (id = 1), -- single-row table
    last_rowid      INTEGER,  -- highest message.ROWID ingested; the sync cursor
    last_guid       TEXT,     -- guid of the message at last_rowid (cursor anchor)
    db_fingerprint  TEXT,     -- detects chat.db replacement / VACUUM (ROWID renumber)
    last_sync_at    TEXT
);
