def test_shared_embeddings_exports():
    from core import embeddings
    assert embeddings.EMBED_DIM == 1024
    assert callable(embeddings.embed_texts)
    assert embeddings.embed_texts([]) == []


def test_email_embedder_reexports_shared():
    # Back-compat: existing imports must keep working. Assert functional equivalence
    # rather than object identity — the `from ... import` alias can be rebound if a
    # different test monkeypatches core.embeddings during embedder's first import.
    from core.email import embedder
    from core import embeddings
    assert embedder.EMBED_DIM == embeddings.EMBED_DIM == 1024
    assert callable(embedder.embed_texts) and callable(embedder.get_model)
    assert embedder.embed_texts([]) == []
