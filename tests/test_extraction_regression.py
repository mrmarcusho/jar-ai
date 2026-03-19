# tests/test_extraction_regression.py

"""
Extraction Pipeline Regression Tests

Every bug found during testing becomes a permanent test case here.
These tests ensure that fixes are never accidentally reverted.

Each test is tagged with its issue ID from doc/extraction_issues.md.

Running:
    python -m pytest tests/test_extraction_regression.py -v
    python -m pytest tests/test_extraction_regression.py -v -k "BofA"       # only BofA tests
    python -m pytest tests/test_extraction_regression.py -v -k "balance"    # only balance tests
    python -m pytest tests/test_extraction_regression.py -v -m "not slow"   # skip slow tests
"""

import json
import time
import pytest
from pathlib import Path

from core.extraction_engine.postprocess.registry import detect_bank, discover_processors
from core.extraction_engine.postprocess.run_postprocess import postprocess_and_structurize


# === Test Configuration ===

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
EXTRACTION_OUTPUT_DIR = DATA_DIR / "extraction_output"

# Maximum acceptable conversion time (seconds)
MAX_CONVERSION_TIME = 300  # 5 minutes — adjust per hardware

# === Statement Registry ===
# Add statement stems here as they're validated. Organized by bank.

BOA_STATEMENTS = [
    "eStmt_2023-01-13",
    "eStmt_2023-02-10",
    "eStmt_2023-12-13",
    # Add more as tested:
    # "eStmt_2023-03-XX",
    # "eStmt_2023-04-XX",
]

TD_BANK_STATEMENTS = [
    # "530588294-TD-Bank-Statement",
]

CHASE_STATEMENTS = [
    # Add as tested
]

ALL_STATEMENTS = BOA_STATEMENTS + TD_BANK_STATEMENTS + CHASE_STATEMENTS


# === Helpers ===

def load_structured_json(stem: str) -> dict:
    """Load a structured JSON file by statement stem name."""
    path = EXTRACTION_OUTPUT_DIR / f"{stem}.structured.json"
    if not path.exists():
        pytest.skip(f"Structured JSON not found: {path}. Run extraction first.")
    with open(path) as f:
        return json.load(f)


def get_md_text(stem: str) -> str:
    """Load markdown text for a statement."""
    path = EXTRACTION_OUTPUT_DIR / f"{stem}.md"
    if not path.exists():
        pytest.skip(f"Markdown not found: {path}. Run Docling first.")
    return path.read_text(encoding="utf-8")


def run_full_pipeline(stem: str) -> dict:
    """Run the full postprocess + structurize pipeline for a statement.

    Returns the structured JSON dict.
    Requires that Docling has already been run (md + json exist).
    """
    md_path = EXTRACTION_OUTPUT_DIR / f"{stem}.md"
    json_path = EXTRACTION_OUTPUT_DIR / f"{stem}.json"

    if not md_path.exists() or not json_path.exists():
        pytest.skip(f"Docling output not found for {stem}. Run preprocessing first.")

    structured_path = postprocess_and_structurize(md_path, json_path, EXTRACTION_OUTPUT_DIR, stem)
    assert structured_path is not None, f"Pipeline returned None for {stem}"

    with open(structured_path) as f:
        return json.load(f)


# === Bank Detection Tests ===

class TestBankDetection:
    """Tests for bank format detection."""

    def test_boa_detected_with_high_confidence(self):
        """All BofA statements should be detected with confidence >= 0.7."""
        discover_processors()
        boa_stems = [p.stem for p in EXTRACTION_OUTPUT_DIR.glob("eStmt_*.md")]

        for stem in boa_stems:
            md_text = get_md_text(stem)
            processor, confidence = detect_bank(md_text)
            assert processor is not None, f"{stem}: no bank detected"
            assert processor.bank_name == "Bank of America", f"{stem}: detected as {processor.bank_name}"
            assert confidence >= 0.7, f"{stem}: low confidence {confidence}"


# === BofA Balance Tests ===

class TestBofABalances:
    """Tests for BofA balance extraction accuracy.

    ISSUE-001: Ending balance sometimes detected inside table by Docling,
    causing incorrect balance extraction.
    """

    @pytest.mark.parametrize("stem", BOA_STATEMENTS)
    def test_boa_balance_not_zero(self, stem):
        """ISSUE-001: Opening and closing balances must not be zero.

        A zero balance almost always means extraction error, not actual zero balance.
        """
        structured = load_structured_json(stem)
        summary = structured.get("account_summary", {})

        opening = summary.get("opening_balance")
        closing = summary.get("closing_balance")

        assert opening is not None, f"{stem}: opening balance missing"
        assert closing is not None, f"{stem}: closing balance missing"
        assert opening != 0.0, f"{stem}: opening balance is $0.00 — likely extraction error"
        assert closing != 0.0, f"{stem}: closing balance is $0.00 — likely extraction error"

    @pytest.mark.parametrize("stem", BOA_STATEMENTS)
    def test_boa_balance_reconciliation(self, stem):
        """ISSUE-001: Balance must reconcile: opening + deposits - withdrawals = closing."""
        structured = load_structured_json(stem)
        validation = structured.get("validation", {})

        assert validation.get("matches_closing_balance") is True, \
            f"{stem}: balance does not reconcile — " \
            f"computed {validation.get('minus_withdrawals')}, " \
            f"expected {structured.get('account_summary', {}).get('closing_balance')}"


# === BofA Transaction Section Tests ===

class TestBofATransactionSections:
    """Tests for BofA transaction section parsing.

    ISSUE-002: Checks in separate section not being parsed.
    """

    @pytest.mark.parametrize("stem", BOA_STATEMENTS)
    def test_boa_deposit_count_matches(self, stem):
        """Extracted deposit count must match stated count."""
        structured = load_structured_json(stem)
        validation = structured.get("validation", {})

        assert validation.get("deposit_count_matches") is True, \
            f"{stem}: deposit count mismatch — " \
            f"extracted {len(structured.get('deposits', []))}, " \
            f"stated {structured.get('account_summary', {}).get('number_of_deposits')}"

    @pytest.mark.parametrize("stem", BOA_STATEMENTS)
    def test_boa_withdrawal_count_matches(self, stem):
        """ISSUE-002: Extracted withdrawal count must match stated count.

        This catches missing sections (checks, fees) that should be
        included in the withdrawal count.
        """
        structured = load_structured_json(stem)
        validation = structured.get("validation", {})

        assert validation.get("withdrawal_count_matches") is True, \
            f"{stem}: withdrawal count mismatch — " \
            f"extracted {len(structured.get('withdrawals', []))}, " \
            f"stated {structured.get('account_summary', {}).get('number_of_withdrawals')}"

    @pytest.mark.parametrize("stem", BOA_STATEMENTS)
    def test_boa_deposit_total_matches(self, stem):
        """Sum of extracted deposits must match stated total."""
        structured = load_structured_json(stem)
        validation = structured.get("validation", {})

        assert validation.get("deposit_total_matches") is True, \
            f"{stem}: deposit total mismatch"

    @pytest.mark.parametrize("stem", BOA_STATEMENTS)
    def test_boa_withdrawal_total_matches(self, stem):
        """Sum of extracted withdrawals must match stated total."""
        structured = load_structured_json(stem)
        validation = structured.get("validation", {})

        assert validation.get("withdrawal_total_matches") is True, \
            f"{stem}: withdrawal total mismatch"

    def test_boa_checks_parsed_when_present(self):
        """ISSUE-002: If statement has a checks total, check transactions must be extracted.

        BofA statements with checks have a separate 'Checks' section.
        The structurizer must parse this section and include checks as withdrawals.
        """
        for stem_path in EXTRACTION_OUTPUT_DIR.glob("eStmt_*.structured.json"):
            stem = stem_path.stem.replace(".structured", "")
            structured = load_structured_json(stem)
            summary = structured.get("account_summary", {})

            checks_total = summary.get("checks", 0.0)
            if checks_total != 0.0:
                # This statement has checks — verify they were extracted
                withdrawals = structured.get("withdrawals", [])
                check_txns = [w for w in withdrawals if w.get("type") == "check"]

                assert len(check_txns) > 0, \
                    f"{stem}: statement shows checks total of {checks_total} " \
                    f"but no check transactions were extracted"

                # Verify check amounts sum correctly
                check_sum = sum(abs(c["amount"]) for c in check_txns)
                assert abs(check_sum - abs(checks_total)) < 0.01, \
                    f"{stem}: check total mismatch — extracted sum {check_sum}, stated {checks_total}"


# === Transaction Quality Tests ===

class TestTransactionQuality:
    """Tests for transaction data quality across all statements."""

    @pytest.mark.parametrize("stem", BOA_STATEMENTS)
    def test_no_zero_amount_transactions(self, stem):
        """No transaction should have a $0.00 amount."""
        structured = load_structured_json(stem)
        all_txns = structured.get("deposits", []) + structured.get("withdrawals", [])

        for txn in all_txns:
            assert txn.get("amount") != 0.0, \
                f"{stem}: transaction {txn.get('id')} has $0.00 amount: {txn.get('description_raw', '')[:50]}"

    @pytest.mark.parametrize("stem", BOA_STATEMENTS)
    def test_all_transactions_have_dates(self, stem):
        """Every transaction must have a valid date."""
        structured = load_structured_json(stem)
        all_txns = structured.get("deposits", []) + structured.get("withdrawals", [])

        for txn in all_txns:
            date = txn.get("date")
            assert date is not None and date != "", \
                f"{stem}: transaction {txn.get('id')} missing date: {txn.get('description_raw', '')[:50]}"
            # Basic date format check (YYYY-MM-DD)
            assert len(date) == 10 and date[4] == "-" and date[7] == "-", \
                f"{stem}: transaction {txn.get('id')} bad date format: {date}"

    @pytest.mark.parametrize("stem", BOA_STATEMENTS)
    def test_all_transactions_have_descriptions(self, stem):
        """Every transaction must have a non-empty description."""
        structured = load_structured_json(stem)
        all_txns = structured.get("deposits", []) + structured.get("withdrawals", [])

        for txn in all_txns:
            desc = txn.get("description_raw", "")
            assert desc.strip() != "", \
                f"{stem}: transaction {txn.get('id')} has empty description"

    @pytest.mark.parametrize("stem", BOA_STATEMENTS)
    def test_transactions_within_statement_period(self, stem):
        """All transaction dates must fall within the statement period."""
        structured = load_structured_json(stem)
        period = structured.get("metadata", {}).get("statement_period", {})
        start = period.get("start")
        end = period.get("end")

        if not start or not end:
            pytest.skip(f"{stem}: statement period not available")

        all_txns = structured.get("deposits", []) + structured.get("withdrawals", [])
        for txn in all_txns:
            date = txn.get("date", "")
            if date:
                assert start <= date <= end, \
                    f"{stem}: transaction {txn.get('id')} date {date} outside period {start} to {end}"

    @pytest.mark.parametrize("stem", BOA_STATEMENTS)
    def test_deposits_are_positive(self, stem):
        """All deposit amounts must be positive."""
        structured = load_structured_json(stem)
        for txn in structured.get("deposits", []):
            assert txn["amount"] > 0, \
                f"{stem}: deposit {txn.get('id')} has non-positive amount: {txn['amount']}"

    @pytest.mark.parametrize("stem", BOA_STATEMENTS)
    def test_withdrawals_are_negative(self, stem):
        """All withdrawal amounts must be negative."""
        structured = load_structured_json(stem)
        for txn in structured.get("withdrawals", []):
            assert txn["amount"] < 0, \
                f"{stem}: withdrawal {txn.get('id')} has non-negative amount: {txn['amount']}"


# === Performance Tests ===

class TestPerformance:
    """Tests for pipeline performance.

    ISSUE-003: Docling conversion too slow on Intel.
    """

    @pytest.mark.slow
    @pytest.mark.parametrize("stem", BOA_STATEMENTS)
    def test_postprocess_time_under_threshold(self, stem):
        """Post-processing (not Docling) should complete in under 10 seconds."""
        md_path = EXTRACTION_OUTPUT_DIR / f"{stem}.md"
        json_path = EXTRACTION_OUTPUT_DIR / f"{stem}.json"

        if not md_path.exists() or not json_path.exists():
            pytest.skip(f"Docling output not found for {stem}")

        start = time.time()
        postprocess_and_structurize(md_path, json_path, EXTRACTION_OUTPUT_DIR, stem)
        elapsed = time.time() - start

        assert elapsed < 10.0, \
            f"{stem}: postprocess took {elapsed:.1f}s — should be under 10s"


# === Ground Truth Tests ===

class TestGroundTruth:
    """Tests against manually verified ground truth.

    These tests only run when ground truth JSON exists for a statement.
    Ground truth is created on an as-needed basis for statements with known issues.
    """

    def _load_ground_truth(self, stem: str) -> dict:
        """Load ground truth JSON if it exists."""
        locations = [
            EXTRACTION_OUTPUT_DIR / f"{stem}.ground_truth.json",
            DATA_DIR / "ground_truth" / f"{stem}.json",
            Path(__file__).parent.parent / "core" / "extraction_engine" / "postprocess" / "banks" / "US" / "boa" / "ground_truth" / f"{stem}.json",
        ]
        for path in locations:
            if path.exists():
                with open(path) as f:
                    return json.load(f)
        return None

    @pytest.mark.parametrize("stem", BOA_STATEMENTS)
    def test_matches_ground_truth(self, stem):
        """If ground truth exists, extracted output must match."""
        ground_truth = self._load_ground_truth(stem)
        if ground_truth is None:
            pytest.skip(f"No ground truth for {stem}")

        structured = load_structured_json(stem)

        # Compare key fields
        gt_summary = ground_truth.get("account_summary", {})
        ex_summary = structured.get("account_summary", {})

        assert ex_summary.get("opening_balance") == gt_summary.get("opening_balance"), \
            f"{stem}: opening balance mismatch"
        assert ex_summary.get("closing_balance") == gt_summary.get("closing_balance"), \
            f"{stem}: closing balance mismatch"

        # Compare transaction counts
        assert len(structured.get("deposits", [])) == len(ground_truth.get("deposits", [])), \
            f"{stem}: deposit count mismatch vs ground truth"
        assert len(structured.get("withdrawals", [])) == len(ground_truth.get("withdrawals", [])), \
            f"{stem}: withdrawal count mismatch vs ground truth"

        # Compare individual transaction amounts
        gt_deposit_amounts = sorted([d["amount"] for d in ground_truth.get("deposits", [])])
        ex_deposit_amounts = sorted([d["amount"] for d in structured.get("deposits", [])])
        assert gt_deposit_amounts == ex_deposit_amounts, \
            f"{stem}: deposit amounts differ from ground truth"

        gt_withdrawal_amounts = sorted([abs(w["amount"]) for w in ground_truth.get("withdrawals", [])])
        ex_withdrawal_amounts = sorted([abs(w["amount"]) for w in structured.get("withdrawals", [])])
        assert gt_withdrawal_amounts == ex_withdrawal_amounts, \
            f"{stem}: withdrawal amounts differ from ground truth"
