import json

from app.llm.types import Usage
from app.llm.usage import UsageTracker, estimate_cost_usd


def test_estimate_cost_known_model():
    usage = Usage(input_tokens=1_000_000, output_tokens=1_000_000, total_tokens=2_000_000)
    cost = estimate_cost_usd("qwen/qwen3.7-flash", usage)
    assert cost == 0.03 + 0.13


def test_estimate_cost_unknown_model_returns_none():
    usage = Usage(input_tokens=100, output_tokens=100, total_tokens=200)
    assert estimate_cost_usd("some/unpriced-model", usage) is None


def test_tracker_accumulates_totals_per_operation():
    tracker = UsageTracker(log_path=None)
    usage_a = Usage(input_tokens=10, output_tokens=5, total_tokens=15)
    usage_b = Usage(input_tokens=20, output_tokens=8, total_tokens=28)

    tracker.record(operation="ingestion", model="qwen/qwen3.7-flash", usage=usage_a)
    tracker.record(operation="ingestion", model="qwen/qwen3.7-flash", usage=usage_b)
    tracker.record(operation="chat_edit", model="qwen/qwen3.7-flash", usage=usage_a)

    summary = tracker.summary()

    assert summary["ingestion"].calls == 2
    assert summary["ingestion"].input_tokens == 30
    assert summary["ingestion"].output_tokens == 13
    assert summary["ingestion"].total_tokens == 43
    assert summary["chat_edit"].calls == 1
    assert tracker.grand_total_tokens() == 58


def test_tracker_flags_incomplete_cost_for_unpriced_model():
    tracker = UsageTracker(log_path=None)
    usage = Usage(input_tokens=10, output_tokens=5, total_tokens=15)

    tracker.record(operation="ingestion", model="some/unpriced-model", usage=usage)

    summary = tracker.summary()
    assert summary["ingestion"].cost_estimate_incomplete is True
    assert summary["ingestion"].estimated_cost_usd == 0.0


def test_tracker_writes_jsonl_log(tmp_path):
    log_path = tmp_path / "usage.jsonl"
    tracker = UsageTracker(log_path=log_path)
    usage = Usage(input_tokens=10, output_tokens=5, total_tokens=15)

    tracker.record(operation="bpmn_generation", model="qwen/qwen3.7-flash", usage=usage)
    tracker.record(operation="bpmn_generation", model="qwen/qwen3.7-flash", usage=usage)

    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2

    parsed = json.loads(lines[0])
    assert parsed["operation"] == "bpmn_generation"
    assert parsed["model"] == "qwen/qwen3.7-flash"
    assert parsed["input_tokens"] == 10
    assert parsed["output_tokens"] == 5
    assert parsed["total_tokens"] == 15
    assert parsed["estimated_cost_usd"] == estimate_cost_usd("qwen/qwen3.7-flash", usage)
    assert "timestamp" in parsed
