from core.embeddings import EMBED_DIM
from core.messages import vector_store


def test_upsert_and_count_and_search(tmp_path):
    rows = [{
        "guid": "g1", "text": "dentist appt tuesday", "contact_name": "Dr. Smith",
        "is_from_me": 0, "is_group": False, "chat_id": 1,
        "date": "2024-01-01T00:00:00+00:00", "vector": [0.1] * EMBED_DIM,
    }]
    assert vector_store.upsert(rows, db_dir=tmp_path) == 1
    assert vector_store.count(db_dir=tmp_path) == 1
    # re-upsert same guid does not duplicate
    vector_store.upsert(rows, db_dir=tmp_path)
    assert vector_store.count(db_dir=tmp_path) == 1
    hits = vector_store.search([0.1] * EMBED_DIM, limit=5, db_dir=tmp_path)
    assert hits and hits[0]["guid"] == "g1" and "vector" not in hits[0]
