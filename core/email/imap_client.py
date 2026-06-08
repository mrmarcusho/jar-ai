"""IMAP connection, envelope fetch, and body fetch for the email pipeline.

Uses the Python stdlib (`imaplib` + `email`) so no extra dependency is needed.
Credentials are stored in a gitignored token file under data/ (mirroring the
Drive scanner's data/google_token.json), not in SQLite and not in the Keychain.

The raw IMAP uid (`_imap_id`) is internal bookkeeping; the vector store keys
rows on the Message-ID instead.
"""
from __future__ import annotations

import email
import imaplib
import json
import logging
import re
import time
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parseaddr
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger("jar.email.imap")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
TOKEN_PATH = PROJECT_ROOT / "data" / "imap_account.json"

KEYCHAIN_SERVICE = "jar-imap"

ENVELOPE_BATCH = 200
BODY_BATCH = 50
BATCH_SLEEP = 0.1  # seconds between batches — avoids Gmail rate limiting

# Auto-detect server from the email domain. Unknown domains require an explicit
# host/port passed to connect().
IMAP_SERVERS: dict[str, tuple[str, int]] = {
    "gmail.com":      ("imap.gmail.com",       993),
    "googlemail.com": ("imap.gmail.com",       993),
    "icloud.com":     ("imap.mail.me.com",     993),
    "me.com":         ("imap.mail.me.com",     993),
    "mac.com":        ("imap.mail.me.com",     993),
    "yahoo.com":      ("imap.mail.yahoo.com",  993),
    "outlook.com":    ("outlook.office365.com", 993),
    "hotmail.com":    ("outlook.office365.com", 993),
    "live.com":       ("outlook.office365.com", 993),
}

_UID_RE = re.compile(rb"UID (\d+)")


class IMAPConfigError(RuntimeError):
    """Raised when no IMAP server can be determined for an account."""


# ---------------------------------------------------------------------------
# Server detection
# ---------------------------------------------------------------------------

def detect_server(username: str) -> Optional[tuple[str, int]]:
    """Return (host, port) for a known email domain, or None if unknown."""
    domain = username.rsplit("@", 1)[-1].lower().strip()
    return IMAP_SERVERS.get(domain)


def resolve_server(
    username: str, host: Optional[str] = None, port: Optional[int] = None
) -> tuple[str, int]:
    """Resolve the IMAP host/port from explicit args or domain auto-detection."""
    if host:
        return host, int(port or 993)
    detected = detect_server(username)
    if not detected:
        raise IMAPConfigError(
            f"Unknown email provider for {username!r}; pass host and port explicitly."
        )
    return detected


# ---------------------------------------------------------------------------
# Credential storage (gitignored token file under data/)
# ---------------------------------------------------------------------------

def save_account(username: str, password: str, host: str, port: int) -> None:
    import keyring
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Store only non-sensitive fields on disk; password goes into the OS keychain.
    TOKEN_PATH.write_text(json.dumps({"username": username, "host": host, "port": port}))
    keyring.set_password(KEYCHAIN_SERVICE, username, password)


def load_account() -> Optional[dict]:
    import keyring
    if not TOKEN_PATH.exists():
        return None
    try:
        data = json.loads(TOKEN_PATH.read_text())
    except (ValueError, OSError):
        return None
    username = data.get("username")
    if not username:
        return None
    password = keyring.get_password(KEYCHAIN_SERVICE, username)
    if password is None:
        logger.warning("No keychain entry for %s; reconnect to re-save credentials", username)
        return None
    return {**data, "password": password}


def clear_account() -> None:
    import keyring
    if TOKEN_PATH.exists():
        try:
            data = json.loads(TOKEN_PATH.read_text())
            username = data.get("username")
            if username:
                keyring.delete_password(KEYCHAIN_SERVICE, username)
        except Exception:
            pass
        TOKEN_PATH.unlink()


def is_connected() -> bool:
    return load_account() is not None


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

def connect(
    username: str,
    password: str,
    host: Optional[str] = None,
    port: Optional[int] = None,
    mailbox: str = "INBOX",
) -> imaplib.IMAP4_SSL:
    """Open an authenticated IMAP SSL connection and select a mailbox read-only."""
    resolved_host, resolved_port = resolve_server(username, host, port)
    conn = imaplib.IMAP4_SSL(resolved_host, resolved_port)
    conn.login(username, password)
    conn.select(mailbox, readonly=True)
    return conn


def connect_saved(mailbox: str = "INBOX") -> Optional[imaplib.IMAP4_SSL]:
    """Connect using the stored account, or None if no account is saved."""
    acct = load_account()
    if not acct:
        return None
    return connect(
        acct["username"], acct["password"],
        host=acct.get("host"), port=acct.get("port"), mailbox=mailbox,
    )


# ---------------------------------------------------------------------------
# Fetch helpers
# ---------------------------------------------------------------------------

def _chunks(seq: list, size: int):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def _decode(value: Optional[str]) -> str:
    """Decode an RFC 2047 encoded header into a plain string."""
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _iter_fetch(data: list):
    """Yield (uid: bytes, raw: bytes) pairs from an imaplib UID FETCH response."""
    for item in data:
        if not isinstance(item, tuple):
            continue
        prefix, raw = item[0], item[1]
        m = _UID_RE.search(prefix)
        if not m or raw is None:
            continue
        yield m.group(1), raw


def get_uidvalidity(conn: imaplib.IMAP4_SSL) -> Optional[str]:
    """Return the UIDVALIDITY of the currently selected mailbox, or None."""
    vals = conn.untagged_responses.get("UIDVALIDITY")
    if vals:
        v = vals[-1]
        return v.decode() if isinstance(v, bytes) else str(v)
    return None


def _search_uids(conn: imaplib.IMAP4_SSL, since: Optional[str] = None,
                 last_uid: Optional[int] = None) -> list[bytes]:
    """Return matching UIDs. Prefers UID range over date when last_uid is set."""
    if last_uid is not None:
        criteria = ["UID", f"{last_uid + 1}:*"]
    elif since:
        criteria = ["SINCE", since]
    else:
        criteria = ["ALL"]
    typ, data = conn.uid("SEARCH", None, *criteria)
    if typ != "OK" or not data or data[0] is None:
        return []
    return data[0].split()


# ---------------------------------------------------------------------------
# Envelope fetch (headers only — bodies come later)
# ---------------------------------------------------------------------------

def fetch_envelopes(
    conn: imaplib.IMAP4_SSL,
    since: Optional[str] = None,
    last_uid: Optional[int] = None,
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> list[dict]:
    """Fetch lightweight envelopes (From/Subject/Date/Message-ID) for all messages.

    Headers only — never RFC822 — so this stays cheap over large mailboxes.
    Returns dicts: {_imap_id, from, subject, date, message_id}.
    """
    uids = _search_uids(conn, since=since, last_uid=last_uid)
    total = len(uids)
    envelopes: list[dict] = []
    fetch_spec = "(UID BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE MESSAGE-ID)])"

    for batch in _chunks(uids, ENVELOPE_BATCH):
        typ, data = conn.uid("FETCH", b",".join(batch), fetch_spec)
        if typ == "OK":
            for uid, raw in _iter_fetch(data):
                hdr = email.message_from_bytes(raw)
                envelopes.append({
                    "_imap_id": uid,
                    "from": _decode(hdr.get("From")),
                    "subject": _decode(hdr.get("Subject")),
                    "date": (hdr.get("Date") or "").strip(),
                    "message_id": (hdr.get("Message-ID") or "").strip(),
                })
        if progress_cb:
            progress_cb(len(envelopes), total)
        time.sleep(BATCH_SLEEP)

    return envelopes


# ---------------------------------------------------------------------------
# Body fetch (full RFC822 + attachments)
# ---------------------------------------------------------------------------

def _extract_body_text(msg: Message) -> str:
    """Return the message's plain-text body (preferring text/plain over html)."""
    plain, html = None, None
    if msg.is_multipart():
        for part in msg.walk():
            if part.is_multipart():
                continue
            disp = str(part.get("Content-Disposition") or "")
            if "attachment" in disp.lower():
                continue
            ctype = part.get_content_type()
            if ctype == "text/plain" and plain is None:
                plain = _part_text(part)
            elif ctype == "text/html" and html is None:
                html = _part_text(part)
    else:
        if msg.get_content_type() == "text/html":
            html = _part_text(msg)
        else:
            plain = _part_text(msg)

    if plain:
        return plain
    if html:
        return re.sub(r"<[^>]+>", " ", html)
    return ""


def _part_text(part: Message) -> str:
    try:
        payload = part.get_payload(decode=True)
        if payload is None:
            return ""
        charset = part.get_content_charset() or "utf-8"
        return payload.decode(charset, errors="replace")
    except Exception:
        return ""


def _extract_attachments(msg: Message) -> list[dict]:
    """Return attachments as {filename, content_type, data} dicts."""
    attachments: list[dict] = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        filename = part.get_filename()
        disp = str(part.get("Content-Disposition") or "")
        if not filename and "attachment" not in disp.lower():
            continue
        data = part.get_payload(decode=True)
        if data is None:
            continue
        attachments.append({
            "filename": _decode(filename) or "attachment",
            "content_type": part.get_content_type(),
            "data": data,
        })
    return attachments


def fetch_bodies(
    conn: imaplib.IMAP4_SSL,
    imap_ids: list[bytes],
    progress_cb: Optional[Callable[[int, int], None]] = None,
    reconnect: Optional[Callable[[], "imaplib.IMAP4_SSL"]] = None,
) -> list[dict]:
    """Fetch full bodies + attachments for the given UIDs (batched).

    Returns dicts: {_imap_id, message_id, body_text, attachments}. PDF
    attachments are returned as raw bytes for the caller to route to the
    existing extraction engine — they are not parsed here.

    If `reconnect` is given, a dropped socket (Gmail closes idle/long-lived
    IMAP connections — surfacing as IMAP4.abort or OSError) is recovered once
    per batch by reconnecting and retrying that batch.
    """
    total = len(imap_ids)
    out: list[dict] = []

    for batch in _chunks(imap_ids, BODY_BATCH):
        data = None
        for attempt in range(2):
            try:
                typ, data = conn.uid("FETCH", b",".join(batch), "(UID BODY.PEEK[])")
                if typ != "OK":
                    data = None
                break
            except (imaplib.IMAP4.abort, imaplib.IMAP4.error, OSError) as exc:
                if reconnect is None or attempt == 1:
                    raise
                logger.warning("Body fetch dropped (%s); reconnecting", exc)
                conn = reconnect()
        if data:
            for uid, raw in _iter_fetch(data):
                msg = email.message_from_bytes(raw)
                out.append({
                    "_imap_id": uid,
                    "message_id": (msg.get("Message-ID") or "").strip(),
                    "body_text": _extract_body_text(msg),
                    "attachments": _extract_attachments(msg),
                })
        if progress_cb:
            progress_cb(len(out), total)
        time.sleep(BATCH_SLEEP)

    return out


# ---------------------------------------------------------------------------
# Sender helpers
# ---------------------------------------------------------------------------

def sender_domain(from_header: str) -> str:
    """Extract just the sender domain from a From header.

    `"Alex Smith" <alex@gmail.com>` -> `gmail.com`. The display name (which
    may contain a person's name) is dropped before anything goes to the cloud.
    """
    _, addr = parseaddr(from_header or "")
    return addr.rsplit("@", 1)[-1].lower().strip() if "@" in addr else ""
