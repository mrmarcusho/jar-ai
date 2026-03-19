"""Tests for the transaction categorization engine."""

import json
from pathlib import Path

import pytest

from core.categorization.categorizer import CategoryStore, TransactionCategorizer


@pytest.fixture
def categorizer():
    store = CategoryStore()
    return TransactionCategorizer(store)


def _txn(description_raw="", txn_type="", ach_company_name=""):
    """Helper to build a minimal transaction dict."""
    return {
        "description_raw": description_raw,
        "type": txn_type,
        "parsed_fields": {"ach_company_name": ach_company_name} if ach_company_name else {},
    }


class TestACHCompanyMatch:
    def test_mr_cooper_mortgage(self, categorizer):
        result = categorizer.categorize(_txn(ach_company_name="NSM DBAMR.COOPER"))
        assert result["category"] == "housing"
        assert result["subcategory"] == "mortgage"
        assert result["confidence"] == 0.9

    def test_pseg_electric(self, categorizer):
        result = categorizer.categorize(_txn(ach_company_name="PSEG - PUBLIC SERV"))
        assert result["category"] == "utilities"
        assert result["subcategory"] == "electric"
        assert result["confidence"] == 0.9


class TestKeywordMatch:
    def test_pseg_description(self, categorizer):
        result = categorizer.categorize(_txn(description_raw="PSEG ONLINE PAYMENT"))
        assert result["category"] == "utilities"
        assert result["subcategory"] == "electric"
        assert result["confidence"] == 0.8

    def test_shoprite(self, categorizer):
        result = categorizer.categorize(_txn(description_raw="SHOPRITE OF PARSIPPANY"))
        assert result["category"] == "groceries"
        assert result["subcategory"] == "supermarket"
        assert result["confidence"] == 0.8

    def test_amazon(self, categorizer):
        result = categorizer.categorize(_txn(description_raw="AMAZON.COM PURCHASE"))
        assert result["category"] == "shopping"
        assert result["subcategory"] == "online"

    def test_case_insensitive(self, categorizer):
        result = categorizer.categorize(_txn(description_raw="pseg online payment"))
        assert result["category"] == "utilities"
        assert result["subcategory"] == "electric"


class TestTypeFallback:
    def test_zelle_transfer(self, categorizer):
        result = categorizer.categorize(_txn(txn_type="zelle_transfer"))
        assert result["category"] == "transfers"
        assert result["subcategory"] == "zelle"
        assert result["confidence"] == 0.5

    def test_direct_deposit_income(self, categorizer):
        result = categorizer.categorize(_txn(txn_type="direct_deposit"))
        assert result["category"] == "income"
        assert result["subcategory"] == "salary"
        assert result["confidence"] == 0.5

    def test_atm_withdrawal(self, categorizer):
        result = categorizer.categorize(_txn(txn_type="atm_withdrawal"))
        assert result["category"] == "financial"
        assert result["subcategory"] == "atm_fee"
        assert result["confidence"] == 0.5


class TestUncategorizedFallback:
    def test_unknown_description(self, categorizer):
        result = categorizer.categorize(_txn(description_raw="XYZZY CORP UNKNOWN"))
        assert result["category"] == "uncategorized"
        assert result["subcategory"] == "uncategorized"
        assert result["confidence"] == 0

    def test_empty_transaction(self, categorizer):
        result = categorizer.categorize({})
        assert result["category"] == "uncategorized"
        assert result["confidence"] == 0


class TestPriorityOrder:
    def test_ach_wins_over_keyword(self, categorizer):
        """ACH company match (0.9) should win over description keyword (0.8)."""
        result = categorizer.categorize(_txn(
            description_raw="ZELLE PAYMENT",
            ach_company_name="NSM DBAMR.COOPER",
        ))
        assert result["category"] == "housing"
        assert result["subcategory"] == "mortgage"
        assert result["confidence"] == 0.9

    def test_keyword_wins_over_type(self, categorizer):
        """Keyword match should win over type fallback."""
        result = categorizer.categorize(_txn(
            description_raw="PSEG ONLINE PAYMENT",
            txn_type="direct_deposit",
        ))
        assert result["category"] == "utilities"
        assert result["subcategory"] == "electric"
        assert result["confidence"] == 0.8

    def test_first_keyword_rule_wins(self, categorizer):
        """First matching rule wins when multiple could match."""
        # "mortgage" appears before generic housing rules
        result = categorizer.categorize(_txn(description_raw="MORTGAGE PAYMENT"))
        assert result["category"] == "housing"
        assert result["subcategory"] == "mortgage"


class TestCategoryStore:
    def test_get_label(self):
        store = CategoryStore()
        assert store.get_label("housing", "mortgage") == "Mortgage"
        assert store.get_label("utilities", "electric") == "Electric"
        assert store.get_label("housing") == "Housing"

    def test_get_label_unknown(self):
        store = CategoryStore()
        assert store.get_label("nonexistent") == "nonexistent"

    def test_categories_property(self):
        store = CategoryStore()
        assert "housing" in store.categories
        assert "uncategorized" in store.categories

    def test_get_rules(self):
        store = CategoryStore()
        rules = store.get_rules()
        assert len(rules) > 0
        assert all("keywords" in r for r in rules)


class TestRealTransactionCoverage:
    """Load actual structured JSONs and verify categorization coverage."""

    DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "extraction_output"

    def test_all_real_transactions_categorized(self, categorizer):
        json_files = list(self.DATA_DIR.glob("*.structured.json"))
        if not json_files:
            pytest.skip("No structured JSON files found")

        total = 0
        categorized = 0

        for json_path in json_files:
            data = json.loads(json_path.read_text())
            for txn_list in [data.get("deposits", []), data.get("withdrawals", [])]:
                for txn in txn_list:
                    result = categorizer.categorize(txn)
                    total += 1
                    if result["category"] != "uncategorized":
                        categorized += 1

        pct = (categorized / total * 100) if total else 0
        print(f"\nCoverage: {categorized}/{total} ({pct:.1f}%) transactions categorized")
        # We expect at least 30% coverage with keyword rules alone
        assert pct > 30, f"Coverage too low: {pct:.1f}%"
