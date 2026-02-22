"""
Parse post-processed BofA statement markdown into structured JSON.

Mechanical extraction only — regex-based parsing of clean 3-column markdown
tables. Enriched fields (description_normalized, category, recurring) are
left null for a future AI step.
"""

import re
from datetime import datetime


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_amount(s: str) -> float:
    """Parse an amount string like '-4,758.89' or '$52,101.37' into a float."""
    s = s.replace("$", "").replace(",", "").strip()
    val = float(s)
    # Normalize -0.0 to 0
    if val == 0:
        return 0.0
    return val


def _parse_date(date_str: str, statement_year: int) -> str:
    """Parse MM/DD/YY into YYYY-MM-DD, using statement year to infer century."""
    m = re.match(r"(\d{2})/(\d{2})/(\d{2})", date_str.strip())
    if not m:
        return date_str
    month, day, yy = int(m.group(1)), int(m.group(2)), int(m.group(3))
    century = (statement_year // 100) * 100
    year = century + yy
    return f"{year:04d}-{month:02d}-{day:02d}"


def _date_to_iso(date_str: str) -> str:
    """Parse 'January 14, 2023' into '2023-01-14'."""
    try:
        dt = datetime.strptime(date_str.strip(), "%B %d, %Y")
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        return date_str


def _classify_transaction(desc: str, is_deposit: bool) -> str:
    """Classify a transaction type from its raw description."""
    d = desc.upper()
    if "BILL PAYMENT" in d:
        return "bill_payment"
    if "DIRECT DEP" in d:
        return "direct_deposit"
    if "ZELLE" in d:
        return "zelle_transfer"
    if "ATM" in d and "WITHDRWL" in d:
        return "atm_withdrawal"
    if "DES:" in d:
        return "direct_deposit" if is_deposit else "ach_debit"
    if is_deposit and "DEPOSIT" in d:
        return "branch_deposit"
    return "other"


def _parse_ach_fields(desc: str) -> dict | None:
    """Extract ACH fields from a DES:-containing description.

    Uses keyword-boundary parsing to handle multi-word values and
    varying field orders produced by column merging.

    ID assignment uses INDN: as the contextual boundary:
      - standalone ID: before INDN:  → ach_company_id
      - standalone ID: after  INDN:  → ach_trace_number
      - CO ID:  → ach_trace_number (if no standalone ID after INDN:)
                   secondary_co_id  (if standalone ID already provides trace)
    """
    if "DES:" not in desc:
        return None

    fields = {}

    # Company name: everything before DES:
    des_idx = desc.index("DES:")
    fields["ach_company_name"] = desc[:des_idx].strip()

    rest = desc[des_idx + 4:]  # text after "DES:"

    # Find all keyword positions, handling CO ID: before ID: and CO
    markers = []

    # 1) CO ID: (must find first to exclude from standalone ID: and CO)
    co_id_ranges = []
    for m in re.finditer(r"CO\s+ID:", rest):
        co_id_ranges.append((m.start(), m.end()))
        markers.append((m.start(), m.end(), "co_id"))

    # 2) Standalone ID: (not part of CO ID:)
    for m in re.finditer(r"ID:", rest):
        if not any(s <= m.start() < e for s, e in co_id_ranges):
            markers.append((m.start(), m.end(), "id"))

    # 3) INDN:
    for m in re.finditer(r"INDN:", rest):
        markers.append((m.start(), m.end(), "indn"))

    # 4) SEC codes (PPD/WEB)
    for m in re.finditer(r"\b(PPD|WEB)\b", rest):
        markers.append((m.start(), m.end(), "sec"))

    # 5) Standalone CO (not part of CO ID:)
    for m in re.finditer(r"\bCO\b", rest):
        if any(s <= m.start() < e for s, e in co_id_ranges):
            continue
        after = rest[m.end():]
        if not after.lstrip().startswith("ID:"):
            markers.append((m.start(), m.end(), "co"))

    markers.sort(key=lambda x: x[0])

    # DES value: from start of rest to first marker
    first_pos = markers[0][0] if markers else len(rest)
    fields["ach_description"] = rest[:first_pos].strip()

    # Extract values between consecutive markers
    indn_pos = None          # position of INDN: marker
    co_id_value = None       # value from CO ID:
    id_entries = []          # (position, value) for standalone ID:

    for i, (start, end, ktype) in enumerate(markers):
        next_start = markers[i + 1][0] if i + 1 < len(markers) else len(rest)
        value = rest[end:next_start].strip()

        if ktype == "id":
            token = value.split()[0] if value else ""
            if token:
                id_entries.append((start, token))
        elif ktype == "co_id":
            token = value.split()[0] if value else ""
            if token:
                co_id_value = token
        elif ktype == "indn":
            if value:
                # Normalize comma spacing: "HO,ALEX" → "HO, ALEX"
                value = re.sub(r",(?!\s)", ", ", value)
                fields["ach_individual_name"] = value
            indn_pos = start
        elif ktype == "sec":
            fields["ach_entry_class"] = rest[start:end]
        elif ktype == "co":
            fields["ach_originator"] = "CO"

    # Assign standalone ID: values by context (INDN: position is the boundary)
    #   - ID: before INDN: (or no INDN:) → ach_company_id
    #   - ID: after  INDN:               → ach_trace_number
    #   - Extra IDs before INDN: when company_id already set → trace_number
    has_trace_from_id = False
    company_id_set = False
    for pos, val in id_entries:
        if indn_pos is not None and pos > indn_pos:
            fields["ach_trace_number"] = val
            has_trace_from_id = True
        elif not company_id_set:
            fields["ach_company_id"] = val
            company_id_set = True
        else:
            # Second standalone ID before INDN: → positional fallback
            fields["ach_trace_number"] = val
            has_trace_from_id = True

    # CO ID: → always a secondary company identifier
    if co_id_value:
        fields["ach_secondary_co_id"] = co_id_value

    # Remove empty values
    return {k: v for k, v in fields.items() if v} or None


def _parse_table_rows(md_text: str, section_header: str) -> list[dict]:
    """Extract rows from a markdown table following section_header.

    Returns list of dicts with keys: date_str, description, amount_str.
    """
    pattern = re.compile(
        r"^##\s+" + re.escape(section_header) + r"\s*$",
        re.MULTILINE,
    )
    match = pattern.search(md_text)
    if not match:
        return []

    remaining = md_text[match.end():]
    rows = []
    in_table = False

    for line in remaining.split("\n"):
        line = line.strip()
        if not line:
            if in_table:
                break
            continue
        if line.startswith("|"):
            if "---" in line or ("Date" in line and "Description" in line):
                in_table = True
                continue
            if not in_table:
                in_table = True
                continue
            # Parse data row
            cells = [c.strip() for c in line.split("|")]
            cells = [c for c in cells if c]
            if len(cells) >= 3:
                rows.append({
                    "date_str": cells[0],
                    "description": cells[1],
                    "amount_str": cells[2],
                })
        elif in_table:
            break

    return rows


def _extract_table_rows_from_docling(
    docling_doc: dict,
    table_type: str,
) -> list[dict] | None:
    """Extract transaction rows from a Docling JSON table using bbox spatial ordering.

    When Docling splits the Description column into multiple sub-columns
    (e.g. 4 "Description" headers), this function sorts cells by their
    bbox.l coordinate to reconstruct the original PDF left-to-right field
    order — preserving trailing CO, correct ID placement, etc.

    Returns list of {date_str, description, amount_str}, or None if no
    suitable multi-column table is found (caller should fall back to markdown).
    """
    tables = docling_doc.get("tables", [])

    for table_entry in tables:
        data = table_entry.get("data", table_entry)
        grid = data.get("grid", [])
        if not grid or len(grid) < 2:
            continue

        # Only useful when Description is split into multiple sub-columns
        num_cols = data.get("num_cols", len(grid[0]))
        if num_cols <= 3:
            continue

        # Verify this is a transaction table (has Date + Amount headers)
        header_texts = [c.get("text", "").strip().lower() for c in grid[0]]
        if not any("date" in h for h in header_texts):
            continue
        if not any("amount" in h for h in header_texts):
            continue

        # Determine if this is deposits or withdrawals by amount signs
        data_rows = grid[1:]
        if not data_rows:
            continue
        sample_amounts = []
        for row in data_rows[:3]:
            last_cell = row[-1]
            amt_text = last_cell.get("text", "").strip()
            if amt_text:
                sample_amounts.append(amt_text)
        is_negative = all(a.startswith("-") for a in sample_amounts if a)
        is_positive = all(not a.startswith("-") for a in sample_amounts if a)

        if table_type == "deposits" and not is_positive:
            continue
        if table_type == "withdrawals" and not is_negative:
            continue

        # Extract rows using bbox spatial ordering
        rows = []
        for row in data_rows:
            # Sort all cells by bbox.l (left coordinate)
            sorted_cells = sorted(
                row, key=lambda c: c.get("bbox", {}).get("l", 0)
            )

            # Deduplicate cells that share the same physical position
            seen_positions = set()
            unique_cells = []
            for cell in sorted_cells:
                bbox = cell.get("bbox", {})
                pos_key = (round(bbox.get("l", 0), 1), round(bbox.get("t", 0), 1))
                if pos_key not in seen_positions:
                    seen_positions.add(pos_key)
                    unique_cells.append(cell)

            if len(unique_cells) < 3:
                continue

            # First unique cell = date, last = amount, middle = description
            date_str = unique_cells[0].get("text", "").strip()
            amount_str = unique_cells[-1].get("text", "").strip()

            desc_fragments = []
            for cell in unique_cells[1:-1]:
                text = cell.get("text", "").strip()
                if text:
                    desc_fragments.append(text)
            description = " ".join(desc_fragments)

            rows.append({
                "date_str": date_str,
                "description": description,
                "amount_str": amount_str,
            })

        return rows

    return None


# Street suffixes for splitting address line into street + city
_STREET_SUFFIXES = (
    r"DR|ST|AVE|BLVD|CT|LN|RD|WAY|PL|CIR|TER|PKWY|HWY|LOOP|SQ|TRAIL|TRL"
)


# ---------------------------------------------------------------------------
# Main function
# ---------------------------------------------------------------------------

def markdown_to_structured_json(
    md_text: str,
    source_filename: str,
    docling_doc: dict | None = None,
) -> dict:
    """Convert post-processed BofA statement markdown into structured JSON."""

    result = {
        "metadata": {},
        "account_summary": {},
        "deposits": [],
        "withdrawals": [],
        "totals": {},
        "validation": {},
    }

    # ---- metadata.statement_period ----
    period_m = re.search(
        r"for (\w+ \d+, \d{4}) to (\w+ \d+, \d{4})", md_text
    )
    start_date = _date_to_iso(period_m.group(1)) if period_m else None
    end_date = _date_to_iso(period_m.group(2)) if period_m else None
    statement_year = int(end_date[:4]) if end_date else 2023

    # ---- metadata.account.account_type ----
    type_m = re.search(r"## Your (.+?) Preferred Rewards", md_text)
    account_type = type_m.group(1).strip() if type_m else None

    # ---- metadata.account.name ----
    # Standalone name line after the "## Your ... Preferred Rewards ..." heading
    name_m = re.search(
        r"## Your .+?Preferred Rewards.*?\n\n.*?\n\n(.+)",
        md_text,
    )
    account_name = name_m.group(1).strip() if name_m else None

    # ---- metadata.account.account_number ----
    acct_m = re.search(r"Account number:\s*([\d\s]+)", md_text)
    account_number = re.sub(r"\s+", "", acct_m.group(1)) if acct_m else None

    # ---- metadata.account.address ----
    # Combined NAME+ADDRESS line near top, e.g.:
    #   ALEX W HO 123 MAIN ST SPRINGFIELD, NJ  00000-0000
    addr_line_m = re.search(
        r"^([A-Z][A-Z\s]+\d+.+\d{5}(?:-\d{4})?)$",
        md_text,
        re.MULTILINE,
    )
    address = {}
    if addr_line_m and account_name:
        full_line = addr_line_m.group(1).strip()
        name_upper = account_name.upper()
        if name_upper in full_line:
            addr_part = full_line[
                full_line.index(name_upper) + len(name_upper) :
            ].strip()
            # Try to split street/city using street suffix
            addr_m = re.match(
                rf"(\d+.+?\b(?:{_STREET_SUFFIXES})\b)"
                rf"\s+([A-Z][A-Z\s]*?)"
                rf",\s*([A-Z]{{2}})\s+(\d{{5}}(?:-\d{{4}})?)",
                addr_part,
            )
            if addr_m:
                address = {
                    "line1": addr_m.group(1).strip(),
                    "city": addr_m.group(2).strip(),
                    "state": addr_m.group(3).strip(),
                    "zip": addr_m.group(4).strip(),
                }

    result["metadata"] = {
        "source_file": source_filename,
        "statement_period": {
            "start": start_date,
            "end": end_date,
        },
        "bank_entity": {
            "name": "Bank of America, N.A.",
            "correspondence_address": {
                "line1": "P.O. Box 15284",
                "city": "Wilmington",
                "state": "DE",
                "zip": "19850",
            },
            "customer_service_information": {
                "customer_service_address": {
                    "line1": "P.O. Box 25118",
                    "city": "Tampa",
                    "state": "FL",
                    "zip": "33622-5118",
                },
                "website": "bankofamerica.com",
                "phone_number": "1.888.888.7937",
                "En Espanol": "1.800.688.6086",
            },
        },
        "account": {
            "name": account_name,
            "account_number": account_number,
            "account_type": account_type,
            "address": address if address else None,
        },
    }

    # ---- account_summary ----
    opening_m = re.search(
        r"Beginning balance on (.+?)\s*\|\s*\$?([\d,]+\.\d+)", md_text
    )
    opening_balance = _parse_amount(opening_m.group(2)) if opening_m else 0.0
    opening_date = (
        _date_to_iso(opening_m.group(1).strip()) if opening_m else None
    )

    closing_date_m = re.search(
        r"Ending balance on (.+?)$", md_text, re.MULTILINE
    )
    closing_date = (
        _date_to_iso(closing_date_m.group(1).strip())
        if closing_date_m
        else None
    )

    # Closing balance: the $xxx line right after "Ending balance on ..."
    closing_balance = 0.0
    if closing_date_m:
        after_closing = md_text[closing_date_m.end() :]
        cb_m = re.search(r"^\$?([\d,]+\.\d+)", after_closing.strip())
        if cb_m:
            closing_balance = _parse_amount(cb_m.group(1))

    # Summary table rows
    summary_deposits = 0.0
    summary_withdrawals = 0.0
    summary_checks = 0.0
    summary_fees = 0.0

    dep_m = re.search(
        r"Deposits and other additions\s*\|\s*\$?([\d,]+\.\d+)", md_text
    )
    if dep_m:
        summary_deposits = _parse_amount(dep_m.group(1))

    wdr_m = re.search(
        r"Withdrawals and other subtractions\s*\|\s*-?\$?([\d,]+\.\d+)",
        md_text,
    )
    if wdr_m:
        summary_withdrawals = -_parse_amount(wdr_m.group(1))

    chk_m = re.search(r"Checks\s*\|\s*-?\$?([\d,]+\.\d+)", md_text)
    if chk_m:
        summary_checks = _parse_amount("-" + chk_m.group(1))

    fee_m = re.search(r"Service fees\s*\|\s*-?\$?([\d,]+\.\d+)", md_text)
    if fee_m:
        summary_fees = _parse_amount("-" + fee_m.group(1))

    # ---- Parse deposit rows ----
    # Try Docling JSON spatial ordering first (multi-column tables only),
    # fall back to markdown regex parsing.
    dep_rows = None
    if docling_doc:
        dep_rows = _extract_table_rows_from_docling(docling_doc, "deposits")
    if dep_rows is None:
        dep_rows = _parse_table_rows(md_text, "Deposits and other additions")
    deposits = []
    for i, row in enumerate(dep_rows):
        txn_date = _parse_date(row["date_str"], statement_year)
        desc_raw = row["description"]
        amount = _parse_amount(row["amount_str"])
        txn_type = _classify_transaction(desc_raw, is_deposit=True)
        parsed = _parse_ach_fields(desc_raw)

        txn = {
            "id": f"txn_{i + 1:03d}",
            "date": txn_date,
            "description_raw": desc_raw,
            "description_normalized": None,
            "amount": amount,
            "type": txn_type,
            "category": None,
            "recurring": None,
        }
        if parsed:
            txn["parsed_fields"] = parsed
        deposits.append(txn)

    result["deposits"] = deposits

    # ---- Parse withdrawal rows ----
    wdr_rows = None
    if docling_doc:
        wdr_rows = _extract_table_rows_from_docling(docling_doc, "withdrawals")
    if wdr_rows is None:
        wdr_rows = _parse_table_rows(
            md_text, "Withdrawals and other subtractions"
        )
    withdrawals = []
    for i, row in enumerate(wdr_rows):
        txn_date = _parse_date(row["date_str"], statement_year)
        desc_raw = row["description"]
        amount = _parse_amount(row["amount_str"])
        txn_type = _classify_transaction(desc_raw, is_deposit=False)
        parsed = _parse_ach_fields(desc_raw)

        txn = {
            "id": f"txn_w{i + 1:02d}",
            "date": txn_date,
            "description_raw": desc_raw,
            "description_normalized": None,
            "amount": amount,
            "type": txn_type,
            "category": None,
            "recurring": None,
        }
        if parsed:
            txn["parsed_fields"] = parsed
        withdrawals.append(txn)

    result["withdrawals"] = withdrawals

    # ---- account_summary (with counts) ----
    result["account_summary"] = {
        "opening_balance": opening_balance,
        "opening_balance_date": opening_date,
        "closing_balance": closing_balance,
        "closing_balance_date": closing_date,
        "total_deposits_and_other_additions": summary_deposits,
        "total_withdrawals_and_other_subtractions": summary_withdrawals,
        "checks": summary_checks,
        "service_fees": summary_fees,
        "number_of_deposits": len(deposits),
        "number_of_withdrawals": len(withdrawals),
    }

    # ---- totals (bank's stated totals from the summary table) ----
    # These are the bank's own numbers; identical to account_summary values
    # but kept separate for validation math.
    bank_total_deposits = summary_deposits
    bank_total_withdrawals = summary_withdrawals

    result["totals"] = {
        "bank_stated_total_deposits": bank_total_deposits,
        "bank_stated_total_withdrawals": bank_total_withdrawals,
    }

    # ---- validation ----
    computed_deposit_sum = round(sum(d["amount"] for d in deposits), 2)
    computed_withdrawal_sum = round(sum(w["amount"] for w in withdrawals), 2)

    opening_plus_deposits = round(opening_balance + bank_total_deposits, 2)
    minus_withdrawals = round(
        opening_plus_deposits + bank_total_withdrawals, 2
    )
    matches_closing = abs(minus_withdrawals - closing_balance) < 0.01

    dep_count_matches = (
        len(deposits) == result["account_summary"]["number_of_deposits"]
    )
    wdr_count_matches = (
        len(withdrawals)
        == result["account_summary"]["number_of_withdrawals"]
    )

    dep_total_matches = abs(computed_deposit_sum - bank_total_deposits) < 0.01
    wdr_total_matches = (
        abs(computed_withdrawal_sum - bank_total_withdrawals) < 0.01
    )

    errors = []
    if not matches_closing:
        errors.append(
            f"Balance mismatch: {opening_balance} + {bank_total_deposits} + "
            f"{bank_total_withdrawals} = {minus_withdrawals}, "
            f"expected {closing_balance}"
        )
    if not dep_total_matches:
        errors.append(
            f"Deposit total mismatch: computed {computed_deposit_sum}, "
            f"bank stated {bank_total_deposits}"
        )
    if not wdr_total_matches:
        errors.append(
            f"Withdrawal total mismatch: computed {computed_withdrawal_sum}, "
            f"bank stated {bank_total_withdrawals}"
        )

    result["validation"] = {
        "opening_plus_deposits": opening_plus_deposits,
        "minus_withdrawals": minus_withdrawals,
        "matches_closing_balance": matches_closing,
        "deposit_count_matches": dep_count_matches,
        "withdrawal_count_matches": wdr_count_matches,
        "deposit_total_matches": dep_total_matches,
        "withdrawal_total_matches": wdr_total_matches,
        "errors": errors,
    }

    return result
