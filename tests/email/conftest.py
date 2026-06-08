"""Shared fixtures for the email pipeline tests.

Everything here is offline: no IMAP connections, no model downloads. The bge-m3
embedder is replaced with a tiny deterministic stub so tests run instantly, and
LanceDB writes go to a tmp directory.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from core.email import embedder

FIXTURES = Path(__file__).resolve().parent / "fixtures"


# ---------------------------------------------------------------------------
# Fake embedder (no model download) + tmp LanceDB
# ---------------------------------------------------------------------------

def _fake_vector(text: str) -> list[float]:
    """Deterministic 1024-dim vector seeded from the text (no model needed)."""
    h = sum(ord(c) for c in text) or 1
    base = (h % 97) / 97.0
    return [base + (i % 7) * 0.01 for i in range(embedder.EMBED_DIM)]


@pytest.fixture
def fake_embedder(monkeypatch):
    """Patch embedder.embed_texts to a deterministic offline stub."""
    def _embed(texts, batch_size=16):
        return [_fake_vector(t) for t in texts]

    monkeypatch.setattr(embedder, "embed_texts", _embed)
    return _embed


@pytest.fixture
def lance_dir(tmp_path):
    """A fresh, empty LanceDB directory for one test."""
    return tmp_path / "lancedb"


# ---------------------------------------------------------------------------
# Email fixtures loaded from disk
# ---------------------------------------------------------------------------

@pytest.fixture
def envelopes():
    return json.loads((FIXTURES / "envelopes.json").read_text())


@pytest.fixture
def body_text():
    def _load(name):
        return (FIXTURES / "bodies" / name).read_text()
    return _load
