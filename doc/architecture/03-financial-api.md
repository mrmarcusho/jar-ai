# 3. Financial API — the deterministic money queries

**Job:** answer specific financial questions with exact numbers from the
database. This is the "calculator" the AI is allowed to use.

**Main file:** `core/api/financial_api.py`
**Class:** `FinancialAPI`

## The golden rule

> The LLM does **not** understand finance. It only decides *which* function to
> call. All the actual math happens here, in plain Python and SQL — no LLM calls
> live in `core/api/`.

That separation is what makes JAR trustworthy: the numbers come from
deterministic code you can read and test, not from a model guessing.

## The functions

| Function | Answers a question like… |
|----------|--------------------------|
| `get_transactions(...)` | "Show me everything over $100 in March." |
| `get_spending_by_category(...)` | "Where did my money go last month?" |
| `get_balance_summary(...)` | "What's my income vs. spending and net change?" |
| `get_recurring_bills()` | "What subscriptions and bills repeat?" |
| `get_top_merchants(limit)` | "Who do I spend the most with?" |
| `get_monthly_trends()` | "How has income vs. spending changed month to month?" |
| `get_spending_comparison(...)` | "How does this month compare to last month?" |
| `get_balance_history()` | "Plot my balance over time." |
| `search_transactions(keyword)` | "Find anything mentioning 'Costco'." |

Each one takes simple filters (date ranges, amounts, types) and returns a plain
dictionary of results. They're used in two places:

1. **Chat** — exposed to Claude as callable tools (see [Chat & tools](05-chat-and-tools.md)).
2. **Dashboard** — the web server calls them to draw charts (see [Server & UI](09-server-and-ui.md)).

## Why deterministic matters

Because these functions are pure code:

- The same question always gives the same answer.
- They can be unit-tested (`tests/test_api.py`).
- A human can audit exactly how any number was produced.

The AI's job is narrative ("you spent **$420** on groceries, up 12% from last
month"); these functions supply the **$420**.
