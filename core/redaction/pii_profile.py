"""Profile loader and token-map builder for PII redaction."""

import json
from pathlib import Path

FIELD_TOKEN_MAP = {
    "names": "PERSON",
    "accounts": "ACCT",
    "addresses": "ADDR",
    "ssn": "SSN",
    "phone": "PHONE",
    "email": "EMAIL",
}

# Sub-fields used for structured address tokenization
_ADDR_SUBFIELDS = ["line1", "city", "state", "zip"]

REQUIRED_FIELDS = set(FIELD_TOKEN_MAP.keys())


def load_profile(path) -> dict:
    """Load and validate a pii_profile.json file.

    Raises ValueError if required fields are missing.
    """
    path = Path(path)
    with open(path, encoding="utf-8") as f:
        profile = json.load(f)

    missing = REQUIRED_FIELDS - set(profile.keys())
    if missing:
        raise ValueError(f"pii_profile.json missing required fields: {sorted(missing)}")

    return profile


def build_token_map(profile: dict) -> dict:
    """Build a mapping of {pii_value: "[TOKEN_N]"}, sorted longest-first.

    Addresses are tokenized per-field when given as dicts:
        {"line1": "123 Main St", "city": "Anytown", ...}
        → "[ADDR_1_LINE1]", "[ADDR_1_CITY]", ...

    Plain string addresses are tokenized as a single token: "[ADDR_1]".
    """
    token_map: dict[str, str] = {}

    for field, prefix in FIELD_TOKEN_MAP.items():
        values = profile.get(field, [])
        if not values:
            continue

        for i, value in enumerate(values, start=1):
            if field == "addresses" and isinstance(value, dict):
                # Structured address — one token per sub-field
                for subfield in _ADDR_SUBFIELDS:
                    sv = value.get(subfield)
                    if sv and str(sv).strip():
                        token = f"[{prefix}_{i}_{subfield.upper()}]"
                        token_map[str(sv).strip()] = token
            else:
                sv = str(value).strip()
                if sv:
                    token = f"[{prefix}_{i}]"
                    token_map[sv] = token

    # Sort longest-first to prevent partial matches
    # ("ALEX J HO" must match before "ALEX HO")
    sorted_map = dict(
        sorted(token_map.items(), key=lambda kv: len(kv[0]), reverse=True)
    )
    return sorted_map


def build_restore_map(token_map: dict) -> dict:
    """Inverse of token_map: {"[TOKEN_N]": pii_value}."""
    return {token: pii for pii, token in token_map.items()}
