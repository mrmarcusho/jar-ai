"""
Extraction pipeline test driver.

Runs Docling and/or Claude extraction on bank statement PDFs in
data/test_pdfs/bank/ and produces a cross-validation comparison report.

Usage:
    source bin/start.sh
    python tests/test_driver.py --engine both --limit 5
    python tests/test_driver.py --engine docling --limit 3
    python tests/test_driver.py --report-only
"""

import argparse
import base64
import difflib
import json
import os
import sys
import time
import traceback
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup — allow imports from sibling modules without __init__.py changes
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
ENGINE_DIR = Path(__file__).resolve().parent
CORE_DIR = ENGINE_DIR.parent

sys.path.insert(0, str(ENGINE_DIR))
sys.path.insert(0, str(CORE_DIR))

from compare_extractions import deep_diff

# Lazy-load extraction prompt and model from claude_determinism
# (that module imports anthropic at top level, which may not be installed)
_CLAUDE_CONFIG = {}

def _get_claude_config():
    if not _CLAUDE_CONFIG:
        sys.path.insert(0, str(CORE_DIR / "experiments"))
        from claude_determinism import EXTRACTION_PROMPT, MODEL
        _CLAUDE_CONFIG["EXTRACTION_PROMPT"] = EXTRACTION_PROMPT
        _CLAUDE_CONFIG["MODEL"] = MODEL
    return _CLAUDE_CONFIG

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
TEST_PDF_DIR = PROJECT_ROOT / "data" / "test_pdfs" / "bank"
OUTPUT_DIR = TEST_PDF_DIR / "extraction_output"
DOCLING_DIR = OUTPUT_DIR / "docling"
CLAUDE_DIR = OUTPUT_DIR / "claude"
REPORT_PATH = OUTPUT_DIR / "comparison_report.md"


# ---------------------------------------------------------------------------
# Phase 1: Discovery
# ---------------------------------------------------------------------------

def discover_pdfs(limit=None):
    """Find all PDFs in the test directory."""
    pdfs = sorted(TEST_PDF_DIR.glob("*.pdf"))
    if limit:
        pdfs = pdfs[:limit]
    return pdfs


# ---------------------------------------------------------------------------
# Phase 2a: Docling extraction
# ---------------------------------------------------------------------------

def run_docling(pdf_path, skip_existing=False):
    """Run Docling preprocessing + postprocessing on a single PDF.

    Returns (structured_json_dict, elapsed_seconds) or (None, elapsed) on error.
    """
    stem = pdf_path.stem
    structured_path = DOCLING_DIR / f"{stem}.structured.json"

    if skip_existing and structured_path.exists():
        data = json.loads(structured_path.read_text())
        return data, 0.0

    DOCLING_DIR.mkdir(parents=True, exist_ok=True)

    # Use a temp working dir inside DOCLING_DIR for intermediate files
    work_dir = DOCLING_DIR / "_work"
    work_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    try:
        from run_preprocess import preprocess_pdf  # noqa: lazy import (docling dep)
        md_path, json_path = preprocess_pdf(pdf_path, work_dir)

        from run_postprocess import postprocess_and_structurize  # noqa: lazy import
        structured_path_out = postprocess_and_structurize(
            md_path, json_path, DOCLING_DIR, stem
        )

        elapsed = time.time() - t0
        data = json.loads(structured_path_out.read_text())
        return data, elapsed
    except Exception as e:
        elapsed = time.time() - t0
        # Save error info
        err_path = DOCLING_DIR / f"{stem}.error.txt"
        err_path.write_text(f"{type(e).__name__}: {e}\n\n{traceback.format_exc()}")
        return None, elapsed


# ---------------------------------------------------------------------------
# Phase 2b: Claude extraction
# ---------------------------------------------------------------------------

def run_claude(pdf_path, client, skip_existing=False):
    """Send a PDF to Claude for extraction.

    Returns (structured_json_dict, elapsed_seconds) or (None, elapsed) on error.
    """
    stem = pdf_path.stem
    structured_path = CLAUDE_DIR / f"{stem}.structured.json"

    if skip_existing and structured_path.exists():
        data = json.loads(structured_path.read_text())
        return data, 0.0

    CLAUDE_DIR.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    try:
        cfg = _get_claude_config()
        pdf_bytes = pdf_path.read_bytes()
        pdf_b64 = base64.standard_b64encode(pdf_bytes).decode("ascii")

        response = client.messages.create(
            model=cfg["MODEL"],
            max_tokens=16384,
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
                        {"type": "text", "text": cfg["EXTRACTION_PROMPT"]},
                    ],
                }
            ],
        )

        # Extract text from response
        text = ""
        for block in response.content:
            if hasattr(block, "text"):
                text += block.text

        # Strip markdown fencing if present
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

        data = json.loads(text)
        elapsed = time.time() - t0

        # Save output
        structured_path.write_text(json.dumps(data, indent=4))
        return data, elapsed
    except Exception as e:
        elapsed = time.time() - t0
        err_path = CLAUDE_DIR / f"{stem}.error.txt"
        err_path.write_text(f"{type(e).__name__}: {e}\n\n{traceback.format_exc()}")
        return None, elapsed


# ---------------------------------------------------------------------------
# Phase 3: Comparison
# ---------------------------------------------------------------------------

def match_transactions(txns_a, txns_b):
    """Match transactions from two lists by (date, abs(amount)).

    Returns list of (txn_a, txn_b) pairs. Unmatched transactions get None
    as the partner.
    """
    matched = []
    used_b = set()

    for ta in txns_a:
        best_idx = None
        best_score = 0.0
        ta_key = (ta.get("date"), abs(ta.get("amount", 0)))

        for j, tb in enumerate(txns_b):
            if j in used_b:
                continue
            tb_key = (tb.get("date"), abs(tb.get("amount", 0)))

            # Primary match: date + amount
            if ta_key == tb_key:
                # Tiebreak by description similarity
                desc_a = ta.get("description_raw", "")
                desc_b = tb.get("description_raw", "")
                score = difflib.SequenceMatcher(None, desc_a, desc_b).ratio()
                if best_idx is None or score > best_score:
                    best_idx = j
                    best_score = score

        if best_idx is not None:
            used_b.add(best_idx)
            matched.append((ta, txns_b[best_idx]))
        else:
            matched.append((ta, None))

    # Add unmatched from B
    for j, tb in enumerate(txns_b):
        if j not in used_b:
            matched.append((None, tb))

    return matched


def compare_single(docling_data, claude_data, stem):
    """Compare Docling vs Claude extraction for a single PDF.

    Returns a comparison dict with match rates and details.
    """
    result = {
        "stem": stem,
        "categories": {},
        "metadata_diffs": [],
        "summary_diffs": [],
        "overall": {},
    }

    # Metadata / summary diffs using deep_diff
    for section in ["metadata", "account_summary", "totals", "validation"]:
        d_section = docling_data.get(section, {})
        c_section = claude_data.get(section, {})
        diffs = deep_diff(d_section, c_section, section)
        if section == "metadata":
            result["metadata_diffs"] = diffs
        else:
            result["summary_diffs"].extend(diffs)

    # Transaction comparison by category
    total_matched = 0
    total_amount_match = 0
    total_date_match = 0
    total_desc_sim = 0.0
    total_type_match = 0
    total_txns = 0

    for category in ["deposits", "withdrawals", "checks"]:
        d_txns = docling_data.get(category, [])
        c_txns = claude_data.get(category, [])

        pairs = match_transactions(d_txns, c_txns)
        cat_stats = {
            "docling_count": len(d_txns),
            "claude_count": len(c_txns),
            "matched": 0,
            "amount_match": 0,
            "date_match": 0,
            "desc_similarity": 0.0,
            "type_match": 0,
            "unmatched_docling": 0,
            "unmatched_claude": 0,
            "pair_details": [],
        }

        for ta, tb in pairs:
            if ta is None:
                cat_stats["unmatched_claude"] += 1
                continue
            if tb is None:
                cat_stats["unmatched_docling"] += 1
                continue

            cat_stats["matched"] += 1
            total_matched += 1
            total_txns += 1

            # Amount match
            amt_match = abs(abs(ta.get("amount", 0)) - abs(tb.get("amount", 0))) < 0.01
            if amt_match:
                cat_stats["amount_match"] += 1
                total_amount_match += 1

            # Date match
            dt_match = ta.get("date") == tb.get("date")
            if dt_match:
                cat_stats["date_match"] += 1
                total_date_match += 1

            # Description similarity
            desc_a = ta.get("description_raw", "")
            desc_b = tb.get("description_raw", "")
            sim = difflib.SequenceMatcher(None, desc_a, desc_b).ratio()
            cat_stats["desc_similarity"] += sim
            total_desc_sim += sim

            # Type match
            tp_match = ta.get("type") == tb.get("type")
            if tp_match:
                cat_stats["type_match"] += 1
                total_type_match += 1

            cat_stats["pair_details"].append({
                "docling_id": ta.get("id"),
                "claude_id": tb.get("id"),
                "amount_match": amt_match,
                "date_match": dt_match,
                "desc_similarity": round(sim, 3),
                "type_match": tp_match,
            })

        if cat_stats["matched"] > 0:
            cat_stats["desc_similarity"] = round(
                cat_stats["desc_similarity"] / cat_stats["matched"], 3
            )

        result["categories"][category] = cat_stats

    # Overall rates
    if total_txns > 0:
        result["overall"] = {
            "total_matched": total_matched,
            "amount_match_rate": round(total_amount_match / total_txns, 3),
            "date_match_rate": round(total_date_match / total_txns, 3),
            "avg_desc_similarity": round(total_desc_sim / total_txns, 3),
            "type_match_rate": round(total_type_match / total_txns, 3),
        }
    else:
        result["overall"] = {
            "total_matched": 0,
            "amount_match_rate": 0,
            "date_match_rate": 0,
            "avg_desc_similarity": 0,
            "type_match_rate": 0,
        }

    # Confidence tier
    o = result["overall"]
    if (o["amount_match_rate"] >= 0.95 and o["date_match_rate"] >= 0.95
            and o["avg_desc_similarity"] >= 0.8):
        result["confidence"] = "High"
    elif o["amount_match_rate"] >= 0.8 and o["date_match_rate"] >= 0.8:
        result["confidence"] = "Medium"
    else:
        result["confidence"] = "Low"

    # Balance reconciliation
    d_val = docling_data.get("validation", {})
    c_val = claude_data.get("validation", {})
    result["balance_reconciliation"] = {
        "docling_balance_ok": d_val.get("matches_closing_balance", False),
        "claude_balance_ok": c_val.get("matches_closing_balance", False),
    }

    return result


# ---------------------------------------------------------------------------
# Phase 4: Report generation
# ---------------------------------------------------------------------------

def generate_report(comparisons, docling_results, claude_results, pdf_list):
    """Generate comparison_report.md from comparison data."""
    lines = []
    w = lines.append

    w("# Extraction Pipeline Comparison Report")
    w("")
    w(f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    w(f"PDFs tested: {len(pdf_list)}")
    w("")

    # --------------- Summary scorecard ---------------
    w("## Summary Scorecard")
    w("")

    docling_ok = sum(1 for s in pdf_list if s.stem in docling_results and docling_results[s.stem] is not None)
    claude_ok = sum(1 for s in pdf_list if s.stem in claude_results and claude_results[s.stem] is not None)
    both_ok = sum(1 for c in comparisons if c is not None)

    w(f"| Metric | Value |")
    w(f"|--------|-------|")
    w(f"| Total PDFs | {len(pdf_list)} |")
    w(f"| Docling succeeded | {docling_ok} |")
    w(f"| Claude succeeded | {claude_ok} |")
    w(f"| Both succeeded (comparable) | {both_ok} |")

    if comparisons:
        valid = [c for c in comparisons if c is not None]
        if valid:
            avg_amt = sum(c["overall"]["amount_match_rate"] for c in valid) / len(valid)
            avg_dt = sum(c["overall"]["date_match_rate"] for c in valid) / len(valid)
            avg_desc = sum(c["overall"]["avg_desc_similarity"] for c in valid) / len(valid)
            avg_type = sum(c["overall"]["type_match_rate"] for c in valid) / len(valid)

            w(f"| Avg amount match rate | {avg_amt:.1%} |")
            w(f"| Avg date match rate | {avg_dt:.1%} |")
            w(f"| Avg description similarity | {avg_desc:.1%} |")
            w(f"| Avg type match rate | {avg_type:.1%} |")

            # Confidence tiers
            high = sum(1 for c in valid if c["confidence"] == "High")
            med = sum(1 for c in valid if c["confidence"] == "Medium")
            low = sum(1 for c in valid if c["confidence"] == "Low")
            w(f"| High confidence | {high} |")
            w(f"| Medium confidence | {med} |")
            w(f"| Low confidence | {low} |")

    w("")

    # --------------- Per-statement detail ---------------
    w("## Per-Statement Detail")
    w("")

    for pdf_path in pdf_list:
        stem = pdf_path.stem
        w(f"### {pdf_path.name}")
        w("")

        d_data = docling_results.get(stem)
        c_data = claude_results.get(stem)

        if d_data is None and c_data is None:
            w("- Docling: **FAILED**")
            w("- Claude: **FAILED**")
            w("")
            continue

        if d_data is None:
            w("- Docling: **FAILED**")
            err_path = DOCLING_DIR / f"{stem}.error.txt"
            if err_path.exists():
                first_line = err_path.read_text().split("\n")[0]
                w(f"  - Error: `{first_line}`")
        else:
            d_deps = len(d_data.get("deposits", []))
            d_wdrs = len(d_data.get("withdrawals", []))
            d_chks = len(d_data.get("checks", []))
            w(f"- Docling: {d_deps} deposits, {d_wdrs} withdrawals, {d_chks} checks")

        if c_data is None:
            w("- Claude: **FAILED**")
            err_path = CLAUDE_DIR / f"{stem}.error.txt"
            if err_path.exists():
                first_line = err_path.read_text().split("\n")[0]
                w(f"  - Error: `{first_line}`")
        else:
            c_deps = len(c_data.get("deposits", []))
            c_wdrs = len(c_data.get("withdrawals", []))
            c_chks = len(c_data.get("checks", []))
            w(f"- Claude: {c_deps} deposits, {c_wdrs} withdrawals, {c_chks} checks")

        # Find comparison for this stem
        comp = None
        for c in comparisons:
            if c is not None and c["stem"] == stem:
                comp = c
                break

        if comp:
            o = comp["overall"]
            w(f"- **Confidence: {comp['confidence']}**")
            w(f"- Matched txns: {o['total_matched']} | "
              f"Amount: {o['amount_match_rate']:.0%} | "
              f"Date: {o['date_match_rate']:.0%} | "
              f"Desc: {o['avg_desc_similarity']:.0%} | "
              f"Type: {o['type_match_rate']:.0%}")
            br = comp["balance_reconciliation"]
            w(f"- Balance: Docling={'OK' if br['docling_balance_ok'] else 'FAIL'}, "
              f"Claude={'OK' if br['claude_balance_ok'] else 'FAIL'}")

            # Category breakdown
            for cat_name, cat in comp["categories"].items():
                if cat["docling_count"] > 0 or cat["claude_count"] > 0:
                    w(f"  - {cat_name}: D={cat['docling_count']} C={cat['claude_count']} "
                      f"matched={cat['matched']} "
                      f"unmatched_D={cat['unmatched_docling']} "
                      f"unmatched_C={cat['unmatched_claude']}")

        w("")

    # --------------- Aggregate metrics ---------------
    w("## Aggregate Metrics")
    w("")

    valid_comps = [c for c in comparisons if c is not None]
    if valid_comps:
        # Disagreement breakdown
        all_amt_mismatches = 0
        all_dt_mismatches = 0
        all_type_mismatches = 0
        all_unmatched_d = 0
        all_unmatched_c = 0

        for comp in valid_comps:
            for cat in comp["categories"].values():
                for pd in cat["pair_details"]:
                    if not pd["amount_match"]:
                        all_amt_mismatches += 1
                    if not pd["date_match"]:
                        all_dt_mismatches += 1
                    if not pd["type_match"]:
                        all_type_mismatches += 1
                all_unmatched_d += cat["unmatched_docling"]
                all_unmatched_c += cat["unmatched_claude"]

        w("| Disagreement Type | Count |")
        w("|-------------------|-------|")
        w(f"| Amount mismatches | {all_amt_mismatches} |")
        w(f"| Date mismatches | {all_dt_mismatches} |")
        w(f"| Type mismatches | {all_type_mismatches} |")
        w(f"| Unmatched in Docling only | {all_unmatched_d} |")
        w(f"| Unmatched in Claude only | {all_unmatched_c} |")
        w("")
    else:
        w("No comparable results to aggregate.")
        w("")

    # --------------- Failure analysis ---------------
    w("## Failure Analysis")
    w("")

    docling_failures = []
    claude_failures = []
    for pdf_path in pdf_list:
        stem = pdf_path.stem
        if docling_results.get(stem) is None:
            err_path = DOCLING_DIR / f"{stem}.error.txt"
            err = err_path.read_text().split("\n")[0] if err_path.exists() else "unknown"
            docling_failures.append((pdf_path.name, err))
        if claude_results.get(stem) is None:
            err_path = CLAUDE_DIR / f"{stem}.error.txt"
            err = err_path.read_text().split("\n")[0] if err_path.exists() else "unknown"
            claude_failures.append((pdf_path.name, err))

    if docling_failures:
        w(f"### Docling Failures ({len(docling_failures)})")
        w("")
        for name, err in docling_failures:
            w(f"- `{name}`: {err}")
        w("")

    if claude_failures:
        w(f"### Claude Failures ({len(claude_failures)})")
        w("")
        for name, err in claude_failures:
            w(f"- `{name}`: {err}")
        w("")

    if not docling_failures and not claude_failures:
        w("No failures.")
        w("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Extraction pipeline test driver"
    )
    parser.add_argument(
        "--engine",
        choices=["docling", "claude", "both"],
        default="both",
        help="Which extraction engine(s) to run (default: both)",
    )
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="Regenerate report from existing JSON outputs",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process only first N PDFs",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip PDFs that already have output",
    )
    args = parser.parse_args()

    # Phase 1: Discovery
    pdfs = discover_pdfs(args.limit)
    if not pdfs:
        print("ERROR: No PDFs found in", TEST_PDF_DIR)
        sys.exit(1)
    print(f"Found {len(pdfs)} PDFs in {TEST_PDF_DIR}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Stores: stem -> structured_json_dict or None
    docling_results = {}
    claude_results = {}

    if args.report_only:
        # Load existing outputs
        print("Loading existing outputs...")
        for pdf_path in pdfs:
            stem = pdf_path.stem
            d_path = DOCLING_DIR / f"{stem}.structured.json"
            c_path = CLAUDE_DIR / f"{stem}.structured.json"
            if d_path.exists():
                docling_results[stem] = json.loads(d_path.read_text())
            else:
                docling_results[stem] = None
            if c_path.exists():
                claude_results[stem] = json.loads(c_path.read_text())
            else:
                claude_results[stem] = None
    else:
        # Set up Claude client if needed
        client = None
        if args.engine in ("claude", "both"):
            import anthropic
            api_key = os.environ.get("ANTHROPIC_API_KEY")
            if not api_key:
                print("ERROR: ANTHROPIC_API_KEY not set. Run: source bin/start.sh")
                sys.exit(1)
            client = anthropic.Anthropic(api_key=api_key)

        # Phase 2: Extraction
        print(f"\nRunning extraction (engine={args.engine})...\n")

        for i, pdf_path in enumerate(pdfs, 1):
            stem = pdf_path.stem
            parts = []

            # Docling
            if args.engine in ("docling", "both"):
                d_data, d_time = run_docling(pdf_path, args.skip_existing)
                docling_results[stem] = d_data
                status = "done" if d_data is not None else "FAIL"
                parts.append(f"Docling: {status} ({d_time:.1f}s)")
            else:
                # Try loading existing
                d_path = DOCLING_DIR / f"{stem}.structured.json"
                if d_path.exists():
                    docling_results[stem] = json.loads(d_path.read_text())
                else:
                    docling_results[stem] = None

            # Claude
            if args.engine in ("claude", "both"):
                c_data, c_time = run_claude(pdf_path, client, args.skip_existing)
                claude_results[stem] = c_data
                status = "done" if c_data is not None else "FAIL"
                parts.append(f"Claude: {status} ({c_time:.1f}s)")
            else:
                c_path = CLAUDE_DIR / f"{stem}.structured.json"
                if c_path.exists():
                    claude_results[stem] = json.loads(c_path.read_text())
                else:
                    claude_results[stem] = None

            progress = " | ".join(parts)
            print(f"[{i}/{len(pdfs)}] {pdf_path.name} — {progress}")

    # Phase 3: Comparison
    print("\nComparing outputs...")
    comparisons = []
    for pdf_path in pdfs:
        stem = pdf_path.stem
        d_data = docling_results.get(stem)
        c_data = claude_results.get(stem)
        if d_data is not None and c_data is not None:
            comp = compare_single(d_data, c_data, stem)
            comparisons.append(comp)
        else:
            comparisons.append(None)

    # Phase 4: Report
    print("Generating report...")
    report = generate_report(comparisons, docling_results, claude_results, pdfs)
    REPORT_PATH.write_text(report)
    print(f"\nReport written to {REPORT_PATH}")

    # Print summary to terminal
    valid = [c for c in comparisons if c is not None]
    if valid:
        avg_amt = sum(c["overall"]["amount_match_rate"] for c in valid) / len(valid)
        avg_dt = sum(c["overall"]["date_match_rate"] for c in valid) / len(valid)
        high = sum(1 for c in valid if c["confidence"] == "High")
        med = sum(1 for c in valid if c["confidence"] == "Medium")
        low = sum(1 for c in valid if c["confidence"] == "Low")
        print(f"\n  Comparable: {len(valid)}/{len(pdfs)}")
        print(f"  Amount match: {avg_amt:.1%} | Date match: {avg_dt:.1%}")
        print(f"  Confidence: {high} high, {med} medium, {low} low")
    else:
        print("\n  No comparable results (need both engines to succeed on same PDF)")


if __name__ == "__main__":
    main()
