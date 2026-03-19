"""Transaction categorization engine.

Deterministic keyword-matching categorizer that assigns spending categories
from the JAR taxonomy to bank transactions.
"""

import json
from pathlib import Path


DEFAULT_TAXONOMY = Path(__file__).resolve().parent / "categories" / "categories_v0.1.0.json"


class CategoryStore:
    """Loads and validates taxonomy JSON, provides lookup helpers."""

    def __init__(self, taxonomy_path: str = None):
        path = Path(taxonomy_path) if taxonomy_path else DEFAULT_TAXONOMY
        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        if data.get("schema") != "jar_categories":
            raise ValueError(f"Invalid taxonomy schema: {data.get('schema')}")

        self._data = data
        self._categories = data["categories"]
        self._rules = data["keyword_mapping"]["rules"]

    @property
    def categories(self) -> dict:
        return self._categories

    def get_label(self, category: str, subcategory: str = None) -> str:
        """Return human-readable label for a category/subcategory pair."""
        cat = self._categories.get(category)
        if not cat:
            return category

        if subcategory:
            sub = cat.get("subcategories", {}).get(subcategory)
            if sub:
                return sub["label"]

        return cat["label"]

    def get_rules(self) -> list:
        return self._rules


class TransactionCategorizer:
    """Applies taxonomy rules to transactions using deterministic keyword matching.

    Priority order (when user_rules is provided):
    1. User override (confidence 1.0)
    2. User rules keyword match (confidence 0.95)
    3. ACH company name match (0.9)
    4. Keyword match on description_raw (0.8)
    5. Type-based fallback (0.5)
    6. Default: uncategorized (0)

    When user_rules is None, steps 1-2 are skipped (backward compatible).
    """

    TYPE_FALLBACKS = {
        "direct_deposit": ("income", "salary"),
        "atm_withdrawal": ("financial", "atm_fee"),
        "zelle_transfer": ("transfers", "zelle"),
    }

    def __init__(self, store: CategoryStore, user_rules=None):
        self.store = store
        self._rules = store.get_rules()
        self.user_rules = user_rules

    def categorize(self, txn: dict) -> dict:
        """Categorize a single transaction dict.

        Args:
            txn: Transaction dict with keys like description_raw, type,
                 parsed_fields (optional dict with ach_company_name).
                 Optional: transaction_id (for user override lookup).

        Returns:
            Dict with category, subcategory, category_label, confidence.
        """
        parsed = txn.get("parsed_fields") or {}
        ach_company = (parsed.get("ach_company_name") or "").upper()
        desc_raw = (txn.get("description_raw") or "").upper()
        txn_type = txn.get("type") or ""
        txn_id = txn.get("transaction_id")

        # 1. User override (highest priority)
        if self.user_rules and txn_id:
            override = self.user_rules.get_override(txn_id)
            if override:
                return self._result(override["category"], override.get("subcategory"), 1.0)

        # 2. User rules keyword match
        if self.user_rules:
            user_rules_list = self.user_rules.get_rules(active_only=True)
            search_fields = [desc_raw, ach_company]
            for ur in user_rules_list:
                kw = ur["keyword"].upper()
                for field in search_fields:
                    if field and kw in field:
                        return self._result(ur["category"], ur.get("subcategory"), 0.95)

        # 3. ACH company name match
        if ach_company:
            for rule in self._rules:
                if rule.get("match_type") != "contains":
                    continue
                for kw in rule["keywords"]:
                    if kw.upper() in ach_company:
                        return self._result(rule["category"], rule["subcategory"], 0.9)

        # 4. Keyword match on description_raw
        if desc_raw:
            for rule in self._rules:
                if rule.get("match_type") != "contains":
                    continue
                for kw in rule["keywords"]:
                    if kw.upper() in desc_raw:
                        return self._result(rule["category"], rule["subcategory"], 0.8)

        # 5. Type-based fallback
        if txn_type in self.TYPE_FALLBACKS:
            cat, sub = self.TYPE_FALLBACKS[txn_type]
            return self._result(cat, sub, 0.5)

        # 6. Default
        return self._result("uncategorized", "uncategorized", 0)

    def _result(self, category: str, subcategory: str, confidence: float) -> dict:
        return {
            "category": category,
            "subcategory": subcategory,
            "category_label": self.store.get_label(category, subcategory),
            "confidence": confidence,
        }
