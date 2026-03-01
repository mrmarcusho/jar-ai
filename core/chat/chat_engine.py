"""Layer 3: LLM function-calling interface over the financial API.

Supports two backends:
  - "ollama"  : local Qwen 2.5 3B via Ollama (default)
  - "claude"  : Anthropic Claude Sonnet via API
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from core.api.financial_api import FinancialAPI

SYSTEM_PROMPT = """\
You are Jar, a personal financial assistant. You have access to the user's bank \
transaction data through a set of query functions.

When answering questions:
- Always call the appropriate function to get real data. Never make up numbers.
- Be concise and direct. Lead with the answer, then add context.
- When showing amounts, use dollar formatting ($1,234.56).
- If a question requires data you don't have, say so honestly.
- The data covers two statement periods: Jan 14 - Feb 10, 2023 and Nov 15 - Dec 13, 2023.
- The account holder is Alex Ho, with spouse Jamie Lee \
(sometimes appears as Jamie Lee due to vendor typo).\
"""

# Tool definitions in Anthropic format (used by Claude backend)
TOOLS_ANTHROPIC = [
    {
        "name": "get_transactions",
        "description": (
            "Search and filter transactions. Use this when the user asks about "
            "specific transactions, wants to find a payment, or asks 'when did I "
            "pay X' or 'show me transactions for X'. Supports filtering by date "
            "range, transaction type, amount range, payee name, and deposit/withdrawal."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "Start date YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "End date YYYY-MM-DD"},
                "type": {
                    "type": "string",
                    "description": (
                        "Transaction type: bill_payment, ach_debit, direct_deposit, "
                        "ach_credit, zelle_transfer, atm_withdrawal, branch_deposit, other"
                    ),
                },
                "min_amount": {"type": "number"},
                "max_amount": {"type": "number"},
                "payee": {"type": "string", "description": "Partial match on payee/description"},
                "is_deposit": {
                    "type": "boolean",
                    "description": "true for income/deposits, false for spending/withdrawals",
                },
                "limit": {"type": "integer", "description": "Max results, default 50"},
            },
        },
    },
    {
        "name": "get_spending_by_category",
        "description": (
            "Get spending breakdown by category and type for a date range. Use when "
            "user asks 'how much did I spend on X', 'where is my money going', "
            "'spending breakdown', or 'what are my biggest expenses'."
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
            "Find bills and payments that recur across multiple statements. Use when "
            "user asks 'what are my recurring bills', 'monthly expenses', "
            "'subscriptions', or 'what do I pay every month'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "get_balance_summary",
        "description": (
            "Get account balance overview and cash flow. Use when user asks "
            "'what's my balance', 'how much do I have', 'cash flow', "
            "'am I saving money', or 'financial summary'."
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
            "Compare spending between two time periods. Use when user asks "
            "'did my spending go up', 'compare January to December', "
            "'spending trend', or 'am I spending more'."
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
            "Free text search across all transaction descriptions and ACH fields. "
            "Use when user asks about a specific company, person, or merchant by name."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "Search term"},
                "limit": {"type": "integer", "description": "Max results, default 20"},
            },
            "required": ["keyword"],
        },
    },
]


def _anthropic_to_openai_tools(tools):
    """Convert Anthropic tool format to OpenAI function-calling format."""
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        }
        for t in tools
    ]


TOOLS_OPENAI = _anthropic_to_openai_tools(TOOLS_ANTHROPIC)


class JarChat:
    def __init__(self, db_path: str, backend: str = "ollama", api_key: str = None,
                 model: str = None, ollama_url: str = "http://localhost:11434/v1"):
        self.api = FinancialAPI(db_path)
        self.backend = backend
        self.conversation_history: list[dict] = []
        self.debug = False

        if backend == "ollama":
            from openai import OpenAI
            self.client = OpenAI(base_url=ollama_url, api_key="ollama")
            self.model = model or "qwen2.5:7b"
        elif backend == "claude":
            import anthropic
            self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
            if not self.api_key:
                raise ValueError("No API key found. Set ANTHROPIC_API_KEY or pass api_key=.")
            self.client = anthropic.Anthropic(api_key=self.api_key)
            self.model = model or "claude-sonnet-4-20250514"
        else:
            raise ValueError(f"Unknown backend: {backend}. Use 'ollama' or 'claude'.")

    def _execute_tool(self, name: str, params: dict) -> dict:
        """Call the matching FinancialAPI method, tolerating LLM parameter errors."""
        import inspect

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

    def _chat_ollama(self, user_message: str) -> str:
        """OpenAI-compatible function-calling loop for Ollama."""
        self.conversation_history.append({"role": "user", "content": user_message})

        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + self.conversation_history

        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=TOOLS_OPENAI,
            temperature=0.1,
        )

        msg = response.choices[0].message

        # Loop for tool calls
        while msg.tool_calls:
            self.conversation_history.append(msg.model_dump(exclude_none=True))

            for tc in msg.tool_calls:
                fn_name = tc.function.name
                fn_args = json.loads(tc.function.arguments)

                if self.debug:
                    print(f"  [DEBUG] tool_call: {fn_name}({json.dumps(fn_args, indent=2)})")

                result = self._execute_tool(fn_name, fn_args)

                if self.debug:
                    print(f"  [DEBUG] result: {json.dumps(result, indent=2, default=str)}")

                self.conversation_history.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result, default=str),
                })

            messages = [{"role": "system", "content": SYSTEM_PROMPT}] + self.conversation_history

            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=TOOLS_OPENAI,
                temperature=0.1,
            )
            msg = response.choices[0].message

        final_text = msg.content or ""
        self.conversation_history.append({"role": "assistant", "content": final_text})
        return final_text

    def _chat_claude(self, user_message: str) -> str:
        """Anthropic function-calling loop for Claude."""
        self.conversation_history.append({"role": "user", "content": user_message})

        response = self.client.messages.create(
            model=self.model,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            tools=TOOLS_ANTHROPIC,
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
                system=SYSTEM_PROMPT,
                tools=TOOLS_ANTHROPIC,
                messages=self.conversation_history,
            )

        final_text = ""
        for block in response.content:
            if hasattr(block, "text"):
                final_text += block.text

        self.conversation_history.append({"role": "assistant", "content": response.content})
        return final_text

    def chat(self, user_message: str) -> str:
        """Route to the appropriate backend."""
        if self.backend == "ollama":
            return self._chat_ollama(user_message)
        else:
            return self._chat_claude(user_message)

    def close(self):
        self.api.close()
