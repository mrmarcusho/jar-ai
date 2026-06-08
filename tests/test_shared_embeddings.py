def test_shared_embeddings_exports():
    from core import embeddings
    assert embeddings.EMBED_DIM == 1024
    assert callable(embeddings.embed_texts)
    assert embeddings.embed_texts([]) == []


def test_email_embedder_reexports_shared():
    # Back-compat: existing imports must keep working.
    from core.email import embedder
    from core import embeddings
    assert embedder.EMBED_DIM is embeddings.EMBED_DIM
    assert embedder.embed_texts is embeddings.embed_texts
