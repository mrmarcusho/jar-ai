# JAR — Architecture Design v2

## Vision

JAR shows you where your money is going and how to keep more of it. Your financial data lives on your device. Nothing leaves without your explicit permission. Every time.

---

## Three-Tier Architecture

JAR supports a range of devices from legacy Intel Macs to modern Apple Silicon machines to iPads and phones. Every user gets the same core value — proactive financial intelligence — regardless of hardware. The tiers differ only in how the chat interface is powered.

### Tier 1 — No LLM (Free, Any Device)

**Target:** Old Intel Macs, any machine with a browser, users who want zero AI involvement.

**What it does:**
- Import bank statement PDFs
- Extract, normalize, and categorize transactions on-device
- Store clean data in local SQLite (no personal identifiers in the database)
- View transactions with sortable/filterable table
- Proactive intelligence dashboard powered by deterministic SQL rules:
  - Idle cash alerts ("$295K in checking earning near zero — a high-yield savings account at 4.5% would earn $13,275/year")
  - Spending spike detection ("Electricity bill 3.7x higher than last month")
  - Subscription creep ("Your recurring bills increased $47/month over 6 months")
  - Bill change tracking ("Mortgage went up $179.52 — likely escrow adjustment")
  - Fee detection ("2 non-network ATM fees this month — $5 avoidable")
  - Cash flow forecasting ("At your current burn rate, you have 24 months of runway")

**Privacy:** 100% local. Nothing ever leaves the device. Period.

**Revenue:** Free. This is the hook that delivers the "holy shit" moment.

### Tier 2 — Local LLM (Free, Apple Silicon 8GB+)

**Target:** Any Mac with Apple Silicon (M1 and up, 8GB+ RAM). Majority of Macs sold since late 2020.

**What it does:**
- Everything in Tier 1
- Natural language chat interface powered by local LLM (Qwen 3.5-4B or best available small model via Ollama)
- Ask questions conversationally: "What's my biggest expense?", "Show me my top 5 recurring bills", "How much did I spend on utilities?"
- Multi-step reasoning for complex queries

**Privacy:** 100% local. Nothing ever leaves the device. Period.

**Revenue:** Free. Differentiates JAR from Tier 1-only competitors.

### Tier 3 — Cloud LLM ($3-5/month Subscription)

**Target:** Any device with a browser — iPads, old laptops, phones. Also Apple Silicon users who want the best reasoning quality.

**What it does:**
- Everything in Tier 1
- Natural language chat powered by hosted LLM (Qwen3-8B+ on JAR's server)
- Superior reasoning, world knowledge, and financial advice
- Proactive financial briefings with actionable recommendations
- Consent-per-query privacy model (see below)

**Privacy:** On-device processing with user-controlled cloud queries. Raw financial data never leaves the device. Only anonymized, user-approved data is sent to JAR's server.

**Revenue:** $3-5/month subscription.

---

## Privacy Architecture

### Core Principle

Raw bank statements and personal identifiers are never stored in the database and never leave the device. The extraction pipeline strips personal details at ingestion time.

### What Gets Stored in SQLite (On-Device)

- Date
- Amount
- Transaction type (ACH, wire, bill payment, check, Zelle, ATM)
- Normalized merchant name ("Mr. Cooper" not "NSM DBAMR.COOPER")
- Category ("Housing/Mortgage", "Utilities/Electric", etc.)
- Direction (deposit/withdrawal)

### What Gets Dropped at Ingestion

- Individual names (INDN: fields)
- Account numbers and IDs
- CO IDs and confirmation numbers
- Wire routing numbers
- Zelle recipient details
- Raw bank description strings (kept only in the original PDF on-device)

### Consent-Per-Query Model (Tier 3 Only)

When a Tier 3 user asks a question that requires cloud LLM reasoning:

**Step 1 — Local execution first.**
JAR executes the relevant API functions locally against the on-device SQLite database. This happens entirely on-device with no network call.

**Step 2 — Show what will be sent.**
Before any data leaves the device, JAR presents the user with an exact preview of the anonymized query payload:

```
To answer your question, I need to send the following to our server:

Query: "How has my mortgage changed this year?"

Data to send:
- Transaction: $4,192.92 (Mortgage, Jan 3, 2023)
- Transaction: $4,192.92 (Mortgage, Feb 3, 2023)
- Transaction: $4,372.44 (Mortgage, Dec 4, 2023)

No names, account numbers, or personal details included.

[Send to Cloud]    [Keep Local Only]
```

**Step 3 — User decides.**
- "Send to Cloud" → anonymized data goes to JAR's server, cloud LLM reasons over it, returns advice
- "Keep Local Only" → JAR falls back to local LLM (if available) or provides a basic deterministic answer

**Step 4 — No retention.**
JAR's server processes the query and returns a response. No user data is stored on the server. No logs of financial data. Query and response are discarded after delivery.

### Privacy Summary by Tier

| | Raw PDFs | SQLite Data | Chat Queries | Personal Identifiers |
|---|---|---|---|---|
| **Tier 1** | On-device only | On-device only | No chat | Never stored |
| **Tier 2** | On-device only | On-device only | On-device only | Never stored |
| **Tier 3** | On-device only | On-device only | Anonymized, user-approved | Never stored, never sent |

---

## Data Flow

### Ingestion (All Tiers — On-Device)

```
Bank Statement PDF
    ↓
Layer 1: Extraction Engine (Docling + validation)
    ↓
Raw transaction data (amounts, dates, descriptions, names, IDs)
    ↓
Normalization Pipeline
    - Merchant normalization: "NSM DBAMR.COOPER" → "Mr. Cooper"
    - Categorization: "Mr. Cooper" → "Housing/Mortgage"
    - Identity stripping: drop INDN names, account IDs, confirmation numbers
    ↓
Clean SQLite Database (on-device)
    - date, amount, type, merchant, category, direction
    - NO personal identifiers
    ↓
Proactive Intelligence Engine (deterministic SQL rules)
    - Runs on import and on schedule
    - Generates alerts and insights
    - No LLM needed
```

### Chat Query — Tier 2 (On-Device)

```
User asks question
    ↓
Local LLM (Qwen 3.5-4B via Ollama)
    ↓
Function call → Layer 2 API → SQLite (all on-device)
    ↓
LLM synthesizes response
    ↓
Response displayed to user

Nothing leaves the device.
```

### Chat Query — Tier 3 (Cloud with Consent)

```
User asks question
    ↓
Layer 2 API executes locally → gets anonymized results
    ↓
JAR shows user exactly what will be sent
    ↓
User approves → anonymized payload sent to JAR cloud server
    ↓
Cloud LLM (Qwen3-8B+) reasons over anonymized data
    - Has world knowledge for financial advice
    - Never sees raw descriptions, names, or account numbers
    ↓
Response returned to device
    ↓
Server discards query and response — zero retention
```

### Proactive Intelligence — Tier 3 (Cloud with Consent)

```
On schedule, device-side agent runs detection rules locally:
    - Spending spikes, bill changes, idle cash, fee patterns
    ↓
Packages anonymized financial snapshot:
    - "Monthly housing: $4,859, Utilities: $302, Subscriptions: $154"
    - "Checking balance: $295K, Savings: $0"
    - "3 ATM fees this month: $7.50 total"
    ↓
User reviews and approves snapshot
    ↓
Cloud LLM analyzes with world knowledge:
    - "Move $250K to a HYSA at 4.5% = $11,250/year"
    - "Switch to in-network ATMs to save $90/year"
    - "Your Verizon plan at $79/month is above market — compare to alternatives"
    ↓
Personalized briefing returned to device
```

---

## Technical Stack

### On-Device

| Component | Technology |
|---|---|
| PDF extraction | Docling + validation pipeline |
| Database | SQLite (no personal identifiers) |
| API layer | Python/FastAPI with 6+ financial query functions |
| Local LLM | Ollama + Qwen 3.5-4B (or best available) |
| Proactive engine | Deterministic SQL rules + scheduled jobs |
| Web UI | FastAPI + HTML/JS (PWA for mobile) |
| Installation | Bash install script with resume-based retry |

### Cloud (Tier 3 Only)

| Component | Technology |
|---|---|
| LLM hosting | GPU server running Qwen3-8B+ |
| API | Receives anonymized queries, returns responses |
| Data retention | Zero — query and response discarded after delivery |
| Authentication | Subscription-based user accounts |

### Supported Devices

| Device | Tier 1 | Tier 2 | Tier 3 |
|---|---|---|---|
| Intel Mac (any RAM) | ✅ | ❌ | ✅ |
| Apple Silicon Mac (8GB+) | ✅ | ✅ | ✅ |
| iPad | ✅ (via browser) | ❌ (future: on-device inference) | ✅ |
| iPhone | ✅ (via PWA) | ❌ (future: on-device inference) | ✅ |
| Windows/Linux | ✅ (future) | ✅ (Ollama supports these) | ✅ |

---

## Roadmap Priority

### Phase 1 — Hardening (Current)
- Extraction accuracy to 99%+ across 12-24 months of BofA statements
- Second bank format (Chase) to prove generalization
- Merchant normalization + categorization pipeline
- Identity resolution (household members)
- Fix remaining Layer 2 bugs (recurring bills filter)

### Phase 2 — Proactive Intelligence (Tier 1 MVP)
- Idle cash detection and savings calculator
- Spending spike alerts
- Bill change tracking
- Fee detection and avoidance
- Subscription creep monitoring
- Cash flow forecasting
- Dashboard UI for insights

### Phase 3 — Mobile + Cloud (Tier 3 MVP)
- PWA for mobile access
- Cloud LLM deployment
- Consent-per-query UI
- Subscription billing
- Zero-retention server architecture

### Phase 4 — Growth
- Additional bank formats
- Tauri wrapper for native app distribution
- On-device inference for iPad/iPhone (as models and hardware improve)
- Windows/Linux support

---

## The Story

**One story. One sentence.**

*"JAR shows you where your money is going and how to keep more of it — and your financial data never leaves your device without your permission."*

This is true for every tier:
- Tier 1: Data never leaves. Period.
- Tier 2: Data never leaves. Period.
- Tier 3: Only anonymized data you've explicitly approved. Every time.

No other personal finance app makes this promise. That's the moat.
