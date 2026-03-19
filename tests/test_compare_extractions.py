#!/usr/bin/env python3
"""Deep diff between Claude-extracted and Docling-extracted structured JSONs."""

import json
import sys
from pathlib import Path


def deep_diff(a, b, path=""):
    """Recursively compare two objects, yielding (path, a_val, b_val) for diffs."""
    diffs = []
    if type(a) != type(b):
        diffs.append((path, a, b, "type_mismatch"))
        return diffs

    if isinstance(a, dict):
        all_keys = set(list(a.keys()) + list(b.keys()))
        for k in sorted(all_keys):
            p = f"{path}.{k}" if path else k
            if k not in a:
                diffs.append((p, "<MISSING>", b[k], "missing_in_claude"))
            elif k not in b:
                diffs.append((p, a[k], "<MISSING>", "missing_in_docling"))
            else:
                diffs.extend(deep_diff(a[k], b[k], p))
    elif isinstance(a, list):
        if len(a) != len(b):
            diffs.append((path + ".__len__", len(a), len(b), "length_mismatch"))
        for i in range(min(len(a), len(b))):
            diffs.extend(deep_diff(a[i], b[i], f"{path}[{i}]"))
    elif isinstance(a, float) and isinstance(b, float):
        if abs(a - b) > 0.001:
            diffs.append((path, a, b, "value_mismatch"))
    elif a != b:
        diffs.append((path, a, b, "value_mismatch"))

    return diffs


def main():
    claude_path = Path("data/statements/eStmt_2023-02-10.claude-extracted.json")
    docling_path = Path("data/extraction_output/eStmt_2023-02-10.structured.json")

    claude = json.loads(claude_path.read_text())
    docling = json.loads(docling_path.read_text())

    print("=" * 70)
    print("DIFF: Claude extraction vs Docling extraction")
    print(f"  Claude: {claude_path}")
    print(f"  Docling: {docling_path}")
    print("=" * 70)

    diffs = deep_diff(claude, docling)

    if not diffs:
        print("\nNO DIFFERENCES FOUND — perfect match!")
    else:
        print(f"\n{len(diffs)} difference(s) found:\n")
        for path, claude_val, docling_val, kind in diffs:
            print(f"  PATH: {path}")
            print(f"    Claude:  {repr(claude_val)}")
            print(f"    Docling: {repr(docling_val)}")
            print(f"    Type:    {kind}")
            print()

    # Validation check
    print("=" * 70)
    print("VALIDATION CHECK")
    print("=" * 70)
    v = claude["validation"]
    summary = claude["account_summary"]
    opening = summary["opening_balance"]
    closing = summary["closing_balance"]
    deposits_total = sum(d["amount"] for d in claude["deposits"])
    withdrawals_total = sum(w["amount"] for w in claude["withdrawals"])
    computed_closing = round(opening + deposits_total + withdrawals_total, 2)

    print(f"  Opening balance:     ${opening:,.2f}")
    print(f"  Sum of deposits:     ${deposits_total:,.2f}")
    print(f"  Sum of withdrawals:  ${withdrawals_total:,.2f}")
    print(f"  Computed closing:    ${computed_closing:,.2f}")
    print(f"  Stated closing:      ${closing:,.2f}")
    print(f"  Balance matches:     {computed_closing == closing}")
    print(f"  Deposit count:       {len(claude['deposits'])} (stated: {summary['number_of_deposits']})")
    print(f"  Withdrawal count:    {len(claude['withdrawals'])} (stated: {summary['number_of_withdrawals']})")
    print(f"  Deposit total match: {abs(deposits_total - summary['total_deposits_and_other_additions']) < 0.01}")
    print(f"  Withdrawal total match: {abs(withdrawals_total - summary['total_withdrawals_and_other_subtractions']) < 0.01}")
    print(f"\n  ALL VALIDATION: {'PASS' if all(v[k] for k in v if isinstance(v[k], bool)) else 'FAIL'}")

    # Transaction-level comparison
    print("\n" + "=" * 70)
    print("TRANSACTION COMPARISON")
    print("=" * 70)
    for section in ["deposits", "withdrawals"]:
        claude_txns = claude[section]
        docling_txns = docling[section]
        print(f"\n  {section.upper()}: Claude={len(claude_txns)}, Docling={len(docling_txns)}")
        for i in range(min(len(claude_txns), len(docling_txns))):
            ct, dt = claude_txns[i], docling_txns[i]
            amount_match = abs(ct["amount"] - dt["amount"]) < 0.01
            date_match = ct["date"] == dt["date"]
            type_match = ct["type"] == dt["type"]
            status = "OK" if (amount_match and date_match and type_match) else "DIFF"
            print(f"    [{status}] {ct['id']}: date={ct['date']} amount={ct['amount']} type={ct['type']}")
            if not amount_match:
                print(f"           AMOUNT DIFF: Claude={ct['amount']} vs Docling={dt['amount']}")
            if not date_match:
                print(f"           DATE DIFF: Claude={ct['date']} vs Docling={dt['date']}")
            if not type_match:
                print(f"           TYPE DIFF: Claude={ct['type']} vs Docling={dt['type']}")


if __name__ == "__main__":
    main()
