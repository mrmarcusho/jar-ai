"""Stage 5 — LLM extractor: turn a KEEP email into structured fields.

Reuses JAR's privacy boundary: the body is redacted with the reversible
``RedactionEngine`` before it leaves the device, Claude Haiku returns JSON over
the *redacted* text, and the result is detokenized so callers see real names
again. The cloud never sees raw PII.

Two schema variants, picked by the coarse bucket from bucket_classifier:

- ``financial`` — a money event: type/date/amount/merchant/direction/status/...
- ``document``  — prose notice: category/date/summary/action_required/deadline.

No regex per-sender parsers — that is deliberately left behind in the v2 pivot.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Optional

from core.email.bucket_classifier import BUCKET_DOCUMENT, BUCKET_FINANCIAL

logger = logging.getLogger("jar.email.extractor")

HAIKU_MODEL = "claude-haiku-4-5-20251001"
MAX_RETRIES = 5
BACKOFF_START = 1.0
BACKOFF_MAX = 60.0
MAX_BODY_CHARS = 12000  # trim runaway bodies before the cloud call

FINANCIAL_SYSTEM_PROMPT = """You extract one financial event from an email body.
The text has had personal names/addresses replaced by [TOKENS] — leave any token
exactly as-is. Amounts and dates are NOT redacted; report them verbatim.

Return JSON only, no markdown, no preamble, with exactly these keys:
{
  "type": "transaction|order|subscription|receipt|charge|payment|bill|invoice|other",
  "date": "YYYY-MM-DD or null",          // when the money moved / event occurred
  "due_date": "YYYY-MM-DD or null",      // when payment is due, if a forward bill
  "amount": number or null,              // absolute value, no currency symbol
  "merchant": "string or null",
  "category": "string or null",          // e.g. groceries, utilities, dining
  "direction": "debit|credit",           // debit = money out, credit = money in
  "status": "settled|forward",           // settled = already happened; forward = upcoming/owed
  "action_required": true or false,
  "deadline": "YYYY-MM-DD or null",
  "summary": "one short sentence",
  "metadata": {}                         // any extra structured detail
}
Use null when a field is unknown. Never invent an amount or date."""

DOCUMENT_SYSTEM_PROMPT = """You summarize one non-transactional email (a notice,
appointment, legal/insurance/tax/medical/government message) into structured form.
The text has had personal names/addresses replaced by [TOKENS] — leave any token
exactly as-is. Dates are NOT redacted; report them verbatim.

Return JSON only, no markdown, no preamble, with exactly these keys:
{
  "category": "legal|insurance|tax|medical|housing|employment|government|appointment|other",
  "date": "YYYY-MM-DD or null",          // the event/notice date
  "amount": number or null,              // any dollar figure mentioned, else null
  "action_required": true or false,
  "deadline": "YYYY-MM-DD or null",
  "summary": "one or two short sentences",
  "metadata": {}                         // e.g. appointment time, provider_type
}
Use null when a field is unknown. Never invent a date or amount."""

_FINANCIAL_KEYS = (
    "type", "date", "due_date", "amount", "merchant", "category",
    "direction", "status", "action_required", "deadline", "summary", "metadata",
)
_DOCUMENT_KEYS = (
    "category", "date", "amount", "action_required", "deadline", "summary", "metadata",
)


def _parse_json(text: str) -> Optional[dict]:
    """Pull the first JSON object out of a model response."""
    try:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        payload = json.loads(match.group(0) if match else text)
    except (json.JSONDecodeError, AttributeError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def _coerce_amount(value) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return abs(float(value))
    if isinstance(value, str):
        cleaned = re.sub(r"[^0-9.\-]", "", value)
        try:
            return abs(float(cleaned)) if cleaned not in ("", "-", ".") else None
        except ValueError:
            return None
    return None


def _coerce_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "1", "required")
    return bool(value)


def _normalize(raw: dict, bucket: str) -> dict:
    """Coerce a parsed model dict into the canonical field set for `bucket`."""
    keys = _DOCUMENT_KEYS if bucket == BUCKET_DOCUMENT else _FINANCIAL_KEYS
    out: dict = {k: raw.get(k) for k in keys}

    out["amount"] = _coerce_amount(out.get("amount"))
    out["action_required"] = _coerce_bool(out.get("action_required"))

    meta = out.get("metadata")
    out["metadata"] = meta if isinstance(meta, dict) else {}

    if bucket != BUCKET_DOCUMENT:
        direction = str(out.get("direction") or "debit").strip().lower()
        out["direction"] = "credit" if direction == "credit" else "debit"
        status = str(out.get("status") or "settled").strip().lower()
        out["status"] = "forward" if status == "forward" else "settled"

    return out


def _empty_result(bucket: str) -> dict:
    """Fail-safe skeleton when the cloud call yields nothing usable."""
    base = _normalize({}, bucket)
    base["summary"] = base.get("summary") or ""
    return base


def _call_haiku(client, model: str, system_prompt: str, user_text: str) -> Optional[dict]:
    """Call Haiku with retry/backoff; return parsed JSON or None on failure."""
    backoff = BACKOFF_START
    last_err = None

    for attempt in range(MAX_RETRIES):
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=1024,
                system=system_prompt,
                messages=[{"role": "user", "content": user_text}],
            )
            parsed = _parse_json(resp.content[0].text)
            if parsed is not None:
                return parsed
            logger.warning("Unparseable extractor response: %s", resp.content[0].text[:300])
            return None  # clean response, just unusable — no retry
        except Exception as exc:  # API/network error — retry with backoff
            last_err = exc
            if attempt < MAX_RETRIES - 1:
                time.sleep(backoff)
                backoff = min(backoff * 2, BACKOFF_MAX)

    logger.warning("Extractor API failed after %d retries: %s", MAX_RETRIES, last_err)
    return None


def extract_email(
    body: str,
    sender_domain: str,
    subject: str,
    bucket: str,
    redactor,
    client,
    model: str = HAIKU_MODEL,
) -> dict:
    """Extract structured fields from one email.

    Redacts the body (reversible), sends the redacted text to Haiku with the
    schema for `bucket`, then detokenizes the result so the caller gets real
    names back. Returns a normalized dict; `_redacted_body` holds the redacted
    body so db_writer can persist it without re-redacting. On any cloud failure
    the result is an empty-but-valid skeleton (the email is not dropped).
    """
    raw_body = (body or "")[:MAX_BODY_CHARS]
    redacted_body = redactor.redact(raw_body)
    redacted_subject = redactor.redact(subject or "")

    is_document = bucket == BUCKET_DOCUMENT
    system_prompt = DOCUMENT_SYSTEM_PROMPT if is_document else FINANCIAL_SYSTEM_PROMPT
    user_text = (
        f"FROM: {sender_domain}\n"
        f"SUBJECT: {redacted_subject}\n\n"
        f"{redacted_body}"
    )

    parsed = _call_haiku(client, model, system_prompt, user_text)
    if parsed is None:
        result = _empty_result(bucket)
    else:
        result = _normalize(parsed, bucket)
        result = redactor.detokenize_dict(result)

    result["bucket"] = bucket
    result["sender_domain"] = sender_domain
    result["_redacted_body"] = redacted_body
    return result
