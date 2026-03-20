"""Tests for the PII redaction pipeline (Path B).

Classes:
    TestPIIProfile  — profile loading, validation, token ordering
    TestRestore     — token replacement in flat/nested/list dicts
    TestRedactPdf   — PDF creation + redaction with PyMuPDF
    TestPipeline    — E2E with real PDF + Claude API (requires ANTHROPIC_API_KEY)
"""

import json
import os
import tempfile
from pathlib import Path

import pytest

from core.redaction.pii_profile import (
    FIELD_TOKEN_MAP,
    build_restore_map,
    build_token_map,
    load_profile,
)
from core.redaction.restore import restore_pii


# ---------------------------------------------------------------------------
# TestPIIProfile
# ---------------------------------------------------------------------------


class TestPIIProfile:
    """Profile loading, validation, and token map construction."""

    def _write_profile(self, tmp_path, data):
        p = tmp_path / "profile.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        return p

    def _full_profile(self, **overrides):
        base = {k: [] for k in FIELD_TOKEN_MAP}
        base.update(overrides)
        return base

    def test_load_valid_profile(self, tmp_path):
        profile_data = self._full_profile(names=["Alice"])
        p = self._write_profile(tmp_path, profile_data)
        profile = load_profile(p)
        assert profile["names"] == ["Alice"]

    def test_load_missing_field_raises(self, tmp_path):
        p = self._write_profile(tmp_path, {"names": ["Alice"]})
        with pytest.raises(ValueError, match="missing required fields"):
            load_profile(p)

    def test_token_map_basic(self):
        profile = self._full_profile(names=["Alice", "Bob"], accounts=["1234"])
        tm = build_token_map(profile)
        assert tm["Alice"] == "[PERSON_1]"
        assert tm["Bob"] == "[PERSON_2]"
        assert tm["1234"] == "[ACCT_1]"

    def test_token_map_longest_first(self):
        profile = self._full_profile(names=["Alex Ho", "Alex J Ho"])
        tm = build_token_map(profile)
        keys = list(tm.keys())
        assert keys[0] == "Alex J Ho"
        assert keys[1] == "Alex Ho"

    def test_token_map_structured_address(self):
        profile = self._full_profile(
            addresses=[{"line1": "123 Main St", "city": "Anytown", "state": "CA", "zip": "90210"}]
        )
        tm = build_token_map(profile)
        assert tm["123 Main St"] == "[ADDR_1_LINE1]"
        assert tm["Anytown"] == "[ADDR_1_CITY]"
        assert tm["CA"] == "[ADDR_1_STATE]"
        assert tm["90210"] == "[ADDR_1_ZIP]"

    def test_token_map_string_address(self):
        profile = self._full_profile(addresses=["123 Main St, Anytown, CA 90210"])
        tm = build_token_map(profile)
        assert tm["123 Main St, Anytown, CA 90210"] == "[ADDR_1]"

    def test_empty_fields_produce_no_tokens(self):
        profile = self._full_profile()  # all empty
        tm = build_token_map(profile)
        assert tm == {}

    def test_restore_map_is_inverse(self):
        profile = self._full_profile(names=["Alice"], accounts=["9999"])
        tm = build_token_map(profile)
        rm = build_restore_map(tm)
        assert rm["[PERSON_1]"] == "Alice"
        assert rm["[ACCT_1]"] == "9999"


# ---------------------------------------------------------------------------
# TestRestore
# ---------------------------------------------------------------------------


class TestRestore:
    """Token→PII restoration in structured dicts."""

    def test_flat_dict(self):
        data = {"name": "[PERSON_1]", "account": "[ACCT_1]"}
        rm = {"[PERSON_1]": "Alice", "[ACCT_1]": "9999"}
        result = restore_pii(data, rm)
        assert result["name"] == "Alice"
        assert result["account"] == "9999"

    def test_nested_dict(self):
        data = {"metadata": {"account": {"name": "[PERSON_1]"}}}
        rm = {"[PERSON_1]": "Alice"}
        result = restore_pii(data, rm)
        assert result["metadata"]["account"]["name"] == "Alice"

    def test_list_values(self):
        data = {"items": ["paid to [PERSON_1]", "[PERSON_2] refund"]}
        rm = {"[PERSON_1]": "Alice", "[PERSON_2]": "Bob"}
        result = restore_pii(data, rm)
        assert result["items"] == ["paid to Alice", "Bob refund"]

    def test_multi_token_string(self):
        data = {"desc": "INDN:[PERSON_1] CO ID:[ACCT_1]"}
        rm = {"[PERSON_1]": "Alex Ho", "[ACCT_1]": "9999"}
        result = restore_pii(data, rm)
        assert result["desc"] == "INDN:Alex Ho CO ID:9999"

    def test_immutability(self):
        original = {"name": "[PERSON_1]"}
        rm = {"[PERSON_1]": "Alice"}
        _ = restore_pii(original, rm)
        assert original["name"] == "[PERSON_1]"  # unchanged

    def test_no_tokens_unchanged(self):
        data = {"amount": 42, "flag": True, "desc": "normal text"}
        rm = {"[PERSON_1]": "Alice"}
        result = restore_pii(data, rm)
        assert result == data

    def test_list_of_dicts(self):
        data = {"txns": [{"desc": "[PERSON_1] payment"}, {"desc": "other"}]}
        rm = {"[PERSON_1]": "Alice"}
        result = restore_pii(data, rm)
        assert result["txns"][0]["desc"] == "Alice payment"
        assert result["txns"][1]["desc"] == "other"


# ---------------------------------------------------------------------------
# TestRedactPdf
# ---------------------------------------------------------------------------


class TestRedactPdf:
    """PDF redaction with PyMuPDF."""

    def _make_pdf(self, text_items: list[tuple[float, float, str]], pages=1) -> Path:
        """Create a simple PDF with text at specified positions.

        text_items: [(x, y, text), ...]
        Returns path to the temp PDF.
        """
        import fitz

        doc = fitz.open()
        for _ in range(pages):
            page = doc.new_page()
            for x, y, text in text_items:
                page.insert_text((x, y), text, fontsize=12)
        path = Path(tempfile.mktemp(suffix=".pdf"))
        doc.save(str(path))
        doc.close()
        return path

    def _extract_text(self, pdf_bytes: bytes) -> str:
        import fitz

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        text = ""
        for page in doc:
            text += page.get_text()
        doc.close()
        return text

    def test_basic_redaction(self):
        from core.redaction.redact_pdf import redact_pdf

        pdf_path = self._make_pdf([(72, 72, "Hello Alex Ho, account 9999")])
        token_map = {"Alex Ho": "[PERSON_1]", "9999": "[ACCT_1]"}

        redacted = redact_pdf(pdf_path, token_map)
        text = self._extract_text(redacted)

        assert "Alex Ho" not in text
        assert "[PERSON_1]" in text
        assert "[ACCT_1]" in text
        pdf_path.unlink()

    def test_original_file_unchanged(self):
        from core.redaction.redact_pdf import redact_pdf

        pdf_path = self._make_pdf([(72, 72, "Alex Ho")])
        original_bytes = pdf_path.read_bytes()
        token_map = {"Alex Ho": "[PERSON_1]"}

        _ = redact_pdf(pdf_path, token_map)
        assert pdf_path.read_bytes() == original_bytes
        pdf_path.unlink()

    def test_multi_page(self):
        from core.redaction.redact_pdf import redact_pdf

        pdf_path = self._make_pdf([(72, 72, "Alex Ho")], pages=3)
        token_map = {"Alex Ho": "[PERSON_1]"}

        redacted = redact_pdf(pdf_path, token_map)
        text = self._extract_text(redacted)

        assert "Alex Ho" not in text
        assert text.count("[PERSON_1]") >= 3
        pdf_path.unlink()

    def test_no_match_leaves_pdf_intact(self):
        from core.redaction.redact_pdf import redact_pdf

        pdf_path = self._make_pdf([(72, 72, "Hello World")])
        token_map = {"NoMatch": "[PERSON_1]"}

        redacted = redact_pdf(pdf_path, token_map)
        text = self._extract_text(redacted)

        assert "Hello World" in text
        assert "[PERSON_1]" not in text
        pdf_path.unlink()


# ---------------------------------------------------------------------------
# TestPipeline — E2E (requires ANTHROPIC_API_KEY)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set",
)
class TestPipeline:
    """End-to-end pipeline test with real PDF + Claude API."""

    PROJECT_ROOT = Path(__file__).resolve().parent.parent

    def test_e2e_pipeline(self, tmp_path):
        from core.redaction.pipeline import run_path_b

        # Use a test PDF if available
        pdf_path = self.PROJECT_ROOT / "data" / "statements" / "eStmt_2023-12-13.pdf"
        if not pdf_path.exists():
            pytest.skip(f"Test PDF not found: {pdf_path}")

        profile_path = self.PROJECT_ROOT / "data" / "pii_profile.json"
        if not profile_path.exists():
            pytest.skip(f"PII profile not found: {profile_path}")

        result_path = run_path_b(pdf_path, profile_path, out_dir=tmp_path)

        assert result_path is not None
        assert result_path.exists()

        structured = json.loads(result_path.read_text(encoding="utf-8"))

        # Schema check: must have top-level keys
        assert "metadata" in structured
        assert "deposits" in structured or "withdrawals" in structured
        assert "account_summary" in structured

        # PII should be restored (no tokens in output)
        text = json.dumps(structured)
        assert "[PERSON_" not in text
        assert "[ACCT_" not in text
