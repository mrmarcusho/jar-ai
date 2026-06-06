# JAR (家) — Claude Code Project Guide

Family finance app for middle-class dual-income households. Extracts bank statements into structured data, stores locally in SQLite, and answers financial questions via LLM.

家 means "family" in Chinese.

## Architecture (MVP — Cloud LLM)

Two zones: **client (local)** and **cloud**. User data lives locally; intelligence runs in the cloud.

### Extraction flow

```
User uploads PDF
    → raw PDF sent to cloud (no local pre-processing)
    → cloud LLM extracts with master prompt (DB schema + instructions)
    → generates SQL INSERT statements validated against schema
    → SQL sent back to client
    → SQLite executes locally
```

The master prompt gives the Cloud LLM the full DB schema and extraction rules. LLM returns SQL INSERTs — no intermediate JSON. The client validates SQL against the schema before executing.

### Query flow

```
User asks question
    → Cloud LLM function routing (decides which APIs to call)
    → API tools are the ones that we have written already in financial_api.py
    → LLM synthesizes narrative answer from API results
    → answer displayed to user
```

The LLM does NOT understand finance. It only knows which function to call and how to present the results.

## Project layout

```
app/
    server.py               FastAPI server (port 5050, configurable via JAR_PORT)
    chat/
        chat_engine.py      LLM function-calling interface (Claude API)
        run_chat.py         CLI chat for testing
    scanner/                Drive scanner — finds PDF bank statements on disk
    static/                 Web UI (HTML/JS/CSS)
core/
    api/
        financial_api.py    Deterministic financial query functions
    categorization/         Transaction categorization engine + user rules
    database/
        schema.sql          SQLite schema (accounts, statements, transactions)
        load_statements.py  Loads structured JSON into SQLite
bin/
    install.sh              Setup script (venv, deps, DB init, launcher)
    start.sh                Activate venv
data/
    jar.db                  SQLite database (gitignored)
    statements/             Raw PDF uploads
tests/                      pytest test suite
doc/                        Architecture diagrams and design docs
```

## Key APIs

**SQL API** (`core/api/financial_api.py`) — deterministic query functions:
- `get_transactions()` — filtered transaction list
- `get_balance_summary()` — account balances across statements
- `get_spending_by_category()` — category breakdown
- `get_monthly_trends()` — income vs spending by month
- `get_recurring_bills()` — detected recurring charges
- `get_top_merchants()` — ranked merchant spending
- `get_spending_comparison()` — compare two periods
- `search_transactions()` — fuzzy text search
- `get_balance_history()` — time-series balance data

**FX API** — live exchange rates (planned)

**Tax API** — tax brackets and deduction rules (planned)

## Tech stack

- **Python 3.11+**
- **FastAPI + Uvicorn** — web server
- **Anthropic SDK** — Claude API (extraction + chat)
- **SQLite** — local database (no encryption for MVP)

## Rules

- Financial APIs stay deterministic — no LLM calls in `core/api/`
- Dates as ISO 8601 (`2023-01-17`) in storage, human-readable in UI
- Amounts as floats in SQLite (integer cents deferred to native app)
- All SQL queries use parameterized statements — never string interpolation
- Extraction must validate against DB schema before executing INSERT
- Never commit `.env`, `data/jar.db`, or files containing API keys

## Running

```bash
# First-time setup
bash bin/install.sh

# Start the server (port 5050 — macOS uses 5000 for AirPlay Receiver)
cd ~/jar-ai && source venv/bin/activate && set -a && source .env && set +a && python3 app/server.py
# → http://127.0.0.1:5050

# Restart the server (kill existing, then start)
lsof -ti:5050 -s TCP:LISTEN | xargs kill -9 2>/dev/null; cd ~/jar-ai && source venv/bin/activate && set -a && source .env && set +a && python3 app/server.py

# Custom port
JAR_PORT=8080 python3 app/server.py

# Run tests
pytest tests/
```

Requires `ANTHROPIC_API_KEY` in `.env` for chat and extraction.

## Google Drive scanning (optional)

The scan page (`/scan`) can connect a Google Drive and scan the entire Drive or a
chosen folder for bank-statement PDFs. Selected files are downloaded to
`data/statements/` and then run through the same extraction pipeline as local
files (`app/scanner/drive_client.py`, `/api/drive/*` endpoints).

Setup (one-time):
1. In Google Cloud Console, create a project and enable the **Google Drive API**.
2. Create an **OAuth 2.0 Client ID** of type **Web application** with authorized
   redirect URI `http://127.0.0.1:5050/api/drive/callback` (match `JAR_PORT` if
   you change the port).
3. On the OAuth consent screen, keep publishing status **Testing** and add the
   household's Google accounts as **test users** (the `drive.readonly` scope is
   restricted; Testing mode avoids Google verification for up to 100 users).
4. Put `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` in `.env`.

The OAuth token is stored at `data/google_token.json` (gitignored).

## Team

- **Marcus** — product, AI/ML, pipeline dev (CS student at Tufts)
- **Dad** — financial domain architect, tech lead, code reviewer (30 yrs exp)
- **Contractor** — native app, on-device integration
- **Claude Code** — pair programmer (~50% of code output)
