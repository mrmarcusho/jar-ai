"""End-to-end pipeline test with IMAP and Claude fully mocked.

Asserts the whole flow: triage -> bodies -> classify -> extract -> write, plus
the security invariants (no raw body text persisted) and PDF attachment handoff.
"""
import json

from core.email import pipeline
from core.privacy.profile import HouseholdProfile

from tests.email.conftest import FakeClient


class _DummyConn:
    def logout(self):
        pass


def _seed_profile(conn):
    """Seed the household profile so Pass-1 redaction is complete + deterministic.

    Without a profile the redactor falls back to presidio NER only, which can miss
    a bare first name — the real app always has a profile by scan time.
    """
    HouseholdProfile(
        first_name="Marcus", last_name="Ho",
        partner_first="Sarah", partner_last="Ho", city="Boston",
    ).save(conn)
    conn.commit()


def _build_bodies(body_text):
    """Map fixture envelopes -> body dicts the pipeline expects."""
    return {
        "101": {"_imap_id": "101", "message_id": "<chase-101@chase.com>",
                "body_text": body_text("chase_transaction.txt"), "attachments": []},
        "102": {"_imap_id": "102", "message_id": "<netflix-102@netflix.com>",
                "body_text": body_text("netflix_receipt.txt"), "attachments": []},
        "103": {"_imap_id": "103", "message_id": "<appt-103@cityhealth.example>",
                "body_text": body_text("doctor_appointment.txt"), "attachments": []},
        "104": {"_imap_id": "104", "message_id": "<irs-104@irs.gov>",
                "body_text": body_text("irs_notice.txt"), "attachments": []},
    }


def _responder(kw):
    """Single client used for both triage and extraction; branch on system prompt."""
    system = kw.get("system", "")
    user = kw["messages"][0]["content"]
    if "triaging emails" in system:
        import re
        n = len(re.findall(r"(?m)^\d+\. FROM:", user))
        return json.dumps({"labels": ["KEEP"] * n})
    if "extract one financial event" in system:
        return json.dumps({
            "type": "transaction", "date": "2026-01-14", "due_date": None,
            "amount": 86.42, "merchant": "Whole Foods Market", "category": "groceries",
            "direction": "debit", "status": "settled", "action_required": False,
            "deadline": None, "summary": "groceries purchase", "metadata": {},
        })
    # document schema
    return json.dumps({
        "category": "government", "date": "2026-01-19", "amount": None,
        "action_required": True, "deadline": "2026-02-28",
        "summary": "a notice", "metadata": {},
    })


def _patch(monkeypatch, db_path, envelopes, bodies, pdf_calls):
    import sqlite3
    seed_conn = sqlite3.connect(db_path)
    try:
        _seed_profile(seed_conn)
    finally:
        seed_conn.close()
    monkeypatch.setattr(pipeline, "DB_PATH", db_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(pipeline.anthropic, "Anthropic", lambda api_key=None: FakeClient(_responder))
    monkeypatch.setattr(pipeline.imap_client, "connect_saved", lambda mailbox="INBOX": _DummyConn())
    monkeypatch.setattr(
        pipeline.imap_client, "fetch_envelopes",
        lambda conn, since=None, progress_cb=None: envelopes,
    )
    monkeypatch.setattr(
        pipeline.imap_client, "fetch_bodies",
        lambda conn, ids, progress_cb=None, reconnect=None: [bodies[i] for i in ids if i in bodies],
    )
    monkeypatch.setattr(
        pipeline, "extract_statement",
        lambda path, db, **kw: pdf_calls.append(str(path)),
    )


def test_full_scan_counts(monkeypatch, db, envelopes, body_text):
    conn, db_path = db
    bodies = _build_bodies(body_text)
    pdf_calls = []
    _patch(monkeypatch, db_path, envelopes, bodies, pdf_calls)

    summary = pipeline.run_full_scan()

    # 5 envelopes; the promo (105) is prefiltered SKIP, others KEEP
    assert summary["envelopes_fetched"] == 5
    assert summary["skipped"] >= 1
    assert summary["kept"] == 4
    assert summary["events_written"] >= 1     # chase + netflix -> transactions
    assert summary["documents_written"] >= 1  # appointment + irs -> documents
    assert summary["errors"] == 0


def test_no_pii_persisted_and_transactions_carry_no_body(monkeypatch, db, envelopes, body_text):
    conn, db_path = db
    bodies = _build_bodies(body_text)
    _patch(monkeypatch, db_path, envelopes, bodies, [])

    pipeline.run_full_scan()

    txn_dump = "\n".join(
        " ".join(str(c) for c in row) for row in conn.execute("SELECT * FROM transactions")
    )
    doc_dump = "\n".join(
        " ".join(str(c) for c in row) for row in conn.execute("SELECT * FROM documents")
    )
    whole_db = txn_dump + "\n" + doc_dump

    # 1. Household PII must never be persisted anywhere (it is redacted to tokens).
    assert "Marcus Ho" not in whole_db
    assert "Sarah Ho" not in whole_db

    # 2. The ledger stores only structured fields — no email body prose.
    assert "If you do not recognize this transaction" not in txn_dump
    assert "Please arrive 15 minutes early" not in txn_dump

    # 3. documents.body_redacted holds the redacted body (prose kept, PII tokenized).
    redacted = conn.execute(
        "SELECT body_redacted FROM documents WHERE body_redacted LIKE '%appointment%'"
    ).fetchone()
    assert redacted is not None
    assert "[ACCT_HOLDER]" in redacted[0]


def test_duplicates_not_double_inserted(monkeypatch, db, envelopes, body_text):
    conn, db_path = db
    bodies = _build_bodies(body_text)
    _patch(monkeypatch, db_path, envelopes, bodies, [])

    pipeline.run_full_scan()
    first_txn = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    first_doc = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]

    summary2 = pipeline.run_full_scan()
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == first_txn
    assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == first_doc
    assert summary2["duplicates"] >= 1


def test_pdf_attachment_handed_to_extract_statement(monkeypatch, db, envelopes, body_text):
    conn, db_path = db
    bodies = _build_bodies(body_text)
    # Give the Chase email a short body + a PDF -> attachment bucket
    bodies["101"] = {
        "_imap_id": "101", "message_id": "<chase-101@chase.com>",
        "body_text": "Statement attached.",
        "attachments": [{"filename": "stmt.pdf", "content_type": "application/pdf", "data": b"%PDF-1.4"}],
    }
    pdf_calls = []
    _patch(monkeypatch, db_path, envelopes, bodies, pdf_calls)

    summary = pipeline.run_full_scan()
    assert summary["attachments_routed"] == 1
    assert len(pdf_calls) == 1
    assert pdf_calls[0].endswith("stmt.pdf")
