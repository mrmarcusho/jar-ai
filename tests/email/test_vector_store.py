"""Unit tests for the LanceDB vector store wrapper."""
from core.email import embedder, vector_store


def _rec(message_id, subject="s", body="b"):
    return {
        "message_id": message_id,
        "sender": "example.com",
        "subject": subject,
        "date": "Wed, 14 Jan 2026 09:15:00 -0500",
        "body": body,
        "vector": [0.1] * embedder.EMBED_DIM,
    }


def test_count_empty(lance_dir):
    assert vector_store.count(db_dir=lance_dir) == 0


def test_upsert_then_count(lance_dir):
    written = vector_store.upsert([_rec("<a>"), _rec("<b>")], db_dir=lance_dir)
    assert written == 2
    assert vector_store.count(db_dir=lance_dir) == 2


def test_upsert_dedupes_by_message_id(lance_dir):
    vector_store.upsert([_rec("<a>", subject="first")], db_dir=lance_dir)
    vector_store.upsert([_rec("<a>", subject="second")], db_dir=lance_dir)

    assert vector_store.count(db_dir=lance_dir) == 1
    row = vector_store.open_table(db_dir=lance_dir).to_arrow().to_pylist()[0]
    assert row["subject"] == "second"  # updated in place


def test_upsert_empty_is_noop(lance_dir):
    assert vector_store.upsert([], db_dir=lance_dir) == 0
    assert vector_store.count(db_dir=lance_dir) == 0


def test_search_returns_rows_without_vector(lance_dir):
    vector_store.upsert([_rec("<a>"), _rec("<b>")], db_dir=lance_dir)
    results = vector_store.search([0.1] * embedder.EMBED_DIM, limit=5, db_dir=lance_dir)
    assert len(results) == 2
    assert all("vector" not in r for r in results)
    assert {r["message_id"] for r in results} == {"<a>", "<b>"}
