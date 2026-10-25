# 9. Server & web UI — the app you click on

**Job:** tie everything together behind a local web app you open in your browser.

**Server:** `app/server.py` (FastAPI, port 5050)
**UI files:** `app/static/`

## The server

A single FastAPI app running on `http://127.0.0.1:5050` (port configurable with
`JAR_PORT` — macOS uses 5000 for AirPlay, hence 5050). It does three kinds of
things:

1. **Serves the web pages** (`/dashboard`, `/chat`, `/scan`).
2. **Exposes the API** the pages call (`/api/...`).
3. **Runs background work** — extraction, scanning, and the nightly email/message sync.

### On startup it…

```
1. Makes sure the database schema is up to date (migrations)
2. Warms up the PII detection model so the first redaction is fast
3. Starts the nightly 02:00 sync scheduler (email + messages)
```

### The API, grouped by what it does

| Group | Example endpoints | Purpose |
|-------|-------------------|---------|
| **Status** | `/api/config`, `/api/chat/status` | Is there data? Is the LLM available? |
| **Import** | `POST /api/import` | Upload a PDF, watch extraction stream live |
| **Transactions** | `/api/transactions`, `/api/transactions/{id}/tag` | List, filter, and tag transactions |
| **Categories** | `/api/categories`, `/api/category-rules/...` | View taxonomy, manage your rules |
| **Dashboard** | `/api/dashboard/...`, `/api/export/csv` | Data behind the charts; CSV export |
| **Chat** | `POST /api/chat`, `/api/chat/backend`, `/api/chat/reset` | Ask questions; switch model |
| **Scanner** | `/api/scan/...`, `/api/drive/...` | Find statement PDFs (see doc 07) |
| **Email/Messages** | `/api/email/...`, `/api/messages/...` | Connect and sync (see doc 08) |
| **Findings** | `/api/findings`, `/api/findings/{id}/dismiss` | Insights surfaced during extraction |

### Live progress with SSE

Slow operations (extraction, scanning, syncing) use **Server-Sent Events** — a
one-way stream from server to browser — so you see a real progress bar instead of
a spinner that hangs. The browser opens the stream and the server pushes
"uploading… extracting… categorizing… done" as it happens.

## The web UI

Plain HTML + JavaScript (no framework), organized as small self-contained widgets.

| Page | File | What you do there |
|------|------|-------------------|
| **Dashboard** | `dashboard.html` + `dashboard.js` | Balance cards, spending charts, findings; first-run onboarding |
| **Chat** | `index.html` + `app.js` | Ask questions; also import PDFs and browse transactions |
| **Scan** | `scan.html` + `scan.js` | Find statement PDFs on disk or Drive |

Reusable widgets handle the connect-and-sync flows:
`scanner-widget.js`, `drive-widget.js`, `email-widget.js`, `messages-widget.js`.
Charts are drawn with Chart.js (`lib/chart.umd.min.js`), and `common.js` holds
shared helpers (fetching, formatting).

## How to run it

```bash
cd ~/jar-ai && source venv/bin/activate \
  && set -a && source .env && set +a \
  && python3 app/server.py
# → http://127.0.0.1:5050
```

You need `ANTHROPIC_API_KEY` in `.env` for extraction and chat. See the project
`CLAUDE.md` for full setup, including first-time install (`bash bin/install.sh`).
