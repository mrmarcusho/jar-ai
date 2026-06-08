"""Redact retrieved excerpts immediately before they cross the network to the cloud
LLM. Shared by the message and email search tools. Reversal happens at answer time
via RedactionEngine.detokenize() on the model's final text.
"""
from __future__ import annotations

from typing import Optional

from core.privacy.redactor import RedactionEngine


def redact_rows(engine: RedactionEngine, rows: list, text_fields: list,
                seed_names: Optional[list] = None) -> list:
    """Return copies of `rows` with `text_fields` redacted via `engine`.

    `seed_names` (e.g. resolved contact names) are pre-registered so they tokenize
    deterministically rather than relying on NER. Non-listed fields pass through
    unchanged. The engine's TokenStore is the reversal key — keep it for the session.
    """
    for name in seed_names or []:
        engine.register_person(name)
    redacted: list = []
    for row in rows:
        new_row = dict(row)
        for field in text_fields:
            if isinstance(new_row.get(field), str):
                new_row[field] = engine.redact(new_row[field])
        redacted.append(new_row)
    return redacted
