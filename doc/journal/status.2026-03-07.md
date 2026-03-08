What we've built so far                                                  
                                                                           
  1. Refactored the extraction pipeline for reuse                          
                                                                           
  - Added preprocess_pdf(pdf_path, out_dir) to run_preprocess.py —         
  importable Docling conversion                                            
  - Added postprocess_and_structurize(md_path, json_path, out_dir, stem) to
   run_postprocess.py — importable post-processing + structurization       
  - Existing CLI usage unchanged                                           
                                                                           
  2. Built the test driver (test_driver.py)                                
                                                                           
  A 4-phase benchmark tool that runs both Docling and Claude on any set of
  PDFs:
  - Discovery — finds PDFs in data/test_pdfs/bank/
  - Extraction — runs Docling and/or Claude per PDF, logs progress and
  errors
  - Comparison — matches transactions by (date, amount), computes match
  rates
  - Report — generates comparison_report.md with scorecard, per-statement
  detail, failure analysis

  CLI: --engine {docling,claude,both}, --report-only, --limit N,
  --skip-existing

  3. Ran the full benchmark (65 PDFs, both engines)

  ┌─────────────────┬───────────┬────────┐
  │                 │ Succeeded │ Failed │
  ├─────────────────┼───────────┼────────┤
  │ Docling         │ 58 (89%)  │ 7      │
  ├─────────────────┼───────────┼────────┤
  │ Claude          │ 60 (92%)  │ 5      │
  ├─────────────────┼───────────┼────────┤
  │ Both comparable │ 54        │ —      │
  └─────────────────┴───────────┴────────┘

  4. What we learned

  Docling is a good PDF→markdown converter but its value is bottlenecked by
   structurize.py:
  - BofA-only structurizer — extracts 0 transactions from non-BofA
  statements because all regex patterns (section headers, date formats, ACH
   fields) are hardcoded for BofA
  - Amount parsing crashes on 6 PDFs — spaces in numbers, trailing
  negatives, European formats, multi-value cells
  - The raw markdown Docling produces is actually well-formed for most
  banks — the data is there, the parser just can't find it

  Claude handles any bank format natively but has:
  - Output truncation on large statements (174-txn statement only got 9
  extracted)
  - Network/rate sensitivity (19 failures on first run, all recovered on
  retry)

  Cross-validation showed ~0% match rate — not a bug, but because Docling
  returns 0 transactions on non-BofA while Claude returns many.

  5. Where we are now

  Discussing how to make extraction generic across all banks. The current
  thinking:

  - Path A: Docling → markdown → LLM → structured.json (LLM interprets any
  format, works immediately, per-statement cost)
  - Path B: Docling → markdown → LLM → generated parser code →
  structured.json (one-time LLM cost, but generated code is fragile to
  format variations even within the same bank)
  - Path A looks like the more robust primary path; Path B is a possible
  optimization for high-volume banks