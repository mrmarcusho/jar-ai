# 2. Database — where everything is stored

**Job:** hold all your structured data in one local SQLite file.

**Schema:** `core/database/schema.sql`
**Migrations:** `core/database/migrate.py` (runs on server startup)
**File on disk:** `data/jar.db` (never committed to git)

Everything is plain SQLite. No encryption in the MVP — privacy comes from the
data staying on your machine and from PII being scrubbed before anything is sent
to the cloud.

## The core tables

```
accounts ──< statements ──< transactions
  one bank     one PDF        one line on the statement
  account      statement
```

| Table | What's in it |
|-------|--------------|
| **accounts** | One row per bank account (bank name, account type, holder). |
| **statements** | One row per imported PDF — the period, opening/closing balances, deposit and withdrawal totals, and whether balance validation passed. |
| **transactions** | One row per line item — date, description, amount (negative = money out), type, category, plus detailed ACH fields when present. |

## The supporting tables

| Table | What it's for |
|-------|---------------|
| **user_category_rules** | Rules you've taught JAR: "anything matching X → category Y." See [Categorization](04-categorization.md). |
| **user_category_overrides** | One-off manual labels on a specific transaction. |
| **findings** | Notable insights surfaced during extraction (e.g. a new recurring charge, an unusual fee). |
| **household_profile** | Your name, partner's name, address — used **only locally** to scrub PII before sending text to the cloud. See [Privacy](06-privacy-and-redaction.md). |
| **documents** | Email-sourced prose (insurance, medical, housing, etc.), redacted. |
| **email_sync_state** / **message_sync_state** | Bookmarks so syncs pick up where they left off. See [Email & Messages](08-email-and-messages.md). |

There are also indexes on the columns queries hit most (date, account, type,
category, amount) so lookups stay fast.

## How data gets in

- **The main path:** [Extraction](01-extraction.md) writes transactions directly
  via validated SQL.
- **Loader (`load_statements.py`):** a helper that loads previously-extracted
  structured JSON into the tables — useful for re-loading and testing.

## Rules the project sticks to

- Dates are stored as ISO 8601 (`2023-01-17`) and shown human-friendly in the UI.
- Amounts are floats for the MVP (integer cents is deferred to the native app).
- **Every query uses parameterized statements** — never string interpolation —
  to stay safe against SQL injection.
