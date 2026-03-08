#!/usr/bin/env python3
"""Run the 8 test questions against JarChat and print function routing + answers."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from app.chat.chat_engine import JarChat

DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "jar.db"

QUESTIONS = [
    "How much did I spend in January?",
    "What are my recurring bills?",
    "When did I pay the electric bill?",
    "Show me all payments to Jamie",
    "Compare my January and December spending",
    "What was my biggest expense?",
    "How much did I deposit in total?",
    "Did my mortgage payment change between January and December?",
]


def main():
    backend = "claude" if "--claude" in sys.argv else "ollama"
    api_key = None
    if backend == "claude":
        import os
        api_key = os.environ.get("ANTHROPIC_API_KEY")
    jar = JarChat(str(DB_PATH), backend=backend, api_key=api_key)
    jar.debug = True
    print(f"Backend: {backend} ({jar.model})\n")

    for i, q in enumerate(QUESTIONS, 1):
        print("=" * 70)
        print(f"Q{i}: {q}")
        print("=" * 70)
        answer = jar.chat(q)
        print(f"\nAnswer:\n{answer}\n")
        # Reset history between questions so each is independent
        jar.conversation_history = []

    jar.close()


if __name__ == "__main__":
    main()
