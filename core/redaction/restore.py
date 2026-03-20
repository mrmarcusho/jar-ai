"""Restore PII tokens back to original values in structured extraction output."""

import copy


def restore_pii(structured: dict, restore_map: dict) -> dict:
    """Replace all [TOKEN] placeholders with original PII values.

    Deep-copies the input so the original is not mutated.
    Walks every string value recursively and applies str.replace()
    for each token→original pair.
    """
    result = copy.deepcopy(structured)
    _walk(result, restore_map)
    return result


def _walk(obj, restore_map: dict):
    """Recursively replace tokens in all string values in-place."""
    if isinstance(obj, dict):
        for key in obj:
            if isinstance(obj[key], str):
                obj[key] = _replace_tokens(obj[key], restore_map)
            elif isinstance(obj[key], (dict, list)):
                _walk(obj[key], restore_map)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            if isinstance(item, str):
                obj[i] = _replace_tokens(item, restore_map)
            elif isinstance(item, (dict, list)):
                _walk(item, restore_map)


def _replace_tokens(text: str, restore_map: dict) -> str:
    """Replace all tokens in a string with their original PII values."""
    for token, original in restore_map.items():
        if token in text:
            text = text.replace(token, original)
    return text
