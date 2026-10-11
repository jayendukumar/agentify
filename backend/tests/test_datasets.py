"""Epic 32: labelled datasets and ground truth."""

import io
import json

import openpyxl

from app.datasets.service import pseudonymize
from app.llm.types import ToolCall
from tests.test_api_twin import _await_terminal_twin_run, _llm_result, _make_artifact, _scenario_payload

SCHEMA = {
    "tools": [
        {
            "system_name": "CRM API",
            "tool_name": "lookup_record",
            "description": "Look up a record by id.",
            "parameters": {"type": "object", "properties": {"record_id": {"type": "string"}}},
            "response_shape_description": "An object with a 'tier' field.",
        }
    ]
}


def _dataset(client, process_id, artifact_id=None, name="Golden set"):
    response = client.post(f"/api/processes/{process_id}/datasets", json={"name": name, "agent_artifact_id": artifact_id})
    assert response.status_code == 201, response.text
    return response.json()


def _url(process_id, dataset_id, path=""):
    return f"/api/processes/{process_id}/datasets/{dataset_id}{path}"


def test_rows_are_revisioned_and_diffable(client):
    process, artifact = _make_artifact(client)
    dataset = _dataset(client, process["id"], artifact["id"])
    summary = client.post(
        _url(process["id"], dataset["id"], "/rows"),
        json=[
            {"row_key": "r1", "inputs": {"record_id": "a"}, "labels": {"outputs": {"summary": "ok"}, "decision": "approve"}, "segment": "typical"},
            {"row_key": "r2", "inputs": {"record_id": "b"}, "labels": {"decision": "reject"}, "segment": "edge", "provenance": "synthetic"},
        ],
    ).json()
    assert summary["version"] == 2 and summary["row_count"] == 2
    assert summary["segments"] == {"typical": 1, "edge": 1} and summary["provenance"] == {"expert": 1, "synthetic": 1}

    # US32.4: correcting a label records who did it and makes it expert-labelled.
    row = client.patch(_url(process["id"], dataset["id"], "/rows/r2"), json={"labels": {"decision": "approve"}}).json()
    assert row["labels"]["decision"] == "approve" and row["confirmed_by"] and row["provenance"] == "expert"
    client.delete(_url(process["id"], dataset["id"], "/rows/r1"))
    client.post(_url(process["id"], dataset["id"], "/rows"), json=[{"row_key": "r3", "inputs": {"record_id": "c"}}])

    current = client.get(_url(process["id"], dataset["id"])).json()
    assert current["version"] == 5 and current["row_count"] == 2
    # The dataset as it was at v2 is still readable exactly.
    old = client.get(_url(process["id"], dataset["id"], "/rows?version=2")).json()
    assert sorted(r["row_key"] for r in old["rows"]) == ["r1", "r2"]
    assert next(r for r in old["rows"] if r["row_key"] == "r2")["labels"]["decision"] == "reject"

    diff = client.get(_url(process["id"], dataset["id"], "/diff?from=2")).json()
    assert (diff["added"], diff["removed"], diff["relabelled"]) == (1, 1, 1)
    assert {c["row_key"]: c["change"] for c in diff["changes"]} == {"r1": "removed", "r2": "relabelled", "r3": "added"}


def test_ambiguity_agreement_and_holdout(client):
    process, artifact = _make_artifact(client)
    dataset = _dataset(client, process["id"], artifact["id"])
    rows = [{"row_key": f"r{i}", "inputs": {"record_id": str(i)}, "labels": {"decision": "approve"}, "segment": "typical" if i < 6 else "edge"} for i in range(10)]
    client.post(_url(process["id"], dataset["id"], "/rows"), json=rows)

    client.put(_url(process["id"], dataset["id"], "/rows/r0/ambiguity"), json={"ambiguous": True, "note": "Policy unclear", "second_labels": {"decision": "reject"}})
    client.put(_url(process["id"], dataset["id"], "/rows/r1/ambiguity"), json={"ambiguous": False, "second_labels": {"decision": "approve"}})
    summary = client.get(_url(process["id"], dataset["id"])).json()
    assert summary["ambiguous_count"] == 1 and summary["agreement_rate"] == 0.5

    split = client.post(_url(process["id"], dataset["id"], "/holdout"), json={"fraction": 0.5, "seed": 3}).json()
    assert split["holdout_count"] == 5  # 3 of 6 typical + 2 of 4 edge
    page = client.get(_url(process["id"], dataset["id"], "/rows?only=holdout")).json()
    assert page["total"] == 5 and all(r["labels"] is None for r in page["rows"])  # labels hidden
    revealed = client.get(_url(process["id"], dataset["id"], "/rows?only=holdout&include_holdout=true")).json()
    assert all(r["labels"] is not None for r in revealed["rows"])


def test_pseudonymize_is_deterministic_and_shape_preserving():
    assert pseudonymize("Ada Lovelace", "fake") == pseudonymize("Ada Lovelace", "fake")
    assert " " in pseudonymize("Ada Lovelace", "fake") and pseudonymize("Ada Lovelace", "fake") != "Ada Lovelace"
    assert pseudonymize("ada@bank.example", "fake").endswith("@example.com")
    phone = pseudonymize("+44 20 7946 0958", "fake")
    assert phone != "+44 20 7946 0958" and len(phone) == len("+44 20 7946 0958") and phone[0] == "+"
    iban = pseudonymize("DE89370400440532013000", "fake")
    assert iban.startswith("DE89") and len(iban) == 22 and iban != "DE89370400440532013000"
    assert pseudonymize("12345", "hash").startswith("anon_")
    assert pseudonymize("x", "keep") == "x"


def test_historical_import_preview_mapping_and_pseudonymization(client):
    process, artifact = _make_artifact(client)
    dataset = _dataset(client, process["id"], artifact["id"])
    csv_text = "record_id,customer_name,Final Status,expected_summary,segment\nA-1,Ada Lovelace,Approved,ok,typical\nA-2,Alan Turing,Declined,,edge\n,,,,\n"
    preview = client.post(
        _url(process["id"], dataset["id"], "/import/preview"), files={"file": ("cases.csv", csv_text.encode(), "text/csv")}
    ).json()
    assert preview["total_rows"] == 3
    roles = {m["column"]: (m["role"], m.get("field")) for m in preview["suggested"]}
    assert roles["record_id"] == ("input", "record_id")
    assert roles["Final Status"][0] == "decision"
    assert roles["expected_summary"] == ("label", "summary")
    assert roles["segment"][0] == "segment"
    assert len(client.get(_url(process["id"], dataset["id"], "/rows")).json()["rows"]) == 0  # nothing stored

    mapping = [dict(m) for m in preview["suggested"]]
    for m in mapping:
        if m["column"] == "customer_name":
            m["pseudonymize"] = "fake"
    result = client.post(
        _url(process["id"], dataset["id"], "/import"),
        files={"file": ("cases.csv", csv_text.encode(), "text/csv")},
        data={"mapping": json.dumps(mapping)},
    ).json()
    assert result["added"] == 2 and result["skipped"] == 1
    rows = {r["inputs"]["record_id"]: r for r in client.get(_url(process["id"], dataset["id"], "/rows")).json()["rows"]}
    assert rows["A-1"]["labels"] == {"outputs": {"summary": "ok"}, "decision": "approve"}
    assert rows["A-2"]["labels"]["decision"] == "reject" and rows["A-2"]["segment"] == "edge"
    assert rows["A-1"]["provenance"] == "historical"
    assert rows["A-1"]["inputs"]["customer_name"] not in ("Ada Lovelace", None)


def test_xlsx_import(client):
    process, artifact = _make_artifact(client)
    dataset = _dataset(client, process["id"], artifact["id"])
    workbook = openpyxl.Workbook()
    workbook.active.append(["record_id", "Decision"])
    workbook.active.append(["X-1", "rejected"])
    buffer = io.BytesIO()
    workbook.save(buffer)
    files = {"file": ("cases.xlsx", buffer.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    preview = client.post(_url(process["id"], dataset["id"], "/import/preview"), files=files).json()
    assert preview["mapped_sample"][0]["labels"]["decision"] == "reject"


def _run_row(client, fake_llm, process, scenario, row_key, final, *, first=False):
    # Tool schemas are inferred once per artifact, then cached.
    fake_llm.complete.side_effect = ([_llm_result(text=json.dumps(SCHEMA))] if first else []) + [
        _llm_result(tool_calls=[ToolCall(id="c1", name="lookup_record", arguments={"record_id": "x"})]),
        _llm_result(tool_calls=[ToolCall(id="c2", name="request_human_decision", arguments={"summary": "ok?", "proposed_action": {}})]),
        _llm_result(text=json.dumps(final)),
    ]
    started = client.post(f"/api/processes/{process['id']}/scenarios/{scenario['id']}/run?row_key={row_key}")
    assert started.status_code == 202, started.text
    return _await_terminal_twin_run(client, process["id"], started.json()["id"])


def test_data_driven_scenario_grades_each_row_against_its_labels(client, fake_llm):
    process, artifact = _make_artifact(client)
    dataset = _dataset(client, process["id"], artifact["id"])
    client.post(
        _url(process["id"], dataset["id"], "/rows"),
        json=[
            {"row_key": "good", "inputs": {"record_id": "a"}, "labels": {"outputs": {"summary": "done"}, "decision": "approve"}},
            {"row_key": "bad", "inputs": {"record_id": "b"}, "labels": {"decision": "reject"}},
        ],
    )
    scenario = client.post(
        f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/scenarios",
        json=_scenario_payload(name="Data-driven", dataset_id=dataset["id"], step_match="in_order", expected_steps=[{"kind": "tool_call", "target": "CRM API"}]),
    ).json()
    assert scenario["dataset_id"] == dataset["id"]

    passed = _run_row(client, fake_llm, process, scenario, "good", {"summary": "done", "decision": "Approved"}, first=True)
    assert passed["graded_passed"] is True and passed["row_key"] == "good" and passed["dataset_version"] == 2
    failed = _run_row(client, fake_llm, process, scenario, "bad", {"summary": "done", "decision": "approved"})
    assert failed["graded_passed"] is False, failed["deviations"]
    assert [d["reason"] for d in failed["deviations"]] == ["Expected the case to be decided 'reject', but it was 'approve'"]

    snapshot = client.get(f"/api/processes/{process['id']}/runs/{failed['id']}/snapshot").json()
    assert json.dumps(snapshot).count(dataset["id"]) >= 1

    # US32.7: promote the failure into the golden set with the right answer.
    promoted = client.post(
        _url(process["id"], dataset["id"], "/rows/from-run"),
        json={"run_id": failed["id"], "labels": {"decision": "reject", "outputs": {"summary": "declined"}}, "critical": True},
    ).json()
    assert promoted["inputs"] == {"record_id": "b"} and promoted["source_run_id"] == failed["id"] and promoted["critical"] is True

    missing = client.post(f"/api/processes/{process['id']}/scenarios/{scenario['id']}/run?row_key=nope")
    assert missing.status_code == 404
