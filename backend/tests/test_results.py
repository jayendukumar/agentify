"""Epic 35: results at volume and the statistical release gate."""

import json

from app.batches.results import mcnemar_p, wilson
from app.llm.types import ToolCall
from tests.test_api_twin import _llm_result
from tests.test_batches import ROWS, SCHEMA, _setup


def test_wilson_and_mcnemar():
    rate = wilson(94, 100)
    assert rate.rate == 0.94 and abs(rate.low - 0.8752) < 0.002 and abs(rate.high - 0.9722) < 0.002
    edge = wilson(10, 10)
    assert edge.high == 1.0 and edge.low > 0.69  # sensible at 100%, unlike the normal approximation
    assert wilson(0, 0).rate is None
    assert abs(mcnemar_p(10, 2) - 0.0386) < 0.001
    assert mcnemar_p(3, 3) == 1.0 and mcnemar_p(0, 0) == 1.0


def _flaky_agent(flips):
    """Approves "a" records and rejects others, except it alternates on a2."""
    state = {"a2": 0}

    def respond(messages, **kwargs):
        if kwargs.get("operation") == "twin_tool_schema_inference":
            return _llm_result(text=json.dumps(SCHEMA))
        if not any(m.role == "tool" for m in messages):
            return _llm_result(tool_calls=[ToolCall(id="c1", name="lookup_record", arguments={"record_id": "x"})])
        user_text = next((m.content for m in messages if m.role == "user" and isinstance(m.content, str)), "")
        record = json.loads(user_text[user_text.index("{"):]) if "{" in user_text else {}
        record_id = str(record.get("record_id", ""))
        decision = flips.get(record_id) or ("approve" if record_id.startswith("a") else "reject")
        if record_id == "a2" and flips.get("flaky"):
            state["a2"] += 1
            decision = "approve" if state["a2"] % 2 else "reject"
        return _llm_result(text=json.dumps({"summary": "done", "decision": decision, "status": "Reject it"}))

    return respond


def _run_batch(client, process, scenario, **body):
    started = client.post(f"/api/processes/{process['id']}/scenarios/{scenario['id']}/batches", json={"tier": "full", **body})
    assert started.status_code == 202, started.text
    return started.json()["id"]


def test_results_headline_breakdowns_clusters_and_consistency(client, fake_llm):
    process, artifact, dataset, scenario = _setup(client, fake_llm, ROWS)
    fake_llm.complete.side_effect = _flaky_agent({"flaky": True})
    batch_id = _run_batch(client, process, scenario, trials=2)
    results = client.get(f"/api/processes/{process['id']}/batches/{batch_id}/results").json()

    # a1 x2 pass, a2 pass once, b1 x2 pass, b2 (labelled approve) x2 fail -> 5/8
    assert results["headline"]["passed"] == 5 and results["headline"]["total"] == 8
    assert results["reading"].startswith("The agent handles about 63 in 100 cases correctly")
    segments = {s["segment"]: s for s in results["segments"]}
    assert segments["typical"]["rate"]["passed"] == 3 and segments["edge"]["rate"]["passed"] == 2
    assert results["decision_matrix"]["approve"] == {"approve": 3, "reject": 3}
    assert results["decision_matrix"]["reject"] == {"reject": 2}
    assert results["clusters"][0]["pattern"] == "Expected the case to be decided '…', but it was '…'"
    assert results["clusters"][0]["count"] == 3 and set(results["clusters"][0]["row_keys"]) == {"a2", "b2"}
    consistency = results["consistency"]
    assert (consistency["always_pass"], consistency["always_fail"], consistency["inconsistent"]) == (2, 1, 1)
    assert consistency["inconsistent_rows"] == ["a2"] and consistency["pass_at_k"] == 0.75 and consistency["pass_hat_k"] == 0.5
    assert results["critical_failures"] == ["b2"]

    # Marking a row ambiguous afterwards removes it from the pass rate.
    client.put(f"/api/processes/{process['id']}/datasets/{dataset['id']}/rows/b2/ambiguity", json={"ambiguous": True, "note": "policy unclear"})
    again = client.get(f"/api/processes/{process['id']}/batches/{batch_id}/results").json()
    assert again["headline"]["total"] == 6 and again["excluded"]["ambiguous"] == 2


def test_paired_comparison_and_statistical_gate(client, fake_llm):
    rows = [{"row_key": f"a{i}", "inputs": {"record_id": f"a{i}"}, "labels": {"decision": "approve"}, "segment": "typical"} for i in range(12)]
    process, artifact, dataset, scenario = _setup(client, fake_llm, rows)
    # v1: rejects ten approvable cases.
    fake_llm.complete.side_effect = _flaky_agent({f"a{i}": "reject" for i in range(10)})
    before = _run_batch(client, process, scenario)
    # Same agent version, behaviour fixed (stands in for a regenerated agent).
    fake_llm.complete.side_effect = _flaky_agent({})
    after = _run_batch(client, process, scenario)

    comparison = client.get(f"/api/processes/{process['id']}/batch-comparison", params={"a": before, "b": after}).json()
    assert len(comparison["fixed"]) == 10 and comparison["regressed"] == []
    assert comparison["significant"] is True and comparison["p_value"] < 0.01
    assert "a real change for the better" in comparison["reading"]

    rc = f"/api/processes/{process['id']}/blueprint/agent-artifacts/{artifact['id']}/mark-release-candidate"
    client.put(
        f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/release-criteria",
        json={"min_pass_rate_lower": 0.95, "min_segment_rate": 0.9},
    )
    gate = client.get(f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/release-gate").json()
    # 12/12 passed, but with only 12 cases the lower bound is about 76% -- not enough evidence for 95%.
    assert gate["passed"] is False and "could be as low as 76%" in gate["reasons"][0]
    refused = client.post(rc)
    assert refused.status_code == 400 and "Release criteria not met" in refused.json()["detail"]

    client.put(
        f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/release-criteria",
        json={"min_pass_rate_lower": 0.7, "min_segment_rate": 0.9},
    )
    assert client.get(f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/release-gate").json()["passed"] is True
    assert client.post(rc).status_code == 200

    evidence = client.get(f"/api/processes/{process['id']}/batches/{after}/evidence").json()
    assert evidence["dataset"]["version"] == 2 and evidence["headline"]["passed"] == 12
    assert evidence["gate"]["passed"] is True and "Wilson" in evidence["method"]


def test_metamorphic_relations_are_graded_from_batch(client, fake_llm):
    rows = [
        {"row_key": "a1", "inputs": {"record_id": "a1"}, "labels": {"decision": "approve"}},
        {"row_key": "b1", "inputs": {"record_id": "b1"}, "labels": {"decision": "reject"}},
    ]
    process, artifact, dataset, scenario = _setup(client, fake_llm, rows)
    base = f"/api/processes/{process['id']}/datasets/{dataset['id']}"
    relation = client.post(f"{base}/relations", json={"name": "Casing", "kind": "invariance", "field": "record_id", "perturbation": {"type": "uppercase"}}).json()
    client.post(f"{base}/relations/{relation['id']}/variants")
    # The agent's prefix check is case-sensitive, so "A1" is decided differently: the relation breaks there.
    fake_llm.complete.side_effect = _flaky_agent({})
    batch_id = _run_batch(client, process, scenario)
    results = client.get(f"/api/processes/{process['id']}/batches/{batch_id}/results").json()
    [rel] = results["relations"]
    assert rel["checked"] == 2 and rel["held"] == 1 and len(rel["broken_rows"]) == 1


def test_load_profile(client, fake_llm):
    process, artifact, dataset, scenario = _setup(client, fake_llm, ROWS)
    fake_llm.complete.side_effect = _flaky_agent({})
    batch_id = _run_batch(client, process, scenario, mode="load", target_concurrency=4, stub_latency_ms=5)
    results = client.get(f"/api/processes/{process['id']}/batches/{batch_id}/results").json()
    assert results["load"]["runs"] == 4 and results["load"]["failure_rate"] == 0.0
    assert results["load"]["p95_seconds"] is not None
