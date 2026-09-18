from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .types import Usage

logger = logging.getLogger("app.llm.usage")

# Approximate $ per 1M tokens, (input, output). Update this if pricing or
# the default model changes -- see planning/claude-api-access-notes.md for
# the source comparison. Unknown models simply get no cost estimate rather
# than a wrong one.
_PRICING_PER_MILLION_USD: dict[str, tuple[float, float]] = {
    "qwen/qwen3.7-flash": (0.03, 0.13),
}


def estimate_cost_usd(model: str, usage: Usage) -> float | None:
    rates = _PRICING_PER_MILLION_USD.get(model)
    if rates is None:
        return None
    input_rate, output_rate = rates
    return (usage.input_tokens * input_rate + usage.output_tokens * output_rate) / 1_000_000


@dataclass(frozen=True)
class UsageEvent:
    timestamp: str
    operation: str
    model: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    estimated_cost_usd: float | None

    def to_json_line(self) -> str:
        return json.dumps(
            {
                "timestamp": self.timestamp,
                "operation": self.operation,
                "model": self.model,
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
                "total_tokens": self.total_tokens,
                "estimated_cost_usd": self.estimated_cost_usd,
            }
        )


@dataclass
class OperationTotals:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0
    cost_estimate_incomplete: bool = False


class UsageTracker:
    """Accumulates LLM token usage for the life of this tracker, and (when a
    log path is configured) appends every call as a JSON line to a local
    file, so usage survives process restarts and can be reviewed later with
    scripts/usage_report.py. This is Epic 10's US10.3 (LLM API cost/usage
    tracking).
    """

    def __init__(self, log_path: Path | None = None) -> None:
        self._log_path = log_path
        self._totals: dict[str, OperationTotals] = {}
        self._events: list[UsageEvent] = []

    def record(self, *, operation: str, model: str, usage: Usage) -> UsageEvent:
        cost = estimate_cost_usd(model, usage)
        event = UsageEvent(
            timestamp=datetime.now(timezone.utc).isoformat(),
            operation=operation,
            model=model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            total_tokens=usage.total_tokens,
            estimated_cost_usd=cost,
        )
        self._events.append(event)

        totals = self._totals.setdefault(operation, OperationTotals())
        totals.calls += 1
        totals.input_tokens += usage.input_tokens
        totals.output_tokens += usage.output_tokens
        totals.total_tokens += usage.total_tokens
        if cost is None:
            totals.cost_estimate_incomplete = True
        else:
            totals.estimated_cost_usd += cost

        logger.info(
            "llm_usage",
            extra={
                "operation": operation,
                "model": model,
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "total_tokens": usage.total_tokens,
                "estimated_cost_usd": cost,
            },
        )

        if self._log_path is not None:
            self._append_to_log(event)

        return event

    def _append_to_log(self, event: UsageEvent) -> None:
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._log_path.open("a", encoding="utf-8") as f:
                f.write(event.to_json_line() + "\n")
        except OSError as exc:
            logger.warning(
                "llm_usage_log_write_failed",
                extra={"error": str(exc), "path": str(self._log_path)},
            )

    def events(self) -> list[UsageEvent]:
        return list(self._events)

    def summary(self) -> dict[str, OperationTotals]:
        return dict(self._totals)

    def grand_total_tokens(self) -> int:
        return sum(t.total_tokens for t in self._totals.values())

    def grand_total_cost_usd(self) -> float:
        return sum(t.estimated_cost_usd for t in self._totals.values())
