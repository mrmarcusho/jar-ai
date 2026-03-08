# TD Bank — Parser Notes

## Detection
- "TD Personal Checking" in section header
- "TD Bank" or "TD Bank, N.A." in footer
- "tdbank.com" or "www.tdbank.com" in footer
- "1-800-937-2000" phone number
- "ACCOUNT SUMMYAR" OCR artifact (consistent across statements)

## Statement Layout
- Single-page header with customer info + account summary
- "DAILY ACCOUNT ACTIVITY" section with Credits and Debits subsections
- Multi-page: transaction tables may span 2-3 pages

## Account Summary Section
- Header: "ACCOUNT SUMMYAR" (OCR for "ACCOUNT SUMMARY")
- Fields: Beginning Balance, Deposits, Electronics Deposits, Others Credits,
  Electronic Payments, Other Withdrawals, Ending Balance
- Right column: Average Collected Balance, Interest Earned, APY

## Date Formats
- Statement period: "Jul 01 2021-Jul 31 2021"
- Transaction dates: MM/DD (no year — inferred from statement period)

## Transaction Description Patterns
- CHECK DEPOSIT: "CHECK D, 59 CONTRACTORS", "CHECK DEPOSIT, 25208 VIDALES AND SONS"
- Counter deposit: "DEPOSIT, COUNTER"
- ATM deposit: "DEPOSIT, ATM"
- ACH debit: "ACH DEPOSIT, QUICKBOOKS DEPOSIT 479395622313"
- CCD debit: "CCD DEPOSIT, TO MERCHANT SVS DEPOSIT 958216955889"

## Docling Quirks
- All table content rendered as single-column pipe cells
- Transaction rows: "| MM/DD DESCRIPTION AMOUNT |"
- Summary rows: "| Label Amount ... |" (key-value pairs in one cell)
- OCR frequently mangles descriptions (truncated words, missing spaces)
- May only capture page 1 of multi-page statements

## Amount Format
- No dollar signs, no negative signs
- Comma-separated thousands: 3,750.00
- Debits listed as positive numbers (must be negated by parser)

## Account Types Tested
- [x] Personal Checking
- [ ] Savings
- [ ] Business Checking
- [ ] Credit Card

## Validation Status
- Parser extracts all transactions from available pages
- Balance reconciliation may fail on sample/template statements with inconsistent numbers
- Withdrawal total may not match if Docling truncates pages
