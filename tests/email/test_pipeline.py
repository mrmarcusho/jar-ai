"""End-to-end pipeline test with IMAP and the embedder fully mocked.

Asserts the whole flow: fetch envelopes -> reconnect -> fetch bodies -> embed
subject+body -> upsert into LanceDB, plus dedup on re-scan and the summary counts.
"""
from core.email import embedder, imap_client, pipeline, vector_store


class _DummyConn:
    untagged_responses = {}  # imaplib stores UIDVALIDITY here after SELECT

    def logout(self):
        pass


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
        "105": {"_imap_id": "105", "message_id": "<promo-105@shopnow.example>",
                "body_text": "50% off everything!", "attachments": []},
    }


def _patch(monkeypatch, envelopes, bodies, fake_embedder):
    monkeypatch.setattr(pipeline.imap_client, "connect_saved", lambda mailbox="INBOX": _DummyConn())
    monkeypatch.setattr(
        pipeline.imap_client, "fetch_envelopes",
        lambda conn, since=None, last_uid=None, progress_cb=None: envelopes,
    )
    monkeypatch.setattr(
        pipeline.imap_client, "fetch_bodies",
        lambda conn, ids, progress_cb=None, reconnect=None: [bodies[i] for i in ids if i in bodies],
    )


def test_full_scan_counts(monkeypatch, envelopes, body_text, fake_embedder, lance_dir):
    bodies = _build_bodies(body_text)
    _patch(monkeypatch, envelopes, bodies, fake_embedder)

    summary = pipeline.run_scan(db_dir=lance_dir)

    # All 5 messages are fetched, embedded, and stored — no triage drops anything.
    assert summary["emails_fetched"] == 5
    assert summary["embedded"] == 5
    assert summary["stored"] == 5
    assert summary["duplicates"] == 0
    assert summary["errors"] == 0
    assert vector_store.count(db_dir=lance_dir) == 5


def test_rows_carry_raw_text_and_domain_only_sender(monkeypatch, envelopes, body_text, fake_embedder, lance_dir):
    bodies = _build_bodies(body_text)
    _patch(monkeypatch, envelopes, bodies, fake_embedder)

    pipeline.run_scan(db_dir=lance_dir)

    table = vector_store.open_table(db_dir=lance_dir)
    rows = {r["message_id"]: r for r in table.to_arrow().to_pylist()}

    chase = rows["<chase-101@chase.com>"]
    # Body is stored raw (no redaction in the local-embedding flow).
    assert chase["body"] == body_text("chase_transaction.txt")
    # Sender is reduced to its domain (display name dropped).
    assert chase["sender"] == "chase.com"
    assert chase["subject"] == "Transaction alert: card ending 1234"
    # Vector has the expected dimensionality.
    assert len(chase["vector"]) == embedder.EMBED_DIM


def test_rescan_dedupes_by_message_id(monkeypatch, envelopes, body_text, fake_embedder, lance_dir):
    bodies = _build_bodies(body_text)
    _patch(monkeypatch, envelopes, bodies, fake_embedder)

    pipeline.run_scan(db_dir=lance_dir)
    summary2 = pipeline.run_scan(db_dir=lance_dir)

    # Second pass updates the same rows rather than inserting new ones.
    assert summary2["stored"] == 0
    assert summary2["duplicates"] == 5
    assert vector_store.count(db_dir=lance_dir) == 5


def test_missing_message_id_falls_back_to_stable_id(monkeypatch, envelopes, body_text, fake_embedder, lance_dir):
    bodies = _build_bodies(body_text)
    # Strip the Message-ID from one body and its envelope.
    bodies["101"]["message_id"] = ""
    for env in envelopes:
        if env["_imap_id"] == "101":
            env["message_id"] = ""
    _patch(monkeypatch, envelopes, bodies, fake_embedder)

    pipeline.run_scan(db_dir=lance_dir)

    ids = {r["message_id"] for r in vector_store.open_table(db_dir=lance_dir).to_arrow().to_pylist()}
    assert any(i.startswith("imap:") for i in ids)
    assert vector_store.count(db_dir=lance_dir) == 5


def test_last_imap_date_is_newest_message(monkeypatch, envelopes, body_text, fake_embedder, lance_dir):
    bodies = _build_bodies(body_text)
    _patch(monkeypatch, envelopes, bodies, fake_embedder)

    summary = pipeline.run_scan(db_dir=lance_dir)
    # Newest fixture date is 20 Jan 2026 (the promo).
    assert summary["last_imap_date"] == "20-Jan-2026"
