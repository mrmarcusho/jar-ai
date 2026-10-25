# JAR (家) — Architecture Overview

JAR is a **family finance app**. You point it at your bank statement PDFs (and
optionally your email and text messages), and it lets you ask plain-English
questions about your money. Your data lives **on your own machine**; the cloud
is used only as the "brain" that reads statements and writes answers — and only
after personal details have been scrubbed out.

These docs explain each part in plain language. Start here, then dive into
whichever part you care about.

## The big idea: two zones

```
        YOUR COMPUTER (local)                      THE CLOUD
  ┌────────────────────────────────┐      ┌────────────────────────┐
  │  • Your PDFs, emails, messages  │      │  Claude (the "brain")   │
  │  • SQLite database (jar.db)     │ ───► │  • reads statements     │
  │  • Local search index (LanceDB) │      │  • decides which        │
  │  • Web UI you click around in   │ ◄─── │    function to call     │
  │  • PII redaction before sending │      │  • writes the answer    │
  └────────────────────────────────┘      └────────────────────────┘
        Everything you own stays here        Sees only scrubbed text
```

- **Local** holds all your real data and does all the searching.
- **Cloud** is smart but never sees your real name, address, or account numbers
  — those are swapped for placeholder tokens before anything is sent, and
  swapped back after the answer comes home. (See [Privacy & Redaction](06-privacy-and-redaction.md).)

## The two things JAR does

**1. Get your data in** — turn a PDF into clean rows in a database:

```
PDF  →  scrub PII  →  Claude reads it  →  Claude writes SQL  →  check it  →  save to SQLite
```

See [Extraction](01-extraction.md).

**2. Answer your questions** — turn a question into a grounded answer:

```
"How much did I spend on groceries?"
   →  Claude picks a function (e.g. get_spending_by_category)
   →  JAR runs the real query on your local database
   →  Claude turns the numbers into a sentence
```

See [Chat & LLM tools](05-chat-and-tools.md). The key rule: **Claude never
invents numbers** — it only decides *which* deterministic function to run and
*how to phrase* the result.

## Map of the parts

| # | Part | What it does | Doc |
|---|------|--------------|-----|
| 1 | **Extraction** | PDF → SQL → database | [01-extraction.md](01-extraction.md) |
| 2 | **Database** | Where your data is stored | [02-database.md](02-database.md) |
| 3 | **Financial API** | The deterministic money queries | [03-financial-api.md](03-financial-api.md) |
| 4 | **Categorization** | Labeling transactions + learning your rules | [04-categorization.md](04-categorization.md) |
| 5 | **Chat & tools** | How questions become answers | [05-chat-and-tools.md](05-chat-and-tools.md) |
| 6 | **Privacy & redaction** | Scrubbing PII at the cloud boundary | [06-privacy-and-redaction.md](06-privacy-and-redaction.md) |
| 7 | **Scanner & Google Drive** | Finding statement PDFs automatically | [07-scanner-and-drive.md](07-scanner-and-drive.md) |
| 8 | **Email & messages** | On-device search of email and iMessage/SMS | [08-email-and-messages.md](08-email-and-messages.md) |
| 9 | **Server & web UI** | The app you actually click on | [09-server-and-ui.md](09-server-and-ui.md) |

## Where things live on disk

| Path | What |
|------|------|
| `data/jar.db` | The SQLite database (your transactions, rules, profile) |
| `data/lancedb/` | On-device search index for emails and messages |
| `data/statements/` | The raw PDFs you've imported |
| `data/extraction_log.md` | A running log of every extraction |
| `.env` | Your `ANTHROPIC_API_KEY` and (optional) Google keys |

## Tech stack at a glance

- **Python 3.11+** with **FastAPI + Uvicorn** for the web server (port 5050)
- **SQLite** for structured financial data
- **LanceDB** + a local embedding model (BAAI/bge-m3) for email/message search
- **Anthropic Claude** for extraction and chat (the only cloud dependency)
- **Presidio + spaCy** for PII detection before anything leaves the machine
