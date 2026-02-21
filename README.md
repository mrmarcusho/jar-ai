# Jar — Project Brief

## What is Jar?
Jar (家 — "family" in Chinese) is a fully local, on-device financial intelligence app. All data processing, storage, and AI inference happen on the user's device. No cloud, no servers, no APIs. Privacy is the core selling point.

## Target User
Middle-class dual-income families with kids. Time-strapped, already dealing with financial paperwork, need something that captures the work they're already doing and makes it intelligent.

## Business Model
One-time purchase, not subscription. No ongoing server costs.
- Mobile app: ~$15
- Desktop app: ~$40
- Family license: ~$80
- Expansion packs ($10-15 each): Tax documents, medical/insurance, investments, small business, language packs
- Major version upgrades every 12-18 months

Revenue target: ~4,500 sales to recover $100K investment in year one. $500K-$1.5M cumulative by end of year two.

## Team
- Alex (me): Product, AI/ML, pipeline development. CS student at Tufts.
- My dad: Financial domain architect, code reviewer. Tech lead at Millennium with 30 years building financial trading systems. Investing $100K.
- One contractor: Native app, on-device ML integration.
- Claude Code: Pair programmer handling ~50% of raw code output.

## Three-Layer Architecture

### Layer 1 — Document Extraction Engine (the moat, 80% of dev effort)
Takes any financial document in (PDF, photo, email), pushes perfectly structured data into SQLite. Uses Docling + PaddleOCR for document parsing, Qwen2.5-VL 7B for semantic extraction, and a validation engine for accuracy. Must extract EVERYTHING — not just transactions but account policies, fee changes, reward tiers, legal notices. Two-pass approach: page classification first, then type-specific extraction per page.

### Layer 2 — APIs (pure code, no AI)
SQL queries, math, pattern detection exposed as clean functions: getSpendingByCategory(), getCashFlowForecast(), getAnomalies(), getRecurringExpenses(), getTaxSummary(), searchTransactions(), etc. Deterministic, testable, reliable.

### Layer 3 — LLM Interface (thin translation layer)
Small model (3-4B) translates natural language into API calls from Layer 2 and formats results back into natural language. The LLM doesn't understand finance — it just knows which function to call.

## Key Technical Decisions
- On-device models: Qwen2.5-VL 7B (document extraction), Qwen2.5 7B (text queries), small embedding model for search
- Database: SQLite + SQLCipher (encrypted)
- Runtimes: MLX on Apple Silicon, llama.cpp on Windows/Linux
- Non-transactional content (bank policies, fee schedules — zero personal data) can be processed via cloud LLM for higher accuracy, extracted once, cached, and pushed to all users as app updates
- Living test suite: We maintain real bank/credit card accounts at major institutions to receive monthly statements as perpetual test data

## Privacy Split for Cloud Processing
- Transaction data (personal) → always local, never leaves device
- Policy/fee/rewards pages (identical for all customers, zero PII) → can use cloud LLM for better extraction accuracy
- Extracted policies get packaged into app updates for all users

## Competitive Moats
1. Format library — every bank format cracked, every edge case handled
2. User-trained categorization — personalization that compounds over time
3. Expansion pack ecosystem — increasing switching cost
4. Community trust — privacy-focused products inspire loyal advocacy
5. The name Jar/家 — dual-meaning brand across languages

## Global Strategy
No dependency on Plaid or banking APIs. Works everywhere because every country has documents. Language expansions open new markets at near-zero cost. Killer acquisition moment: tax season every January.

## Current Status
Pre-development. Starting with one Bank of America checking account PDF statement to build and perfect the extraction engine before anything else. The philosophy: extract every piece of data from every page with zero data left behind. Build ground truth by hand, then iterate the pipeline until it matches perfectly.

## Development Roadmap (6 months)
- Month 1-3: Layer 1 — Extraction engine (start with BofA, expand)
- Month 3-4: Layer 2 — APIs
- Month 4-5: Layer 3 — LLM interface
- Month 6: Polish, test, ship

## Tech Stack
- Docling (IBM, open source) for PDF parsing and layout understanding
- PaddleOCR for photos and scanned documents
- Qwen2.5-VL 7B for semantic document extraction
- SQLite + SQLCipher for encrypted local storage
- SwiftUI for iOS/macOS app
- MLX for Apple Silicon inference
- Claude Code for development acceleration