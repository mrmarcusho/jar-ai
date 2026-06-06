"""Tests for the LLM extractor (Claude mocked)."""
import json

from core.email import extractor
from core.email.bucket_classifier import BUCKET_DOCUMENT, BUCKET_FINANCIAL
from core.email.extractor import extract_email

from tests.email.conftest import FakeClient


def _financial_responder(kw):
    # summary echoes a reserved profile token to prove the detokenize round-trip
    return json.dumps({
        "type": "transaction",
        "date": "2026-01-14",
        "due_date": None,
        "amount": 86.42,
        "merchant": "Whole Foods Market",
        "category": "groceries",
        "direction": "debit",
        "status": "settled",
        "action_required": False,
        "deadline": None,
        "summary": "[ACCT_HOLDER] spent at Whole Foods Market",
        "metadata": {},
    })


def _document_responder(kw):
    return json.dumps({
        "category": "government",
        "date": "2026-01-19",
        "amount": 1240.0,
        "action_required": True,
        "deadline": "2026-02-28",
        "summary": "IRS notice CP2000 about a tax discrepancy",
        "metadata": {"notice": "CP2000"},
    })


def test_financial_extraction_preserves_amount_and_date(redactor):
    client = FakeClient(_financial_responder)
    result = extract_email(
        "You spent $86.42 at Whole Foods on Jan 14.",
        "chase.com", "Transaction alert", BUCKET_FINANCIAL,
        redactor, client,
    )
    assert result["amount"] == 86.42
    assert result["date"] == "2026-01-14"
    assert result["direction"] == "debit"
    assert result["status"] == "settled"
    assert result["bucket"] == BUCKET_FINANCIAL


def test_financial_extraction_detokenizes_summary(redactor):
    client = FakeClient(_financial_responder)
    result = extract_email(
        "body", "chase.com", "subject", BUCKET_FINANCIAL, redactor, client,
    )
    # [ACCT_HOLDER] must be restored to the real household name
    assert "Marcus Ho" in result["summary"]
    assert "[ACCT_HOLDER]" not in result["summary"]


def test_document_extraction_uses_document_schema(redactor):
    client = FakeClient(_document_responder)
    result = extract_email(
        "Dear taxpayer ...", "irs.gov", "tax notice", BUCKET_DOCUMENT, redactor, client,
    )
    assert result["category"] == "government"
    assert result["action_required"] is True
    assert result["deadline"] == "2026-02-28"
    assert result["amount"] == 1240.0
    assert "merchant" not in result  # document schema has no merchant field
    assert result["bucket"] == BUCKET_DOCUMENT


def test_redacted_body_attached_and_not_raw(redactor):
    client = FakeClient(_document_responder)
    body = "Dear Marcus Ho, your appointment is confirmed."
    result = extract_email(body, "x.example", "s", BUCKET_DOCUMENT, redactor, client)
    assert "Marcus Ho" not in result["_redacted_body"]
    assert "[ACCT_HOLDER]" in result["_redacted_body"]


def test_api_failure_returns_empty_skeleton(redactor, monkeypatch):
    monkeypatch.setattr(extractor, "MAX_RETRIES", 1)

    def boom(kw):
        raise RuntimeError("network down")

    client = FakeClient(boom)
    result = extract_email("body", "x.example", "s", BUCKET_FINANCIAL, redactor, client)
    assert result["amount"] is None
    assert result["summary"] == ""
    assert result["bucket"] == BUCKET_FINANCIAL
    assert result["action_required"] is False
