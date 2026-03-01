"""Layer 3: LLM function-calling interface over the financial API.

Supports two backends:
  - "ollama"  : local Qwen 2.5 3B via Ollama (default)
  - "claude"  : Anthropic Claude Sonnet via API
"""

import json
import os
import re
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

# Additional instructions appended for Qwen (Ollama) only.
# Small models don't naturally retry or verify — they need explicit procedural guidance.
QWEN_INSTRUCTIONS = """\

IMPORTANT — How to answer questions step by step:

1. THINK before calling a function. Ask yourself: what exactly is the user asking for?
   - "largest withdrawal" = the single biggest outgoing transaction of ANY type (ACH, wire, bill pay, ATM, check, Zelle — all of them). Do NOT filter by type unless the user specifically says "ATM withdrawal" or "wire transfer" etc.
   - "spending" = all outgoing transactions, not just one category.
   - When in doubt, use is_deposit=false with NO type filter to get all outgoing transactions.

2. CHECK your results before answering.
   - If you got 0 results: try again with broader filters. Remove type filters, widen the date range, or try search_transactions with a different keyword.
   - If you got results but the question asks for "largest" or "smallest": scan ALL the amounts and pick the correct one. Do not just return the first result.
   - If the count is low (1-2 results) but you expected more: your filters may be too narrow. Try again without the type filter.

3. VERIFY when the stakes are high.
   - For "largest", "smallest", "total", or "how much" questions: if your first call used any filters beyond date range and is_deposit, make a second call WITHOUT those extra filters to make sure you didn't miss anything.
   - It is BETTER to make 2-3 function calls and give the right answer than to make 1 call and give the wrong answer.

4. Common mistakes to avoid:
   - Do NOT set type="atm_withdrawal" when the user says "withdrawal" — that excludes wires, ACH debits, bill payments, and checks.
   - Do NOT set limit=1 unless you are sure the results are sorted correctly.
   - Do NOT summarize all transactions when the user asked a specific question. Answer the specific question first.

5. PRESENT all the data you collected.
   - If you made multiple function calls, use the results from ALL of them in your response, not just the last one.
   - For summary or overview questions: structure your response with clear sections covering each part of what the user asked for.
   - For "financial summary" questions: always include cash flow (income vs spending), biggest expenses (top 3-5), and any notable or unusual transactions.
   - Do NOT throw away useful data. If you retrieved it, the user probably wants to see it.
   - Use dollar formatting ($1,234.56) and keep it organized but concise.\
"""

SYSTEM_PROMPT_QWEN = SYSTEM_PROMPT + "\n\n" + QWEN_INSTRUCTIONS

# Tool definitions in Anthropic format (used by Claude backend)
TOOLS_ANTHROPIC = [
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
            self.model = model or "qwen3:8b"
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

        messages = [{"role": "system", "content": SYSTEM_PROMPT_QWEN}] + self.conversation_history

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

            messages = [{"role": "system", "content": SYSTEM_PROMPT_QWEN}] + self.conversation_history

            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=TOOLS_OPENAI,
                temperature=0.1,
            )
            msg = response.choices[0].message

        final_text = msg.content or ""
        final_text = self._strip_thinking(final_text)
        self.conversation_history.append({"role": "assistant", "content": final_text})
        return final_text

    @staticmethod
    def _strip_thinking(text: str) -> str:
        """Remove Qwen3 <think>...</think> blocks, log them for debugging."""
        think_pattern = re.compile(r'<think>(.*?)</think>', re.DOTALL)
        thoughts = think_pattern.findall(text)
        for thought in thoughts:
            print(f"  [THINK] {thought.strip()}")
        return think_pattern.sub('', text).strip()

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
