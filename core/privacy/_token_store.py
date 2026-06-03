"""In-memory token ↔ PII mapping for one extraction session.

Never persisted or logged. Created fresh for each extract_statement() call.
"""
from __future__ import annotations

from collections import defaultdict


class TokenStore:
    """Maps PII values to anonymization tokens and back.

    - Fixed tokens (e.g. [ACCT_HOLDER]) are reserved at init time from the
      household profile and always map to a canonical name.
    - Dynamic tokens (e.g. [PERSON_1]) are created on demand during NER and
      map to the exact text span that was found.

    The same input value always yields the same token within a session
    (idempotent), so repeated occurrences of an account number collapse to one
    token and detokenize correctly.
    """

    def __init__(self) -> None:
        self._token_to_value: dict[str, str] = {}   # "[PERSON_1]" → "Sarah Johnson"
        self._value_to_token: dict[str, str] = {}   # "sarah johnson" (lower) → "[PERSON_1]"
        self._counters: defaultdict[str, int] = defaultdict(int)

    # ------------------------------------------------------------------
    # Fixed tokens (profile-based, reserved at engine init)
    # ------------------------------------------------------------------

    def reserve(self, token: str, canonical_value: str) -> None:
        """Register a fixed token with its canonical restoration value.

        Also registers the lowercased value so Pass 2 (Presidio) can
        recognise already-redacted spans and skip them.
        """
        self._token_to_value[token] = canonical_value
        self._value_to_token[canonical_value.lower()] = token

    # ------------------------------------------------------------------
    # Dynamic tokens (NER-based, created during redaction)
    # ------------------------------------------------------------------

    def get_or_create(self, entity_type: str, found_text: str) -> str:
        """Return the token for found_text, creating one if new.

        entity_type becomes part of the token name, e.g. 'PERSON' → '[PERSON_1]'.
        """
        key = found_text.lower()
        if key in self._value_to_token:
            return self._value_to_token[key]

        self._counters[entity_type] += 1
        token = f"[{entity_type}_{self._counters[entity_type]}]"
        self._value_to_token[key] = token
        self._token_to_value[token] = found_text
        return token

    # ------------------------------------------------------------------
    # Detokenization
    # ------------------------------------------------------------------

    def original(self, token: str) -> str | None:
        """Reverse lookup: token → original value. None if unknown."""
        return self._token_to_value.get(token)

    def all_tokens(self) -> dict[str, str]:
        """Return a copy of the full token → value mapping."""
        return dict(self._token_to_value)
