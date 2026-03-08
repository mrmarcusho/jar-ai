# Qwen 3.5 0.8B Benchmark — Intel MacBook Pro

**Date:** 2026-03-07
**Objective:** Determine if the smallest Qwen 3.5 model can handle basic financial query routing on an 8GB Intel Mac (expected Phase I device).

## Machine Specs

| Spec | Value |
|------|-------|
| Model | MacBook Pro 14,1 (2017) |
| CPU | Dual-Core Intel Core i5 @ 2.3 GHz |
| RAM | 8 GB |
| macOS | 13.6.9 (Ventura) |
| Disk | 349 GB available |
| Ollama | v0.17.7 (upgraded from v0.12.3 for qwen3.5 support) |
| Backend | CPU-only (no GPU/Metal — Intel, not Apple Silicon) |

## Model Info

| Model | Size on disk | Parameters |
|-------|-------------|------------|
| qwen3.5:0.8b | 1.0 GB | 0.8B |
| qwen3:8b (default) | ~5 GB | 8B (not tested — not pulled on this machine) |

## Per-Query Results

| # | Category | Query | Expected Fn | Functions Called | Correct? | Time (s) | Mem (GB) | Quality |
|---|----------|-------|-------------|-----------------|----------|----------|----------|---------|
| 1 | basic | What is my account balance? | get_balance_summary | get_balance_summary | YES | 414.6 | 6.14 | Good — correct balance, clear formatting |
| 2 | basic | How much did I spend in January? | get_spending_by_category | get_spending_by_category, get_balance_summary | YES | 487.1 | 6.21 | Good — correct total, nice table breakdown |
| 3 | basic | What are my recurring bills? | get_recurring_bills | get_recurring_bills | YES | 380.1 | 6.17 | Good — formatted table with payee/amount/dates |
| 4 | filtering | Spending by category for January | get_spending_by_category | get_spending_by_category | YES | 200.7 | 6.10 | Good — clean category breakdown |
| 5 | filtering | Top 5 largest expenses | get_transactions | get_transactions (x2), get_spending_by_category | YES | 1202.5 | 6.01 | OK — correct but took 3 calls and 20 min |
| 6 | reasoning | Mortgage change Dec vs Jan? | search_transactions | get_spending_comparison, get_transactions, search_transactions (x2) | YES* | 1198.8 | 5.51 | Weak — searched broadly for "payment" instead of mortgage; didn't directly answer the question |
| 7 | reasoning | Compare Jan and Dec spending | get_spending_comparison | get_spending_comparison | YES | 545.9 | 6.07 | Good — correct comparison, well-structured |
| 8 | reasoning | % of income to housing costs | get_balance_summary | get_spending_by_category, get_balance_summary, search_transactions (x8), get_spending_by_category, get_balance_summary | YES* | 1589.0 | 5.99 | Weak — made 12 calls but couldn't connect "NSM DBAMR.COOPER" to mortgage; reported "no mortgage found" |

\* Function routing was technically correct (called relevant functions) but the model couldn't reason well enough to answer the actual question.

## Summary Statistics

| Metric | Value |
|--------|-------|
| Correct routing | 8/8 (100%) |
| Produced response | 8/8 (100%) |
| Errors | 0 |
| Avg time per query | 752s (~12.5 min) |
| Total benchmark time | 6,019s (~100 min) |
| Fastest query | Q4: 201s (3.3 min) |
| Slowest query | Q8: 1,589s (26.5 min) |

## Memory & Resource Usage

| Metric | Before | After | Delta |
|--------|--------|-------|-------|
| Used RAM | 6.65 GB | 5.99 GB | -0.66 GB |
| Free RAM | 0.02 GB | 0.01 GB | -0.01 GB |
| Active RAM | 1.33 GB | 2.00 GB | +0.67 GB |
| Wired RAM | 2.59 GB | 2.49 GB | -0.10 GB |
| Compressed | 2.73 GB | 1.50 GB | -1.23 GB |
| Swap used | 2,826 MB | 3,484 MB | +658 MB |
| Swap total | 4,096 MB | 5,120 MB | +1,024 MB |

**Observations:**
- Model fits in RAM (1.0 GB) without excessive swap pressure
- Swap grew by 658 MB during the 100-minute run — moderate but not alarming
- No OOM kills or crashes
- System remained responsive (no beach-balling observed)
- CPU pinned at ~92% during inference (expected for CPU-only, dual-core)

## Quality Assessment

### Strengths (Q1-Q4, Q7)
- **Function routing is excellent** — 8/8 correct, even with complex multi-function queries
- **Basic queries produce clean, well-formatted responses** with tables and dollar formatting
- **Follows system prompt instructions** — uses dollar formatting, gives structured answers
- **Multi-function chaining works** — Q2 called both spending + balance functions

### Weaknesses (Q5, Q6, Q8)
- **Slow reasoning loops** — complex queries trigger many function calls (Q8 made 12 calls over 26 minutes)
- **Cannot connect domain-specific payee names to concepts** — "NSM DBAMR.COOPER" is Mr. Cooper mortgage, but the model doesn't know this and searched for generic "mortgage" / "rent" which returned nothing
- **Loses focus on multi-step questions** — Q6 asked about mortgage changes but the model gave a general payment summary instead
- **Thinking overhead is significant** — the built-in chain-of-thought adds substantial latency even for simple queries

## Comparison Context

No direct comparison to Qwen3-8B was possible (model not pulled on this machine). However, based on previous testing on Apple Silicon:

| Metric | qwen3.5:0.8b (Intel i5) | qwen3:8b (Apple Silicon, est.) |
|--------|-------------------------|-------------------------------|
| Avg query time | ~12.5 min | ~15-30 sec |
| Routing accuracy | 8/8 | 8/8 |
| Reasoning quality | Weak on complex | Good on complex |
| Memory footprint | ~1 GB model | ~5 GB model |
| Min RAM needed | 8 GB (tight) | 16 GB |

## Recommendation

**The 0.8B model should NOT be used for Phase II (LLM chat) on Intel Macs.**

Reasons:
1. **Unacceptable latency** — 3-26 minutes per query makes interactive chat impossible. Users will not wait 7 minutes for a balance check.
2. **Reasoning quality drops on complex queries** — the model can route functions correctly but cannot perform the multi-step reasoning needed for questions like "what % of income goes to housing?"
3. **CPU bottleneck** — dual-core Intel i5 pins at 92% during inference with no room for other processes.

**However, the results confirm the Intel MacBook Pro is a valid Phase I device:**
- The system prompt, function schemas, and API all work correctly
- Ollama runs fine (after upgrade to v0.17.7)
- The machine can serve the web UI and API layer without the LLM

**If LLM chat is desired on Intel Macs**, possible mitigations:
- Use a cloud-hosted model (Claude API) instead of local inference
- Use a quantized 0.5B model (if one becomes available) with reduced quality expectations
- Accept the latency for batch/async queries (not interactive chat)
