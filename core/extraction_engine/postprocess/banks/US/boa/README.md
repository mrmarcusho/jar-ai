# Bank of America — Parser Notes

## Detection
- "Bank of America" or "bankofamerica.com" in markdown content
- "BOFA FIN CTR" in branch deposit descriptions

## Statement Sections
- "Deposits and other additions"
- "Withdrawals and other subtractions"

## Date Formats
- Statement period: "Beginning balance on January 14, 2023"
- Transaction dates: MM/DD/YYYY

## Known Quirks
- Unicode escape sequences in raw Docling markdown
- Missing CO ID fields in some ACH descriptions
- INDN field contains full individual name (PII — must strip)
- Column headers sometimes span multiple columns in Docling output

## Account Types Tested
- [x] Regular Checking
- [ ] Savings
- [ ] Business Checking
- [ ] Credit Card

## Validation Status
- Extraction accuracy: 100% on tested statements
- Balance reconciliation: passing
