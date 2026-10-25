# 5. Chat & tools — how a question becomes an answer

**Job:** let you ask plain-English questions and get grounded, accurate answers.

**Main file:** `app/chat/chat_engine.py` (class `JarChat`)
**Local alternative:** `app/chat/local_chat_engine.py` (runs on Ollama, offline)
**CLI for testing:** `app/chat/run_chat.py`

## The core idea: function calling

Claude is good with language but shouldn't be trusted with arithmetic on your
money. So JAR gives it a set of **tools** (functions) it can ask to run. Claude
picks the tool and the arguments; JAR runs the real code; Claude reads the
results and writes the answer.

```
You: "How much did I spend on groceries last month vs. the month before?"
  │
  ▼
Claude looks at its tools and decides:
  call get_spending_comparison(period1=..., period2=..., category="groceries")
  │
  ▼
JAR runs the real query on your local database  → exact numbers
  │
  ▼
Claude turns the numbers into a sentence:
  "You spent $412 on groceries last month, down 8% from $448 the month before."
```

If answering needs several steps, Claude can call tools repeatedly in a loop
until it has everything it needs, then writes the final reply.

## The tools available

**Money questions** (from the [Financial API](03-financial-api.md)):
`get_transactions`, `get_spending_by_category`, `get_recurring_bills`,
`get_balance_summary`, `get_spending_comparison`, `search_transactions`,
`get_balance_history`, `get_top_merchants`, `get_monthly_trends`.

**Search questions** (from [Email & Messages](08-email-and-messages.md)):
`search_emails` and `search_messages` — semantic search over your locally-indexed
email and texts (e.g. "what did the plumber quote me?").

## The privacy round-trip

Email and message search results contain real names and personal text, so they
pass through the **cloud boundary**:

```
local search results ──[ scrub PII → tokens ]──► sent to Claude
                                                       │
final answer ◄──[ swap tokens back to real names ]── Claude's reply
```

You always see real names; Claude only ever sees placeholder tokens. See
[Privacy & Redaction](06-privacy-and-redaction.md).

## Practical details

- **History is trimmed** to the most recent handful of messages, so long
  conversations don't blow up token usage.
- **Claude is told to never make up numbers** — if it doesn't have a tool result,
  it says so rather than guessing.
- **Two backends:** the cloud Claude engine (full features, needs an API key) or
  a local Ollama engine (offline, financial tools only — no email/message search).
  Switch via `POST /api/chat/backend`.
