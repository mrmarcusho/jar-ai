# JAR (家) — Claude Code Context

Privacy-first, fully local personal finance app for middle-class dual-income families.
家 means "family" in Chinese. No cloud, no servers, no APIs. Ever.

## Team
- **Alex** — product, AI/ML, pipeline dev (CS student at Tufts)
- **Dad** — financial domain architect, tech lead, code reviewer (30 years exp, $100K invested)
- **Contractor** — native app, on-device ML integration
- **Claude Code** — pair programmer (~50% of raw code output)

## Architecture (3 layers)

```
┌─────────────────────────────┐
│  SwiftUI App                │
│  - UI / UX                  │
│  - SQLCipher (direct)       │
│  - File management          │
│                             │
│  ←── JSON-RPC / stdin ──→   │
│                             │
│  Python Sidecar (bundled)   │
│  - Docling + PaddleOCR      │
│  - MLX + Qwen inference     │
│  - Extraction pipeline      │
└─────────────────────────────┘
```

### Layer 1 — Extraction Engine (`extraction/`) [THE MOAT — 80% of dev effort]
Takes any financial document (PDF, photo, email), outputs structured data into SQLite.
Must extract EVERYTHING: transactions, account policies, fee changes, reward tiers, legal notices.

**Pipeline:**
1. Docling parses PDF structure (tables, headers, paragraphs, reading order)
2. Page classification (rules-based for known formats, LLM for unknown)
3. Transaction extraction (structured parsing first, LLM fallback)
4. Validation engine (opening + deposits - withdrawals = closing balance)
5. Normalization & SQLite insertion

**Models:**
- Docling + PaddleOCR for document parsing
- Qwen2.5-VL 7B via MLX for document extraction
- Two-pass: page classification first, then type-specific extraction per page

### Layer 2 — APIs (`api/`) [Pure Python, zero AI]
SQL queries, math, pattern detection as clean deterministic functions.
Examples: `getSpendingByCategory()`, `getCashFlowForecast()`, `getAnomalies()`
No external dependencies. Fully testable.

### Layer 3 — LLM Interface (`llm/`) [Thin translation layer]
Small model (Qwen2.5 7B via MLX) translates natural language → Layer 2 API calls → formatted results.
The LLM does NOT understand finance. It only knows which function to call.

## Rules
- **API layer must stay dependency-free** — no pip installs in `api/`
- **No network calls** outside the LLM layer
- **All transaction amounts in cents (integer)** — never float
- **SQLCipher accessed from Swift only** — database never touches Python layer
- **Never modify extraction layer** without validating against ground truth JSON
- **Dates as ISO 8601** (2023-01-17) in storage, human-readable in UI

## Database
SQLite + SQLCipher (encrypted). Schema lives in `schema/`.

## Ground Truth JSON Schema
The answer key all extraction work is measured against:
```
metadata          → source file, statement period, bank entity, account info
account_summary   → opening/closing balance, totals, counts
deposits          → full transaction objects with parsed ACH fields
withdrawals       → full transaction objects with parsed fields
fees_and_charges
account_policies  → Preferred Rewards tier, ATM policies, fee waivers
notices           → fee changes with effective dates, standard disclosures
validation        → math reconciliation checks
```

**Key schema rules:**
- Every transaction has `description_raw` (exact PDF text) AND `description_normalized` (human-readable)
- ACH transactions have full `parsed_fields`: company name, individual name, entry class, trace number, originator
- Categories use hierarchical taxonomy: `housing:utilities:electric`, `transfer:credit_card_payment`
- Multi-service merchants use tags: `"tags": ["phone", "internet", "cable"]`
- Every transaction has `recurring: true/false/null`
- Derived data (summaries, recurring lists) NOT in ground truth — computed at query time by Layer 2
- `_note` fields for parser guidance (e.g. "JAMIE LEE likely typo for JAMIE LEE")

## Running the Project
```bash
# Start Python sidecar / extraction pipeline
python extraction/main.py

# Run test suite
pytest tests/

# Validate extraction against ground truth
python extraction/validate.py <pdf> <ground_truth.json>
```

## Current Status
- ✅ Ground truth JSON completed for BofA checking account statement (8 pages)
- ✅ All transactions validated (math reconciles to the penny)
- ✅ ACH fields parsed for all electronic transactions
- ✅ Account policies captured (Preferred Rewards Platinum Honors)
- ✅ Fee change notices captured with effective dates
- 🔄 NEXT: Get Docling running, feed it BofA PDF, compare output to ground truth

## Roadmap
- Month 1-3: Layer 1 — Extraction engine (BofA first, expand to other banks)
- Month 3-4: Layer 2 — Financial APIs
- Month 4-5: Layer 3 — LLM interface
- Month 6: Polish, test, ship
- Target ship: late 2026

## Key Lessons (inform all extraction work)
1. Copy-paste text extraction destroys column alignment — Docling preserves spatial relationships
2. ACH fields are a goldmine nobody else extracts (company name, individual name, entry class, trace number)
3. Multi-line descriptions wrap across rows and break sequential alignment — handle explicitly
4. Financial documents are self-verifying: opening + deposits - withdrawals = closing balance
5. Non-transaction pages (policies, notices, fee changes) are where JAR differentiates from competitors
6. Truncated ACH company names need a lookup table ("NEW JERSEY - AME" → "New Jersey American Water")
7. Bank typos in ACH records are real — capture as-is, link in normalization layer
8. Bank statements have two addresses (issuing entity vs. correspondence center) — useful for classification

## Privacy Architecture
- Transaction data (personal) → ALWAYS local, never leaves device
- Policy/fee/rewards pages (zero PII, identical for all customers) → can use cloud LLM for accuracy,
  extracted once, cached, pushed to users as app updates

## Competitive Context
Key moat: extraction depth (policies, notices, ACH fields) + format library that compounds over time.
Main competitors: Vaultwise (developer-oriented), Actual Budget (no AI), Monarch/Copilot/YNAB (cloud).
Window to ship: ~12 months before space gets crowded.