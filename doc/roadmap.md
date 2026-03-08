# JAR — Product Roadmap

> JAR shows you where your money is going and how to keep more of it — and your financial data never leaves your device without your permission.

---

## Phase I — Proactive Intelligence, No LLM (Free, Any Device)

**The Promise:** Install JAR. It scans your drives, finds your bank statements, and shows you exactly where your money is going and how to keep more of it. No AI. No cloud. No manual upload. Just answers.

### 💥 Holy Shit Moment

User installs JAR. Without uploading a single file, JAR scans accessible drives, finds 18 months of bank statements buried in Downloads, Documents, and email attachment folders — and within seconds shows them:

> *"I found 14 bank statements across your drives (Jan 2024 – Feb 2026). Here's your financial picture:"*
>
> *"You spent $87,342 last year. Your top 3 categories: Housing ($52K), Utilities ($4.8K), Groceries ($6.2K)."*
>
> *"Your balance grew 12% from January to December. Here's the chart."*
>
> *"Your top merchant is Mr. Cooper at $50,315/year. Your 5 recurring bills total $5,847/month."*

No signup. No AI. No data leaving their device. They didn't even have to find the files. JAR found them, built a complete financial picture across 18 months, and put real numbers in front of them before they finished their coffee.

### Hardening — Extraction Pipeline (Top Priority)

| Task | Status | Notes |
|---|---|---|
| 100% extraction accuracy on BofA statements | ✅ Done | Validated against ground truth |
| Build statement collection library | 🔲 To Do | Collect as many real statements as possible — every bank, credit union, brokerage, credit card |
| Bank format catalog | 🔲 To Do | Document layout patterns, field positions, and quirks for each institution |
| Universal extraction engine | 🔲 To Do | Generalize parser to handle diverse statement formats, not one-off per bank |
| Format detection + auto-routing | 🔲 To Do | Identify institution from PDF layout/content, route to correct parsing logic |
| Edge case handling | 🔲 To Do | Multi-page statements, mid-month closings, foreign transactions, joint accounts |
| Merchant normalization pipeline | 🔲 To Do | "NSM DBAMR.COOPER" → "Mr. Cooper" across all banks |
| Transaction categorization pipeline | 🔲 To Do | "Mr. Cooper" → "Housing/Mortgage" |
| Identity resolution (household members) | 🔲 To Do | Distinguish transactions by household member |
| Identity stripping at ingestion | 🔲 To Do | Drop names, account IDs, routing numbers, Zelle details |
| SQLite schema finalization | 🔲 To Do | date, amount, type, merchant, category, direction — no PII |
| Accuracy regression suite | 🔲 To Do | Automated tests against ground truth for every supported format |

### Drive Scanning + Auto-Discovery

| Task | Status | Notes |
|---|---|---|
| File system scanner for PDF discovery | 🔲 To Do | Crawl Downloads, Documents, Desktop, email attachment folders |
| Bank statement identification | 🔲 To Do | Distinguish bank statements from other PDFs (heuristics on layout, keywords) |
| Multi-bank detection | 🔲 To Do | Identify which bank each statement belongs to, route to correct parser |
| Deduplication | 🔲 To Do | Handle duplicate statements across folders |
| Scan progress UX | 🔲 To Do | "Scanning... found 14 statements across 3 folders" |
| Permission handling | 🔲 To Do | Respect OS-level folder permissions, prompt for access when needed |
| Manual import fallback | 🔲 To Do | Drag-and-drop for statements scanner can't reach |

### Financial Overview + Core Features

| Task | Status | Notes |
|---|---|---|
| Expense breakdown by category | 🔲 To Do | Pie/bar chart: Housing, Utilities, Groceries, Dining, Transport, etc. |
| Income vs. expenses summary | 🔲 To Do | Monthly and yearly net cash flow at a glance |
| Year-end balance | 🔲 To Do | Closing balance for each calendar year |
| Mid-year balance | 🔲 To Do | Balance snapshot at June 30 for mid-year check-in |
| Month-over-month balance chart | 🔲 To Do | Line chart showing balance trajectory over time |
| Net worth growth visualization | 🔲 To Do | Chart balance growth across all available statement history |
| Top merchants by spend | 🔲 To Do | Ranked list of where the most money is going |
| Recurring bills summary | 🔲 To Do | Detect and list recurring charges with monthly totals |
| Monthly spending trends | 🔲 To Do | Compare spending by category across months — spot seasonal patterns |
| Deposit / withdrawal breakdown | 🔲 To Do | Total inflows vs. outflows per month with trend line |
| Largest transactions | 🔲 To Do | Flag unusually large one-time charges or deposits |
| Average daily spend | 🔲 To Do | Simple daily burn rate calculated from transaction history |
| Export to CSV | 🔲 To Do | Let users take their clean data into Excel or Google Sheets |

### Dashboard UI

| Task | Status | Notes |
|---|---|---|
| Scan results summary | 🔲 To Do | "Found 14 statements in 3 folders spanning Jan 2024 – Feb 2026" |
| Sortable/filterable transaction table | 🔲 To Do | Core data view across all discovered statements |
| Proactive insights cards | 🔲 To Do | Each insight with dollar amount + recommended action |
| PDF import flow | 🔲 To Do | Drag-and-drop fallback for manual imports |
| Dashboard design + UX | 🔲 To Do | Clean, trust-building, privacy-first visual language |

**Exit Criteria:** A user installs JAR, it automatically finds their bank statements, and within seconds they see a complete financial picture — category breakdown, balance growth, top merchants, recurring bills, and trends across their full history. Works on any device with a browser. Zero AI. Zero cloud. Zero PII stored. Zero manual file hunting.

---

## Phase II — Local LLM Chat ($50–200 One-Time License, Apple Silicon)

**The Promise:** Talk to your financial data like you'd talk to a financial advisor — except this one lives on your device, never phones home, and never forgets a transaction.

### 💥 Holy Shit Moment

User asks a question they'd normally need a spreadsheet, an accountant, or three hours to answer:

> *User: "My mortgage went up this year — what happened and how much is it costing me?"*
>
> *JAR: "Your mortgage payment increased from $4,192.92 to $4,372.44 starting in December — that's $179.52/month more, or $2,154.24/year. This is consistent with an escrow adjustment. Your property taxes or homeowner's insurance likely went up. Check your escrow analysis statement from your lender to confirm."*

A conversational answer with exact numbers, context, and next steps — entirely on-device. Nothing left the machine.

### Local LLM Integration

| Task | Status | Notes |
|---|---|---|
| Local LLM integration (Qwen 3.5-4B via Ollama) | ✅ Done | Functional on Apple Silicon 8GB+ |
| Function routing optimization | 🔲 To Do | Improve accuracy of query → API function mapping |
| Structured prompt templates | 🔲 To Do | Procedural instructions for smaller models |
| Multi-step query handling | 🔲 To Do | Complex queries requiring chained API calls |
| Fix Layer 2 bugs (recurring bills filter) | 🔲 To Do | Known issue with recurring detection |
| Conversational financial reasoning | 🔲 To Do | Synthesize data into advice, not just numbers |

### Licensing + Packaging

| Task | Status | Notes |
|---|---|---|
| License tier definitions | 🔲 To Do | Define what $50 / $100 / $200 unlocks |
| License key / activation system | 🔲 To Do | One-time purchase, no subscription infra needed |
| Feature gating by tier | 🔲 To Do | Gate advanced reasoning and proactive features by license |
| Install script redesign | 🔲 To Do | Async UX with progress tracking (progress/pause/resume/complete) |

**Exit Criteria:** A user asks a natural language question about their finances and gets a specific, accurate, contextual answer — with dollar amounts and actionable advice — entirely on their device. Licensing tiers are defined and enforceable.

---

## Phase III — Cloud Intelligence ($3–5/month Subscription, Any Device)

**The Promise:** World-class financial reasoning powered by cloud AI — but you see every byte before it leaves your device, and nothing is ever stored on our servers.

### 💥 Holy Shit Moment

User gets a proactive briefing they never asked for:

> *"Monthly Financial Briefing — March 2026"*
>
> *"Your Verizon plan at $79/month is $15 above the current market rate for comparable plans. Switching could save you $180/year."*
>
> *"You've paid $22.50 in non-network ATM fees over the past 3 months. Your bank has 4 in-network ATMs within 2 miles of your most-used locations."*
>
> *"Your checking account balance has averaged $295K this quarter. Moving $250K to a high-yield savings account at the current best rate of 4.65% would generate $11,625/year — that's $968/month of passive income."*

Personalized, actionable, with real numbers — and they approved every data point before it left their device.

### Cloud LLM + Privacy Architecture

| Task | Status | Notes |
|---|---|---|
| Cloud LLM deployment (Qwen3-8B+ on GPU server) | 🔲 To Do | JAR-hosted for full data retention control |
| Consent-per-query UI | 🔲 To Do | Show exact anonymized payload → user approves or stays local |
| Zero-retention server architecture | 🔲 To Do | Query + response discarded after delivery, no logs |
| Proactive financial briefings | 🔲 To Do | Scheduled anonymized snapshots → cloud reasoning → briefing |
| World knowledge integration | 🔲 To Do | Current HYSA rates, plan comparisons, market context |

### Platform + Distribution

| Task | Status | Notes |
|---|---|---|
| PWA for mobile access | 🔲 To Do | iPad + iPhone via browser |
| Subscription billing ($3–5/month) | 🔲 To Do | User accounts + payment processing |
| Additional bank formats | 🔲 To Do | Beyond BofA + Chase |
| Tauri wrapper for native app | 🔲 To Do | macOS, Windows, Linux distribution |
| Windows / Linux support | 🔲 To Do | Validate full pipeline on non-Mac platforms |
| On-device inference for mobile | 🔲 Future | Dependent on model + hardware improvements |

**Exit Criteria:** A user on any device gets proactive, personalized financial briefings with world knowledge context. They see and approve every byte before it leaves. Server retains nothing. Subscription model is live and sustainable.

---

## Architecture Reference

```
Phase I (Free, Any Device)       Phase II ($50-200, Apple Silicon)  Phase III ($3-5/mo, Any Device)
┌─────────────────────┐          ┌─────────────────────┐            ┌─────────────────────┐
│  Drive Scanner      │          │  Drive Scanner      │            │  Drive Scanner      │
│  ↓                  │          │  ↓                  │            │  ↓                  │
│  Auto-Discovery     │          │  Auto-Discovery     │            │  Auto-Discovery     │
│  ↓                  │          │  ↓                  │            │  ↓                  │
│  Extraction Engine  │          │  Extraction Engine  │            │  Extraction Engine  │
│  ↓                  │          │  ↓                  │            │  ↓                  │
│  SQLite (no PII)    │          │  SQLite (no PII)    │            │  SQLite (no PII)    │
│  ↓                  │          │  ↓                  │            │  ↓                  │
│  Financial Overview  │          │  Financial Overview  │            │  Financial Overview  │
│  + Charts           │          │  + Charts           │            │  + Charts           │
│  ↓                  │          │  ↓                  │            │  ↓                  │
│  Dashboard UI       │          │  Local LLM Chat     │            │  Consent-Per-Query  │
│                     │          │  (Qwen 3.5-4B)      │            │  ↓                  │
│  💥 "Found 14       │          │                     │            │  Cloud LLM          │
│  statements — you   │          │  💥 "Your mortgage   │            │  (Qwen3-8B+)        │
│  spent $87K last    │          │  went up $179.52    │            │                     │
│  year, here's the   │          │  — here's why"      │            │  💥 Proactive        │
│  breakdown"         │          │                     │            │  briefings you       │
│                     │          │                     │            │  briefings you       │
│  100% LOCAL         │          │  100% LOCAL         │            │  never asked for     │
│                     │          │                     │            │                     │
│                     │          │                     │            │  Zero Retention      │
└─────────────────────┘          └─────────────────────┘            └─────────────────────┘
```

---

## Revenue Model

| Phase | Pricing | Target |
|---|---|---|
| **Phase I** | Free | Everyone — the hook |
| **Phase II** | $50–200 one-time license | Apple Silicon Mac users who want conversational access |
| **Phase III** | $3–5/month subscription | Any device, premium intelligence + proactive briefings |

---

## Privacy Promise

Every phase. Every query. Every time.

- **Phase I** — Data never leaves your device. Period.
- **Phase II** — Data never leaves your device. Period.
- **Phase III** — Only anonymized data you've explicitly approved. Zero server retention.

No other personal finance app makes this promise.