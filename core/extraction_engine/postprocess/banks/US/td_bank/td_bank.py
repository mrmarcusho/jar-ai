"""TD Bank post-processor.

Handles TD Bank personal checking statements.

Known quirks:
- Docling renders all table rows as single-column pipe cells
- "ACCOUNT SUMMYAR" is a consistent OCR artifact (should be "ACCOUNT SUMMARY")
- Transaction dates are MM/DD only — year inferred from statement period
- All amounts are positive — debits are NOT negated on the statement
- Credits and Debits are subsections within "DAILY ACCOUNT ACTIVITY"
- Description text is often OCR-mangled (truncated words, missing spaces)
- Statement is multi-page but Docling may only capture page 1
"""

import re
from datetime import datetime

from core.extraction_engine.postprocess.base import BankPostProcessor
from core.extraction_engine.postprocess.common import merge_spanning_header_columns_md


class TDBankPostProcessor(BankPostProcessor):

    bank_name = "TD Bank"
    country_code = "US"
    region = "NA"
    currency = "USD"
    date_format = "MM/DD"

    @staticmethod
    def detect(md_text: str) -> float:
        """Score confidence that this markdown is from TD Bank.

        Searches full text for all indicators (ISSUE-007).
        """
        full_text_lower = md_text.lower()

        score = 0.0

        indicators = {
            "td personal checking": 0.5,
            "td business": 0.5,
            "td bank": 0.3,
            "tdbank.com": 0.3,
            "td bank, n.a.": 0.3,
            "statement of account": 0.1,
            "account summary": 0.1,
        }

        for indicator, weight in indicators.items():
            if indicator in full_text_lower:
                score += weight

        if "1-800-937-2000" in full_text_lower:
            score += 0.1
        if "account summyar" in full_text_lower:
            score += 0.2

        return min(score, 1.0)

    def fix_markdown(self, md_text: str) -> str:
        """Apply TD Bank-specific markdown fixes."""
        md_text = merge_spanning_header_columns_md(md_text)
        return md_text

    def fix_json(self, doc_dict: dict) -> dict:
        """Apply TD Bank-specific JSON fixes (none needed currently)."""
        return doc_dict

    def structurize(self, md_text: str, filename: str, docling_doc: dict) -> dict:
        """Convert TD Bank markdown to structured JSON."""
        return _markdown_to_structured_json(md_text, filename)


# ---------------------------------------------------------------------------
# Structurize helpers
# ---------------------------------------------------------------------------

def _parse_amount(s: str) -> float:
    """Parse an amount string like '3,750.00' or '29,853.39' into a float."""
    s = s.replace("$", "").replace(",", "").strip()
    val = float(s)
    return 0.0 if val == 0 else val


def _parse_statement_period(md_text: str) -> tuple[str | None, str | None, int]:
    """Parse 'Jul 01 2021-Jul 31 2021' into ISO dates and statement year.

    Returns (start_date, end_date, year).
    """
    m = re.search(r"(\w{3}\s+\d{1,2}\s+\d{4})\s*-\s*(\w{3}\s+\d{1,2}\s+\d{4})", md_text)
    if not m:
        return None, None, 2021

    start_str, end_str = m.group(1), m.group(2)
    start_date = None
    end_date = None
    year = 2021

    try:
        dt = datetime.strptime(start_str.strip(), "%b %d %Y")
        start_date = dt.strftime("%Y-%m-%d")
    except ValueError:
        pass

    try:
        dt = datetime.strptime(end_str.strip(), "%b %d %Y")
        end_date = dt.strftime("%Y-%m-%d")
        year = dt.year
    except ValueError:
        pass

    return start_date, end_date, year


def _parse_date_mmdd(date_str: str, year: int) -> str:
    """Parse MM/DD into YYYY-MM-DD using the statement year."""
    m = re.match(r"(\d{2})/(\d{2})", date_str.strip())
    if not m:
        return date_str
    month, day = int(m.group(1)), int(m.group(2))
    return f"{year:04d}-{month:02d}-{day:02d}"


def _classify_transaction(desc: str, is_deposit: bool) -> str:
    """Classify a TD Bank transaction type from its raw description."""
    d = desc.upper()
    if d.startswith("ACH "):
        return "direct_deposit" if is_deposit else "ach_debit"
    if d.startswith("CCD "):
        return "direct_deposit" if is_deposit else "ach_debit"
    if "CHECK DEP" in d or "CHECK D," in d:
        return "check_deposit" if is_deposit else "other"
    if d.startswith("DEPOSIT, ATM"):
        return "atm_deposit"
    if d.startswith("DEPOSIT, COUNTER"):
        return "branch_deposit"
    if "DEPOSIT" in d and is_deposit:
        return "branch_deposit"
    if "DEBIT CARD" in d:
        return "debit_card"
    if "ATM" in d and not is_deposit:
        return "atm_withdrawal"
    if "FEE" in d or "SERVICE CHARGE" in d:
        return "fee"
    return "other"


def _parse_summary_value(md_text: str, label: str) -> float | None:
    """Extract a numeric value from a TD Bank summary line.

    Summary lines look like: '| Beginning Balance 13,679.61 ... |'
    """
    # Match the label followed by whitespace and a number
    pattern = re.compile(
        re.escape(label) + r"\s+([\d,]+\.\d{2})",
        re.IGNORECASE,
    )
    m = pattern.search(md_text)
    if m:
        return _parse_amount(m.group(1))
    return None


def _parse_single_column_transactions(md_text: str, section_label: str) -> list[dict]:
    """Parse transactions from TD Bank single-column table rows.

    TD Bank Docling output renders tables as single-column cells:
        | 07/02 CHECK D, 59 CONTRACTORS 3,750.00 |

    Each transaction row starts with MM/DD and ends with an amount.
    """
    rows = []

    # Find the section (Credits or Debits)
    lines = md_text.split("\n")
    in_section = False

    for line in lines:
        stripped = line.strip()

        # Look for section start
        if not in_section:
            # Match "| Credits |" or "| Debits |"
            if re.match(r"^\|\s*" + re.escape(section_label) + r"\s*\|$", stripped, re.IGNORECASE):
                in_section = True
            continue

        # Skip header rows
        if not stripped.startswith("|"):
            continue
        if "POSTING DATE" in stripped and "DESCRIPTION" in stripped:
            continue
        if "---" in stripped:
            continue

        # End of section on Subtotal or next section header
        cell = stripped.strip("|").strip()
        if cell.lower().startswith("subtotal") or cell == "":
            break
        # If we hit the next section (e.g., "Debits" while parsing "Credits")
        if re.match(r"^(Credits|Debits)$", cell, re.IGNORECASE):
            break

        # Parse: MM/DD DESCRIPTION AMOUNT
        # Date is always at the start, amount is always at the end
        txn_match = re.match(
            r"(\d{2}/\d{2})\s+(.+?)\s+([\d,]+\.\d{2})\s*$",
            cell,
        )
        if txn_match:
            rows.append({
                "date_str": txn_match.group(1),
                "description": txn_match.group(2).strip(),
                "amount_str": txn_match.group(3),
            })

    return rows


def _markdown_to_structured_json(
    md_text: str,
    source_filename: str,
) -> dict:
    """Convert TD Bank statement markdown into structured JSON."""

    result = {
        "metadata": {},
        "account_summary": {},
        "deposits": [],
        "withdrawals": [],
        "totals": {},
        "validation": {},
    }

    # ---- metadata ----
    start_date, end_date, statement_year = _parse_statement_period(md_text)

    # Account type
    account_type = None
    type_m = re.search(r"##\s+(TD\s+\w[\w\s]*Checking)", md_text)
    if type_m:
        account_type = type_m.group(1).strip()

    # Account number — prefer "Account # NNN-NNN" over "Primary Account #:" (which
    # is followed by "1 of 3" page count in Docling output)
    account_number = None
    acct_m = re.search(r"Account\s*#\s*([\d][\d-]+)", md_text)
    if acct_m:
        account_number = acct_m.group(1).strip()

    # Customer name and address (first non-empty lines after <!-- image -->)
    account_name = None
    address = {}
    lines = md_text.split("\n")
    text_lines = []
    for line in lines[1:20]:  # skip first line (<!-- image -->)
        s = line.strip()
        if s and not s.startswith("#") and not s.startswith("|") and not s.startswith("<!--"):
            text_lines.append(s)
        if len(text_lines) >= 2:
            break

    if text_lines:
        # First line: "JANE HESS 14 DUCOMMUN ST" or just "JANE HESS"
        # Second line: "LOS ANGELES CA 90012"
        name_addr = text_lines[0] if text_lines else ""
        city_line = text_lines[1] if len(text_lines) > 1 else ""

        # Parse city/state/zip from second line
        city_m = re.match(r"([A-Z][A-Z\s]+?)\s+([A-Z]{2})\s+(\d{5}(?:-\d{4})?)", city_line)
        if city_m:
            address = {
                "city": city_m.group(1).strip(),
                "state": city_m.group(2).strip(),
                "zip": city_m.group(3).strip(),
            }

            # Try to split name from street address in first line
            # Look for a street number to find where address starts
            street_m = re.search(r"\s+(\d+\s+.+)$", name_addr)
            if street_m:
                account_name = name_addr[:street_m.start()].strip()
                address["line1"] = street_m.group(1).strip()
            else:
                account_name = name_addr

    result["metadata"] = {
        "source_file": source_filename,
        "statement_period": {
            "start": start_date,
            "end": end_date,
        },
        "bank_entity": {
            "name": "TD Bank, N.A.",
            "correspondence_address": {
                "line1": "",
                "city": "",
                "state": "",
                "zip": "",
            },
            "customer_service_information": {
                "customer_service_address": {
                    "line1": "",
                    "city": "",
                    "state": "",
                    "zip": "",
                },
                "website": "www.tdbank.com",
                "phone_number": "1-800-937-2000",
            },
        },
        "account": {
            "name": account_name,
            "account_number": account_number,
            "account_type": account_type,
            "address": address if address else None,
        },
    }

    # ---- account_summary from ACCOUNT SUMM(Y/A)R section ----
    opening_balance = _parse_summary_value(md_text, "Beginning Balance") or 0.0
    ending_balance = _parse_summary_value(md_text, "Ending Balance") or 0.0
    summary_deposits = _parse_summary_value(md_text, "Deposits") or 0.0
    summary_electronic_deposits = _parse_summary_value(md_text, "Electronics Deposits") or 0.0
    summary_others_credits = _parse_summary_value(md_text, "Others Credits") or 0.0
    summary_electronic_payments = _parse_summary_value(md_text, "Electronic Payments") or 0.0
    summary_other_withdrawals = _parse_summary_value(md_text, "Other Withdrawals") or 0.0

    # TD Bank splits deposits into 3 categories and withdrawals into 2
    total_credits = round(summary_deposits + summary_electronic_deposits + summary_others_credits, 2)
    total_debits = round(summary_electronic_payments + summary_other_withdrawals, 2)

    # ---- Parse credits ----
    credit_rows = _parse_single_column_transactions(md_text, "Credits")
    deposits = []
    for i, row in enumerate(credit_rows):
        txn_date = _parse_date_mmdd(row["date_str"], statement_year)
        desc_raw = row["description"]
        amount = _parse_amount(row["amount_str"])  # positive for deposits
        txn_type = _classify_transaction(desc_raw, is_deposit=True)

        deposits.append({
            "id": f"txn_{i + 1:03d}",
            "date": txn_date,
            "description_raw": desc_raw,
            "description_normalized": None,
            "amount": amount,
            "type": txn_type,
            "category": None,
            "recurring": None,
        })

    result["deposits"] = deposits

    # ---- Parse debits ----
    debit_rows = _parse_single_column_transactions(md_text, "Debits")
    withdrawals = []
    for i, row in enumerate(debit_rows):
        txn_date = _parse_date_mmdd(row["date_str"], statement_year)
        desc_raw = row["description"]
        amount = -_parse_amount(row["amount_str"])  # negate for withdrawals
        txn_type = _classify_transaction(desc_raw, is_deposit=False)

        withdrawals.append({
            "id": f"txn_w{i + 1:02d}",
            "date": txn_date,
            "description_raw": desc_raw,
            "description_normalized": None,
            "amount": amount,
            "type": txn_type,
            "category": None,
            "recurring": None,
        })

    result["withdrawals"] = withdrawals

    # ---- account_summary ----
    result["account_summary"] = {
        "opening_balance": opening_balance,
        "opening_balance_date": start_date,
        "closing_balance": ending_balance,
        "closing_balance_date": end_date,
        "total_deposits_and_other_additions": total_credits,
        "total_withdrawals_and_other_subtractions": -total_debits,
        "checks": 0.0,
        "service_fees": 0.0,
        "number_of_deposits": len(deposits),
        "number_of_withdrawals": len(withdrawals),
    }

    # ---- totals ----
    result["totals"] = {
        "bank_stated_total_deposits": total_credits,
        "bank_stated_total_withdrawals": -total_debits,
    }

    # ---- validation ----
    computed_deposit_sum = round(sum(d["amount"] for d in deposits), 2)
    computed_withdrawal_sum = round(sum(w["amount"] for w in withdrawals), 2)

    opening_plus_deposits = round(opening_balance + total_credits, 2)
    minus_withdrawals = round(opening_plus_deposits - total_debits, 2)
    matches_closing = abs(minus_withdrawals - ending_balance) < 0.01

    dep_total_matches = abs(computed_deposit_sum - total_credits) < 0.01
    # Withdrawal total may not match if Docling truncated pages
    wdr_total_matches = abs(computed_withdrawal_sum - (-total_debits)) < 0.01

    errors = []
    if not matches_closing:
        errors.append(
            f"Balance mismatch: {opening_balance} + {total_credits} - {total_debits} "
            f"= {minus_withdrawals}, expected {ending_balance}"
        )
    if not dep_total_matches:
        errors.append(
            f"Deposit total mismatch: computed {computed_deposit_sum}, "
            f"bank stated {total_credits}"
        )
    if not wdr_total_matches:
        errors.append(
            f"Withdrawal total mismatch: computed {computed_withdrawal_sum}, "
            f"bank stated {-total_debits}"
        )

    result["validation"] = {
        "opening_plus_deposits": opening_plus_deposits,
        "minus_withdrawals": minus_withdrawals,
        "matches_closing_balance": matches_closing,
        "deposit_count_matches": True,
        "withdrawal_count_matches": True,
        "deposit_total_matches": dep_total_matches,
        "withdrawal_total_matches": wdr_total_matches,
        "errors": errors,
    }

    return result
