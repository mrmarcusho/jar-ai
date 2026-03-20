# JAR AI — Claude Code Project Guide

## What is JAR?

JAR (家 — "family" in Chinese) is a privacy-first financial intelligence app for
middle-class dual-income families. It extracts structured data from bank statements
and financial documents, stores everything locally, and lets users ask natural language
questions about their finances.

## Architecture (v2 — Cloud LLM with PII Redaction)

JAR uses Claude as the cloud intelligence engine. Privacy is preserved by **redacting
all PII locally** before any data leaves the machine.

```
┌──────────────────────────────────────────────────────────────┐
│  JAR UI  (FastAPI + web frontend)                            │
│                                                              │
│  Phase 1: Startup                                            │
│    └─ Send master prompt to Claude (API catalog + role)      │
│                                                              │
│  Phase 2: Document Ingestion                                 │
│    └─ Scan dir → Redact PII → Upload to Claude → JSON back   │
│    └─ Restore PII → Store in local SQLite                    │
│                                                              │
│  Phase 3: Query                                              │
│    └─ User question → Claude → function-call JSON            │
│    └─ Execute API calls locally → Results back to Claude     │
│    └─ Claude synthesizes → Formatted response to user        │
│                                                              │
│  Local only:                                                 │
│    - SQLite database (all financial data)                     │
│    - PII profile + redaction engine                          │
│    - Layer 2 Financial APIs (deterministic, no AI)           │
│                                                              │
│  Cloud (Claude API):                                         │
│    - Document extraction (receives redacted PDFs only)       │
│    - Query reasoning + API orchestration (tool use)          │
│    - Response synthesis                                      │
└──────────────────────────────────────────────────────────────┘
```

### Master Prompt (two sections)

1. **API Catalog** — Full documentation of every JAR financial API function
   (name, description, parameters, return schema). Teaches Claude what tools
   it has available.

2. **LLM Role** — Instructions defining Claude as a financial document analyst
   and query engine. Responsibilities: parse documents into structured JSON,
   answer financial questions by selecting and sequencing API calls.

### Three Layers

| Layer | Location | Purpose | AI? |
|-------|----------|---------|-----|
| 1 — Extraction | `core/redaction/` | Redact PII → send to Claude → parse → restore → store | Cloud LLM |
| 2 — APIs | `core/api/` | Deterministic SQL queries, math, pattern detection | No |
| 3 — Intelligence | `core/intelligence/` | Master prompt, tool-use orchestration, response synthesis | Cloud LLM |

### The Query Loop (tool use)

```
User question
  → JAR sends to Claude with master prompt + tool definitions
  → Claude returns tool_use blocks (which APIs to call, params, sequence)
  → JAR executes each API locally against SQLite
  → Results sent back to Claude
  → Claude synthesizes a natural language answer
  → JAR displays to user
```

## Project Layout

```
jar-ai/
├── app/
│   ├── server.py              # FastAPI server (all HTTP endpoints)
│   ├── chat/                   # Chat engine (Claude + Ollama backends)
│   ├── scanner/                # Drive scanner for financial documents
│   └── static/                 # Web UI (HTML/CSS/JS)
├── core/
│   ├── api/
│   │   └── financial_api.py   # Layer 2: 9 deterministic financial functions
│   ├── redaction/
│   │   ├── pipeline.py        # Full pipeline: redact → extract → restore
│   │   ├── pii_profile.py     # PII profile loader + token map builder
│   │   ├── redact_pdf.py      # PyMuPDF-based PDF redaction
│   │   ├── extract_claude.py  # Send redacted PDF to Claude API
│   │   └── restore.py         # Replace tokens with original PII
│   ├── intelligence/          # Layer 3: master prompt + orchestration (TO BUILD)
│   ├── categorization/        # Transaction categorization engine
│   ├── database/
│   │   ├── schema.sql         # SQLite schema (accounts, statements, transactions)
│   │   └── load_statements.py # Load structured JSON into SQLite
│   ├── extraction_engine/     # Docling-based extraction (Path A, legacy)
│   └── schema/                # JSON schemas for extraction output
├── data/
│   ├── jar.db                 # SQLite database
│   ├── statements/            # Raw PDF uploads
│   ├── extraction_output/     # Structured JSON outputs
│   └── pii_profile.json       # User's PII for redaction
├── tests/
│   └── test_redaction.py      # PII redaction pipeline tests
└── requirements.txt           # Python dependencies
```

## Key Financial APIs (Layer 2)

Located in `core/api/financial_api.py`:

| Function | Purpose |
|----------|---------|
| `get_transactions()` | Query with filters (date, amount, type, payee) |
| `get_spending_by_category()` | Category breakdown for a date range |
| `get_recurring_bills()` | Payees appearing across multiple statements |
| `get_balance_summary()` | Account balances and cash flow overview |
| `get_spending_comparison()` | Compare spending between two periods |
| `get_balance_history()` | Time-series balance data |
| `get_top_merchants()` | Ranked merchant list by spend |
| `get_monthly_trends()` | Monthly income vs spending |
| `search_transactions()` | Free text search across descriptions |

## Tech Stack

- **Python 3.11+**
- **FastAPI + Uvicorn** — web server
- **Anthropic SDK** — Claude API client
- **PyMuPDF (fitz)** — PDF redaction
- **SQLite** — local database (no encryption yet, SQLCipher planned)
- **Docling** — PDF parsing (Path A / legacy)

## Rules

- **All PII must be redacted before sending to Claude** — the redaction layer is the trust boundary
- **Financial APIs stay deterministic** — no AI in Layer 2, pure SQL + math
- **Amounts stored as floats** (migration to integer cents planned)
- **Dates as ISO 8601** (YYYY-MM-DD) in storage
- **Never commit pii_profile.json** or any file containing real PII
- **Test extraction changes against ground truth JSON**

## Running

```bash
# Start the web server
ANTHROPIC_API_KEY=sk-... python app/server.py
# → http://localhost:5000

# Run redaction pipeline directly
python -m core.redaction.pipeline data/statements/some_statement.pdf

# Run tests
pytest tests/
```

## Team

- **Marcus** — product, AI/ML, pipeline dev (CS student at Tufts)
- **Dad** — financial domain architect, tech lead, code reviewer (30 yrs exp)
- **Contractor** — native app, on-device ML integration
- **Claude Code** — pair programmer
