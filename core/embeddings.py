"""Local text embeddings via BAAI/bge-m3 (1024-dim, fully on-device).

Shared by the email and message pipelines so the large model is loaded at most once
and both stores live in the same vector space. The model is downloaded once (~2.3GB)
into the HuggingFace cache and then runs entirely on the local machine — text is never
sent to any cloud service, which is why these pipelines need no redaction at ingest.
"""
from __future__ import annotations

import logging
from typing import List

logger = logging.getLogger("jar.embeddings")

MODEL_NAME = "BAAI/bge-m3"
EMBED_DIM = 1024
DEFAULT_BATCH = 8

# bge-m3 supports up to 8192 tokens, but encoding sequences that long blows past
# the MPS 4GB single-buffer limit (attention is O(seq^2)) and is far more context
# than semantic search needs. Cap the sequence length, and pre-truncate the raw text
# by characters so tokenization itself stays cheap on huge inputs.
MAX_SEQ_LENGTH = 512
MAX_CHARS = 4000

_model = None


def get_model():
    """Lazily load (and cache) the SentenceTransformer model."""
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        logger.info("Loading embedding model %s (first run downloads ~2.3GB)", MODEL_NAME)
        _model = SentenceTransformer(MODEL_NAME)
        _model.max_seq_length = MAX_SEQ_LENGTH
    return _model


def embed_texts(texts: List[str], batch_size: int = DEFAULT_BATCH) -> List[List[float]]:
    """Embed a list of texts into normalized 1024-dim vectors (cosine-ready)."""
    if not texts:
        return []
    model = get_model()
    clipped = [(t or "")[:MAX_CHARS] for t in texts]
    vectors = model.encode(
        clipped,
        batch_size=batch_size,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return [list(map(float, v)) for v in vectors]
