"""Tests for the coarse bucket classifier (on-device, no network)."""
from core.email.bucket_classifier import (
    BUCKET_ATTACHMENT, BUCKET_DOCUMENT, BUCKET_FINANCIAL, classify,
)

PDF_ATT = [{"filename": "statement.pdf", "content_type": "application/pdf", "data": b"%PDF"}]


def test_short_body_with_pdf_is_attachment():
    assert classify("Statement attached.", "chase.com", "Your statement", PDF_ATT) == BUCKET_ATTACHMENT


def test_long_body_with_pdf_is_not_attachment():
    body = "x" * 500
    assert classify(body, "chase.com", "Your statement", PDF_ATT) == BUCKET_FINANCIAL


def test_structured_sender_is_financial():
    assert classify("some body text", "chase.com", "hello", None) == BUCKET_FINANCIAL


def test_structured_subject_is_financial():
    assert classify("body", "unknown.example", "your receipt is here", None) == BUCKET_FINANCIAL


def test_prose_is_document():
    assert classify("Dear taxpayer, this is a notice.", "irs.gov", "notice", None) == BUCKET_DOCUMENT


def test_pdf_detected_by_extension_without_content_type():
    atts = [{"filename": "doc.PDF", "content_type": "application/octet-stream", "data": b""}]
    assert classify("short", "x.example", "s", atts) == BUCKET_ATTACHMENT


def test_no_attachments_argument_is_safe():
    assert classify("short body", "x.example", "subject") == BUCKET_DOCUMENT
