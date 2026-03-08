# JAR — Session Summary: Local LLM Testing & Optimization

## What We Tested Today

We ran a head-to-head comparison of the same query ("What is my largest withdrawal in 2023?") across four backends on two machines: Claude Sonnet via API on an Intel MacBook Pro, and Qwen 2.5-3B, Qwen 2.5-7B, and Qwen3-8B via Ollama on an M4 Mac Mini (16GB).

---

## Results: Model Comparison

### Claude Sonnet (API) — Baseline
- **Result:** ✅ Correct ($55,218.65 wire transfer)
- **Approach:** 3 tool calls — searched "kia" (0 results), retried "telluride" (found it), pulled all withdrawals to verify
- **Strengths:** Semantic understanding, agentic retry/verify loop, world knowledge (knows Mr. Cooper = mortgage), comprehensive response synthesis

### Qwen 2.5-3B (Ollama) — Before Fixes
- **Result:** ❌ Wrong (generic summary of all transactions)
- **Approach:** 1 tool call — used wrong param names (`period_start`/`period_end` instead of `start_date`/`end_date`), params silently dropped, dumped everything
- **Failure:** Parameter naming, no reasoning over results

### Qwen 2.5-7B (Ollama) — Before Fixes
- **Result:** ❌ Wrong ($200 ATM withdrawal)
- **Approach:** 1 tool call — filtered to `type: "atm_withdrawal"` (matched the word "withdrawal" to the enum value), got $200 ATM transaction
- **Failure:** Semantic misunderstanding of "withdrawal", no retry

### Qwen 2.5-7B (Ollama) — With "spending" Rewording
- **Result:** ❌ Wrong ($82.81 — most recent, not largest)
- **Approach:** 1 tool call — correct filters this time (`is_deposit: false`), but used `limit: 1` without `sort_by: amount`, got most recent transaction instead of largest
- **Failure:** API didn't support sorting by amount

### Qwen3-8B (Ollama) — After All Fixes
- **Result:** ✅ Correct ($55,218.65 wire transfer)
- **Approach:** 1 tool call — `get_transactions(is_deposit=false, sort_by=amount, sort_dir=desc, limit=1, start_date=2023-01-01, end_date=2023-12-31)`
- **Strength:** Perfect parameter selection, more efficient than Claude (1 call vs 3)

### Qwen 2.5-3B (Ollama) — After Schema Fixes
- **Result:** ✅ Correct ($55,218.65) — but with wrong param names still dropped
- **Note:** The `sort_by=amount` fix compensated for the param naming issue

---

## Fixes Implemented Today

### 1. Added Qwen3-8B Model
- Pulled `qwen3:8b` via Ollama (generational upgrade over Qwen 2.5 — matches Qwen 2.5-14B performance)
- Added as fourth option in model selector, set as default local model
- Added `<think>` tag stripping for Qwen3's thinking mode (stripped from user response, logged at DEBUG level)

### 2. Layer 2 API: `sort_by` Parameter
- Added `sort_by` (date/amount) and `sort_dir` (asc/desc) to `get_transactions()`
- Allows `sort_by=amount, limit=1` to return the largest transaction directly
- Moves analytical work from the LLM into the database where it belongs

### 3. Improved Function Schema Descriptions
- `type` parameter: explicit instruction to only use when user names a specific type, not for general "withdrawal" or "spending" queries
- `is_deposit` parameter: clear guidance that `false` covers all outgoing transactions
- `sort_by`, `sort_dir`, `limit`: descriptions explain when and how to use them together
- All six functions got improved descriptions explaining when to use each one

### 4. Qwen-Specific System Prompt
- Added procedural instructions for think-before-calling, checking results, verifying, and common mistakes
- Added response synthesis instruction: "if you retrieved it, present it"
- Only appended for Ollama backends; Claude gets the clean shared prompt

### 5. Fixed Statement Period Date Confusion
- Old prompt: "The data covers two statement periods: Jan 14 - Feb 10, 2023 and Nov 15 - Dec 13, 2023"
- Qwen3 used statement boundaries instead of calendar dates when asked about "January"
- New prompt explicitly says to use calendar dates and let the API handle statement mapping

### 6. install.sh Improvements
- Added `qwen3:8b` pull (prioritized first as default model)
- Resume-based retry for model downloads (5 attempts with resume)
- `set +e` wrapper around model pulls so install completes even if downloads fail
- Ollama readiness check (polls `ollama list` before pulling)
- Database overwrite protection (skips init if `jar.db` exists)

### 7. get_recurring_bills() Bug Found
- Was including deposits alongside outgoing payments (MPG OPERATIONS L bonus of $71K appeared as a "recurring bill")
- Fix: add `is_deposit = 0` filter — prompt written, pending implementation

---

## Current Capability Matrix

| Capability | Qwen 2.5 3B | Qwen3 8B | Claude |
|---|---|---|---|
| Simple lookups (largest, balance) | ✅ with schema help | ✅ | ✅ |
| Correct function routing | Partial | ✅ | ✅ |
| Correct parameter naming | ❌ (still uses wrong names) | ✅ | ✅ |
| Multi-step function calls | ❌ | ✅ | ✅ |
| Response synthesis | ❌ | Partial (improving) | ✅ |
| World knowledge (merchant → category) | ❌ | ❌ | ✅ |
| Agentic retry/verify loop | ❌ | Not yet tested deeply | ✅ |

---

## What's Left To Do

### Immediate (Close the Qwen3 Gaps)

**Merchant Normalization** — The #1 way to bridge the world knowledge gap. Build a lookup table mapping raw merchant names to human-readable names and categories. Use Claude for the initial pass (it only sees merchant names, not full transactions — privacy preserved), user confirms, locked forever. Once `NSM DBAMR.COOPER` → "Mr. Cooper (Mortgage/Housing)" is in the database, Qwen3 doesn't need world knowledge.

**Categories** — LLM categorization workflow: categorize once per unique merchant, user confirms, locked forever. This makes `get_spending_by_category()` actually useful and eliminates the null categories that confused Qwen3 on the mortgage query.

**Identity Resolution** — Household member table (Alex Ho, Jamie Lee/Lee, Marcus Ho). Variant name matching with user confirmation.

### Short-Term (Harden the Product)

**Extraction Hardening** — Batch process 12-24 months of BofA statements. Test a second bank format (Chase or Wells Fargo) to prove the architecture isn't BofA-specific.

**Fix get_recurring_bills()** — Filter to outgoing only (prompt already written).

**Response Templates** — Add structured response templates to the Qwen system prompt for common query types (summary, comparison, top-N) to improve synthesis quality.

### Medium-Term (Product Experience)

**Async Install Experience** — Redesign install.sh to keep users informed of download progress (progressing/paused/resumed/complete). Current workaround is resume-based retry.

**PWA for Mobile** — Progressive Web App + Tailscale for phone access to Mac Mini. Concise response format for mobile.

**Web UI Enhancement** — Charts in chat responses (donut charts for spending, bar charts for comparisons).

**Proactive Intelligence** — Spending spike detection, bill due date prediction, anomaly detection, balance alerts — all deterministic SQL + rules.

### Future

**Tauri Wrapper** — Native .app with DMG installer when ready for beta.
**SQLCipher** — Encryption for the SQLite database.
**Additional Bank Formats** — Beyond BofA and Chase.

---

## Key Insight from Today

The biggest lesson is that the intelligence doesn't all need to live in the LLM. By making Layer 2 (the API) smarter — `sort_by=amount`, better schema descriptions, enriched data — even small models can give correct answers. The model upgrade from Qwen 2.5 to Qwen3 was significant, but the infrastructure improvements (schema, API, prompt) were equally important. The two work together: smart API reduces what the model needs to reason about, and a better model handles the edge cases that the API can't anticipate.

The path forward is clear: put the knowledge into the data (merchant normalization, categories, identity resolution), put the analytical logic into the database (sort, filter, aggregate), and let the LLM focus on what it's good at — understanding the question and presenting the answer.