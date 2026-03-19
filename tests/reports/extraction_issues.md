# Extraction Pipeline — Known Issues

> Every issue found during testing is logged here.
> Each issue becomes a regression test in `tests/test_extraction_regression.py`.
> When an issue is fixed, it stays in this document with status FIXED and a test ID.

## Issue Template

Copy this template for new issues:

~~~
### ISSUE-XXX: [Short title]
- **Status:** OPEN | FIXED | WONTFIX
- **Found:** YYYY-MM-DD
- **Fixed:** YYYY-MM-DD (if fixed)
- **Bank:** [bank name — e.g., Bank of America, TD Bank, Chase, ALL]
- **Country:** [US, UK, CA, etc.]
- **Statement:** [filename]
- **Account Type:** [checking, savings, credit_card, business_checking]
- **Severity:** CRITICAL | HIGH | MEDIUM | LOW
- **Category:** extraction | balance | transaction | section_parsing | performance | detection
- **Regression Test:** `test_xxx` in test_extraction_regression.py
- **Root Cause:** [one-line description]

**What happened:**
[describe the failure]

**Expected:**
[what the correct output should be]

**Actual:**
[what the pipeline produced]

**Fix:**
[how it was fixed, or proposed fix]
~~~

---

## Issues by Bank

### Bank of America (US)

#### ISSUE-001: Ending balance missing when Docling renders it as image
- **Status:** FIXED
- **Found:** 2026-03-09
- **Fixed:** 2026-03-09
- **Bank:** Bank of America
- **Country:** US
- **Statement:** eStmt_2023-06-14.pdf, eStmt_2023-08-16.pdf
- **Account Type:** checking
- **Severity:** CRITICAL
- **Category:** balance
- **Regression Test:** `test_boa_balance_not_zero`, `test_boa_balance_reconciliation`
- **Root Cause:** Docling renders the closing balance dollar amount as `<!-- image -->` instead of text on some statements. The standalone-format regex fallback finds "Ending balance on [date]" but the amount on the next line is an image tag, not a number.

**What happened:**
`eStmt_2023-06-14` and `eStmt_2023-08-16` have closing balance = $0.00. The markdown shows "Ending balance on [date]" followed by `<!-- image -->` instead of the dollar amount.

**Expected:**
Closing balance extracted correctly.

**Actual:**
Closing balance = $0.00, balance reconciliation failed.

**Fix:**
When closing balance is $0.00 but opening balance is non-zero, compute closing balance from account summary values: `opening + deposits + withdrawals + checks + fees`. All summary line items are in the table (which Docling parses correctly), so the computed value is reliable.

---

#### ISSUE-002: Check transactions in separate section not parsed
- **Status:** FIXED
- **Found:** 2026-03-09
- **Fixed:** 2026-03-09
- **Bank:** Bank of America
- **Country:** US
- **Statement:** eStmt_2024-02-13.pdf
- **Account Type:** checking
- **Severity:** HIGH
- **Category:** section_parsing
- **Regression Test:** `test_boa_withdrawal_count_matches`, `test_boa_checks_parsed_when_present`
- **Root Cause:** Two issues: (1) `_parse_table_rows` stopped at Docling artifact headers like `## -$6,352.71` that split check tables across pages. (2) Parsed checks were stored in a separate `checks` array but not included in `withdrawals`.

**What happened:**
`eStmt_2024-02-13` has 3 checks totaling $17,010 split across two tables separated by a Docling artifact header. Only 2 of 3 checks were parsed, and none appeared in the `withdrawals` array.

**Expected:**
All checks extracted and included in withdrawals with type="check".

**Actual:**
2 of 3 checks parsed (missing check #117 for $110), 0 in withdrawals array.

**Fix:**
1. Changed `_parse_table_rows` to only stop at real section headers (starting with a letter after `## `), not Docling artifacts like `## -$6,352.71`.
2. Added checks to the `withdrawals` array with `type="check"`.
3. Adjusted validation to compare non-check withdrawals against bank stated withdrawal total separately.

---

#### ISSUE-004: BofA detection fails on 2024 statements with scam warning page
- **Status:** FIXED
- **Found:** 2026-03-09
- **Fixed:** 2026-03-09
- **Bank:** Bank of America
- **Country:** US
- **Statement:** eStmt_2024-06-12.pdf
- **Account Type:** checking
- **Severity:** HIGH
- **Category:** detection
- **Regression Test:** `test_boa_detected_with_high_confidence`
- **Root Cause:** `detect()` only searched first 50 lines for bank identifiers. BofA added a scam warning page in 2024 that pushed "bankofamerica.com" and "Bank of America, N.A." past line 50, dropping confidence from 1.0 to 0.4.

**What happened:**
`eStmt_2024-06-12` has "BANK OF AMERICA" on line 1 (scoring 0.4 for "bank of america") but "bankofamerica.com" and "Bank of America, N.A." are past line 50 due to a new scam warning section. Confidence = 0.4, below the 0.7 high-confidence threshold.

**Expected:**
All BofA statements detected with confidence >= 0.7.

**Actual:**
Confidence = 0.4 for 2024 statements with the new layout.

**Fix:**
Changed `detect()` to search full text for all indicators instead of only the first 50 lines. "bank of america, n.a." and "bankofamerica.com" are specific enough to avoid false positives in full-text search.

---

### All Banks

#### ISSUE-003: Docling PDF conversion slow — 3+ minutes per statement
- **Status:** OPEN
- **Found:** 2026-03-09
- **Bank:** ALL
- **Country:** ALL
- **Statement:** all
- **Account Type:** all
- **Severity:** MEDIUM
- **Category:** performance
- **Regression Test:** `test_postprocess_time_under_threshold`
- **Root Cause:** Docling runs DocLayNet + TableFormer AI models on CPU (Intel i5). No GPU/Neural Engine acceleration available on Intel Macs.

**What happened:**
Single-page BofA statements take 3+ minutes to convert. Multi-page statements even longer. Users watching a progress bar for 14 statements would wait 40+ minutes.

**Expected:**
Under 30 seconds per statement for acceptable UX.

**Actual:**
180+ seconds per statement on Intel MacBook Pro.

**Fix:**
Investigate Granite-Docling with MLX on M4 Mac Mini. For Intel, consider pre-processing optimizations or caching Docling output. Long-term: adaptive — run Docling on first import, cache markdown for subsequent runs.

---

#### ISSUE-006: Multi-page withdrawal tables with "continued" header not parsed
- **Status:** FIXED
- **Found:** 2026-03-09
- **Fixed:** 2026-03-09
- **Bank:** Bank of America
- **Country:** US
- **Statement:** eStmt_2024-01-16.pdf
- **Account Type:** checking
- **Severity:** CRITICAL
- **Category:** section_parsing
- **Regression Test:** `test_boa_withdrawal_total_matches`
- **Root Cause:** When a withdrawal table spans multiple pages, Docling emits a second header `## Withdrawals and other subtractions - continued`. The `_parse_table_rows` function stops at this header because it matches the "real section header" pattern (starts with `## ` followed by a letter), missing all transactions on subsequent pages.

**What happened:**
`eStmt_2024-01-16` has 16 withdrawals split across two pages. The first table (10 rows) is parsed, but the "- continued" table (6 rows totaling $4,438.75) is skipped. Also affects `## Total withdrawals and other subtractions` which is a summary header, not a new section.

**Expected:**
All withdrawals extracted regardless of page splits.

**Actual:**
Non-check withdrawal total: computed -$43,779.37, bank stated -$48,218.12. Missing $4,438.75.

**Fix:**
Update `_parse_table_rows` to treat "- continued" and "Total" headers as continuations of the current section, not as new sections.

---

### TD Bank (US)

#### ISSUE-007: TD Bank detection fails on business checking statements
- **Status:** FIXED
- **Found:** 2026-03-09
- **Fixed:** 2026-03-09
- **Bank:** TD Bank
- **Country:** US
- **Statement:** View PDF Statement_2025-02-28.pdf
- **Account Type:** business_checking
- **Severity:** CRITICAL
- **Category:** detection
- **Regression Test:** `test_boa_detected_with_high_confidence` (needs TD Bank equivalent)
- **Root Cause:** Two issues: (1) `detect()` only searches first 50 lines — "td bank" and "td bank, n.a." appear later in the document. (2) Missing indicator for "td business" account types — only "td personal checking" is weighted.

**What happened:**
TD Business Convenience Plus statement (`View PDF Statement_2025-02-28.pdf`) scores 0.0. The indicators "td bank" and "td bank, n.a." are in the full text but not in the first 50 lines. Also, "td business" is not a detection indicator.

**Expected:**
TD Bank statements detected with confidence >= 0.7.

**Actual:**
Confidence = 0.0, "Unknown bank format" error.

**Fix:**
Search full text for indicators (same as ISSUE-004 fix for BofA). Add "td business" as a detection indicator.

### Chase (US)

(no issues yet)

---

## Data Loading Issues

#### ISSUE-005: Duplicate transaction_id when loading checks into database
- **Status:** FIXED
- **Fixed:** 2026-03-09
- **Found:** 2026-03-09
- **Bank:** ALL
- **Country:** ALL
- **Statement:** all statements with checks
- **Account Type:** all
- **Severity:** CRITICAL
- **Category:** extraction
- **Regression Test:** `test_boa_checks_parsed_when_present`
- **Root Cause:** ISSUE-002 fix added checks to the `withdrawals` array in structured JSON. But `load_statements.py` iterates over both `withdrawals` and `checks` separately, inserting the same check transactions twice with the same `transaction_id`, causing a UNIQUE constraint failure.

**What happened:**
After ISSUE-002 fix, checks appear in both `data["withdrawals"]` (with `type="check"`) and `data["checks"]`. The database loader at line 102-104 iterates over `deposits`, `withdrawals`, and `checks` independently, producing duplicate inserts for check transactions.

**Expected:**
All transactions load without constraint violations.

**Actual:**
`UNIQUE constraint failed: transactions.transaction_id` on any statement with checks.

**Fix:**
Remove the separate `checks` iteration from `load_statements.py` since checks are already included in the `withdrawals` array after the ISSUE-002 fix.

---

## Issue Statistics

### By Bank

| Bank | Open | Fixed | Total |
|---|---|---|---|
| Bank of America | 0 | 4 | 4 |
| All Banks | 0 | 1 | 1 |
| TD Bank | 0 | 0 | 0 |
| Chase | 0 | 0 | 0 |
| **Total** | **1** | **5** | **6** |

### By Severity

| Severity | Open | Fixed | Total |
|---|---|---|---|
| CRITICAL | 0 | 3 | 3 |
| HIGH | 0 | 2 | 2 |
| MEDIUM | 1 | 0 | 1 |
| LOW | 0 | 0 | 0 |

### By Category

| Category | Open | Fixed | Total |
|---|---|---|---|
| balance | 0 | 1 | 1 |
| section_parsing | 0 | 2 | 2 |
| performance | 1 | 0 | 1 |
| extraction | 0 | 1 | 1 |
| transaction | 0 | 0 | 0 |
| detection | 0 | 1 | 1 |

Last updated: 2026-03-09
