# 8. Email & messages — on-device semantic search

**Job:** let you ask about things that aren't in your bank statements — "what did
the contractor quote me?", "did the insurance company set a deadline?" — by
searching your email and text messages. All searching happens **on your machine**.

**Files:**
- `core/email/` — IMAP email pipeline (`pipeline.py`, `imap_client.py`, …)
- `core/messages/` — macOS iMessage/SMS pipeline (`pipeline.py`, `chat_db.py`, `contacts.py`)
- `core/embeddings.py` — the shared local embedding model
- `core/messages/vector_store.py` / `core/email/vector_store.py` — the search indexes

These two pipelines are deliberately twins — they work the same way and share
the same embedder.

## How indexing works (the same for both)

```
Read your email (IMAP) / texts (~/Library/Messages/chat.db)
        │
        ▼  turn each one into a list of numbers (an "embedding")
           using a local model — text never leaves the machine
Store the numbers in LanceDB (data/lancedb/)
        │
        ▼  now you can search by meaning, not just keywords
```

The embedding model is **BAAI/bge-m3**, downloaded once (~2.3 GB) and then run
fully offline. A question and a stored message that *mean* the same thing end up
with similar numbers, which is what makes semantic search work.

## How search works (during chat)

When Claude calls `search_emails` or `search_messages`
(see [Chat & tools](05-chat-and-tools.md)):

```
your query → embed locally → find the closest few items in LanceDB
           → scrub PII from the excerpts → send only those to Claude
```

Only a handful of short, **redacted** excerpts are ever sent to the cloud — never
your whole inbox. See [Privacy & Redaction](06-privacy-and-redaction.md).

## Messages specifics (macOS)

- Reads the Messages database **read-only and immutable** — it never locks or
  copies your live Messages app data.
- **Requires Full Disk Access** (System Settings → Privacy & Security). Without
  it, the reader raises a clear "grant Full Disk Access" error that the UI
  surfaces. This permission also covers the Contacts database.
- **Contacts resolution:** phone numbers and emails are matched against your
  macOS address book so results say "Mom" instead of "+15551234567". Those
  contact names are pre-registered with the redactor so they tokenize
  consistently.

## Keeping it fresh

- **Scan** (`POST /api/messages/scan`, `/api/email/scan`) — index everything.
- **Sync** (`POST /api/messages/sync`, `/api/email/sync`) — pick up just what's new.
- A **nightly job at 02:00** runs both syncs automatically, using the
  `*_sync_state` tables as bookmarks. (A database fingerprint detects when the
  Messages DB has been replaced or compacted, triggering a fresh scan.)

For the deeper design write-ups, see `doc/email-pipeline-architecture.md` and
`doc/message-pipeline-architecture.md`.
