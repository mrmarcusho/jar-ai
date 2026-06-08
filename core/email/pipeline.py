"""Email pipeline orchestrator: IMAP → embed → LanceDB.

A deliberately simple flow that replaced the earlier triage/extraction pipeline:

1. Fetch **every** message in the mailbox (envelopes, then full bodies). No
   triage, no LLM, no SQLite routing.
2. Embed ``subject + body`` locally with BAAI/bge-m3 (1024-dim) — the model runs
   on-device, so raw email text never leaves the machine and needs no redaction.
3. Upsert each row + vector into a LanceDB table (``data/lancedb/``), deduped by
   Message-ID so re-scans update rather than duplicate.

``run_scan`` is the single entry point; ``run_full_scan`` is the date-bounded
convenience wrapper used by the API. Incremental sync lives in sync.py and calls
``run_scan`` with an explicit ``since``.
"""
from __future__ import annotations

import hashlib
import logging
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Callable, Optional

from core.email import embedder, imap_client, vector_store

logger = logging.getLogger("jar.email.pipeline")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH = PROJECT_ROOT / "data" / "jar.db"  # sync cursor bookkeeping only

DEFAULT_DAYS_BACK = 365 * 3
EMBED_BATCH = 8

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


def _stable_id(message_id: str, imap_id: str) -> str:
    """Use the Message-ID when present, else a deterministic fallback hash."""
    if message_id:
        return message_id
    return "imap:" + hashlib.sha256(imap_id.encode("utf-8", "replace")).hexdigest()[:32]


def _embed_text(subject: str, body: str) -> str:
    subject = (subject or "").strip()
    body = (body or "").strip()
    return f"{subject}\n\n{body}".strip() if subject else body


def run_scan(
    since: Optional[str] = None,
    days_back: int = DEFAULT_DAYS_BACK,
    progress_cb: ProgressCb = None,
    db_dir: Optional[Path] = None,
    last_uid: Optional[int] = None,
    uidvalidity: Optional[str] = None,
) -> dict:
    """Fetch the mailbox, embed every message, and upsert into LanceDB.

    `since` (IMAP DD-Mon-YYYY) takes precedence over `days_back`. Requires a
    saved IMAP account (imap_client.save_account).
    """
    start = time.time()
    summary = {
        "emails_fetched": 0, "embedded": 0, "stored": 0, "duplicates": 0,
        "errors": 0, "duration_seconds": 0.0, "last_imap_date": None,
        "last_uid": last_uid, "uidvalidity": None,
    }
    errors: list[str] = []

    if since is None and last_uid is None:
        since = _since_from_days(days_back)

    _emit(progress_cb, "connect", 0, 1, "Connecting to mailbox")
    imap_conn = imap_client.connect_saved()
    if imap_conn is None:
        errors.append("no saved IMAP account")
        summary["errors"] = len(errors)
        summary["error_detail"] = errors
        summary["duration_seconds"] = round(time.time() - start, 2)
        return summary

    try:
        server_uidvalidity = imap_client.get_uidvalidity(imap_conn)
        summary["uidvalidity"] = server_uidvalidity
        if (uidvalidity is not None and server_uidvalidity is not None
                and server_uidvalidity != uidvalidity):
            logger.warning(
                "UIDVALIDITY changed (%s → %s); forcing full rescan",
                uidvalidity, server_uidvalidity,
            )
            last_uid = None
            since = _since_from_days(days_back)

        _emit(progress_cb, "envelopes", 0, 1, "Fetching message headers")
        envelopes = imap_client.fetch_envelopes(
            imap_conn, since=since, last_uid=last_uid,
            progress_cb=lambda c, t: _emit(progress_cb, "envelopes", c, t, "Fetching headers"),
        )
        summary["emails_fetched"] = len(envelopes)
        env_by_id = {env["_imap_id"]: env for env in envelopes}

        latest = None
        for env in envelopes:
            dt = _email_datetime(env.get("date", ""))
            if dt and (latest is None or dt > latest):
                latest = dt
        if latest is not None:
            summary["last_imap_date"] = latest.strftime("%d-%b-%Y")

        # Envelope fetch can run for a while on a large mailbox; reconnect a
        # fresh socket before the body fetch and hand fetch_bodies a reconnect
        # hook for mid-stream drops (Gmail closes idle IMAP connections).
        all_ids = [env["_imap_id"] for env in envelopes]
        _emit(progress_cb, "bodies", 0, len(all_ids), "Reconnecting to mailbox")
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

        _emit(progress_cb, "bodies", 0, len(all_ids), "Fetching email bodies")
        bodies = imap_client.fetch_bodies(
            imap_conn, all_ids,
            progress_cb=lambda c, t: _emit(progress_cb, "bodies", c, t, "Fetching bodies"),
            reconnect=imap_client.connect_saved,
        )

        # Build one record per body, embedding in batches.
        count_before = vector_store.count(db_dir=db_dir)
        total = len(bodies)
        records: list[dict] = []
        for start_i in range(0, total, EMBED_BATCH):
            chunk = bodies[start_i:start_i + EMBED_BATCH]
            texts, metas = [], []
            for body in chunk:
                env = env_by_id.get(body["_imap_id"], {})
                texts.append(_embed_text(env.get("subject", ""), body.get("body_text", "")))
                metas.append((env, body))
            try:
                vectors = embedder.embed_texts(texts, batch_size=EMBED_BATCH)
            except Exception as exc:  # one bad batch must not abort the scan
                logger.warning("Embedding failed for a batch: %s", exc)
                errors.append(f"embed:{exc}")
                _emit(progress_cb, "embed", min(start_i + len(chunk), total), total, "Embedding")
                continue
            for (env, body), vector in zip(metas, vectors):
                records.append({
                    "message_id": _stable_id(
                        body.get("message_id") or env.get("message_id", ""),
                        body["_imap_id"].decode() if isinstance(body["_imap_id"], bytes)
                        else str(body["_imap_id"]),
                    ),
                    "sender": imap_client.sender_domain(env.get("from", "")),
                    "subject": env.get("subject", ""),
                    "date": env.get("date", ""),
                    "body": body.get("body_text", ""),
                    "vector": vector,
                })
            summary["embedded"] += len(vectors)
            _emit(progress_cb, "embed", min(start_i + len(chunk), total), total, "Embedding")

        max_uid = last_uid  # preserve cursor when no new messages arrive
        for body in bodies:
            uid = body["_imap_id"]
            uid_int = int(uid.decode() if isinstance(uid, bytes) else uid)
            if max_uid is None or uid_int > max_uid:
                max_uid = uid_int
        summary["last_uid"] = max_uid

        _emit(progress_cb, "store", 0, len(records), "Writing to vector database")
        vector_store.upsert(records, db_dir=db_dir)
        count_after = vector_store.count(db_dir=db_dir)
        summary["stored"] = count_after - count_before
        summary["duplicates"] = len(records) - summary["stored"]
    finally:
        try:
            imap_conn.logout()
        except Exception:
            pass

    summary["errors"] = len(errors)
    if errors:
        summary["error_detail"] = errors[:20]
    summary["duration_seconds"] = round(time.time() - start, 2)
    return summary


def run_full_scan(
    days_back: int = DEFAULT_DAYS_BACK,
    progress_cb: ProgressCb = None,
    db_dir: Optional[Path] = None,
) -> dict:
    """Date-bounded full scan (default: last 3 years)."""
    return run_scan(days_back=days_back, progress_cb=progress_cb, db_dir=db_dir)
