#!/usr/bin/env python3
"""Benchmark LLM function-calling: timing, correctness, and resource usage.

Usage:
    python benchmark_chat.py                    # default model (qwen3:8b)
    python benchmark_chat.py --model qwen3.5:0.8b
    python benchmark_chat.py --model qwen3.5:0.8b --json  # machine-readable output
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from core.chat.chat_engine import JarChat

DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "jar.db"

# 8 benchmark queries grouped by difficulty
QUERIES = [
    # --- Basic routing (1-3) ---
    {
        "id": 1,
        "category": "basic",
        "query": "What is my account balance?",
        "expected_function": "get_balance_summary",
        "expected_params": [],  # no required params beyond date range
        "description": "Balance check — should route to get_balance_summary",
    },
    {
        "id": 2,
        "category": "basic",
        "query": "How much did I spend in January?",
        "expected_function": "get_spending_by_category",
        "expected_params": ["period_start", "period_end"],
        "description": "Spending total — should route to get_spending_by_category",
    },
    {
        "id": 3,
        "category": "basic",
        "query": "What are my recurring bills?",
        "expected_function": "get_recurring_bills",
        "expected_params": [],
        "description": "Recurring bills — should route to get_recurring_bills",
    },
    # --- Category/filtering (4-5) ---
    {
        "id": 4,
        "category": "filtering",
        "query": "Show me a breakdown of my spending by category for January",
        "expected_function": "get_spending_by_category",
        "expected_params": ["period_start", "period_end"],
        "description": "Category breakdown — should use get_spending_by_category with Jan dates",
    },
    {
        "id": 5,
        "category": "filtering",
        "query": "What were my top 5 largest expenses?",
        "expected_function": "get_transactions",
        "expected_params": ["is_deposit", "sort_by", "limit"],
        "description": "Top merchants — should use get_transactions sorted by amount",
    },
    # --- Multi-step reasoning (6-8) ---
    {
        "id": 6,
        "category": "reasoning",
        "query": "Did my mortgage payment change between December and January?",
        "expected_function": "search_transactions",
        "expected_params": ["keyword"],
        "description": "Mortgage analysis — search for mortgage, compare across periods",
    },
    {
        "id": 7,
        "category": "reasoning",
        "query": "Compare my January and December spending",
        "expected_function": "get_spending_comparison",
        "expected_params": ["period1_start", "period1_end", "period2_start", "period2_end"],
        "description": "Month comparison — should use get_spending_comparison",
    },
    {
        "id": 8,
        "category": "reasoning",
        "query": "What percentage of my income went to housing costs?",
        "expected_function": "get_balance_summary",
        "expected_params": [],
        "description": "Income percentage — needs balance (income) + search for housing",
    },
]


def get_memory_usage():
    """Get current system memory usage via vm_stat."""
    try:
        result = subprocess.run(["vm_stat"], capture_output=True, text=True)
        lines = result.stdout.strip().split("\n")
        stats = {}
        for line in lines[1:]:
            parts = line.split(":")
            if len(parts) == 2:
                key = parts[0].strip()
                val = parts[1].strip().rstrip(".")
                try:
                    stats[key] = int(val)
                except ValueError:
                    pass
        page_size = 16384  # macOS default
        # Try to get actual page size
        ps_result = subprocess.run(["sysctl", "-n", "hw.pagesize"],
                                   capture_output=True, text=True)
        if ps_result.returncode == 0:
            page_size = int(ps_result.stdout.strip())

        free = stats.get("Pages free", 0) * page_size
        active = stats.get("Pages active", 0) * page_size
        inactive = stats.get("Pages inactive", 0) * page_size
        wired = stats.get("Pages wired down", 0) * page_size
        compressed = stats.get("Pages occupied by compressor", 0) * page_size

        used = active + wired + compressed
        total = free + active + inactive + wired + compressed

        return {
            "used_gb": round(used / (1024**3), 2),
            "free_gb": round(free / (1024**3), 2),
            "active_gb": round(active / (1024**3), 2),
            "wired_gb": round(wired / (1024**3), 2),
            "compressed_gb": round(compressed / (1024**3), 2),
        }
    except Exception as e:
        return {"error": str(e)}


def get_swap_usage():
    """Get swap usage via sysctl."""
    try:
        result = subprocess.run(["sysctl", "vm.swapusage"],
                                capture_output=True, text=True)
        return result.stdout.strip()
    except Exception:
        return "unknown"


def run_benchmark(model: str, output_json: bool = False):
    """Run all 8 queries and collect results."""
    print(f"{'=' * 70}")
    print(f"BENCHMARK: {model}")
    print(f"{'=' * 70}")

    # Pre-benchmark resource snapshot
    mem_before = get_memory_usage()
    swap_before = get_swap_usage()
    print(f"\nMemory before: {mem_before}")
    print(f"Swap before:   {swap_before}\n")

    jar = JarChat(str(DB_PATH), backend="ollama", model=model)
    jar.debug = True

    results = []
    total_start = time.time()

    for q in QUERIES:
        print(f"\n{'─' * 70}")
        print(f"Q{q['id']} [{q['category']}]: {q['query']}")
        print(f"Expected: {q['expected_function']}")
        print(f"{'─' * 70}")

        # Capture tool calls by monkey-patching
        tool_calls_made = []
        original_execute = jar._execute_tool

        def capture_execute(name, params, _orig=original_execute):
            tool_calls_made.append({"function": name, "params": params})
            return _orig(name, params)

        jar._execute_tool = capture_execute

        error_msg = None
        response = ""
        start = time.time()
        try:
            response = jar.chat(q["query"])
        except Exception as e:
            error_msg = str(e)
            print(f"  ERROR: {e}")
        elapsed = time.time() - start

        # Restore original
        jar._execute_tool = original_execute

        # Check correctness
        functions_called = [tc["function"] for tc in tool_calls_made]
        correct_function = q["expected_function"] in functions_called
        has_response = len(response.strip()) > 0

        mem_after_query = get_memory_usage()

        result = {
            "id": q["id"],
            "category": q["category"],
            "query": q["query"],
            "expected_function": q["expected_function"],
            "functions_called": functions_called,
            "tool_calls": tool_calls_made,
            "correct_function": correct_function,
            "has_response": has_response,
            "response_length": len(response),
            "response_preview": response[:200] if response else "",
            "elapsed_seconds": round(elapsed, 2),
            "error": error_msg,
            "memory_used_gb": mem_after_query.get("used_gb", "?"),
        }
        results.append(result)

        print(f"\n  Functions called: {functions_called}")
        print(f"  Correct routing: {'YES' if correct_function else 'NO'}")
        print(f"  Has response:    {'YES' if has_response else 'NO'}")
        print(f"  Time:            {elapsed:.2f}s")
        print(f"  Memory used:     {mem_after_query.get('used_gb', '?')} GB")
        print(f"\n  Response preview: {response[:300]}")

        # Reset conversation between queries
        jar.conversation_history = []

    total_elapsed = time.time() - total_start

    # Post-benchmark resource snapshot
    mem_after = get_memory_usage()
    swap_after = get_swap_usage()

    # Summary
    correct_count = sum(1 for r in results if r["correct_function"])
    response_count = sum(1 for r in results if r["has_response"])
    error_count = sum(1 for r in results if r["error"])
    avg_time = sum(r["elapsed_seconds"] for r in results) / len(results)

    summary = {
        "model": model,
        "total_queries": len(QUERIES),
        "correct_routing": correct_count,
        "has_response": response_count,
        "errors": error_count,
        "avg_response_time_s": round(avg_time, 2),
        "total_time_s": round(total_elapsed, 2),
        "memory_before": mem_before,
        "memory_after": mem_after,
        "swap_before": swap_before,
        "swap_after": swap_after,
    }

    print(f"\n{'=' * 70}")
    print(f"SUMMARY: {model}")
    print(f"{'=' * 70}")
    print(f"  Correct routing: {correct_count}/{len(QUERIES)}")
    print(f"  Has response:    {response_count}/{len(QUERIES)}")
    print(f"  Errors:          {error_count}")
    print(f"  Avg time/query:  {avg_time:.2f}s")
    print(f"  Total time:      {total_elapsed:.2f}s")
    print(f"  Memory after:    {mem_after}")
    print(f"  Swap after:      {swap_after}")

    jar.close()

    if output_json:
        output = {"summary": summary, "results": results}
        print(f"\n--- JSON OUTPUT ---")
        print(json.dumps(output, indent=2, default=str))

    return summary, results


if __name__ == "__main__":
    model = "qwen3:8b"
    output_json = "--json" in sys.argv

    for arg in sys.argv[1:]:
        if arg.startswith("--model"):
            idx = sys.argv.index(arg)
            if idx + 1 < len(sys.argv):
                model = sys.argv[idx + 1]

    run_benchmark(model, output_json)
