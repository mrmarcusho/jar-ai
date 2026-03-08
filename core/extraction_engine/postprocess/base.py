"""Base class for bank-specific post-processing.

Every bank module must subclass BankPostProcessor and implement all
abstract methods. The registry discovers processors automatically and
dispatches based on detect().
"""

from abc import ABC, abstractmethod


class BankPostProcessor(ABC):

    bank_name: str = "Unknown"
    country_code: str = "XX"        # ISO 3166-1 alpha-2
    region: str = "Unknown"         # NA, EU, AS, etc.
    currency: str = "USD"           # ISO 4217
    date_format: str = "MM/DD/YYYY" # expected date format on statements

    @staticmethod
    @abstractmethod
    def detect(md_text: str) -> float:
        """Return a confidence score (0.0 to 1.0) that this markdown is from this bank.

        Detection should be based on institutional markers found in the
        FIRST ~50 LINES of the markdown (header area), NOT transaction content.

        Scoring guidelines:
        - 0.9-1.0: Bank name + address + website found in header area
        - 0.7-0.8: Bank name found in header area
        - 0.3-0.5: Bank-related keywords found but not in header area
        - 0.0-0.2: No meaningful match

        IMPORTANT: Transaction descriptions often mention other banks
        (e.g., "Chase Credit Cards Bill Payment" on a BofA statement).
        Detection must focus on the statement header, not transaction content.

        Should be fast — called for every bank module during dispatch.
        """
        raise NotImplementedError

    @abstractmethod
    def fix_markdown(self, md_text: str) -> str:
        """Apply bank-specific fixes to the raw Docling markdown.

        Examples: unicode escape fixes, missing field repairs,
        column header corrections, section header normalization.

        Returns the cleaned markdown text.
        """
        raise NotImplementedError

    @abstractmethod
    def fix_json(self, doc_dict: dict) -> dict:
        """Apply bank-specific fixes to the raw Docling JSON.

        Examples: column merging, table structure corrections.

        Returns the cleaned document dictionary.
        """
        raise NotImplementedError

    @abstractmethod
    def structurize(self, md_text: str, filename: str, docling_doc: dict) -> dict:
        """Convert cleaned markdown + JSON into structured extraction JSON (schema v0).

        This is the core extraction logic — parsing transactions, balances,
        account info, and producing the output conforming to jar_extraction_schema_v0.

        Args:
            md_text: Cleaned markdown text (after fix_markdown)
            filename: Original PDF filename
            docling_doc: Cleaned Docling document dict (after fix_json)

        Returns:
            Dictionary conforming to jar_extraction_schema_v0.json
        """
        raise NotImplementedError

    def validate(self, structured: dict) -> list[str]:
        """Optional: run bank-specific validation checks on the structured output.

        Default implementation checks basic balance reconciliation.
        Banks can override to add specific checks.

        Returns list of error/warning strings. Empty list = valid.
        """
        errors = []

        summary = structured.get("account_summary", {})
        opening = summary.get("opening_balance")
        closing = summary.get("closing_balance")
        total_deposits = summary.get("total_deposits_and_other_additions")
        total_withdrawals = summary.get("total_withdrawals_and_other_subtractions")

        if all(v is not None for v in [opening, closing, total_deposits, total_withdrawals]):
            computed = opening + total_deposits + total_withdrawals  # withdrawals are negative
            if abs(computed - closing) > 0.01:
                errors.append(
                    f"Balance mismatch: {opening} + {total_deposits} + {total_withdrawals} "
                    f"= {computed}, expected {closing}"
                )

        return errors
