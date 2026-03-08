"""
Experiment: Claude extraction determinism test.

Sends the same BofA PDF statement to Claude N times with temperature=0
and compares the structured JSON outputs to measure extraction consistency.

Usage:
    source bin/start.sh
    python core/experiments/claude_determinism.py
"""

import base64
import hashlib
import json
import os
import sys
import time
from itertools import combinations
from pathlib import Path

import anthropic

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
PDF_PATH = PROJECT_ROOT / "data" / "statements" / "eStmt_2023-02-10.pdf"
REFERENCE_PATH = PROJECT_ROOT / "data" / "statements" / "eStmt_2023-02-10.claude-extracted.json"
OUTPUT_DIR = PROJECT_ROOT / "data" / "experiments"
NUM_RUNS = 10
MODEL = "claude-sonnet-4-20250514"

# The JSON schema template, derived from the reference extraction
SCHEMA_TEMPLATE = """\
{
    "metadata": {
        "source_file": "<string: PDF filename>",
        "statement_period": {
            "start": "<YYYY-MM-DD>",
            "end": "<YYYY-MM-DD>"
        },
        "bank_entity": {
            "name": "<string>",
            "correspondence_address": {
                "line1": "<string>",
                "city": "<string>",
                "state": "<string>",
                "zip": "<string>"
            },
            "customer_service_information": {
                "customer_service_address": {
                    "line1": "<string>",
                    "city": "<string>",
                    "state": "<string>",
                    "zip": "<string>"
                },
                "website": "<string>",
                "phone_number": "<string>",
                "En Espanol": "<string>"
            }
        },
        "account": {
            "name": "<string: account holder name>",
            "account_number": "<string: full account number>",
            "account_type": "<string: e.g. Regular Checking>",
            "address": {
                "line1": "<string>",
                "city": "<string>",
                "state": "<string>",
                "zip": "<string>"
            }
        }
    },
    "account_summary": {
        "opening_balance": "<number>",
        "opening_balance_date": "<YYYY-MM-DD>",
        "closing_balance": "<number>",
        "closing_balance_date": "<YYYY-MM-DD>",
        "total_deposits_and_other_additions": "<number: positive>",
        "total_withdrawals_and_other_subtractions": "<number: negative>",
        "checks": "<number>",
        "service_fees": "<number>",
        "number_of_deposits": "<integer>",
        "number_of_withdrawals": "<integer>"
    },
    "deposits": [
        {
            "id": "txn_001",
            "date": "<YYYY-MM-DD>",
            "description_raw": "<string: exact text from statement>",
            "description_normalized": null,
            "amount": "<number: positive>",
            "type": "<branch_deposit|direct_deposit|ach_credit|other>",
            "category": null,
            "recurring": null,
            "parsed_fields": {
                "ach_company_name": "<string: if ACH/DES: present>",
                "ach_description": "<string: value after DES:>",
                "ach_individual_name": "<string: value after INDN:>",
                "ach_entry_class": "<PPD|WEB|CCD>",
                "ach_company_id": "<string: value after ID:>",
                "ach_secondary_co_id": "<string: value after CO ID:>"
            }
        }
    ],
    "withdrawals": [
        {
            "id": "txn_w01",
            "date": "<YYYY-MM-DD>",
            "description_raw": "<string: exact text from statement>",
            "description_normalized": null,
            "amount": "<number: negative>",
            "type": "<bill_payment|ach_debit|zelle_transfer|atm_withdrawal|other>",
            "category": null,
            "recurring": null,
            "parsed_fields": {
                "ach_company_name": "<string: if ACH/DES: present>",
                "ach_description": "<string: value after DES:>",
                "ach_individual_name": "<string: value after INDN:>",
                "ach_entry_class": "<PPD|WEB|CCD>",
                "ach_company_id": "<string: value after ID:>",
                "ach_secondary_co_id": "<string: value after CO ID:>"
            }
        }
    ],
    "totals": {
        "bank_stated_total_deposits": "<number: positive>",
        "bank_stated_total_withdrawals": "<number: negative>"
    },
    "validation": {
        "opening_plus_deposits": "<number: opening_balance + total_deposits>",
        "minus_withdrawals": "<number: opening_plus_deposits + total_withdrawals>",
        "matches_closing_balance": "<boolean>",
        "deposit_count_matches": "<boolean>",
        "withdrawal_count_matches": "<boolean>",
        "deposit_total_matches": "<boolean>",
        "withdrawal_total_matches": "<boolean>",
        "errors": []
    }
}"""

EXTRACTION_PROMPT = f"""\
Extract all data from this Bank of America checking account statement into structured JSON.

## Requirements

1. **Metadata**: Extract the statement period, bank entity info, account holder name, account number, account type, and mailing address exactly as printed.

2. **Account Summary**: Extract opening/closing balances with dates, total deposits, total withdrawals, checks, service fees, and transaction counts.

3. **Transactions**: Extract every deposit and withdrawal with:
   - Sequential IDs: deposits as txn_001, txn_002, ...; withdrawals as txn_w01, txn_w02, ...
   - Dates in YYYY-MM-DD format
   - description_raw: the EXACT text from the statement, preserving original spacing and punctuation
   - amount: positive for deposits, negative for withdrawals
   - type: classify as branch_deposit, direct_deposit, ach_credit, bill_payment, ach_debit, zelle_transfer, atm_withdrawal, or other
   - For ACH transactions (those containing "DES:"), extract parsed_fields with: ach_company_name (text before DES:), ach_description (text after DES:), ach_individual_name (text after INDN:, normalize "HO,ALEX" to "HO, ALEX"), ach_entry_class (PPD/WEB/CCD), ach_company_id (value after standalone ID:), ach_secondary_co_id (value after CO ID:)
   - Only include parsed_fields for transactions that have ACH fields (contain "DES:")
   - Set description_normalized, category, and recurring to null

4. **Totals**: Record the bank's stated total deposits (positive) and total withdrawals (negative).

5. **Validation**: Compute:
   - opening_plus_deposits = opening_balance + total_deposits
   - minus_withdrawals = opening_plus_deposits + total_withdrawals (should equal closing_balance)
   - Check that computed deposit/withdrawal sums match bank stated totals
   - Check that transaction counts match

## Output Schema

Return ONLY valid JSON matching this exact structure (no markdown fencing, no commentary):

{SCHEMA_TEMPLATE}

Important:
- Use the exact field names shown above
- All amounts are numbers (not strings)
- Dates are YYYY-MM-DD strings
- description_raw must be the exact text from the PDF
- Include parsed_fields ONLY on transactions containing "DES:" in description_raw
- Withdrawals amounts are negative numbers
"""


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def extract_once(client: anthropic.Anthropic, pdf_b64: str, run_num: int) -> dict:
    """Send the PDF to Claude and return the parsed JSON response."""
    print(f"  Run {run_num}/{NUM_RUNS}: sending to {MODEL}...", end=" ", flush=True)
    t0 = time.time()

    response = client.messages.create(
        model=MODEL,
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
                        "text": EXTRACTION_PROMPT,
                    },
                ],
            }
        ],
    )

    elapsed = time.time() - t0
    print(f"done ({elapsed:.1f}s)")

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


# ---------------------------------------------------------------------------
# Validation (ported from structurize.py)
# ---------------------------------------------------------------------------

def validate_extraction(data: dict) -> dict:
    """Run validation checks on an extraction result."""
    summary = data.get("account_summary", {})
    deposits = data.get("deposits", [])
    withdrawals = data.get("withdrawals", [])
    totals = data.get("totals", {})

    opening = summary.get("opening_balance", 0)
    closing = summary.get("closing_balance", 0)
    bank_dep = totals.get("bank_stated_total_deposits", 0)
    bank_wdr = totals.get("bank_stated_total_withdrawals", 0)

    computed_dep = round(sum(d.get("amount", 0) for d in deposits), 2)
    computed_wdr = round(sum(w.get("amount", 0) for w in withdrawals), 2)

    opening_plus = round(opening + bank_dep, 2)
    minus_wdr = round(opening_plus + bank_wdr, 2)

    return {
        "balance_check": abs(minus_wdr - closing) < 0.01,
        "deposit_count": len(deposits),
        "withdrawal_count": len(withdrawals),
        "deposit_sum": computed_dep,
        "withdrawal_sum": computed_wdr,
        "deposit_total_matches": abs(computed_dep - bank_dep) < 0.01,
        "withdrawal_total_matches": abs(computed_wdr - bank_wdr) < 0.01,
        "opening_plus_deposits": opening_plus,
        "computed_closing": minus_wdr,
        "stated_closing": closing,
    }


# ---------------------------------------------------------------------------
# Diffing
# ---------------------------------------------------------------------------

def deep_diff(a, b, path="") -> list[str]:
    """Recursively diff two JSON-like structures. Returns list of difference descriptions."""
    diffs = []

    if type(a) != type(b):
        diffs.append(f"{path}: type mismatch ({type(a).__name__} vs {type(b).__name__})")
        return diffs

    if isinstance(a, dict):
        all_keys = sorted(set(list(a.keys()) + list(b.keys())))
        for key in all_keys:
            child_path = f"{path}.{key}" if path else key
            if key not in a:
                diffs.append(f"{child_path}: missing in A")
            elif key not in b:
                diffs.append(f"{child_path}: missing in B")
            else:
                diffs.extend(deep_diff(a[key], b[key], child_path))
    elif isinstance(a, list):
        if len(a) != len(b):
            diffs.append(f"{path}: list length {len(a)} vs {len(b)}")
        for i in range(min(len(a), len(b))):
            diffs.extend(deep_diff(a[i], b[i], f"{path}[{i}]"))
    else:
        if a != b:
            a_str = repr(a) if len(repr(a)) < 80 else repr(a)[:77] + "..."
            b_str = repr(b) if len(repr(b)) < 80 else repr(b)[:77] + "..."
            diffs.append(f"{path}: {a_str} != {b_str}")

    return diffs


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    # API key
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        api_key = input("Enter your ANTHROPIC_API_KEY: ").strip()
        if not api_key:
            print("Error: No API key provided.")
            sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)

    # Read PDF
    print(f"Reading PDF: {PDF_PATH}")
    pdf_bytes = PDF_PATH.read_bytes()
    pdf_b64 = base64.standard_b64encode(pdf_bytes).decode("ascii")
    print(f"  PDF size: {len(pdf_bytes):,} bytes ({len(pdf_b64):,} base64 chars)")

    # Run extractions
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    results = {}
    hashes = {}

    print(f"\nRunning {NUM_RUNS} extractions with {MODEL} (temperature=0):\n")

    for i in range(1, NUM_RUNS + 1):
        data = extract_once(client, pdf_b64, i)
        out_path = OUTPUT_DIR / f"claude-extract-run-{i}.json"
        json_str = json.dumps(data, indent=4)
        out_path.write_text(json_str + "\n")
        results[i] = data
        hashes[i] = hashlib.sha256(json_str.encode()).hexdigest()
        print(f"    Saved: {out_path.relative_to(PROJECT_ROOT)}")

    # ---------------------------------------------------------------------------
    # Analysis
    # ---------------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("DETERMINISM ANALYSIS")
    print("=" * 70)

    # 1. Hash comparison
    print("\n--- SHA256 Hashes ---")
    unique_hashes = set(hashes.values())
    for i in sorted(hashes):
        print(f"  Run {i}: {hashes[i][:16]}...")
    print(f"\n  Unique hashes: {len(unique_hashes)} / {NUM_RUNS}")
    if len(unique_hashes) == 1:
        print("  RESULT: All runs are BYTE-IDENTICAL")
    else:
        print("  RESULT: Outputs DIFFER across runs")

    # 2. Pairwise diffs
    print("\n--- Pairwise Diffs ---")
    pairs = list(combinations(range(1, NUM_RUNS + 1), 2))
    total_diffs = 0
    diff_summary = {}

    for a, b in pairs:
        diffs = deep_diff(results[a], results[b])
        label = f"Run {a} vs Run {b}"
        if not diffs:
            print(f"  {label}: identical")
        else:
            total_diffs += len(diffs)
            print(f"  {label}: {len(diffs)} difference(s)")
            for d in diffs[:5]:  # Show first 5
                print(f"    - {d}")
            if len(diffs) > 5:
                print(f"    ... and {len(diffs) - 5} more")

            # Track which fields differ
            for d in diffs:
                field = d.split(":")[0]
                diff_summary[field] = diff_summary.get(field, 0) + 1

    print(f"\n  Total differences across all {len(pairs)} pairs: {total_diffs}")

    if diff_summary:
        print("\n--- Most Variable Fields ---")
        for field, count in sorted(diff_summary.items(), key=lambda x: -x[1]):
            print(f"  {field}: differs in {count}/{len(pairs)} pairs")

    # 3. Validation per run
    print("\n--- Validation Per Run ---")
    all_valid = True
    for i in sorted(results):
        v = validate_extraction(results[i])
        status = "PASS" if (v["balance_check"] and v["deposit_total_matches"] and v["withdrawal_total_matches"]) else "FAIL"
        if status == "FAIL":
            all_valid = False
        print(f"  Run {i}: {status}  "
              f"(balance={'ok' if v['balance_check'] else 'MISMATCH'}, "
              f"deps={v['deposit_count']}, wdrs={v['withdrawal_count']}, "
              f"dep_sum=${v['deposit_sum']:,.2f}, wdr_sum=${v['withdrawal_sum']:,.2f})")

    # 4. Key amount consistency
    print("\n--- Amount Consistency ---")
    amount_fields = [
        ("account_summary.opening_balance", lambda d: d["account_summary"]["opening_balance"]),
        ("account_summary.closing_balance", lambda d: d["account_summary"]["closing_balance"]),
        ("account_summary.total_deposits", lambda d: d["account_summary"]["total_deposits_and_other_additions"]),
        ("account_summary.total_withdrawals", lambda d: d["account_summary"]["total_withdrawals_and_other_subtractions"]),
    ]
    for label, getter in amount_fields:
        values = set()
        for i in sorted(results):
            try:
                values.add(getter(results[i]))
            except (KeyError, TypeError):
                values.add("MISSING")
        if len(values) == 1:
            print(f"  {label}: consistent ({values.pop()})")
        else:
            print(f"  {label}: VARIES {values}")

    # 5. Transaction amount consistency
    print("\n--- Transaction Amount Consistency ---")
    for txn_type in ["deposits", "withdrawals"]:
        max_len = max(len(results[i].get(txn_type, [])) for i in results)
        for idx in range(max_len):
            amounts = {}
            for i in sorted(results):
                txns = results[i].get(txn_type, [])
                if idx < len(txns):
                    amounts[i] = txns[idx].get("amount")
                else:
                    amounts[i] = "MISSING"
            unique = set(amounts.values())
            if len(unique) > 1:
                print(f"  {txn_type}[{idx}]: VARIES {amounts}")

    all_amounts_consistent = True
    for txn_type in ["deposits", "withdrawals"]:
        max_len = max(len(results[i].get(txn_type, [])) for i in results)
        for idx in range(max_len):
            amounts = set()
            for i in results:
                txns = results[i].get(txn_type, [])
                if idx < len(txns):
                    amounts.add(txns[idx].get("amount"))
            if len(amounts) > 1:
                all_amounts_consistent = False

    if all_amounts_consistent:
        print("  All transaction amounts are consistent across runs.")

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"  Runs:              {NUM_RUNS}")
    print(f"  Model:             {MODEL}")
    print(f"  Temperature:       0")
    print(f"  Byte-identical:    {'YES' if len(unique_hashes) == 1 else 'NO'}")
    print(f"  Total field diffs: {total_diffs}")
    print(f"  All valid:         {'YES' if all_valid else 'NO'}")
    print(f"  Amounts consistent:{'YES' if all_amounts_consistent else 'NO'}")
    print("=" * 70)


if __name__ == "__main__":
    main()
