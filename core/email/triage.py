"""Stage 3 — Triage: decide KEEP/SKIP per email before bodies are fetched.

Two passes:

1. **Local prefilter (no network):** combine sender-domain AND subject-line
   keyword signals in both directions. Known-good senders or finance/health/etc.
   subject keywords auto-KEEP; clear marketing/social/newsletter senders or
   subjects auto-SKIP. KEEP wins on conflict (fail-safe — better to keep a
   marginal email than drop an important one).
2. **Cloud pass (Claude Haiku):** only the emails the prefilter couldn't decide
   are sent to Haiku, in batches, with redacted subjects and sender domain only
   (display names are never sent). On any failure the batch defaults to KEEP.
"""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Callable, Optional

from core.email.imap_client import sender_domain

logger = logging.getLogger("jar.email.triage")

HAIKU_MODEL = "claude-haiku-4-5-20251001"
TRIAGE_BATCH = 500
MAX_RETRIES = 5
BACKOFF_START = 1.0
BACKOFF_MAX = 60.0

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
ERROR_LOG = PROJECT_ROOT / "doc" / "email_triage_errors.log"

# ---------------------------------------------------------------------------
# Prefilter lists
# ---------------------------------------------------------------------------

KEEP_DOMAINS: set[str] = {
    # Banks / cards
    "bankofamerica.com", "bofa.com", "chase.com", "jpmorgan.com",
    "wellsfargo.com", "citi.com", "citibank.com", "americanexpress.com",
    "discover.com", "capitalone.com", "usbank.com", "tdbank.com", "ally.com",
    # Payments
    "venmo.com", "paypal.com", "cash.app", "zelle.com",
    # Subscriptions / billers
    "netflix.com", "spotify.com", "hulu.com", "apple.com", "microsoft.com",
    # Utilities / telecom
    "coned.com", "pseg.com", "verizon.com", "att.com", "t-mobile.com",
    "comcast.com", "xfinity.com",
    # Healthcare / insurance
    "cigna.com", "aetna.com", "bcbs.com", "unitedhealthcare.com",
    # Government / legal
    "irs.gov", "ssa.gov", "dmv.org", "usa.gov",
}

KEEP_SUBJECT_KEYWORDS: tuple[str, ...] = (
    "statement", "receipt", "invoice", "payment", "transaction alert",
    "amount due", "your bill", "past due", "order confirmed", "order confirmation",
    "your order", "has shipped", "out for delivery", "delivered",
    "subscription", "renewal", "appointment", "lab results", "explanation of benefits",
    "claim", "prescription", "wire transfer", "tax", "w-2", "1099", "payroll",
    "mortgage", "rent", "hoa", "policy", "refund",
)

SKIP_DOMAINS: set[str] = {
    "facebookmail.com", "facebook.com", "instagram.com", "mail.instagram.com",
    "linkedin.com", "twitter.com", "x.com", "pinterest.com", "tiktok.com",
    "reddit.com", "quora.com", "medium.com", "substack.com",
}

SKIP_SUBJECT_KEYWORDS: tuple[str, ...] = (
    "unsubscribe", "% off", "newsletter", "digest", "deal of the day",
    "today's deals", "flash sale", "limited time", "save big", "don't miss",
    "new arrivals", "trending now", "webinar", "follow us", "you have a new",
    "liked your", "commented on", "new follower",
)


def _matches_any(subject: str, keywords) -> bool:
    s = subject.lower()
    return any(kw in s for kw in keywords)


def prefilter_label(domain: str, subject: str) -> Optional[str]:
    """Local KEEP/SKIP decision, or None when uncertain (needs the cloud).

    KEEP wins on conflict so a promotional subject from a real bank still gets
    kept rather than discarded.
    """
    domain = (domain or "").lower()
    subject = subject or ""

    keep = domain in KEEP_DOMAINS or _matches_any(subject, KEEP_SUBJECT_KEYWORDS)
    if keep:
        return "KEEP"
    skip = domain in SKIP_DOMAINS or _matches_any(subject, SKIP_SUBJECT_KEYWORDS)
    if skip:
        return "SKIP"
    return None


# ---------------------------------------------------------------------------
# Cloud pass
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are triaging emails for a personal finance and life assistant.
Label each email KEEP or SKIP.

KEEP if the email relates to any of:
- Financial: bank alerts, statements, receipts, invoices, wire transfers,
  investment summaries, tax documents, loan notices
- Healthcare: appointments, lab results, EOBs, prescriptions, insurance
  claims, medical bills, FSA/HSA notices
- Travel: flight, hotel, car rental confirmations, itineraries
- Orders: purchases, shipping, delivery confirmations, returns, refunds
- Employment: payroll, benefits, 401k, HR notices, offer letters, W-2 delivery
- Housing: mortgage, rent, utilities, insurance, HOA, property tax, refinance
- Legal/government: IRS, DMV, court, licenses, contracts, estate
- Subscriptions: any recurring service the person pays for
- Appointments: doctor, dentist, legal, financial advisor, vet
- Rewards: loyalty points, miles expiry, cashback notices

SKIP if the email is:
- Promotional or marketing (even from real companies like Amazon or Apple)
- Newsletters, blog posts, or content digests
- Spam or scams
- Social media notifications (Facebook, Instagram, LinkedIn, Twitter)
- Generic product announcements with no personal transaction or action
- News digests

When in doubt, KEEP. It is better to keep a marginally useful email than to skip
something important.

Respond with JSON only. No explanation. No markdown. No preamble."""


def _build_user_prompt(batch: list[dict]) -> str:
    lines = ["Label each email KEEP or SKIP.", ""]
    for i, env in enumerate(batch, 1):
        lines.append(
            f"{i}. FROM: {env['domain']} | SUBJECT: {env['subject']} | DATE: {env['date']}"
        )
    lines.append("")
    lines.append('{"labels": ["KEEP", "SKIP", ...]}')
    return "\n".join(lines)


def _log_error(message: str) -> None:
    try:
        ERROR_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(ERROR_LOG, "a") as f:
            f.write(message.rstrip() + "\n")
    except OSError:
        logger.warning("Could not write triage error log: %s", message)


def _parse_labels(text: str, expected: int) -> Optional[list[str]]:
    """Parse a {"labels": [...]} response, normalizing to KEEP/SKIP."""
    try:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        payload = json.loads(match.group(0) if match else text)
        labels = payload["labels"]
    except (json.JSONDecodeError, KeyError, TypeError, AttributeError):
        return None
    if not isinstance(labels, list) or len(labels) != expected:
        return None
    return ["SKIP" if str(lbl).strip().upper() == "SKIP" else "KEEP" for lbl in labels]


def _call_haiku_batch(client, model: str, batch: list[dict]) -> list[str]:
    """Triage one batch via Haiku. Fail-safe: any failure → all KEEP."""
    user_prompt = _build_user_prompt(batch)
    backoff = BACKOFF_START
    last_err = None

    for attempt in range(MAX_RETRIES):
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )
            text = resp.content[0].text
            labels = _parse_labels(text, len(batch))
            if labels is not None:
                return labels
            _log_error(f"Unparseable triage response ({len(batch)} envelopes): {text[:500]}")
            return ["KEEP"] * len(batch)  # fail-safe, no retry on a clean response
        except Exception as exc:  # API/network error — retry with backoff
            last_err = exc
            if attempt < MAX_RETRIES - 1:
                time.sleep(backoff)
                backoff = min(backoff * 2, BACKOFF_MAX)

    _log_error(f"Triage API failed after {MAX_RETRIES} retries ({len(batch)} envelopes): {last_err}")
    return ["KEEP"] * len(batch)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def triage(
    envelopes: list[dict],
    client=None,
    redactor=None,
    model: str = HAIKU_MODEL,
    batch_size: int = TRIAGE_BATCH,
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> list[str]:
    """Return a KEEP/SKIP label per envelope, aligned to input order.

    Prefilter decides what it can locally; only the uncertain remainder is sent
    to Haiku. If no `client` is given, uncertain emails default to KEEP.
    """
    labels: list[Optional[str]] = [None] * len(envelopes)
    uncertain: list[tuple[int, dict]] = []

    for idx, env in enumerate(envelopes):
        domain = sender_domain(env.get("from", ""))
        decision = prefilter_label(domain, env.get("subject", ""))
        if decision is not None:
            labels[idx] = decision
        else:
            redacted_subject = env.get("subject", "")
            if redactor is not None:
                redacted_subject = redactor.redact(redacted_subject)
            uncertain.append((idx, {
                "domain": domain,
                "subject": redacted_subject,
                "date": env.get("date", ""),
            }))

    total = len(uncertain)
    done = 0
    if client is not None and uncertain:
        for start in range(0, total, batch_size):
            chunk = uncertain[start:start + batch_size]
            batch_labels = _call_haiku_batch(client, model, [e for _, e in chunk])
            for (idx, _), label in zip(chunk, batch_labels):
                labels[idx] = label
            done += len(chunk)
            if progress_cb:
                progress_cb(done, total)
    else:
        for idx, _ in uncertain:
            labels[idx] = "KEEP"  # fail-safe when no cloud client available

    return [lbl or "KEEP" for lbl in labels]
