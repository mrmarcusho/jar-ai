# Extraction Pipeline Benchmark Summary

**Date:** 2026-03-05
**Corpus:** 65 bank statement PDFs from diverse banks (Wells Fargo, Citizens Bank, Key Bank, Chase, TD Bank, BofA, Truist, Capital One, UK/EU banks, etc.)

## Overall Results

| Metric | Docling | Claude |
|--------|---------|--------|
| **Succeeded** | 58/65 (89%) | 60/65 (92%) |
| **Failed** | 7 | 5 |
| **Comparable (both succeeded)** | 54 | 54 |

## Cross-Validation

The comparison yielded **very low match rates** (5.6% amount/date match). This is not a bug in either engine — it reflects a structural limitation: Docling's `structurize.py` is BofA-specific, so it extracts 0 transactions from non-BofA statements while Claude extracts many. The test driver correctly identified this gap.

Only **3 of 54 comparable PDFs** reached "High" confidence. All were cases where both engines extracted few or zero transactions (trivially matching on emptiness).

---

## Docling Findings

### Strengths
- **High conversion rate:** 58/65 PDFs (89%) successfully converted to markdown + JSON
- **Fast on simple PDFs:** 10-20s for single-page statements
- **Good table extraction:** Produces clean markdown tables from most bank formats

### Weakness 1: BofA-only structurizer (~50 PDFs affected)
`structurize.py` uses BofA-specific regex patterns:
- Section headers: `"Deposits and other additions"`, `"Withdrawals and other subtractions"`
- Date format: `"Beginning balance on January 14, 2023"`
- Account patterns: BofA-specific address/account number regex

**Result:** For non-BofA banks, Docling produces valid markdown with transaction tables visible in the raw output, but structurization returns 0 deposits, 0 withdrawals. The data is there — the parser just can't find it.

### Weakness 2: Amount parsing crashes (6 PDFs)
`_parse_amount()` does a naive `float()` conversion that crashes on edge cases:

| File | Bad Value | Root Cause |
|------|-----------|------------|
| `667193493-US-Bank-Statement` | `'5468 .00'` | OCR inserted space before decimal |
| `966820102-Bank-of-America-Statement` | `'5  700 . 00'` | Multiple spaces throughout |
| `755328612-June-Bank-Statement` | `'650.00-'` | Trailing negative sign (accounting format) |
| `875714637-Chase-Business-Bank-Statement` | `'1.435.63'` | European thousands separator (period) |
| `670593719-Bank-of-America-Statement` | `'-1431.17 -396.40'` | Two amounts merged into one cell |
| `BBT-Bank-Statement-TemplateLab.com_` | `'= 701.39'` | Leading equals sign from Excel export |

These `ValueError` exceptions kill the entire PDF instead of skipping the bad row.

### Weakness 3: Slow on complex PDFs
Some multi-page statements took extremely long:
- `556749999-us-bank-statement-pdf.pdf`: 20,662s (~5.7 hours)
- `511169792-Citizens-Bank-2020.pdf`: 3,379s (~56 min)
- `700292723-Anisha-Statem.pdf`: 2,032s (~34 min)

### Conversion failures (1 PDF)
`706423840-Statement-602048-45624011-12-Jan-2024.pdf` — Docling `ConversionError` on all 4 pages (`basic_string`). Likely a corrupt or unsupported PDF encoding.

---

## Claude Findings

### Strengths
- **Handles diverse bank formats natively:** No bank-specific parsing needed — understands Wells Fargo, Chase, TD Bank, Citizens, UK banks, etc.
- **Robust amount parsing:** Correctly interprets `$5,468.00` from OCR artifacts, European formats, accounting notation
- **Good transaction classification:** Produces typed transactions (zelle_transfer, branch_deposit, ach_debit, etc.)
- **Structured output with validation:** Self-validates balance reconciliation, flags discrepancies

### Weakness 1: Output truncation on large statements (7 PDFs)
`max_tokens` limits cause JSON to be cut off mid-output, producing `JSONDecodeError`:

| File | Stated txn count | Extracted |
|------|-----------------|-----------|
| `670593719-Bank-of-America-Statement` | 174 withdrawals | 9 |
| `755328612-June-Bank-Statement` | Large | Failed (truncated) |
| `836934700-Susie-Overholt-Wells-Fargo` | Large | Initially failed, recovered at 16384 |
| `875714637-Chase-Business-Bank-Statement` | Large | Initially failed, recovered at 16384 |
| `899577265-Bank-Statement-2-2025` | Large | Initially failed, recovered at 16384 |
| `BBT-Bank-Statement-TemplateLab.com_` | Large | Initially failed, recovered at 16384 |

Bumping `max_tokens` from 8192 to 16384 recovered 7 of 12 failures, but very large statements (100+ transactions) may need 32768+ or a chunked extraction approach.

**Subtle danger:** Some truncated outputs produce valid JSON that parses successfully but contains only a fraction of the transactions. The validation fields (`withdrawal_count_matches: false`) catch this, but it looks like a "success" in the pipeline.

### Weakness 2: Network/rate sensitivity (19 PDFs on first run)
The first run hit `APIConnectionError` on 19 consecutive PDFs due to a transient network issue. All recovered on retry. One PDF hit the free-tier rate limit (30K input tokens/min).

### Weakness 3: Non-parseable PDFs (3 PDFs)
- `bank-statement-template-11.pdf` — Empty response (image-only template)
- `bank-statement-template-15.pdf` — Empty response (image-only template)
- `733479414-prepayment-statement.pdf` — Not a bank statement (prepayment amortization schedule)

---

## Key Takeaway

**Claude is the stronger general-purpose extractor** — it handles diverse bank formats out of the box and produces structured data from 60/65 PDFs. Its main limitation is output size for large statements.

**Docling is a strong PDF-to-markdown converter** but its value is gated by `structurize.py`, which currently only understands BofA format. The raw markdown Docling produces contains the transaction data for most banks — it just needs bank-aware parsers.

## Recommended Next Steps

1. **Harden `_parse_amount()`** — strip spaces, handle trailing negatives, European format, multi-value cells
2. **Generalize `structurize.py`** — or build bank-specific parsers that detect format and dispatch
3. **Increase Claude `max_tokens`** — use 32768 for large statements, or implement chunked extraction
4. **Add retry with backoff** — for Claude API connection/rate errors
5. **Add truncation detection** — compare extracted transaction count against stated count, flag partial extractions as failures
