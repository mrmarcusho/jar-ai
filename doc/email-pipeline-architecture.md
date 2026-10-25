# Email Pipeline Architecture

## Overview

Jar's email pipeline ingests your inbox locally, embeds every message on-device,
and exposes semantic search to the chat engine. Raw email text never leaves your
machine — only the final chat answer touches the cloud.

```
IMAP inbox
    → fetch headers + bodies (imaplib, stdlib)
    → embed subject + body locally (bge-m3, 1024-dim)
    → upsert into LanceDB vector store (data/lancedb/)
    → search_emails tool in chat engine queries the store
    → Claude sees short excerpts, writes a natural-language answer
```

---

## Components

| File | Role |
|---|---|
| `core/email/imap_client.py` | IMAP connection, envelope fetch, body fetch, credential storage |
| `core/email/embedder.py` | Local bge-m3 embeddings via SentenceTransformer |
| `core/email/vector_store.py` | LanceDB table — upsert, search, count |
| `core/email/pipeline.py` | Orchestrator: connect → embed → store; `run_scan` entry point |
| `core/email/sync.py` | Incremental sync cursor logic; `incremental_sync` entry point |
| `app/chat/chat_engine.py` | `search_emails` tool — embeds query, calls `vector_store.search` |
| `app/server.py` | API endpoints + APScheduler nightly job |

---

## Data Flow

### Full scan (first run)
1. `pipeline.run_full_scan()` — fetches all emails since `days_back` (default 3 years)
2. Envelopes fetched first (headers only, cheap): From, Subject, Date, Message-ID
3. Fresh IMAP connection opened for body fetch (Gmail closes idle sockets)
4. Bodies fetched in batches of 50; reconnect hook handles mid-stream drops
5. Each email's `subject + body` clipped to 4000 chars, encoded to 1024-dim vector
6. Rows upserted into LanceDB keyed by `message_id` (dedup is automatic)

### Incremental sync (nightly at 2:00 AM)
1. Load cursor from `email_sync_state`: `last_uid` (preferred) or `last_imap_date` (fallback)
2. If `last_uid` is set: `SEARCH UID {last_uid+1}:*` — fetches only truly new messages
3. Check UIDVALIDITY against stored value; mismatch → force full date-based rescan
4. After scan: persist `last_uid`, `uidvalidity`, `last_imap_date` back to SQLite
5. Scheduler runs via APScheduler `AsyncIOScheduler` started at server startup

### Chat query
1. User asks a question in the chat UI
2. Claude calls `search_emails(query="...")` tool
3. Query is embedded with the same bge-m3 model (on-device)
4. LanceDB cosine nearest-neighbour search returns top-5 emails
5. Each result trimmed to 600 chars of body text before being sent to Claude API
6. Claude synthesises a natural-language answer from the excerpts

---

## Storage

| Store | Path | What lives there |
|---|---|---|
| LanceDB | `data/lancedb/` | Vectors + metadata (sender domain, subject, date, body) |
| SQLite | `data/jar.db` → `email_sync_state` | Sync cursor: `last_uid`, `uidvalidity`, `last_imap_date`, `last_sync_at` |
| Keychain | macOS Keychain (`jar-imap` service) | IMAP password |
| JSON | `data/imap_account.json` | Username, host, port only (no password) |

---

## Privacy Model

- **Raw email text never leaves the device.** Embeddings are produced by bge-m3 running locally.
- Only the **chat answer step** sends data to the cloud: Claude API receives short body excerpts (≤600 chars each, top 5 results) to compose its response.
- The sender field stored in LanceDB is **domain-only** (e.g. `chase.com`) — the display name is dropped before storage.

---

## Key Design Decisions

**Why embed locally instead of sending to a cloud API?**
Email contains highly personal information. Running bge-m3 on-device means no email
content is ever transmitted, with no redaction needed.

**Why IMAP UID cursor instead of date?**
IMAP `SEARCH SINCE` is day-inclusive and day-granular. Using the UID of the last
seen message as the cursor means incremental syncs fetch exactly zero messages
when nothing is new, instead of re-fetching the entire last day.

**Why UIDVALIDITY?**
Each IMAP mailbox has a UIDVALIDITY value. If it changes (e.g. after a mailbox
rebuild), all previously seen UIDs are invalid. Jar detects the mismatch and
falls back to a full date-based rescan automatically.

**Why APScheduler instead of a cron job?**
The scheduler runs inside the FastAPI process, so it starts and stops with the
server and requires no OS-level configuration. Nightly at 2:00 AM local time.
