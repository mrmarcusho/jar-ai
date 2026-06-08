"""RedactionEngine — two-pass PII scrubber for bank statement text.

Pass 1  Exact / fuzzy matching against the household profile (names, address).
        Also strips names embedded inside P2P payment descriptions (VENMO, ZELLE, etc.).
        These are the highest-confidence matches: the user told us exactly what to look for.

Pass 2  Presidio NER (spaCy en_core_web_sm) + custom bank-statement recognizers.
        Catches residual PII: SSNs, phone numbers, emails, account numbers, routing
        numbers, and names the profile didn't cover (Zelle recipients, etc.).

Both passes work on the *same* TokenStore so tokens are consistent across a
session and every token can be reversed via detokenize().

ALL-CAPS handling: bank statement text is typically ALL-CAPS.  spaCy NER degrades
significantly on all-caps input, so Pass 2 lowercases before analysis and remaps
character positions back to the original text (safe because lower() is a 1-to-1
character map that preserves string indices).
"""
from __future__ import annotations

import logging
import re
from typing import Any

from core.privacy._token_store import TokenStore
from core.privacy.profile import HouseholdProfile

logger = logging.getLogger("jar.privacy")

# ---------------------------------------------------------------------------
# P2P payment app patterns — Pass 1 pre-processing
# Each tuple: (compiled_regex, name_group_index)
# We replace only the name capture group with a token.
# ---------------------------------------------------------------------------
_PAYMENT_APP_RES: list[tuple[re.Pattern, int]] = [
    # VENMO* JOHN SMITH  /  VENMO - JOHN SMITH  /  VENMO JOHN SMITH
    (re.compile(r"(VENMO\s*[\*\-]\s*)([A-Z][A-Z\s\.\-]{2,}[A-Z])(?=\s|$|\d)", re.I), 2),
    # ZELLE PAYMENT TO/FROM JOHN SMITH  /  ZELLE - JOHN SMITH
    (re.compile(r"(ZELLE\s*(?:PAYMENT\s*)?(?:TO|FROM)?\s*[\-\*]?\s*)([A-Z][A-Z\s\.\-]{2,}[A-Z])(?=\s|$|\d)", re.I), 2),
    # CASH APP* JOHN SMITH  (usernames may not have spaces; catch only 2-word forms)
    (re.compile(r"(CASH\s*APP\s*[\*\-]\s*)([A-Z][A-Z\s\.\-]{2,}[A-Z])(?=\s|$|\d)", re.I), 2),
    # SQ * JOHN SMITH PLUMBING → only replace if 2 word capitalised names precede any extra words
    (re.compile(r"(SQ\s*\*\s*)([A-Z][A-Z]+\s+[A-Z][A-Z]+)(?=\s|$)", re.I), 2),
    # PAY TO THE ORDER OF JOHN SMITH  (checks)
    (re.compile(r"(PAY(?:ABLE)?\s+TO\s+(?:THE\s+ORDER\s+OF\s+)?)([A-Z][A-Z\s\.\-]{2,}[A-Z])(?=\s|$|\d)", re.I), 2),
]

# ---------------------------------------------------------------------------
# Pass 3 sweep — deterministic safety net for structured PII
# Each tuple: (compiled_regex, entity_type). Applied to the already-tokenized
# text to guarantee no contiguous structured PII survives NER/dedup gaps (e.g.
# a digit run left exposed after an adjacent span was replaced). Order matters:
# more specific patterns run first so they claim the right entity type.
# ---------------------------------------------------------------------------
_SWEEP_RES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"), "EMAIL_ADDRESS"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "US_SSN"),
    (re.compile(r"\b\d{4}[ -]\d{4}[ -]\d{4}[ -]\d{1,4}\b"), "BANK_ACCOUNT"),
    (re.compile(r"\b\d{5}-\d{4}\b"), "ZIP"),
    (re.compile(r"\b[A-Za-z]{1,2}\d[A-Za-z\d]?[ ]?\d[A-Za-z]{2}\b"), "ZIP"),  # UK postcode
    (re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b"), "IP_ADDRESS"),
    (re.compile(r"\b\d{3,}(?:[ -]\d{3,}){2,}\b"), "BANK_ACCOUNT"),  # grouped acct (3+ groups of 3+)
    (re.compile(r"\b\d{8,}\b"), "BANK_ACCOUNT"),
]

# Matches a redaction token like [PERSON_1] or [ACCT_HOLDER] — used to avoid
# re-redacting token internals during the sweep.
_TOKEN_RE = re.compile(r"\[[A-Z_]+(?:_\d+)?\]")

# Presidio entity types to analyse in Pass 2
_PRESIDIO_ENTITIES = [
    "PERSON",
    "LOCATION",
    "US_SSN",
    "PHONE_NUMBER",
    "EMAIL_ADDRESS",
    "BANK_ACCOUNT",
    "BANK_ROUTING",
    "BANK_ACCT_PARTIAL",
    "ADDRESS",
    "ZIP",
    "TAX_ID",
    "DOB",
    "IP_ADDRESS",
]


class RedactionEngine:
    """Scrubs PII from text before it is sent to the cloud LLM.

    One instance per extraction session.  Never reuse across sessions —
    the internal TokenStore accumulates mappings that must not bleed between
    different statements.
    """

    def __init__(self, profile: HouseholdProfile, lowercase_for_ner: bool = True) -> None:
        self._profile = profile
        # Bank statements are ALL-CAPS (NER needs lowercasing); conversational chat
        # excerpts keep their natural casing so spaCy NER retains capitalization cues.
        self._lowercase_for_ner = lowercase_for_ner
        self._store = TokenStore()
        self._analyzer = None    # lazy — expensive to load
        self._anonymizer = None  # lazy

        # Pre-register profile tokens so detokenize() restores canonical names
        fn = profile.first_name.strip()
        ln = profile.last_name.strip()
        if fn and ln:
            self._store.reserve("[ACCT_HOLDER]", f"{fn} {ln}")
        elif ln:
            self._store.reserve("[ACCT_HOLDER]", ln)
        elif fn:
            self._store.reserve("[ACCT_HOLDER]", fn)

        pfn = profile.partner_first.strip()
        pln = profile.partner_last.strip()
        if pfn and pln:
            self._store.reserve("[PARTNER]", f"{pfn} {pln}")
        elif pln:
            self._store.reserve("[PARTNER]", pln)

        if profile.street:
            self._store.reserve("[ADDRESS]", profile.street.strip())
        if profile.city:
            self._store.reserve("[CITY]", profile.city.strip())

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def redact(self, text: str) -> str:
        """Return text with all detected PII replaced by tokens."""
        text = self._pass1_profile(text)
        text = self._pass1_payment_apps(text)
        text = self._pass2_presidio(text)
        text = self._pass3_sweep(text)
        return text

    def register_person(self, name: str) -> str:
        """Pre-register a known person name so it tokenizes deterministically.

        Used by the cloud-boundary helper to seed resolved contact names before
        redacting excerpts, so we don't rely on NER to catch them. Returns the token.
        """
        name = (name or "").strip()
        if not name:
            return ""
        return self._store.get_or_create("PERSON", name)

    def detokenize(self, text: str) -> str:
        """Replace all [TOKEN] placeholders back with their original values."""
        result = text
        for token, original in self._store.all_tokens().items():
            if token in result:
                result = result.replace(token, original)
        return result

    def redact_dict(self, data: Any) -> Any:
        """Recursively redact all string values in a dict / list structure."""
        if isinstance(data, str):
            return self.redact(data)
        if isinstance(data, dict):
            return {k: self.redact_dict(v) for k, v in data.items()}
        if isinstance(data, list):
            return [self.redact_dict(item) for item in data]
        return data  # int, float, bool, None — untouched

    def detokenize_dict(self, data: Any) -> Any:
        """Recursively detokenize all string values."""
        if isinstance(data, str):
            return self.detokenize(data)
        if isinstance(data, dict):
            return {k: self.detokenize_dict(v) for k, v in data.items()}
        if isinstance(data, list):
            return [self.detokenize_dict(item) for item in data]
        return data

    # ------------------------------------------------------------------
    # Pass 1 — Profile matching
    # ------------------------------------------------------------------

    def _pass1_profile(self, text: str) -> str:
        """Case-insensitive regex replacement for all household name variants.

        Uses word boundaries (\b) so a shorter variant (e.g. last name "Ho") cannot
        match inside a token already placed by a longer variant (e.g. "[ACCT_HOLDER]").
        """
        variants = self._profile.all_name_variants()
        if not variants:
            return text

        counts: dict[str, int] = {}
        for variant, token in variants:
            pattern = re.compile(r"\b" + re.escape(variant) + r"\b", re.IGNORECASE)
            new_text, n = pattern.subn(token, text)
            if n:
                counts[token] = counts.get(token, 0) + n
                text = new_text

        if counts:
            logger.debug("Pass 1 profile: %s", counts)
        return text

    # ------------------------------------------------------------------
    # Pass 1 — Payment app name extraction
    # ------------------------------------------------------------------

    def _pass1_payment_apps(self, text: str) -> str:
        """Replace person names embedded in P2P payment descriptions."""
        for pattern, name_group in _PAYMENT_APP_RES:
            def _replace(m: re.Match) -> str:
                name = m.group(name_group)
                token = self._store.get_or_create("PERSON", name)
                parts = list(m.groups())
                parts[name_group - 1] = token  # groups() is 0-indexed
                return "".join(parts)

            text = pattern.sub(_replace, text)
        return text

    # ------------------------------------------------------------------
    # Pass 2 — Presidio NER
    # ------------------------------------------------------------------

    def _load_presidio(self) -> None:
        """Attach the shared Presidio analyzer (built once, process-wide).

        The analyzer + spaCy model are stateless and expensive to construct
        (~1-2 s), so they are cached at module level and reused across every
        RedactionEngine. Only the per-engine TokenStore holds session state.
        """
        if self._analyzer is not None:
            return
        analyzer, anonymizer = _get_shared_presidio()
        self._analyzer = analyzer
        self._anonymizer = anonymizer

    def _pass2_presidio(self, text: str) -> str:
        """NER-based PII detection with ALL-CAPS normalisation."""
        self._load_presidio()
        if self._analyzer is None:
            return text  # Presidio not installed — best-effort with Pass 1 only

        # Lowercase so spaCy NER works on bank-statement ALL-CAPS text.
        # lower() preserves character positions (1-to-1 map), so spans are
        # directly usable on the original text. For conversational text (chat
        # excerpts), keep the original casing — capitalization is a NER signal.
        analyzed_text = text.lower() if self._lowercase_for_ner else text

        try:
            results = self._analyzer.analyze(
                text=analyzed_text,
                language="en",
                entities=_PRESIDIO_ENTITIES,
                score_threshold=0.45,
            )
        except Exception as exc:
            logger.warning("Presidio analyze() failed: %s", exc)
            return text

        if not results:
            return text

        # Remove overlapping results (keep highest-score span)
        results = _dedup_spans(results)

        # Sort ascending by start so we can build the output left-to-right
        results.sort(key=lambda r: r.start)

        # Apply spans to the ORIGINAL (pre-lowercase) text
        out: list[str] = []
        last = 0
        replaced = 0
        for r in results:
            span_text = text[r.start:r.end]
            # Skip spans that are already tokens from Pass 1
            if span_text.startswith("[") and span_text.endswith("]"):
                continue
            out.append(text[last:r.start])
            token = self._store.get_or_create(r.entity_type, span_text)
            out.append(token)
            last = r.end
            replaced += 1
        out.append(text[last:])

        if replaced:
            logger.debug("Pass 2 presidio: %d spans replaced", replaced)
        return "".join(out)

    # ------------------------------------------------------------------
    # Pass 3 — Deterministic sweep (safety net)
    # ------------------------------------------------------------------

    def _pass3_sweep(self, text: str) -> str:
        """Tokenize any residual contiguous structured PII.

        Runs after Pass 2 as a deterministic guarantee: no email, SSN, grouped
        card number, ZIP+4, or 8+ digit run survives — even when an adjacent
        span replacement in Pass 2 exposed a boundary the NER pass couldn't see.
        Existing [TOKENS] are skipped so their internals are never re-redacted.
        """
        for pattern, entity_type in _SWEEP_RES:
            def _replace(m: re.Match) -> str:
                span = m.group(0)
                # Never touch an existing token (defensive — patterns are digit/
                # email shaped and shouldn't match tokens, but guard anyway).
                if _TOKEN_RE.fullmatch(span):
                    return span
                return self._store.get_or_create(entity_type, span)

            text = pattern.sub(_replace, text)
        return text


# ---------------------------------------------------------------------------
# Shared Presidio analyzer (built once per process)
# ---------------------------------------------------------------------------

_SHARED_ANALYZER = None       # type: ignore[var-annotated]
_SHARED_ANONYMIZER = None     # type: ignore[var-annotated]
_PRESIDIO_LOAD_FAILED = False


def _get_shared_presidio():
    """Build (once) and return the shared (analyzer, anonymizer) pair.

    Returns (None, None) if Presidio/spaCy are unavailable. The result is
    cached so the expensive spaCy model load happens at most once per process,
    which also removes the test-suite flakiness caused by repeated loads.
    """
    global _SHARED_ANALYZER, _SHARED_ANONYMIZER, _PRESIDIO_LOAD_FAILED
    if _SHARED_ANALYZER is not None or _PRESIDIO_LOAD_FAILED:
        return _SHARED_ANALYZER, _SHARED_ANONYMIZER
    try:
        from presidio_analyzer import AnalyzerEngine, RecognizerRegistry
        from presidio_analyzer.nlp_engine import NlpEngineProvider
        from presidio_anonymizer import AnonymizerEngine

        from core.privacy.recognizers import build_registry

        provider = NlpEngineProvider(nlp_configuration={
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}],
        })
        nlp_engine = provider.create_engine()

        # Build the registry MANUALLY rather than via Presidio's bulk loaders
        # (load_predefined_recognizers / RecognizerRegistryProvider). In this
        # Presidio version that loader instantiates some predefined recognizers
        # with an unsupported `supported_language` kwarg and throws TypeError
        # nondeterministically. We only need spaCy NER (PERSON/LOCATION) and
        # email; everything else structured is covered by our own recognizers
        # and the Pass 3 sweep — so we add exactly those, deterministically.
        from presidio_analyzer.predefined_recognizers import (
            EmailRecognizer,
            SpacyRecognizer,
        )

        registry = RecognizerRegistry()
        registry.add_recognizer(SpacyRecognizer(supported_language="en"))
        registry.add_recognizer(EmailRecognizer(supported_language="en"))
        for recognizer in build_registry():
            registry.add_recognizer(recognizer)

        analyzer = AnalyzerEngine(
            nlp_engine=nlp_engine,
            registry=registry,
            supported_languages=["en"],
        )

        _SHARED_ANALYZER = analyzer
        _SHARED_ANONYMIZER = AnonymizerEngine()
        logger.debug("Presidio loaded (en_core_web_sm) — shared instance")
    except Exception as exc:
        logger.warning("Presidio unavailable — Pass 2 skipped: %s", exc)
        _PRESIDIO_LOAD_FAILED = True
    return _SHARED_ANALYZER, _SHARED_ANONYMIZER


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dedup_spans(results: list) -> list:
    """Remove overlapping recognition results, keeping the highest-score one."""
    if not results:
        return results
    results = sorted(results, key=lambda r: (r.start, -r.score))
    deduped = [results[0]]
    for r in results[1:]:
        prev = deduped[-1]
        if r.start < prev.end:  # overlaps — keep whichever has higher score
            if r.score > prev.score:
                deduped[-1] = r
        else:
            deduped.append(r)
    return deduped
