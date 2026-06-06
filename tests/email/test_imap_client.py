"""Tests for imap_client: parsing + credential storage (no real IMAP)."""
import email as email_lib

import pytest

from core.email import imap_client
from core.email.imap_client import (
    IMAPConfigError, _extract_attachments, _extract_body_text,
    detect_server, resolve_server, sender_domain,
)


# ---------------------------------------------------------------------------
# Sender / server helpers
# ---------------------------------------------------------------------------

def test_sender_domain_strips_display_name():
    assert sender_domain('"Alex Smith" <alex@gmail.com>') == "gmail.com"


def test_sender_domain_empty():
    assert sender_domain("") == ""
    assert sender_domain("not-an-address") == ""


def test_detect_server_known_domain():
    assert detect_server("someone@gmail.com") == ("imap.gmail.com", 993)


def test_detect_server_unknown_domain():
    assert detect_server("someone@unknown.example") is None


def test_resolve_server_explicit_host():
    assert resolve_server("a@unknown.example", "imap.x.example", 1234) == ("imap.x.example", 1234)


def test_resolve_server_unknown_raises():
    with pytest.raises(IMAPConfigError):
        resolve_server("a@unknown.example")


# ---------------------------------------------------------------------------
# Body / attachment parsing
# ---------------------------------------------------------------------------

_MULTIPART = b"""From: sender@example.com
Subject: Test
Content-Type: multipart/mixed; boundary="BOUND"

--BOUND
Content-Type: text/plain; charset="utf-8"

Hello plain body.
--BOUND
Content-Type: application/pdf
Content-Disposition: attachment; filename="statement.pdf"
Content-Transfer-Encoding: base64

JVBERi0xLjQK
--BOUND--
"""


def test_extract_body_prefers_plain_text():
    msg = email_lib.message_from_bytes(_MULTIPART)
    assert "Hello plain body." in _extract_body_text(msg)


def test_extract_attachments_returns_pdf():
    msg = email_lib.message_from_bytes(_MULTIPART)
    atts = _extract_attachments(msg)
    assert len(atts) == 1
    assert atts[0]["filename"] == "statement.pdf"
    assert atts[0]["content_type"] == "application/pdf"
    assert atts[0]["data"].startswith(b"%PDF")


def test_extract_body_html_fallback_strips_tags():
    raw = b"""From: a@b.com
Content-Type: text/html; charset="utf-8"

<html><body><p>Hi <b>there</b></p></body></html>
"""
    msg = email_lib.message_from_bytes(raw)
    text = _extract_body_text(msg)
    assert "Hi" in text and "there" in text
    assert "<b>" not in text


# ---------------------------------------------------------------------------
# Credential token file
# ---------------------------------------------------------------------------

def test_save_load_clear_account(monkeypatch, tmp_path):
    token = tmp_path / "imap_account.json"
    monkeypatch.setattr(imap_client, "TOKEN_PATH", token)

    assert imap_client.load_account() is None
    assert imap_client.is_connected() is False

    imap_client.save_account("me@gmail.com", "secret", "imap.gmail.com", 993)
    assert imap_client.is_connected() is True
    acct = imap_client.load_account()
    assert acct["username"] == "me@gmail.com"
    assert acct["password"] == "secret"

    imap_client.clear_account()
    assert imap_client.load_account() is None
