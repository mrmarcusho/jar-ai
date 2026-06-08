# Message Pipeline Architecture

## Overview

Jar's message pipeline ingests the user's iMessage/SMS history from the local macOS
Messages database, embeds every message on-device, and exposes semantic search to the
chat engine. Raw message text never leaves the machine; only **redacted** excerpts
touch the cloud, and only at answer time.

```
~/Library/Messages/chat.db (SQLite)
    → read read-only + immutable (no byte copy, never locks Messages)
    → decode attributedBody, convert Apple-epoch dates, skip body-less rows
    → resolve handles → contact names via macOS AddressBook (E.164)
    → embed text locally (bge-m3, 1024-dim — shared with the email pipeline)
    → upsert into LanceDB `messages` table (data/lancedb/)
    → search_messages tool in chat engine queries the store
    → excerpts redacted (cloud_boundary) → Claude → answer detokenized on display
```

---

## Components

| File | Role |
|---|---|
| `core/messages/chat_db.py` | Read-only `chat.db` reader: `attributedBody` decode, Apple-epoch conversion, group flag, fingerprint, `MessagesAccessError` |
| `core/messages/contacts.py` | AddressBook → `handle → contact_name` map (E.164 normalized) |
| `core/messages/vector_store.py` | LanceDB `messages` table — upsert, search, count |
| `core/messages/pipeline.py` | Orchestrator: read → resolve → embed → store; `run_full_scan` |
| `core/messages/sync.py` | Incremental sync via ROWID cursor + fingerprint/guid validation |
| `core/embeddings.py` | Shared bge-m3 embedder (used by both email and message pipelines) |
| `core/privacy/cloud_boundary.py` | Redacts retrieved excerpts before they reach the cloud |
| `app/chat/chat_engine.py` | `search_messages` tool; session `RedactionEngine`; answer detokenize |
| `app/server.py` | `/api/messages/scan` + `/api/messages/sync` endpoints; nightly job |

---

## Data Flow

### Full scan (first run)
1. `pipeline.run_full_scan()` reads messages from `chat.db` (read-only/immutable).
2. Each message's body comes from `text`, or is decoded from `attributedBody` when
   `text` is NULL (modern macOS). Reactions/tapbacks/attachment-only rows (no body)
   are skipped.
3. `message.date` (Apple epoch, ns or legacy seconds) is converted to ISO 8601.
4. Sender handles are resolved to contact names via AddressBook (outgoing = "Me").
5. Bodies are embedded in batches (bge-m3, on-device) and upserted into LanceDB
   keyed by `guid` (dedup is automatic).

### Incremental sync (nightly at 02:00)
1. Load `last_rowid`, `last_guid`, `db_fingerprint` from `message_sync_state`.
2. The cursor is valid only if every field is present, the fingerprint matches, and
   the row at `last_rowid` still has `last_guid`. First run, fingerprint mismatch
   (DB replaced or VACUUMed → ROWIDs renumbered), or a guid-anchor mismatch all force
   a full rescan (safe — `guid` is the dedup key).
3. Otherwise only `ROWID > last_rowid` rows are read, embedded, and upserted.
4. The new cursor + fingerprint are persisted.
5. Runs in the same APScheduler job as the email sync, isolated so one failing never
   blocks the other.

### Chat query
1. Claude calls `search_messages(query="...")`.
2. The query is embedded on-device (same bge-m3 model) and run against LanceDB.
3. Top-5 rows are trimmed to ~600 chars and passed through `cloud_boundary`:
   resolved contact names are pre-seeded into the redaction `TokenStore`, then the
   text + contact-name fields are redacted (`[PERSON_1]`, `[PHONE_NUMBER_2]`, …).
4. Redacted excerpts are sent to the Claude API; Claude answers in tokens.
5. The chat engine detokenizes the final answer before display — the user sees real
   names; the cloud only ever saw tokens.

---

## Storage

| Store | Path | What lives there |
|---|---|---|
| LanceDB | `data/lancedb/` (`messages` table) | vector + `guid`, `text`, `contact_name`, `is_from_me`, `is_group`, `chat_id`, `date` |
| SQLite | `data/jar.db → message_sync_state` | `last_rowid`, `last_guid`, `db_fingerprint`, `last_sync_at` (single row) |

No credentials are stored — `chat.db` and AddressBook are local file reads.

---

## Privacy Model

- **Raw message text never leaves the device.** Embeddings are produced by bge-m3
  running locally, and the full-fidelity text is stored locally for search.
- **Redaction at the cloud boundary, not at ingest.** Only the retrieved excerpts
  sent to the cloud at answer time are redacted (reversibly, via the session
  `RedactionEngine`). Storing redacted tokens would wreck local search.
- **Scope: excerpts only.** The user's typed question is sent to the cloud
  unredacted (an accepted trade-off — the bulk private data is the message history).
- Storing resolved contact names at rest is more PII than the email pipeline's
  domain-only sender, but it never leaves the device except as redacted excerpts.

---

## Permissions

`chat.db` and the AddressBook database are protected by macOS TCC. A single **Full
Disk Access** grant (System Settings → Privacy & Security → Full Disk Access) covers
both. Without it, the reader raises `MessagesAccessError`, which the API surfaces as
a clear "Grant Full Disk Access" message rather than silently returning no messages.

---

## Key Design Decisions

**Why read `chat.db` read-only + immutable instead of copying it?**
A naive byte copy of the live DB can capture a torn write if Messages is mid-write.
Opening with `mode=ro&immutable=1` gives a consistent read without ever taking a
write lock or blocking the Messages app.

**Why a ROWID cursor with a fingerprint + guid anchor?**
ROWIDs are monotonic only within one stable DB. A `VACUUM` renumbers them and a
device migration replaces the file. The fingerprint (`count:min_rowid:max_rowid`
hash) detects both, and the guid anchor confirms the cursor still points at the same
message — otherwise a full rescan runs (deduped by `guid`).

**Why decode `attributedBody` with a heuristic instead of a library?**
Modern macOS stores the body in a serialized `NSAttributedString`. A small
pure-Python heuristic (text follows the `NSString` marker after a length prefix)
avoids a flaky compiled/Foundation dependency and runs in tests on any platform.
