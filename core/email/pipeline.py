"""Email pipeline orchestrator: IMAP → triage → extract → store.

Wires the six stages together and reuses JAR's existing machinery:

- ONE ``RedactionEngine`` (built from the household profile) is shared across
  triage and extraction — building one per email would reload spaCy/Presidio
  every time. The cloud only ever sees its redacted output.
- ONE ``anthropic.Anthropic`` client is shared across the Haiku triage and the
  Haiku extractor.
- PDF attachments are NOT parsed here; they are written to ``data/statements/``
  and handed to the existing ``extract_statement`` engine, exactly like a Drive
  or local upload.

``run_scan`` is the single entry point; ``run_full_scan`` is the date-bounded
convenience wrapper used by the API. Incremental sync lives in sync.py and calls
``run_scan`` with an explicit ``since``.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Callable, Optional

import anthropic

from core.database.migrate import ensure_schema
from core.email import imap_client
from core.email.bucket_classifier import BUCKET_ATTACHMENT, classify
from core.email.db_writer import route_and_write
from core.email.extractor import extract_email
from core.email.triage import triage
from core.extraction_engine.extract import extract_statement
from core.privacy.profile import HouseholdProfile
from core.privacy.redactor import RedactionEngine

logger = logging.getLogger("jar.email.pipeline")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH = PROJECT_ROOT / "data" / "jar.db"
STATEMENTS_DIR = PROJECT_ROOT / "data" / "statements"

DEFAULT_DAYS_BACK = 365 * 3
HAIKU_MODEL = "claude-haiku-4-5-20251001"

ProgressCb = Optional[Callable[[dict], None]]


def _emit(cb: ProgressCb, stage: str, current: int, total: int, message: str) -> None:
    if cb:
        cb({"stage": stage, "current": current, "total": total, "message": message})


def _since_from_days(days_back: int) -> str:
    """IMAP SEARCH SINCE date (DD-Mon-YYYY) for `days_back` days ago."""
    return (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime("%d-%b-%Y")


def _email_datetime(date_header: str) -> Optional[datetime]:
    try:
        dt = parsedate_to_datetime(date_header)
    except (TypeError, ValueError, IndexError):
        return None
    if dt is None:
        return None
    # Headers ending in "-0000" parse to naive datetimes; normalize all to
    # aware UTC so comparisons never mix offset-naive and offset-aware values.
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _route_attachments(body: dict, errors: list) -> int:
    """Write each PDF attachment to data/statements/ and run extract_statement."""
    routed = 0
    STATEMENTS_DIR.mkdir(parents=True, exist_ok=True)
    for att in body.get("attachments", []):
        is_pdf = att.get("content_type") == "application/pdf" or (
            att.get("filename", "").lower().endswith(".pdf")
        )
        if not is_pdf:
            continue
        pdf_path = STATEMENTS_DIR / (att.get("filename") or "email_attachment.pdf")
        try:
            pdf_path.write_bytes(att["data"])
            extract_statement(pdf_path, DB_PATH)
            routed += 1
        except Exception as exc:  # one bad PDF must not abort the scan
            logger.warning("Attachment extraction failed (%s): %s", pdf_path.name, exc)
            errors.append(f"attachment:{pdf_path.name}:{exc}")
    return routed


def run_scan(
    since: Optional[str] = None,
    days_back: int = DEFAULT_DAYS_BACK,
    progress_cb: ProgressCb = None,
    db_path: Optional[Path] = None,
) -> dict:
    """Run one full pass over the saved mailbox and return summary counts.

    `since` (IMAP DD-Mon-YYYY) takes precedence over `days_back`. Requires a
    saved IMAP account (imap_client.save_account) and ANTHROPIC_API_KEY.
    """
    db_path = db_path or DB_PATH
    start = time.time()
    summary = {
        "envelopes_fetched": 0, "kept": 0, "skipped": 0,
        "events_written": 0, "documents_written": 0, "duplicates": 0,
        "attachments_routed": 0, "errors": 0, "duration_seconds": 0.0,
        "last_imap_date": None,
    }
    errors: list[str] = []

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_schema(conn)

    key = os.environ.get("ANTHROPIC_API_KEY")
    client = anthropic.Anthropic(api_key=key) if key else None
    redactor = RedactionEngine(HouseholdProfile.load(conn))

    if since is None:
        since = _since_from_days(days_back)

    _emit(progress_cb, "connect", 0, 1, "Connecting to mailbox")
    imap_conn = imap_client.connect_saved()
    if imap_conn is None:
        conn.close()
        summary["duration_seconds"] = round(time.time() - start, 2)
        errors.append("no saved IMAP account")
        summary["errors"] = len(errors)
        summary["error_detail"] = errors
        return summary

    try:
        _emit(progress_cb, "envelopes", 0, 1, "Fetching message headers")
        envelopes = imap_client.fetch_envelopes(
            imap_conn, since=since,
            progress_cb=lambda c, t: _emit(progress_cb, "envelopes", c, t, "Fetching headers"),
        )
        summary["envelopes_fetched"] = len(envelopes)

        latest = None
        for env in envelopes:
            dt = _email_datetime(env.get("date", ""))
            if dt and (latest is None or dt > latest):
                latest = dt
        if latest is not None:
            summary["last_imap_date"] = latest.strftime("%d-%b-%Y")

        _emit(progress_cb, "triage", 0, len(envelopes), "Triaging")
        labels = triage(
            envelopes, client=client, redactor=redactor,
            progress_cb=lambda c, t: _emit(progress_cb, "triage", c, t, "Triaging (cloud)"),
        )
        keep_ids = [
            env["_imap_id"] for env, lbl in zip(envelopes, labels) if lbl == "KEEP"
        ]
        env_by_id = {env["_imap_id"]: env for env in envelopes}
        summary["kept"] = len(keep_ids)
        summary["skipped"] = len(envelopes) - len(keep_ids)

        # Triage can run for minutes on a large mailbox, long enough for Gmail
        # to drop the idle IMAP socket. Reconnect a fresh connection before the
        # body fetch, and hand fetch_bodies a reconnect hook for mid-stream drops.
        _emit(progress_cb, "bodies", 0, len(keep_ids), "Reconnecting to mailbox")
        try:
            imap_conn.logout()
        except Exception:
            pass
        imap_conn = imap_client.connect_saved()
        if imap_conn is None:
            errors.append("lost IMAP connection before body fetch")
            summary["errors"] = len(errors)
            summary["error_detail"] = errors
            summary["duration_seconds"] = round(time.time() - start, 2)
            return summary

        _emit(progress_cb, "bodies", 0, len(keep_ids), "Fetching kept bodies")
        bodies = imap_client.fetch_bodies(
            imap_conn, keep_ids,
            progress_cb=lambda c, t: _emit(progress_cb, "bodies", c, t, "Fetching bodies"),
            reconnect=imap_client.connect_saved,
        )

        total = len(bodies)
        for i, body in enumerate(bodies, 1):
            env = env_by_id.get(body["_imap_id"], {})
            domain = imap_client.sender_domain(env.get("from", ""))
            subject = env.get("subject", "")
            message_id = body.get("message_id") or env.get("message_id", "")
            bucket = classify(
                body.get("body_text", ""), domain, subject, body.get("attachments"),
            )

            if bucket == BUCKET_ATTACHMENT:
                summary["attachments_routed"] += _route_attachments(body, errors)
                _emit(progress_cb, "extract", i, total, "Routing attachment")
                continue

            try:
                result = extract_email(
                    body.get("body_text", ""), domain, subject, bucket,
                    redactor, client, model=HAIKU_MODEL,
                )
                outcome = route_and_write(conn, result, message_id)
                if outcome == "transaction":
                    summary["events_written"] += 1
                elif outcome == "document":
                    summary["documents_written"] += 1
                else:
                    summary["duplicates"] += 1
            except Exception as exc:  # one bad email must not abort the scan
                logger.warning("Extraction failed for one email: %s", exc)
                errors.append(f"extract:{exc}")
            _emit(progress_cb, "extract", i, total, "Extracting")

        conn.commit()
    finally:
        try:
            imap_conn.logout()
        except Exception:
            pass
        conn.close()

    summary["errors"] = len(errors)
    if errors:
        summary["error_detail"] = errors[:20]
    summary["duration_seconds"] = round(time.time() - start, 2)
    return summary


def run_full_scan(
    days_back: int = DEFAULT_DAYS_BACK,
    progress_cb: ProgressCb = None,
    db_path: Optional[Path] = None,
) -> dict:
    """Date-bounded full scan (default: last 3 years)."""
    return run_scan(days_back=days_back, progress_cb=progress_cb, db_path=db_path)
