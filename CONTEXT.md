# JAR (家) — Project Context

## What Is Jar?
A fully local, on-device financial intelligence app for middle-class dual-income families. All data processing, storage, and AI inference happen on the user's device. No cloud, no servers, no APIs. Privacy is the fundamental selling point.

The name 家 means "family" in Chinese.

## Business Model
- One-time purchase (NOT subscription)
- Desktop: ~$40, Mobile: ~$15, Family license: ~$80
- Expansion packs: $10-15 each
- Major version upgrades every 12-18 months
- Revenue target: ~4,500 sales to recover $100K investment in year one

## Team
- **Alex** (product, AI/ML, pipeline development, CS student at Tufts)
- **Alex's dad** (financial domain architect, code reviewer, tech lead at Millennium with 30 years experience, investing $100K)
- **One contractor** (native app, on-device ML integration)
- **Claude Code** (pair programmer handling ~50% of raw code output)

## Three-Layer Architecture

### Layer 1 — Document Extraction Engine (THE MOAT, 80% of dev effort)
Takes any financial document (PDF, photo, email), outputs perfectly structured data into SQLite. Must extract EVERYTHING — not just transactions but account policies, fee changes, reward tiers, legal notices.

**Pipeline:**
1. Docling parses PDF structure (tables, headers, paragraphs, reading order)
2. Page classification (rules-based for known formats, LLM for unknown)
3. Transaction extraction (structured parsing first, LLM fallback)
4. Validation engine (financial documents are self-verifying — opening + deposits - withdrawals = closing)
5. Normalization & SQLite insertion

**Key models:**
- Docling + PaddleOCR for document parsing
- Qwen2.5-VL 7B for semantic extraction (vision-language model)
- Two-pass approach: page classification first, then type-specific extraction per page

### Layer 2 — APIs (pure code, no AI)
SQL queries, math, pattern detection exposed as clean functions: getSpendingByCategory(), getCashFlowForecast(), getAnomalies(), etc. Deterministic, testable, reliable.

### Layer 3 — LLM Interface (thin translation layer)
Small model (3-4B) translates natural language into API calls from Layer 2 and formats results back. The LLM doesn't understand finance — it just knows which function to call.

## Technical Architecture: Mac-First with SwiftUI + Python Sidecar

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
│  - Docling                  │
│  - MLX + Qwen inference     │
│  - PaddleOCR                │
│  - Extraction pipeline      │
└─────────────────────────────┘
```

**Why this stack:**
- MLX is purpose-built for Apple Silicon, best inference performance on Mac
- SwiftUI gives genuinely native app (drag-and-drop, menu bar, Spotlight, notifications)
- Python environment bundled via PyInstaller into app bundle
- SQLCipher accessed directly from Swift (database stays out of Python layer)
- Direct distribution (not App Store) to avoid 30% cut

**On-device models:**
- Qwen2.5-VL 7B (document extraction) via MLX
- Qwen2.5 7B (text queries) via MLX
- Small embedding model for search
- llama.cpp on Windows/Linux (future)

**Database:** SQLite + SQLCipher (encrypted)

## Privacy Architecture
- Transaction data (personal) → ALWAYS local, never leaves device
- Policy/fee/rewards pages (identical for all customers, zero PII) → can use cloud LLM for better accuracy, extracted once, cached, pushed to all users as app updates

## Ground Truth JSON Schema

The ground truth file is the test answer key. The parser gets measured against it. Schema:

```json
{
    "metadata": { ... },        // source file, statement period, bank entity, account info
    "account_summary": { ... }, // opening/closing balance, totals, counts
    "deposits": [ ... ],        // full transaction objects with parsed ACH fields
    "withdrawals": [ ... ],     // full transaction objects with parsed fields
    "fees_and_charges": { ... },
    "account_policies": { ... }, // Preferred Rewards tier, ATM policies, fee waivers
    "notices": [ ... ],         // fee changes with effective dates, standard disclosures
    "validation": { ... }       // math reconciliation checks
}
```

**Key schema decisions:**
- Dates stored as ISO 8601 (2023-01-17), displayed as human-readable in UI
- Every transaction has both `description_raw` (exact PDF text) and `description_normalized` (human-readable)
- ACH transactions have full `parsed_fields` (company name, description, company ID, individual name, trace number, entry class, originator)
- Categories use hierarchical taxonomy: `housing:utilities:electric`, `housing:mortgage`, `transfer:credit_card_payment`
- Multi-service merchants use tags: `"category": "housing:utilities:telecom", "tags": ["phone", "internet", "cable"]`
- Every transaction has `recurring: true/false/null`
- Derived data (category summaries, recurring transaction lists) NOT stored in ground truth — computed at query time by Layer 2
- `_note` fields for parser guidance (e.g., "JAMIE LEE likely typo for JAMIE LEE")

## Current Status
- **COMPLETED:** First ground truth JSON from BofA checking account statement (8 pages)
  - All transactions aligned and validated (math reconciles to the penny)
  - ACH fields parsed for all electronic transactions
  - Account policies captured (Preferred Rewards Platinum Honors tier with specific benefit numbers)
  - Fee change notices captured with effective dates
  - Standard disclosures cataloged
- **NEXT STEP:** Get Docling running, feed it the same BofA PDF, compare output to ground truth JSON

## Development Roadmap (6 months)
- Month 1-3: Layer 1 — Extraction engine (start with BofA, expand)
- Month 3-4: Layer 2 — APIs
- Month 4-5: Layer 3 — LLM interface
- Month 6: Polish, test, ship

## Competitive Landscape
- **Vaultwise** — closest competitor (local AI + PDF extraction) but developer-oriented, not productized
- **SenticMoney** — Windows-only, simpler extraction, subscription model
- **Actual Budget** — open source local-first but no AI, manual entry only
- **Monarch Money, Copilot, YNAB, Quicken** — all cloud-based, Plaid-dependent
- **Window to ship:** ~12 months before space gets crowded. Need shippable product by late 2026.
- **Key moat:** extraction engine depth (policies, notices, ACH fields) + format library that compounds over time

## Key Lessons from Ground Truth Creation
1. Copy-paste text extraction destroys column alignment — Docling preserves spatial relationships
2. ACH fields are a goldmine nobody else extracts (company name, individual name, entry class, trace number)
3. Bank statements have two addresses (issuing entity vs. correspondence center) — useful for classification
4. Multi-line descriptions wrap across rows and break sequential alignment
5. Financial documents are self-verifying: opening + deposits - withdrawals = closing balance
6. Non-transaction pages (policies, notices, fee changes) are where Jar differentiates from competitors
7. Truncated ACH company names need a lookup table (e.g., "NEW JERSEY - AME" → "New Jersey American Water")
8. Bank typos in ACH records are real (JAMIE LEE vs JAMIE LEE) — capture as-is, link in normalization layer