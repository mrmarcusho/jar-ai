"""Layer 3: LLM function-calling interface over the financial API.

Uses Anthropic Claude via API.
"""

import json
import logging
import os
import re
import sqlite3
import sys
from pathlib import Path

import anthropic

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from core.api.financial_api import FinancialAPI

_SYSTEM_PROMPT_TEMPLATE = """\
You are Jar, a personal financial assistant. You have access to the user's bank \
transaction data through a set of query functions.

You can also search the user's email inbox with the search_emails function: it does
semantic search over every message the user has synced (receipts, bills, orders,
appointments, travel, notices, anything). Use search_emails for any question about
the user's emails, and the financial functions for questions about their bank
transactions.

You can also search the user's text messages (iMessage/SMS) with the search_messages
function: semantic search over their synced messages. Use it for questions about
texts — appointments, order confirmations, plans, or anything someone texted them.

When answering questions:
- Always call the appropriate function to get real data. Never make up numbers,
  email contents, or message contents.
- For email questions, call search_emails; for text-message questions, call
  search_messages. If the first results aren't relevant, rephrase the query and
  search again before giving up.
- Be concise and direct. Lead with the answer, then add context.
- When showing amounts, use dollar formatting ($1,234.56).
- If a question requires data you don't have, say so honestly.
- {date_range_info}When the user asks about a calendar month like "January", \
use calendar dates (e.g. 2023-01-01 to 2023-01-31), not statement period \
boundaries — the API will pull from the correct statements automatically.
- The account holder is Alex Ho, with spouse Jamie Lee \
(sometimes appears as Jamie Lee due to vendor typo).\
"""


def _build_system_prompt(db_path: str) -> str:
    """Build system prompt with actual date ranges from the database."""
    import sqlite3
    try:
        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            "SELECT period_start, period_end FROM statements ORDER BY period_start"
        ).fetchall()
        conn.close()
        if rows:
            ranges = [f"{r[0]} to {r[1]}" for r in rows]
            info = f"The data comes from bank statements covering: {', '.join(ranges)}. "
        else:
            info = "No bank statements have been loaded yet. "
    except Exception:
        info = ""
    return _SYSTEM_PROMPT_TEMPLATE.format(date_range_info=info)

TOOLS = [
    {
        "name": "get_transactions",
        "description": (
            "Get a list of transactions with optional filters. Use this for questions "
            "about specific transactions, finding largest/smallest amounts, or listing "
            "transactions by date or amount."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": {
                    "type": "string",
                    "description": "Filter transactions on or after this date (YYYY-MM-DD format).",
                },
                "end_date": {
                    "type": "string",
                    "description": "Filter transactions on or before this date (YYYY-MM-DD format).",
                },
                "type": {
                    "type": "string",
                    "description": (
                        "Filter by a SPECIFIC transaction type. ONLY use this when the user "
                        "explicitly names a type like 'ATM withdrawal', 'wire transfer', "
                        "'ACH debit', 'check', 'bill payment', or 'Zelle transfer'. Do NOT "
                        "use this for general questions about withdrawals, spending, or "
                        "expenses — use is_deposit=false instead. Allowed values: "
                        "atm_withdrawal, ach_debit, bill_payment, check, zelle_transfer, wire, other."
                    ),
                },
                "min_amount": {"type": "number"},
                "max_amount": {"type": "number"},
                "payee": {"type": "string", "description": "Partial match on payee/description"},
                "is_deposit": {
                    "type": "boolean",
                    "description": (
                        "Filter by transaction direction. Set to false for all outgoing "
                        "transactions (withdrawals, payments, debits, transfers, checks — "
                        "everything leaving the account). Set to true for all incoming "
                        "transactions (deposits, credits). For general questions about "
                        "'withdrawals', 'spending', or 'expenses', always use is_deposit=false."
                    ),
                },
                "sort_by": {
                    "type": "string",
                    "description": (
                        "Sort results by 'date' (default, most recent first) or 'amount' "
                        "(largest absolute amount first). Use 'amount' when the user asks "
                        "for largest, smallest, biggest, or most expensive transactions."
                    ),
                },
                "sort_dir": {
                    "type": "string",
                    "description": (
                        "Sort direction: 'desc' (default, largest/newest first) or "
                        "'asc' (smallest/oldest first)."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": (
                        "Maximum number of results to return. Use with sort_by='amount' to "
                        "get the top N largest or smallest transactions. Without "
                        "sort_by='amount', this just returns the N most recent transactions."
                    ),
                },
            },
        },
    },
    {
        "name": "get_spending_by_category",
        "description": (
            "Get total spending grouped by category and transaction type. Use this for "
            "'how much did I spend' or 'breakdown of spending' questions, NOT for "
            "finding individual transactions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "period_start": {"type": "string", "description": "Start date YYYY-MM-DD"},
                "period_end": {"type": "string", "description": "End date YYYY-MM-DD"},
            },
            "required": ["period_start", "period_end"],
        },
    },
    {
        "name": "get_recurring_bills",
        "description": (
            "Find recurring payments that appear across multiple statements. Use this "
            "for 'what are my recurring bills' or 'what do I pay every month' questions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "get_balance_summary",
        "description": (
            "Get account balance, total deposits, and total withdrawals for a period. "
            "Use this for balance checks, cash flow summaries, or 'how much came in "
            "vs went out' questions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "period_start": {"type": "string", "description": "Start date YYYY-MM-DD"},
                "period_end": {"type": "string", "description": "End date YYYY-MM-DD"},
            },
        },
    },
    {
        "name": "get_spending_comparison",
        "description": (
            "Compare spending between two time periods. Use this for 'did my spending "
            "go up' or 'compare January vs December' questions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "period1_start": {"type": "string", "description": "First period start YYYY-MM-DD"},
                "period1_end": {"type": "string", "description": "First period end YYYY-MM-DD"},
                "period2_start": {"type": "string", "description": "Second period start YYYY-MM-DD"},
                "period2_end": {"type": "string", "description": "Second period end YYYY-MM-DD"},
            },
            "required": ["period1_start", "period1_end", "period2_start", "period2_end"],
        },
    },
    {
        "name": "search_transactions",
        "description": (
            "Search transactions by keyword in the description. Use this when the "
            "user asks about a specific merchant, payee, or transaction by name."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "keyword": {
                    "type": "string",
                    "description": (
                        "Search term to match against transaction descriptions. Try "
                        "common variations if the first search returns no results (e.g., "
                        "'kia' then 'telluride', or 'cooper' for Mr. Cooper mortgage)."
                    ),
                },
                "limit": {"type": "integer", "description": "Max results, default 20"},
            },
            "required": ["keyword"],
        },
    },
    {
        "name": "get_balance_history",
        "description": (
            "Get time-series balance and cash flow data by statement period. "
            "Use this for balance trend questions or 'how has my balance "
            "changed over time'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "get_top_merchants",
        "description": (
            "Get the top merchants ranked by total spending. Use this for "
            "'where do I spend the most' or 'who are my biggest payees' questions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Number of merchants to return, default 10",
                },
            },
        },
    },
    {
        "name": "get_monthly_trends",
        "description": (
            "Get monthly income vs spending totals. Use this for 'how does my "
            "spending change month to month' or 'income vs expenses by month'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "search_emails",
        "description": (
            "Semantic search over the user's synced email inbox. Use this for ANY "
            "question about the user's emails — receipts, bills, appointments, orders, "
            "travel, people, or anything mentioned in a message. Returns the most "
            "relevant emails (sender domain, subject, date, body text)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What to look for, in natural language.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max emails to return, default 5 (capped at 5).",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_messages",
        "description": (
            "Semantic search over the user's synced text messages (iMessage/SMS). "
            "Use this for ANY question about texts — appointments, orders, "
            "confirmations, people, plans, or anything mentioned in a message. "
            "Returns the most relevant messages (contact name, direction, date, text)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What to look for, in natural language.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max messages to return, default 5 (capped at 5).",
                },
            },
            "required": ["query"],
        },
    },
]


class JarChat:
    def __init__(self, db_path: str, api_key: str = None, model: str = None):
        self.db_path = db_path
        self.api = FinancialAPI(db_path)
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ValueError("No API key found. Set ANTHROPIC_API_KEY or pass api_key=.")
        self.client = anthropic.Anthropic(api_key=self.api_key)
        self.model = model or "claude-sonnet-4-20250514"
        self.system_prompt = _build_system_prompt(db_path)
        self.conversation_history: list[dict] = []
        self.debug = False

        # Session-scoped redaction engine. Tool results (excerpts) are redacted before
        # they reach the cloud; the final answer is detokenized before display. The
        # store must live for the whole session so tokens stay consistent across turns.
        from core.privacy.profile import HouseholdProfile
        from core.privacy.redactor import RedactionEngine
        _conn = sqlite3.connect(self.db_path)
        try:
            _profile = HouseholdProfile.load(_conn)
        finally:
            _conn.close()
        # lowercase_for_ner=False: chat excerpts keep natural casing for spaCy NER.
        self.redaction = RedactionEngine(_profile, lowercase_for_ner=False)
        if not _profile.is_configured():
            logging.getLogger("jar.chat").warning(
                "Household profile not configured — redaction relies on NER only."
            )

    # Keep tool-result payloads small: this JSON is re-sent to the API on every
    # subsequent turn (it lives in conversation_history), so large bodies or many
    # rows compound across the chat and quickly blow the per-minute token limit.
    MAX_EMAIL_RESULTS = 5
    MAX_EMAIL_BODY_CHARS = 600

    # Cap how much prior conversation we replay to the API each turn. Without this
    # the history (including every past tool result) grows unbounded and re-sending
    # it eventually trips the per-minute input-token rate limit.
    MAX_HISTORY_MESSAGES = 12

    def _trim_history(self) -> None:
        """Drop oldest turns, keeping the tail short and API-valid.

        We only ever cut at a user *question* (string content), never inside a
        tool_use/tool_result pair, so the trimmed history can't start with an
        orphaned tool_result or end on a dangling tool_use.
        """
        msgs = self.conversation_history
        if len(msgs) <= self.MAX_HISTORY_MESSAGES:
            return
        cut_points = [
            i for i, m in enumerate(msgs)
            if m["role"] == "user" and isinstance(m["content"], str)
        ]
        for idx in cut_points:
            if len(msgs) - idx <= self.MAX_HISTORY_MESSAGES:
                self.conversation_history = msgs[idx:]
                return
        if cut_points:
            self.conversation_history = msgs[cut_points[-1]:]

    def _search_emails(self, query: str, limit: int = 5) -> dict:
        """Semantic search over the synced email vector store (LanceDB)."""
        from core import embeddings
        from core.email import vector_store

        query = (query or "").strip()
        if not query:
            return {"error": "empty query"}
        limit = min(limit or self.MAX_EMAIL_RESULTS, self.MAX_EMAIL_RESULTS)
        try:
            vector = embeddings.embed_texts([query])[0]
            rows = vector_store.search(vector, limit=limit)
        except Exception as exc:
            return {"error": f"email search failed: {exc}"}

        emails = [{
            "sender": r.get("sender", ""),
            "subject": r.get("subject", ""),
            "date": r.get("date", ""),
            "body": (r.get("body") or "")[:self.MAX_EMAIL_BODY_CHARS],
        } for r in rows]
        # Redact excerpts before they cross to the cloud. Email rows store sender
        # domain-only (no display name), so there are no seed names — body/subject
        # redaction relies on profile + NER + the deterministic sweep.
        from core.privacy import cloud_boundary
        emails = cloud_boundary.redact_rows(
            self.redaction, emails, text_fields=["body", "subject"], seed_names=None,
        )
        return {"count": len(emails), "emails": emails}

    MAX_MESSAGE_RESULTS = 5
    MAX_MESSAGE_BODY_CHARS = 600

    def _search_messages(self, query: str, limit: int = 5) -> dict:
        """Semantic search over the synced message vector store, redacted for cloud."""
        from core import embeddings
        from core.messages import vector_store
        from core.privacy import cloud_boundary

        query = (query or "").strip()
        if not query:
            return {"error": "empty query"}
        limit = min(limit or self.MAX_MESSAGE_RESULTS, self.MAX_MESSAGE_RESULTS)
        try:
            vector = embeddings.embed_texts([query])[0]
            rows = vector_store.search(vector, limit=limit)
        except Exception as exc:
            return {"error": f"message search failed: {exc}"}

        trimmed = [{
            "contact_name": r.get("contact_name", ""),
            "direction": "me" if r.get("is_from_me") else "them",
            "date": r.get("date", ""),
            "text": (r.get("text") or "")[:self.MAX_MESSAGE_BODY_CHARS],
        } for r in rows]
        seeds = [r.get("contact_name", "") for r in rows]
        redacted = cloud_boundary.redact_rows(
            self.redaction, trimmed, text_fields=["text", "contact_name"], seed_names=seeds,
        )
        return {"count": len(redacted), "messages": redacted}

    def _execute_tool(self, name: str, params: dict) -> dict:
        """Call the matching FinancialAPI method, tolerating LLM parameter errors."""
        import inspect

        if name == "search_emails":
            return self._search_emails(params.get("query", ""), params.get("limit", 8))

        if name == "search_messages":
            return self._search_messages(params.get("query", ""), params.get("limit", 5))

        fn = getattr(self.api, name, None)
        if fn is None:
            return {"error": f"Unknown function: {name}"}

        # Strip parameters the function doesn't accept
        sig = inspect.signature(fn)
        valid_params = set(sig.parameters.keys())
        cleaned = {k: v for k, v in params.items() if k in valid_params}
        dropped = set(params) - set(cleaned)

        if self.debug and dropped:
            print(f"  [DEBUG] dropped unknown params: {dropped}")

        return fn(**cleaned)

    def chat(self, user_message: str) -> str:
        """Send a message and get a response via Claude function-calling loop."""
        self.conversation_history.append({"role": "user", "content": user_message})
        self._trim_history()

        response = self.client.messages.create(
            model=self.model,
            max_tokens=1024,
            system=self.system_prompt,
            tools=TOOLS,
            messages=self.conversation_history,
        )

        while response.stop_reason == "tool_use":
            assistant_content = response.content
            self.conversation_history.append({"role": "assistant", "content": assistant_content})

            tool_results = []
            for block in assistant_content:
                if block.type == "tool_use":
                    if self.debug:
                        print(f"  [DEBUG] tool_call: {block.name}({json.dumps(block.input, indent=2)})")

                    result = self._execute_tool(block.name, block.input)

                    if self.debug:
                        print(f"  [DEBUG] result: {json.dumps(result, indent=2, default=str)}")

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result, default=str),
                    })

            self.conversation_history.append({"role": "user", "content": tool_results})

            response = self.client.messages.create(
                model=self.model,
                max_tokens=1024,
                system=self.system_prompt,
                tools=TOOLS,
                messages=self.conversation_history,
            )

        final_text = ""
        for block in response.content:
            if hasattr(block, "text"):
                final_text += block.text

        self.conversation_history.append({"role": "assistant", "content": response.content})
        # Reverse redaction tokens so the user sees real names; the cloud never did.
        final_text = self.redaction.detokenize(final_text)
        if re.search(r"\[[A-Z_]+(?:_\d+)?\]", final_text):
            logging.getLogger("jar.chat").warning(
                "Undetokenized redaction token survived in the answer."
            )
        return final_text

    def close(self):
        self.api.close()
