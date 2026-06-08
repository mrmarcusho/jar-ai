"""Backwards-compatible re-export. The embedder now lives in core.embeddings so both
the email and message pipelines share one model load and one vector space."""
from core.embeddings import (  # noqa: F401
    MODEL_NAME, EMBED_DIM, DEFAULT_BATCH, MAX_SEQ_LENGTH, MAX_CHARS,
    get_model, embed_texts,
)

__all__ = [
    "MODEL_NAME", "EMBED_DIM", "DEFAULT_BATCH", "MAX_SEQ_LENGTH",
    "MAX_CHARS", "get_model", "embed_texts",
]
