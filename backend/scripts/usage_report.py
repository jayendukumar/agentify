"""Summarize recorded LLM token usage from the local usage log.

Reads the JSONL file at Settings().usage_log_path (default
backend/.data/llm_usage.jsonl) and prints per-operation totals plus a grand
total, so you can see where API spend went across runs -- not just within
one process. See Epic 10, US10.3.

Run from the backend/ directory:
    .venv/Scripts/python.exe scripts/usage_report.py
"""

import json
import sys
from collections import defaultdict
from dataclasses import dataclass

from app.config import get_settings


@dataclass
class _Totals:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0
    cost_estimate_incomplete: bool = False


def main() -> None:
    log_path = get_settings().usage_log_path
    if not log_path.exists():
        print(f"No usage log found at {log_path} -- make at least one LLM call first.")
        sys.exit(0)

    totals: dict[str, _Totals] = defaultdict(_Totals)
    event_count = 0

    with log_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            event = json.loads(line)
            event_count += 1
            op = totals[event["operation"]]
            op.calls += 1
            op.input_tokens += event["input_tokens"]
            op.output_tokens += event["output_tokens"]
            op.total_tokens += event["total_tokens"]
            if event["estimated_cost_usd"] is None:
                op.cost_estimate_incomplete = True
            else:
                op.estimated_cost_usd += event["estimated_cost_usd"]

    print(f"Usage log: {log_path} ({event_count} calls)\n")
    header = f"{'operation':<24} {'calls':>6} {'input':>10} {'output':>10} {'total':>10} {'est. cost':>12}"
    print(header)
    print("-" * len(header))

    grand_calls = grand_input = grand_output = grand_total = 0
    grand_cost = 0.0
    grand_incomplete = False

    for operation, t in sorted(totals.items()):
        cost_str = f"${t.estimated_cost_usd:.6f}" if not t.cost_estimate_incomplete else "unknown"
        print(f"{operation:<24} {t.calls:>6} {t.input_tokens:>10} {t.output_tokens:>10} {t.total_tokens:>10} {cost_str:>12}")
        grand_calls += t.calls
        grand_input += t.input_tokens
        grand_output += t.output_tokens
        grand_total += t.total_tokens
        grand_cost += t.estimated_cost_usd
        grand_incomplete = grand_incomplete or t.cost_estimate_incomplete

    print("-" * len(header))
    grand_cost_str = f"${grand_cost:.6f}" if not grand_incomplete else "partial (some models unpriced)"
    print(f"{'TOTAL':<24} {grand_calls:>6} {grand_input:>10} {grand_output:>10} {grand_total:>10} {grand_cost_str:>12}")


if __name__ == "__main__":
    main()
