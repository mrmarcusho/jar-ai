"""Layer 3: LLM function-calling interface over the financial API — local Ollama backend.

Uses Ollama's /api/chat endpoint with native tool calling support.
Drop-in replacement for JarChat when running local models like Gemma 4.
"""

import inspect
import json
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from app.chat.chat_engine import TOOLS
from core.api.financial_api import FinancialAPI

_LOCAL_SYSTEM_TEMPLATE = """\
You are Jar, a personal financial assistant. Your job has three steps:

1. ANALYZE the user's question to decide which financial tool(s) will answer it.
2. CALL the tool(s). Never skip this step. Every answer must come from tool results.
3. RESPOND to the user in a clear, friendly sentence using the data the tool returned.

You have access to these tools:

- get_transactions — list transactions with filters (date range, type, payee, deposit/withdrawal, amount, sort, limit). Use for "show me", "find", "largest", "smallest", "how many" questions about individual transactions.
- get_balance_summary — account balance, total deposits, total withdrawals for a period. Use for "what's my balance", "cash flow", "how much came in vs went out".
- get_spending_by_category — spending grouped by category. Use for "how much did I spend", "breakdown", "where does my money go".
- get_monthly_trends — income vs spending by month. Use for "month over month", "trends", "how has my spending changed".
- get_recurring_bills — recurring charges across statements. Use for "recurring", "subscriptions", "what do I pay every month".
- get_top_merchants — merchants ranked by total spending. Use for "where do I spend the most", "top merchants", "biggest payees".
- get_spending_comparison — compare spending between two time periods. Use for "compare January vs February", "did spending go up".
- search_transactions — fuzzy keyword search on descriptions. Use when the user names a specific merchant or payee.
- get_balance_history — balance over time by statement period. Use for "balance trend", "balance over time".

Rules:
- ALWAYS call at least one tool. Never answer a financial question without calling a tool first.
- If a parameter is optional and the user did not specify it, omit it. Do not ask the user for optional values.
- All dollar amounts in your response must use $X,XXX.XX formatting.
- When the user says a month name like "January", use calendar dates (2023-01-01 to 2023-01-31).
- For questions about spending, withdrawals, or expenses: use is_deposit=false.
- For questions about income or deposits: use is_deposit=true.
- {date_range_info}
- The account holder is Alex Ho, with spouse Jamie Lee (sometimes appears as Jamie Lee due to vendor typo).

Examples:
- User: "What's my balance?" → call get_balance_summary()
- User: "Show my last 5 transactions" → call get_transactions(limit=5)
- User: "How much did I spend in January?" → call get_spending_by_category(period_start="2023-01-01", period_end="2023-01-31")
- User: "Any payments to Comcast?" → call search_transactions(keyword="Comcast")
- User: "What are my recurring bills?" → call get_recurring_bills()
- User: "Where do I spend the most?" → call get_top_merchants()\
"""


def _build_local_system_prompt(db_path: str) -> str:
    """Build system prompt for local models with date range info from the database."""
    import sqlite3
    try:
        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            "SELECT period_start, period_end FROM statements ORDER BY period_start"
        ).fetchall()
        conn.close()
        if rows:
            ranges = [f"{r[0]} to {r[1]}" for r in rows]
            info = f"The data covers bank statements from: {', '.join(ranges)}."
        else:
            info = "No bank statements have been loaded yet."
    except Exception:
        info = ""
    return _LOCAL_SYSTEM_TEMPLATE.format(date_range_info=info)

OLLAMA_BASE = "http://localhost:11434"
OLLAMA_TIMEOUT = 300  # 5 min — first request loads model into RAM, can be slow on 8GB


def _anthropic_tools_to_ollama(tools: list[dict]) -> list[dict]:
    """Convert Anthropic-format tool definitions to Ollama/OpenAI format."""
    converted = []
    for tool in tools:
        converted.append({
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool["input_schema"],
            },
        })
    return converted


OLLAMA_TOOLS = _anthropic_tools_to_ollama(TOOLS)


class JarLocalChat:
    def __init__(self, db_path: str, model: str = "gemma4:e4b"):
        self.db_path = db_path
        self.api = FinancialAPI(db_path)
        self.model = model
        self.system_prompt = _build_local_system_prompt(db_path)
        self.conversation_history: list[dict] = []
        self.debug = False

    def _execute_tool(self, name: str, params: dict) -> dict:
        """Call the matching FinancialAPI method, tolerating LLM parameter errors."""
        fn = getattr(self.api, name, None)
        if fn is None:
            return {"error": f"Unknown function: {name}"}

        sig = inspect.signature(fn)
        valid_params = set(sig.parameters.keys())
        cleaned = {k: v for k, v in params.items() if k in valid_params}
        dropped = set(params) - set(cleaned)

        if self.debug and dropped:
            print(f"  [DEBUG] dropped unknown params: {dropped}")

        return fn(**cleaned)

    def chat(self, user_message: str) -> str:
        """Send a message and get a response via Ollama function-calling loop."""
        self.conversation_history.append({"role": "user", "content": user_message})

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self.system_prompt},
                *self.conversation_history,
            ],
            "tools": OLLAMA_TOOLS,
            "stream": False,
        }

        resp = requests.post(f"{OLLAMA_BASE}/api/chat", json=payload, timeout=OLLAMA_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        message = data["message"]

        # Function-calling loop — keep going while the model requests tool calls
        while message.get("tool_calls"):
            # Record the assistant's tool-call message
            self.conversation_history.append(message)

            for tool_call in message["tool_calls"]:
                fn_name = tool_call["function"]["name"]
                fn_args = tool_call["function"]["arguments"]
                if isinstance(fn_args, str):
                    fn_args = json.loads(fn_args)

                if self.debug:
                    print(f"  [DEBUG] tool_call: {fn_name}({json.dumps(fn_args, indent=2)})")

                result = self._execute_tool(fn_name, fn_args)

                if self.debug:
                    print(f"  [DEBUG] result: {json.dumps(result, indent=2, default=str)}")

                # Send tool result back
                self.conversation_history.append({
                    "role": "tool",
                    "content": json.dumps(result, default=str),
                })

            # Next round
            payload["messages"] = [
                {"role": "system", "content": self.system_prompt},
                *self.conversation_history,
            ]
            resp = requests.post(f"{OLLAMA_BASE}/api/chat", json=payload, timeout=OLLAMA_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            message = data["message"]

        # Final text response
        final_text = message.get("content", "")
        self.conversation_history.append({"role": "assistant", "content": final_text})
        return final_text

    def close(self):
        self.api.close()
