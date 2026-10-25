# 7. Scanner & Google Drive — finding statement PDFs for you

**Job:** save you from hunting through folders. JAR can sweep your computer (or a
Google Drive folder), guess which PDFs are bank statements, and let you confirm
before importing.

**Files:**
- `app/scanner/drive_scanner.py` — walks local folders for PDFs
- `app/scanner/filename_classifier.py` — scores each PDF by its filename
- `app/scanner/scanner_config.py` — where to look, what to skip
- `app/scanner/drive_client.py` — the Google Drive connection

## How a local scan works

```
Pick folders (Downloads, Documents, Desktop, iCloud, Dropbox… by default)
        │
        ▼  walk each folder (up to 5 levels deep, skipping system/junk dirs)
For each PDF: score it 0.0–1.0 by filename
        │
        ▼  keep the likely ones (size between ~10KB and 50MB)
Show you a ranked list  →  you tick the real statements  →  import them
```

Progress streams to the browser live as it walks.

## How the filename guesser scores

It's heuristic and deliberately **broad** — it would rather show you a false
positive (you untick it) than miss a real statement. Signals it looks for:

- **Statement words:** "statement", "estmt", "e-statement", "checking", "savings"…
- **Bank names:** Chase, Bank of America, Wells Fargo, TD, and ~50 others.
- **Date patterns:** `2023-02-10`, `02-2023`, etc.
- **Instant rejects:** "resume", "recipe", "manual", "homework"… score 0.

The PDFs you select then go through the exact same
[extraction pipeline](01-extraction.md) as a manual upload.

## Google Drive (optional)

Same idea, but the files live in your Drive instead of on disk:

```
Connect Drive (Google OAuth, read-only)  →  pick a folder
        │
        ▼  list PDFs, score them by name
You select the real statements
        │
        ▼  download to data/statements/  →  run extraction
```

- Uses the **read-only** Drive scope — JAR can list and download, never modify.
- The login token is stored locally at `data/google_token.json` (gitignored) and
  auto-refreshes. You can disconnect anytime.
- Setup (one-time Google Cloud OAuth client) is documented in the project
  `CLAUDE.md`. Requires `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` in `.env`.

All of this is driven from the `/scan` page and the `/api/scan/*` and
`/api/drive/*` endpoints. See [Server & UI](09-server-and-ui.md).
