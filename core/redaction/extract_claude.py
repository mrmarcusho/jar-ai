"""Send a redacted PDF to Claude API for structured extraction."""

import base64
import json
import os

import anthropic

# Reuse the schema and prompt from the determinism experiment
from tests.test_claude_determinism import EXTRACTION_PROMPT, SCHEMA_TEMPLATE

MODEL = "claude-sonnet-4-20250514"

PII_NOTICE = """\
IMPORTANT — PII Redaction Notice:
This document has been redacted for privacy. You will see placeholder tokens
like [PERSON_1], [ACCT_1], [ADDR_1_LINE1], etc. in place of real names,
account numbers, and addresses. You MUST preserve these tokens EXACTLY as-is
in all output fields (metadata, descriptions, parsed_fields, etc.).
Do NOT attempt to guess or reconstruct the original values.
Treat each [TOKEN] as the literal text that appears in the document.

"""


def extract_with_claude(redacted_pdf_bytes: bytes, model: str = MODEL) -> dict:
    """Extract structured data from a redacted PDF via Claude API.

    Sends the PDF as a base64-encoded document, with a prompt that includes
    the PII redaction notice + standard extraction instructions.

    Returns the parsed JSON dict.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY environment variable is not set")

    client = anthropic.Anthropic(api_key=api_key)
    pdf_b64 = base64.standard_b64encode(redacted_pdf_bytes).decode("ascii")

    prompt = PII_NOTICE + EXTRACTION_PROMPT

    response = client.messages.create(
        model=model,
        max_tokens=8192,
        temperature=0,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": pdf_b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": prompt,
                    },
                ],
            }
        ],
    )

    # Extract text from response
    text = ""
    for block in response.content:
        if hasattr(block, "text"):
            text += block.text

    # Strip markdown fencing if present
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]  # remove first ``` line
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

    return json.loads(text)
