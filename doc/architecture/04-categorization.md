# 4. Categorization — labeling transactions, and learning your habits

**Job:** give every transaction a sensible category (Groceries, Utilities,
Salary, …), and get smarter as you correct it.

**Files:**
- `core/categorization/categorizer.py` — the labeling engine + the category list
- `core/categorization/user_rules.py` — your personal rules and overrides
- `core/categorization/categories/categories_v0.1.0.json` — the JAR taxonomy

## How a transaction gets labeled

When a new transaction comes in, the categorizer tries rules in priority order
and stops at the first match:

```
1. Did you manually tag THIS exact transaction?        → use that        (most trusted)
2. Does it match a rule you've taught JAR?             → use that
3. Does the ACH company name match the taxonomy?       → use that
4. Does a keyword in the description match?            → use that
5. Can we guess from the transaction type?             → fallback guess
6. None of the above                                   → "uncategorized"  (least trusted)
```

Each result carries a **confidence** score, so the UI can highlight the
shaky guesses for you to confirm.

## The learning loop

This is the part that makes JAR feel personal:

```
You tag a transaction            "STARBUCKS #1234" → Coffee
        │
        ├─ Save an override on that transaction
        ├─ Extract a clean keyword from the description    → "Starbucks"
        ├─ Save a rule: "Starbucks" → Coffee
        └─ Apply that rule retroactively to every past
           matching transaction
```

So one correction cleans up your whole history *and* every future statement.
The keyword extractor is deliberately careful — it strips out bank noise (ACH
codes like `PPD`/`WEB`, dates, dollar amounts, long reference numbers) so the
rule keys on the real merchant name, not the clutter.

## Where it fits

Categorization runs automatically at the end of [Extraction](01-extraction.md),
and again whenever you tag something in the [web UI](09-server-and-ui.md)
(`/api/transactions/{id}/tag`).
