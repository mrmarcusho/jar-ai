"""Send a redacted PDF to Gemini API for structured extraction."""

import json
import os

from google import genai
from google.genai import types

# Reuse the schema and prompt from the determinism experiment
from tests.test_claude_determinism import EXTRACTION_PROMPT

from core.redaction.extract_claude import PII_NOTICE

MODEL = "gemini-2.5-flash"


def extract_with_gemini(redacted_pdf_bytes: bytes, model: str = MODEL) -> dict:
    """Extract structured data from a redacted PDF via Gemini API.

    Sends the PDF as inline bytes, with a prompt that includes
    the PII redaction notice + standard extraction instructions.

    Returns the parsed JSON dict.
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY environment variable is not set")

    client = genai.Client(api_key=api_key)
    prompt = PII_NOTICE + EXTRACTION_PROMPT

    response = client.models.generate_content(
        model=model,
        contents=[
            types.Part.from_bytes(data=redacted_pdf_bytes, mime_type="application/pdf"),
            prompt,
        ],
        config=types.GenerateContentConfig(
            temperature=0,
            max_output_tokens=65536,
            response_mime_type="application/json",
        ),
    )

    text = response.text

    # Debug: save raw response for inspection
    from pathlib import Path
    debug_path = Path(__file__).resolve().parent.parent.parent / "data" / "extraction_output" / "gemini_debug_raw.txt"
    debug_path.write_text(text, encoding="utf-8")
    print(f"  Raw Gemini response saved to {debug_path}")

    return json.loads(text)
