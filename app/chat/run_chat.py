#!/usr/bin/env python3
"""Interactive REPL for the Jar financial assistant.

Usage:
    python run_chat.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from app.chat.chat_engine import JarChat

DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "jar.db"


def main():
    jar = JarChat(str(DB_PATH))
    jar.debug = True

    print(f"\nJar Financial Assistant — Claude ({jar.model})")
    print("Commands: 'debug on' / 'debug off' / 'quit'")
    print(f"Data: {DB_PATH}\n")

    while True:
        try:
            user_input = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        if not user_input:
            continue
        if user_input.lower() == "quit":
            print("Goodbye!")
            break
        if user_input.lower() == "debug on":
            jar.debug = True
            print("Debug mode ON")
            continue
        if user_input.lower() == "debug off":
            jar.debug = False
            print("Debug mode OFF")
            continue

        try:
            answer = jar.chat(user_input)
            print(f"\n{answer}\n")
        except Exception as e:
            print(f"\nError: {e}\n")

    jar.close()


if __name__ == "__main__":
    main()
