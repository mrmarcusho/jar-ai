# Text-Message Pipeline — Design Spec

**Date:** 2026-06-08
**Status:** Approved for planning
**Author:** Marcus + Claude Code

## 1. Goal

Let the user ask natural-language questions answered from their **text messages**
(iMessage + SMS) — e.g. *"When's my dentist appointment?"*, *"Did I confirm that
furniture order?"*. Texts frequently carry appointments, order confirmations, and
other practical details that never appear in bank statements or email.

The pipeline **mirrors the existing email pipeline** (`core/email/`) as closely as
possible: ingest locally, embed on-device, store in LanceDB, expose a semantic
search tool to the chat engine. The only data that ever leaves the machine is a
small set of **redacted** excerpts sent to the cloud LLM at answer time.

## 2. Scope

### In scope
- Read iMessage + forwarded SMS from the local macOS Messages database.
- Resolve sender phone numbers / Apple IDs to **contact names** via macOS Contacts.
- Embed messages on-device and store them in LanceDB for semantic search.
- A `search_messages` chat tool, parallel to `search_emails`.
- Incremental nightly sync.
- **Redact retrieved excerpts before they are sent to the cloud**, and retrofit the
  same redaction into the existing `search_emails` path.

### Out of scope (explicit non-goals)
- Structured extraction of appointments/orders into typed records (search only —
  no new appointment/order schema). Possible phase 2.
- Redacting the **user's typed question** (only retrieved excerpts are redacted —
  see §7).
- Non-macOS sources (Android, direct iPhone access). iOS sandboxing makes the Mac
  `chat.db` the only viable source; this is the foundation regardless of the
  contractor's future native app.
- Attachments / media content (filenames may be noted later; binary content is not
  ingested).

## 3. Source & Access

### 3.1 Messages database
- Path: `~/Library/Messages/chat.db` (SQLite).
- **Read-only via a temp copy.** Copy `chat.db` (and its `-wal`/`-shm` sidecars if
  present) to a temp file and read from the copy. Never open the live DB — avoids
  locking the user's Messages app.
- Core query joins `message` ↔ `chat_message_join` ↔ `chat` and `message.handle_id`
  ↔ `handle.ROWID` to get text, timestamp, direction, and the other party's handle.

### 3.2 Two decoding quirks (must handle)
- **`attributedBody`**: on modern macOS `message.text` is often NULL and the body
  lives in `message.attributedBody`, a serialized `NSAttributedString` (typed-stream
  / binary blob). The reader must decode it to recover the message text. Falling
  back to `text` only would silently drop most recent messages.
- **Apple epoch**: `message.date` is nanoseconds since `2001-01-01 00:00:00 UTC`
  (older rows may be seconds). Convert to ISO 8601 before storage.

### 3.3 Contact resolution
- macOS AddressBook DB: `~/Library/Application Support/AddressBook/Sources/*/AddressBook-v22.abcddb`.
- Build a `handle → contact_name` map. Normalize phone numbers to **E.164** on both
  sides (handles and AddressBook numbers) so matching is reliable.
- Unresolved handles fall back to the raw handle string (phone/Apple ID).

### 3.4 Permission (TCC)
- Both `chat.db` and the AddressBook DB are protected. A **single Full Disk Access**
  grant (System Settings → Privacy & Security → Full Disk Access) covers both.
- The reader must **fail loudly**: on `PermissionError` / "operation not permitted",
  return a clear, actionable error ("grant Full Disk Access to …") — never return an
  empty result that looks like "no messages".

## 4. Components (`core/messages/`, mirroring `core/email/`)

| File | Role |
|---|---|
| `core/messages/chat_db.py` | Copy + read `chat.db`; decode `attributedBody`; convert Apple-epoch dates; yield message dicts (`guid`, `text`, `handle`, `date_iso`, `is_from_me`, `chat_id`, `is_group`). |
| `core/messages/contacts.py` | Read AddressBook; build `handle → contact_name` map (E.164 normalized). |
| `core/messages/vector_store.py` | LanceDB **`messages`** table — `upsert`/`search`/`count`, keyed by `guid`. Mirrors `core/email/vector_store.py`. |
| `core/messages/pipeline.py` | `run_full_scan(days_back=…)`: read → resolve contacts → embed → upsert. Owns `DB_PATH`, progress callbacks. |
| `core/messages/sync.py` | `incremental_sync()` via a **ROWID cursor**; reads/writes `message_sync_state`. |

### 4.1 Shared modules (lifted out so both pipelines use them)
| File | Role |
|---|---|
| `core/embeddings.py` | The bge-m3 embedder, **lifted from `core/email/embedder.py`** (`get_model`, `embed_texts`, `EMBED_DIM=1024`). `core/email/embedder.py` becomes a thin re-export to avoid churn; `core/messages` imports the shared module. Both pipelines share one model load and one vector space. |
| `core/privacy/cloud_boundary.py` | The redact-at-answer-boundary helper used by **both** `search_messages` and `search_emails` (see §7). |

## 5. Storage

| Store | Path / Table | Contents |
|---|---|---|
| LanceDB | `data/lancedb/` → **`messages`** table | `guid` (dedup key), `text`, `contact_name`, `is_from_me` (direction), `is_group`, `chat_id`, `date` (ISO 8601), `vector` (1024-dim) |
| SQLite | `data/jar.db` → **`message_sync_state`** | `last_rowid` (cursor), `db_fingerprint`, `last_sync_at` |

### 5.1 `message_sync_state` schema
```sql
CREATE TABLE IF NOT EXISTS message_sync_state (
    id              INTEGER PRIMARY KEY CHECK (id = 1), -- single-row table
    last_rowid      INTEGER,  -- highest message.ROWID ingested; the sync cursor
    db_fingerprint  TEXT,     -- detects chat.db replacement (device migration)
    last_sync_at    TEXT
);
```
Added via `core/database/schema.sql` (`CREATE TABLE IF NOT EXISTS`) and, if needed
for existing DBs, a guarded `ALTER`/create in `core/database/migrate.py`.

## 6. Data Flow

### 6.1 Full scan (first run)
1. `pipeline.run_full_scan(days_back=1095)` — default **3 years** (matches email).
2. Copy `chat.db` → read all messages since the window; resolve handles → contact
   names; decode `attributedBody`; convert dates.
3. Embed each message's `text` on-device (bge-m3, ≤512 seq / ≤4000 chars).
4. Upsert into LanceDB `messages`, keyed by `guid` (re-scan updates, never dupes).
5. Record the highest `ROWID` seen and a `db_fingerprint` in `message_sync_state`.

### 6.2 Incremental sync (nightly, 02:00)
1. Load `last_rowid` + `db_fingerprint` from `message_sync_state`.
2. If the fingerprint changed (new `chat.db` after device migration), force a full
   date-based rescan; otherwise query `WHERE message.ROWID > last_rowid`.
3. Embed + upsert only the new rows; persist the new `last_rowid` + fingerprint.
4. Extends the **existing APScheduler nightly job** in `app/server.py` (which already
   runs email `incremental_sync` at 02:00) to also run message sync.

### 6.3 Query (chat)
1. User asks a question; Claude calls `search_messages(query=…)`.
2. The query is embedded **locally** (same bge-m3) and run against LanceDB; top-5
   rows returned with raw text and real contact names (all on-device).
3. The tool passes the rows through `cloud_boundary` (see §7) → **redacted excerpts**.
4. Redacted excerpts become the tool result and are sent to the cloud LLM.
5. Claude composes an answer containing tokens (`[PERSON_1]`, `[PHONE_NUMBER_2]`).
6. The chat engine **detokenizes** the final answer before display → user sees real
   names; the cloud never did.

## 7. Privacy & Redaction

### 7.1 Model
- Raw message text and embeddings are produced and stored **entirely on-device**.
- The **only** outbound data is the redacted top-5 excerpts (≤600 chars each) at
  answer time — mirroring email's excerpt limits.
- Storing resolved **contact names at rest** in LanceDB is more PII at rest than
  email's domain-only choice. Acceptable because it never leaves the device except
  as redacted excerpts; noted here for honesty.

### 7.2 Redact at the boundary, not at ingest
Store **raw** text in LanceDB (it never leaves the device anyway). Redact **only the
excerpts about to be sent to the cloud**, at query time. Redacting at ingest would
embed `[PERSON_1]` instead of "dentist" and wreck local search. This matches how the
extraction engine already redacts immediately before the cloud call.

### 7.3 Shared helper: `core/privacy/cloud_boundary.py`
A pipeline-agnostic step wired into **both** `search_messages` and `search_emails`:
- Input: a `RedactionEngine`, the retrieved rows, the names of the text fields to
  scrub (`body`/`text`), and the known **seed names** in those rows (contact names
  for messages; sender display info for email if available).
- Behavior: **pre-seed** the engine's `TokenStore` with the known contact names
  (deterministic, high-confidence tokenization — we know who these messages are
  from, so we don't gamble on NER), then `redact()` the configured text fields.
- Output: rows with redacted text fields, ready to serialize as the tool result.

### 7.4 RedactionEngine changes
- **Add `lowercase_for_ner: bool = True`** to `RedactionEngine.__init__`. Default
  preserves today's bank-statement behavior (ALL-CAPS → lowercase for spaCy). Pass
  `False` for chat excerpts so Presidio/spaCy keep the capitalization cues that
  conversational text provides (lowercasing would make NER *miss* names).
- **Add a public method to register a known name** (e.g.
  `register_person(name: str)`), used by `cloud_boundary` to pre-seed contact names
  via the existing `TokenStore.get_or_create("PERSON", name)` path.
- **Warm Presidio at server startup** (call the shared loader once on boot) so the
  first query doesn't pay a ~1–2s model-load stall.

### 7.5 Engine lifetime (per chat session)
- The `RedactionEngine` lives on the **`JarChat` instance** (created in `__init__`
  from the household profile), **not per turn**. Tool results are stored in
  `conversation_history` and replayed every turn; a session-scoped store keeps
  tokens consistent so any token Claude echoes from an earlier turn can still be
  detokenized.
- `JarChat.chat()` detokenizes `final_text` via the engine before returning.

### 7.6 Scope decision (explicit)
- **Excerpts only.** The user's typed question is sent to the cloud **unredacted**.
  Redaction covers the retrieved message/email *content* (the bulk private data —
  years of texts), which is the high-value target. Redacting the user's own question
  was considered and deferred (would require redacting every user turn; the user
  accepted this trade-off).
- **Honest limitation:** automatic redaction is defense-in-depth, not a guarantee.
  Free-text messages are messier than bank statements; a name NER misses could slip
  through. Contact-name pre-seeding is what makes it reliable in practice.

## 8. Chat-engine integration (`app/chat/chat_engine.py`)

- Add a `search_messages` tool definition parallel to `search_emails` (query +
  capped limit). Update the system prompt so the model knows to use it for
  text-message questions.
- Add `JarChat._search_messages(query, limit)` mirroring `_search_emails`: embed
  query locally → `vector_store.search` → pass rows through `cloud_boundary` →
  return `{count, messages:[{contact_name, date, direction, text}]}`.
- Route it in `_execute_tool`.
- **Retrofit `_search_emails`** to also pass its rows through `cloud_boundary`
  (closes the existing unredacted-email gap).
- Construct the session `RedactionEngine` in `__init__` (load `HouseholdProfile`
  from the DB) and detokenize `final_text` in `chat()`.
- Reuse the existing `MAX_*_RESULTS=5` / `MAX_*_BODY_CHARS=600` caps for messages.

## 9. Server / API (`app/server.py`)

- Endpoints parallel to the email ones: a full-scan trigger and an incremental-sync
  trigger for messages (run in an executor, like email).
- Extend the nightly `_nightly_sync` job to run message `incremental_sync` after the
  email sync.
- Warm Presidio on startup (§7.4).

## 10. Error Handling

- **No Full Disk Access** → explicit, actionable error surfaced to the UI; not an
  empty result.
- **`attributedBody` decode failure** for a single message → skip that message, log,
  continue (don't fail the whole scan).
- **Presidio unavailable** → redaction falls back to Pass 1 + Pass 3 (profile +
  deterministic sweep), as today; log a warning. (Note: with Presidio down, NER-only
  names rely on contact-name pre-seeding.)
- **`chat.db` missing** (Messages never used) → clear "no Messages database found".
- **Empty query** → `{"error": "empty query"}`, mirroring `_search_emails`.

## 11. Testing

- **Synthetic `chat.db` fixture**: build a tiny SQLite with `message`, `handle`,
  `chat`, `chat_message_join` rows including (a) one row using `text`, (b) one using
  `attributedBody` only, (c) Apple-epoch timestamps (ns and s), (d) a group chat.
  Assert: text extraction (both paths), date conversion, direction, group flag.
- **Contacts**: synthetic AddressBook map → assert E.164 normalization + handle
  resolution + raw-handle fallback.
- **Incremental cursor**: seed `last_rowid`, add new rows, assert only new ones are
  read; assert fingerprint change forces full rescan.
- **`cloud_boundary` round-trip**: redact an excerpt containing a seeded contact name
  + a phone number → assert names/numbers are tokenized → `detokenize` restores the
  originals exactly.
- **`lowercase_for_ner=False`** path: a normally-capitalized name is tokenized.
- Mirror existing `tests/` patterns and fixtures.

## 12. Defaults (applied; flag to change)

1. **History window:** 3 years (`days_back=1095`), matching email.
2. **Group chats:** included, stored with an `is_group` flag (not 1:1 only).
3. **Embedder:** lifted to shared `core/embeddings.py` rather than duplicated.

## 13. Open questions / future

- Phase 2: structured extraction of appointments/orders into typed records the app
  can list and remind on.
- Possible later: redact the user's question too (conversation-scoped store) and/or
  route sensitive queries to `local_chat_engine.py` to skip the cloud entirely.
- Attachment filenames as searchable metadata.
