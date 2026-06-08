from core.messages import pipeline
from tests.test_chat_db import _make_chat_db  # reuse fixture builder


def test_run_full_scan(tmp_path, monkeypatch):
    db = tmp_path / "chat.db"
    _make_chat_db(db)
    monkeypatch.setattr(pipeline.contacts, "build_contact_map",
                        lambda home=None: {"+16175550142": "Dr. Smith"})
    monkeypatch.setattr(pipeline.embeddings, "embed_texts",
                        lambda texts, **k: [[0.1] * 1024 for _ in texts])
    summary = pipeline.run_full_scan(chat_db_path=db, db_dir=tmp_path)
    assert summary["stored"] == 2          # 2 bodies, tapback skipped
    assert summary["last_rowid"] == 2
    assert summary["last_guid"] == "g2"
    from core.messages import vector_store
    rows = vector_store.search([0.1] * 1024, limit=5, db_dir=tmp_path)
    names = {r["contact_name"] for r in rows}
    assert "Dr. Smith" in names and "Me" in names
