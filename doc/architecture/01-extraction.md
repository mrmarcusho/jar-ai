# 1. Extraction — turning a PDF into data

**Job:** take a bank statement PDF and end up with clean, structured rows in the
database — accurate down to the penny.

**Main file:** `core/extraction_engine/extract.py`
**The prompt:** `core/extraction_engine/prompts/master_extraction.py`
**The safety check:** `core/extraction_engine/sql_validator.py`

## The flow

```
PDF file
  │
  ├─ 1. Read the text   (PyMuPDF; if the PDF is scanned, OCR it locally)
  ├─ 2. Scrub PII       (RedactionEngine swaps names/accounts for tokens)
  ├─ 3. Build a prompt  (full DB schema + extraction rules + the text)
  ├─ 4. Ask Claude      → Claude replies with SQL INSERT statements
  ├─ 5. Validate the SQL (does it match the schema? right types? no funny business?)
  │        └─ if invalid, retry once with the errors fed back
  ├─ 6. Run the SQL     (one transaction, locally, on jar.db)
  ├─ 7. Categorize      (label each new transaction — see doc 04)
  ├─ 8. Verify balances (opening + deposits + withdrawals must equal closing)
  └─ 9. Find insights   (a second Claude pass spots notable things → "findings")
```

The result is an `ExtractionResult` reporting how many transactions were added,
whether the balance checks passed, and any findings.

## Why it works this way

- **No intermediate JSON.** Claude writes SQL directly. The schema *is* the
  contract — anything that doesn't fit the schema gets rejected by the validator
  before it can touch your database.
- **Scrubbed before sending.** The raw statement text never leaves your machine
  with real names or account numbers in it. See
  [Privacy & Redaction](06-privacy-and-redaction.md).
- **Balance math is the truth test.** If `opening + deposits − withdrawals`
  doesn't equal the closing balance, the statement is flagged. This catches a
  surprising number of extraction mistakes automatically.
- **Scanned PDFs stay private too.** If a PDF is an image (no selectable text),
  JAR runs OCR *locally* with Tesseract rather than shipping the image to the cloud.

## A few rules Claude is told to follow

- Withdrawals are **negative** amounts, deposits are **positive** — the sign
  encodes the direction.
- IDs are derived deterministically from the filename (e.g. `eStmt_2023-02-10`
  becomes statement `stmt_2023-02-10`, transactions `stmt_2023-02-10_001`, …).
- Pull data from **all pages**, skip pending transactions, and never confuse the
  running balance with a transaction amount.

## Where it's triggered

The web server calls extraction when you import a PDF (`POST /api/import`) or
process files found by the scanner. Progress is streamed back to the browser
live so you watch it work. See [Server & UI](09-server-and-ui.md).
