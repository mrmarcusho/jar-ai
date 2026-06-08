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
- **Read-only, torn-write-safe open.** Do **not** do a naive `shutil.copy` of the
  live DB — if Messages is mid-write, a byte copy can capture a torn/partial DB.
  Instead open the original through SQLite in read-only immutable mode:
  `sqlite3.connect("file:<path>?mode=ro&immutable=1", uri=True)`. This never takes a
  write lock and never blocks Messages. (If a future macOS locks even `mode=ro`,
  fall back to the SQLite **online backup API** — `source.backup(dest)` — which
  produces a consistent snapshot; still never a raw byte copy.)
- Core query joins `message` ↔ `chat_message_join` ↔ `chat` and `message.handle_id`
  ↔ `handle.ROWID` to get text, timestamp, direction, and the other party's handle.

### 3.2 Decoding quirks (must handle)
- **`attributedBody`** — on modern macOS `message.text` is often NULL and the body
  lives in `message.attributedBody`, a serialized `NSAttributedString` (Apple
  "typedstream" / `streamtyped` binary blob). The reader must decode it to recover
  the text; falling back to `text` only would silently drop most recent messages.
  - **New dependency required** — no typedstream decoder is currently installed.
    Add a **pure-Python** typedstream decoder to `requirements.txt` (candidate:
    `pytypedstream` / `typedstream`; the exact PyPI distribution is confirmed in the
    plan step, and must be pure-Python — no compiled/Foundation dependency so it runs
    cross-platform in tests). Implement a **heuristic fallback** (extract the longest
    printable-UTF8 run between the known `NSString` markers) for blobs the decoder
    can't parse, so a single odd row never crashes the scan.
- **Apple epoch** — `message.date` is nanoseconds since `2001-01-01 00:00:00 UTC`.
  Older rows store **seconds** instead; detect by magnitude (ns values are ~10^18)
  and convert both to ISO 8601 before storage.
- **NULL text AND NULL attributedBody** — reactions/tapbacks, attachment-only
  messages, and stickers carry no body. **Skip** these (don't store empty rows);
  they are not useful for search. Log a per-scan count of skipped rows.

### 3.3 Contact resolution
- macOS AddressBook DB: `~/Library/Application Support/AddressBook/Sources/*/AddressBook-v22.abcddb`.
- Build a `handle → contact_name` map. Normalize phone numbers to **E.164** on both
  sides (handles and AddressBook numbers) so matching is reliable.
- Unresolved handles fall back to the raw handle string (phone/Apple ID).
- **Group chats**: a single message still has exactly one sender — `message.handle_id`
  — so sender resolution is identical to 1:1 (one handle → one name). The `is_group`
  flag is **contextual metadata only**; we do **not** resolve the full participant
  roster in this phase. (Outgoing messages have `is_from_me=1` and `handle_id=0`;
  store `contact_name="Me"` / direction `me`.)

### 3.4 Permission (TCC) and error flow
- Both `chat.db` and the AddressBook DB are protected. A **single Full Disk Access**
  grant (System Settings → Privacy & Security → Full Disk Access) covers both.
- **Typed, propagated error.** `chat_db.py` raises a dedicated
  `MessagesAccessError` on `sqlite3.OperationalError`/`PermissionError`/"unable to
  open database file" (the symptom of missing Full Disk Access) and on a missing
  `chat.db`. `pipeline.py` lets it propagate; the API layer (`app/server.py`)
  catches it and returns a clear, actionable message ("Grant Full Disk Access to …",
  or "No Messages database found"). **Never** swallow it into an empty result that
  looks like "no messages". The nightly job catches it, logs it, and moves on.

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
| `core/embeddings.py` | The bge-m3 embedder, **lifted from `core/email/embedder.py`** (`get_model`, `embed_texts`, `EMBED_DIM=1024`). `core/email/embedder.py` becomes a thin re-export (`from core.embeddings import *`) to avoid churn; `core/messages` imports the shared module. **Import-update checklist**: `core/email/vector_store.py:19` (`from core.email.embedder import EMBED_DIM`) and `app/chat/chat_engine.py:322` (`from core.email import embedder`) keep working via the re-export, but should be migrated to `core.embeddings` in the same change. Both pipelines share one model load and one vector space. |
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
    last_guid       TEXT,     -- guid of the message at last_rowid (cursor anchor)
    db_fingerprint  TEXT,     -- detects chat.db replacement / VACUUM (see §6.2)
    last_sync_at    TEXT
);
```
- **Added in two places** (both required): the `CREATE TABLE IF NOT EXISTS` block in
  `core/database/schema.sql`, **and** a guarded create in `migrate.py`'s
  `ensure_schema()` so existing production DBs get the table. (`ensure_schema()` runs
  `schema.sql` via `executescript`, so the `IF NOT EXISTS` block covers existing DBs
  too; add an explicit step only if a future column needs a guarded `ALTER`, mirroring
  the existing `last_uid`/`uidvalidity` migration pattern.)
- **No manual row init needed.** Writes use upsert:
  `INSERT INTO message_sync_state (id, …) VALUES (1, …) ON CONFLICT(id) DO UPDATE SET …`.
  The first sync creates row `id=1`; the `CHECK (id = 1)` guarantees a single row.

## 6. Data Flow

### 6.1 Full scan (first run)
1. `pipeline.run_full_scan(days_back=1095)` — default **3 years** (matches email).
2. Copy `chat.db` → read all messages since the window; resolve handles → contact
   names; decode `attributedBody`; convert dates.
3. Embed each message's `text` on-device (bge-m3, ≤512 seq / ≤4000 chars).
4. Upsert into LanceDB `messages`, keyed by `guid` (re-scan updates, never dupes).
5. Record the highest `ROWID` seen and a `db_fingerprint` in `message_sync_state`.

### 6.2 Incremental sync (nightly, 02:00)
1. Load `last_rowid`, `last_guid`, `db_fingerprint` from `message_sync_state`.
   **First run / no row / any NULL field → treat the cursor as invalid and run a full
   scan** (`run_full_scan`). No NULL-deref: the absence of a row short-circuits before
   the anchor recheck.
2. **Validate the cursor** before trusting it (ROWIDs are only monotonic within one
   stable DB; a device migration *or* a `VACUUM` of `chat.db` can renumber them):
   - `db_fingerprint` = a cheap hash that changes when the DB is replaced or
     compacted — concretely `sha1(f"{total_message_count}:{min_rowid}:{max_rowid}")`.
     A `VACUUM` renumbers ROWIDs so `min/max_rowid` shift → fingerprint changes →
     detected.
   - **Anchor recheck**: confirm the row currently at `ROWID = last_rowid` still has
     `guid = last_guid`. If the fingerprint changed OR the anchor guid no longer
     matches, the cursor is stale → **force a full rescan** (guid is the LanceDB dedup
     key, so a rescan re-upserts without duplicating).
3. Otherwise query `WHERE message.ROWID > last_rowid`; embed + upsert only new rows.
4. Persist the new `last_rowid`, its `last_guid`, and the recomputed `db_fingerprint`.
5. Extends the **existing APScheduler nightly job** in `app/server.py` (which already
   runs email `incremental_sync` at 02:00) to also run message sync — see §9 for the
   error-isolation requirement.

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
  scrub (`body`/`text`), and an optional list of known **seed names** for those rows.
- Behavior: **pre-seed** the engine's `TokenStore` with any known names
  (deterministic, high-confidence tokenization), then `redact()` the configured text
  fields.
- Output: rows with redacted text fields, ready to serialize as the tool result.

**Asymmetry between the two pipelines (important):**
- **Messages** carry a resolved `contact_name` per row → these are passed as seed
  names, so the sender *and* any in-body mention of that contact tokenize
  deterministically. This is the strong case.
- **Email rows have no person name to seed.** `core/email/pipeline.py` stores
  `sender` as **domain-only** (`imap_client.sender_domain(...)`) — the display name
  was dropped at ingest. So for email there are **no seed names**; body redaction
  relies entirely on the household profile (Pass 1) + Presidio NER (Pass 2) + the
  deterministic sweep (Pass 3). The retrofit still meaningfully reduces leakage
  (emails, phone numbers, SSNs, profile names) but **cannot** guarantee third-party
  sender names are caught the way messages can. Documented, not a blocker.
- The `sender`/`subject`/`date` and `contact_name` fields are short labels; redact
  the **body/text** field and the **`contact_name`/sender label**; leave `date`
  untouched (the DOB recognizer could otherwise mangle an ISO date).

### 7.4 RedactionEngine changes
- **Add `lowercase_for_ner: bool = True`** to `RedactionEngine.__init__`. Default
  preserves today's bank-statement behavior (ALL-CAPS → lowercase for spaCy). Pass
  `False` for chat excerpts so Presidio/spaCy keep the capitalization cues that
  conversational text provides (lowercasing would make NER *miss* names).
- **Add a public method `register_person(name: str)`** on `RedactionEngine` that
  delegates to its private `self._store.get_or_create("PERSON", name)` (the same
  `TokenStore` method `_pass1_payment_apps` already uses). `cloud_boundary` calls this
  wrapper rather than reaching into the private `_store`, keeping the encapsulation
  boundary clean. (`TokenStore` exposes `get_or_create`, `reserve`, `all_tokens`.)
- **Warm Presidio at server startup** (call the shared loader once on boot) so the
  first query doesn't pay a ~1–2s model-load stall.

### 7.5 Engine lifetime + construction (per chat session)
- The `RedactionEngine` lives on the **`JarChat` instance** (created in `__init__`),
  **not per turn**. Tool results are stored in `conversation_history` and replayed
  every turn; a session-scoped store keeps tokens consistent so any token Claude
  echoes from an earlier turn can still be detokenized. (`conversation_history` is
  in-memory per instance, so there is no stale cross-session token risk.)
- **Construction**: `profile = HouseholdProfile.load(conn)` (already exists in
  `core/privacy/profile.py`; returns an empty profile safely if the table/rows are
  absent), then `RedactionEngine(profile, lowercase_for_ner=False)`.
- `JarChat.chat()` detokenizes `final_text` via the engine before returning. The
  detokenizer is substring-based; add a final safety net that logs (does not crash)
  if any `[A-Z_]+(_\d+)?`-shaped token survives detokenization, so leaked
  *placeholders* (not PII) are observable rather than silent.

### 7.6 Degraded-redaction handling (must not be silent)
Redaction can quietly weaken in two ways; both must surface:
- **Profile not configured** (`profile.is_configured()` is `False`): Pass 1 name
  matching is a no-op, so the user's *own* name relies on NER. Log a one-time warning
  at chat init.
- **Presidio/spaCy unavailable**: `RedactionEngine` already falls back to Pass 1 +
  Pass 3 only (profile + deterministic sweep) and logs a warning. For messages this
  means body person-names rely solely on contact-name seeding. Surface this as a
  startup log warning (Presidio is warmed on boot per §7.4, which is also where this
  is detected). Neither condition blocks chat — redaction is **best-effort by
  design**, and this trade-off is stated in §7 rather than hidden.

### 7.7 Scope decision (explicit)
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
  trigger for messages (run in an executor via `run_in_executor`, awaited, like the
  email endpoints).
- Extend the nightly `_nightly_sync` job to run message `incremental_sync`. **Isolate
  the two syncs** — wrap email sync and message sync in **separate** try/except
  blocks so a failure (or missing config) in one never prevents the other from
  running. Each logs its own success/failure.
- **Concurrency**: guard the message sync with a module-level
  `asyncio.Lock`/boolean so a manual sync endpoint and the nightly job can't run
  message ingestion concurrently against the same LanceDB table (mirror this for the
  email path if it lacks it). A second concurrent request returns "sync already
  running" rather than racing.
- Warm Presidio on startup (§7.4), and detect/log the degraded conditions in §7.6.

### 9.1 Dependency
Add the pure-Python typedstream decoder (§3.2) to `requirements.txt` under a new
"Text-message ingestion" group. No keychain/credential dependency is needed (local
file read). `install.sh` requires no change beyond `pip install -r requirements.txt`.

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
- **NULL-both rows** (reaction/tapback/attachment-only) are skipped, not stored as
  empty.
- **Cursor validation**: fingerprint change OR anchor-guid mismatch forces a full
  rescan; a clean run advances `last_rowid`/`last_guid` and re-upserts without dupes.
- **`MessagesAccessError`**: simulate an unreadable DB → assert the typed error
  propagates and the API returns the actionable message (not an empty result).
- Decoder is **pure-Python** so these run on the test box's Python (3.9 locally per
  project memory) with no macOS/Foundation dependency.
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

## 14. Robustness review — resolved findings (2026-06-08)

A production-readiness review against the real code resolved these into the spec:
- **`attributedBody` decoder** is a new, required pure-Python dependency (none
  installed) + heuristic fallback + NULL-both skip — §3.2, §9.1, §11.
- **Torn-copy risk** → read-only `immutable=1` open / backup API, not byte copy — §3.1.
- **ROWID invalidation** (VACUUM/migration) → `db_fingerprint` definition +
  `last_guid` anchor recheck forcing full rescan — §5.1, §6.2.
- **Email has no display name to seed** (domain-only at ingest) → seeding asymmetry
  documented; email body redaction relies on profile + NER + sweep — §7.3.
- **Silent redaction degradation** (empty profile / Presidio down) → surfaced via
  logs, `HouseholdProfile.load` + `is_configured()` path specified — §7.5, §7.6.
- **`message_sync_state`** → upsert (no manual init), added to both `schema.sql` and
  `migrate.py` — §5.1.
- **FDA permission** → typed `MessagesAccessError` propagated to a user-facing API
  message — §3.4, §10.
- **Group-chat sender** → per-message single handle; `is_group` is metadata only — §3.3.
- **Scheduler** → email/message syncs isolated in separate try/except; concurrency
  lock on message ingestion — §9.
- **Embedder lift** → import-update checklist for the two existing import sites — §4.1.
- Deferred as non-blocking: detokenizer uses substring replace (a mangled token would
  leak a *placeholder*, never PII or cloud data) — mitigated with a logging safety
  net in §7.5.
