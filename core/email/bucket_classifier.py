"""Stage 4 — Bucket classifier: coarse on-device routing (no network).

Assigns each KEEP email a coarse bucket that decides how it is processed:

- ``attachment`` — the value is in a PDF, not the body ("statement attached").
  The PDF is routed to JAR's existing extraction engine.
- ``financial`` — looks like a settled money event (bank/payment/subscription/
  retail sender, or a transaction-style subject). Sent to the LLM extractor;
  db_writer makes the final settled-vs-forward call.
- ``document`` — everything else (legal/insurance/tax/medical prose, notices).

The fine-grained settled-vs-forward routing happens later in db_writer based on
the extracted fields; this stage only picks the processing path.
"""
from __future__ import annotations

BUCKET_ATTACHMENT = "attachment"
BUCKET_FINANCIAL = "financial"
BUCKET_DOCUMENT = "document"

SHORT_BODY_THRESHOLD = 200  # chars; below this + a PDF == attachment-only email

STRUCTURED_SENDERS: set[str] = {
    # Banks / cards
    "bankofamerica.com", "bofa.com", "chase.com", "jpmorgan.com",
    "wellsfargo.com", "citi.com", "citibank.com", "americanexpress.com",
    "discover.com", "capitalone.com", "usbank.com", "tdbank.com", "ally.com",
    # Payments
    "venmo.com", "paypal.com", "cash.app", "zelle.com",
    # Subscriptions
    "netflix.com", "spotify.com", "hulu.com", "amazon.com", "apple.com",
    "google.com", "microsoft.com",
    # Utilities / telecom
    "coned.com", "pseg.com", "verizon.com", "att.com", "t-mobile.com",
    "comcast.com", "xfinity.com",
    # Retail receipts
    "walmart.com", "target.com", "costco.com", "wholefoodsmarket.com",
}

STRUCTURED_SUBJECTS: tuple[str, ...] = (
    "transaction alert", "your receipt", "order confirmation",
    "payment confirmation", "your statement", "amount due",
    "appointment confirmed", "booking confirmed", "your order",
    "has shipped", "out for delivery", "delivered",
    "subscription renewal", "your invoice", "payment received",
)


def _has_pdf(attachments: list[dict]) -> bool:
    for att in attachments or []:
        if att.get("content_type") == "application/pdf":
            return True
        if (att.get("filename") or "").lower().endswith(".pdf"):
            return True
    return False


def classify(
    body_text: str,
    sender_domain: str,
    subject: str,
    attachments: list[dict] | None = None,
) -> str:
    """Return the coarse bucket for an email."""
    body = body_text or ""
    domain = (sender_domain or "").lower()
    subj = (subject or "").lower()

    if len(body.strip()) < SHORT_BODY_THRESHOLD and _has_pdf(attachments or []):
        return BUCKET_ATTACHMENT

    if domain in STRUCTURED_SENDERS or any(kw in subj for kw in STRUCTURED_SUBJECTS):
        return BUCKET_FINANCIAL

    return BUCKET_DOCUMENT
