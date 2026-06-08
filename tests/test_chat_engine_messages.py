import sqlite3

from app.chat.chat_engine import JarChat
from core.database.migrate import ensure_schema


def _engine(tmp_path, monkeypatch):
    db = tmp_path / "jar.db"
    c = sqlite3.connect(db)
    ensure_schema(c)
    c.close()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    return JarChat(db_path=str(db))


def test_search_messages_redacts_excerpts(tmp_path, monkeypatch):
    jc = _engine(tmp_path, monkeypatch)
    monkeypatch.setattr("core.embeddings.embed_texts", lambda t, **k: [[0.1] * 1024])
    monkeypatch.setattr(
        "core.messages.vector_store.search",
        lambda v, limit=5, db_dir=None: [{
            "guid": "g1", "contact_name": "Dr. Smith", "is_from_me": 0,
            "is_group": False, "date": "2024-01-01T00:00:00+00:00",
            "text": "Dr. Smith: appt is Tuesday, call 617-555-0142",
        }],
    )
    result = jc._search_messages("dentist", limit=5)
    body = result["messages"][0]["text"]
    assert "Dr. Smith" not in body and "617-555-0142" not in body
    # token reverses through the session engine
    assert jc.redaction.detokenize(result["messages"][0]["contact_name"]) == "Dr. Smith"


def test_email_path_now_redacts(tmp_path, monkeypatch):
    jc = _engine(tmp_path, monkeypatch)
    monkeypatch.setattr("core.embeddings.embed_texts", lambda t, **k: [[0.1] * 1024])
    monkeypatch.setattr(
        "core.email.vector_store.search",
        lambda v, limit=5, db_dir=None: [{
            "sender": "clinic.com", "subject": "appt", "date": "d",
            "body": "Reach me at john@x.com",
        }],
    )
    result = jc._search_emails("appt", limit=5)
    assert "john@x.com" not in result["emails"][0]["body"]
