"""Epic 28: context resolver and context packs, and their effect on gap
analysis, blueprint evaluation, agent generation, chat, and staleness."""

import json

from app.context.resolver import render_pack, resolve_pack
from app.db.session import get_session_factory
from app.schemas.context import ProcessScope
from tests.test_api_blueprint import _finalize, _llm_json, _node_result


def _create(client, **body):
    response = client.post("/api/organization/entities", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def _review_control(client, **extra):
    return _create(
        client,
        kind="control",
        code="AML-4",
        name="Sanctions matches reviewed by MLRO",
        data={
            "description": "Potential sanctions matches go to the MLRO.",
            "control_type": "human_review",
            "requires_human_review": True,
            "review_role": "MLRO",
            "required_guardrails": ["decision_logging"],
        },
        applies_to={"jurisdictions": ["EU"]},
        **extra,
    )


def _scoped_process(client, jurisdictions=("DE",)):
    return client.post(
        "/api/processes", json={"name": "Onboarding", "scope": {"jurisdictions": list(jurisdictions)}}
    ).json()


def test_resolver_ranks_budgets_and_dedups(client):
    _review_control(client)
    _create(client, kind="control", code="LOG-1", name="Log decisions", data={"description": "Keep a log."})
    _create(client, kind="glossary_term", name="Merchant", data={"definition": "A business we settle payments for."})
    _create(client, kind="glossary_term", name="Chargeback", data={"definition": "A reversed card payment."})
    _create(client, kind="control", code="US-1", name="US only", applies_to={"jurisdictions": ["US"]})

    session = get_session_factory()()
    try:
        scope = ProcessScope(jurisdictions=["DE"])
        pack = resolve_pack(session, scope, purpose="blueprint_evaluation", query_text="Onboard a new merchant")
        names = [e["name"] for e in pack.entries]
        # Human-review control first, then other controls; the US control
        # doesn't apply; only the glossary term the text mentions is in.
        assert names[0] == "Sanctions matches reviewed by MLRO"
        assert "Log decisions" in names and "US only" not in names
        assert "Merchant" in names and "Chargeback" not in names
        assert [e["label"] for e in pack.entries] == [f"C{i}" for i in range(1, len(names) + 1)]
        rendered = render_pack(pack)
        assert "[C1] Control AML-4: Sanctions matches reviewed by MLRO." in rendered
        assert "Requires human review by MLRO" in rendered

        # Same inputs -> same pack (content-addressed).
        again = resolve_pack(session, scope, purpose="blueprint_evaluation", query_text="Onboard a new merchant")
        assert again.id == pack.id

        tiny = resolve_pack(session, scope, purpose="blueprint_evaluation", query_text="merchant", budget=20)
        assert len(tiny.entries) == 1 and tiny.truncated >= 2

        # Chat/extraction see vocabulary only.
        vocab = resolve_pack(session, scope, purpose="chat_edit", query_text="rename the merchant step")
        assert [e["kind"] for e in vocab.entries] == ["glossary_term"]
        assert render_pack(vocab).startswith("## Organization vocabulary")
        session.commit()
    finally:
        session.close()


def test_passage_retrieval_only_from_applicable_policies(client, fake_llm):
    fake_llm.complete.return_value = _llm_json({})
    eu = client.post(
        "/api/organization/documents",
        files={"file": ("eu.txt", b"1. Sanctions\nPotential sanctions matches must be reviewed by the MLRO.", "text/plain")},
        data={"title": "EU AML policy", "jurisdictions": "EU"},
    ).json()
    client.post(
        "/api/organization/documents",
        files={"file": ("us.txt", b"1. Sanctions\nUS sanctions matches go to the OFAC officer.", "text/plain")},
        data={"title": "US AML policy", "jurisdictions": "US"},
    )
    process = _scoped_process(client)
    pack = client.get(
        f"/api/processes/{process['id']}/scope/context-pack", params={"purpose": "gap_analysis", "query": "sanctions screening"}
    ).json()
    assert len(pack["passages"]) == 1
    assert pack["passages"][0]["policy_entity_id"] == eu["policy_entity_id"]
    assert pack["passages"][0]["label"] == "P1"
    assert "MLRO" in pack["rendered"]
    # Inspector returns the same pinned pack by id.
    assert client.get(f"/api/context-packs/{pack['id']}").json()["passages"] == pack["passages"]


def test_blueprint_cites_rules_and_never_automates_a_review_step(client, fake_llm):
    _review_control(client)
    process = _scoped_process(client)
    _finalize(client, process["id"])

    fake_llm.complete.return_value = _llm_json(
        {
            "nodes": [
                _node_result("Event_start", verdict="not_automatable", agent_spec=None, not_automatable_reason="entry"),
                # The model cites the control but still says automatable.
                _node_result("Task_a", applicable_rules=["C1", "C99"]),
                _node_result("Event_end", verdict="not_automatable", agent_spec=None, not_automatable_reason="exit"),
            ]
        }
    )
    overlay = client.post(f"/api/processes/{process['id']}/blueprint/generate", json={}).json()
    prompt = fake_llm.complete.call_args.args[0][0].content
    assert "## Organization context" in prompt and "[C1] Control AML-4" in prompt
    assert '"applicable_rules"' in prompt

    task = next(n for n in overlay["nodes"] if n["node_id"] == "Task_a")
    assert task["verdict"] == "partial"
    assert task["agent_spec"]["human_checkpoint"] == "review_before_action"
    assert "AML-4 requires human review" in task["rationale"]
    assert task["applicable_rules"] == ["C1"]  # unknown C99 dropped
    assert task["citations"][0]["clause_ref"] == "AML-4"
    assert overlay["context_pack_id"] and overlay["context_stale"] is False

    # US28.6: the generated agent carries the rule.
    artifact = client.post(f"/api/processes/{process['id']}/blueprint/nodes/Task_a/agent-artifact").json()
    definition = artifact["definition"]
    assert definition["escalation_policy"]["escalation_target"]["role"] == "MLRO"
    assert "policy_requires_human_review" in definition["escalation_policy"]["escalate_when"]
    assert definition["policy_guardrails"] == ["decision_logging"]
    assert [s["name"] for s in definition["knowledge_sources"]] == ["AML-4"]
    assert "Organization rules you must follow" in definition["system_prompt"]
    assert artifact["context_pack_id"] == overlay["context_pack_id"]
    assert artifact["status"] == "generated"


def test_context_change_marks_blueprint_and_agent_stale_and_reports_impact(client, fake_llm):
    control = _review_control(client)
    process = _scoped_process(client)
    _finalize(client, process["id"])
    fake_llm.complete.return_value = _llm_json(
        {
            "nodes": [
                _node_result("Event_start", verdict="not_automatable", agent_spec=None, not_automatable_reason="entry"),
                _node_result("Task_a", verdict="partial", applicable_rules=["C1"]),
                _node_result("Event_end", verdict="not_automatable", agent_spec=None, not_automatable_reason="exit"),
            ]
        }
    )
    client.post(f"/api/processes/{process['id']}/blueprint/generate", json={})
    artifact = client.post(f"/api/processes/{process['id']}/blueprint/nodes/Task_a/agent-artifact").json()

    impact = client.get(f"/api/organization/entities/{control['id']}/impact").json()
    assert impact["blueprint_process_ids"] == [process["id"]]
    assert impact["agent_artifact_ids"] == [artifact["id"]]
    assert "1 blueprint, 1 agent, 0 regression suites affected" in impact["summary"]

    client.put(f"/api/organization/entities/{control['id']}", json={"data": {"requires_human_review": True, "review_role": "Head of Compliance"}})

    overlay = client.get(f"/api/processes/{process['id']}/blueprint").json()
    assert overlay["context_stale"] is True
    artifacts = client.get(f"/api/processes/{process['id']}/blueprint/agent-artifacts").json()
    assert artifacts[0]["context_stale"] is True and artifacts[0]["status"] == "stale"
    pack = client.get(f"/api/context-packs/{overlay['context_pack_id']}").json()
    assert pack["stale_entries"][0]["pinned_version"] == 1
    assert pack["stale_entries"][0]["current_version"] == 2


def test_gap_analysis_raises_cited_policy_findings(client, fake_llm):
    _review_control(client)
    process = _scoped_process(client)
    session = get_session_factory()()
    from app.db import repository
    from app.schemas.common import ProcessElement, ProcessSchema

    try:
        repository.merge_process_schema(
            session,
            process["id"],
            ProcessSchema(
                process_name="Onboarding",
                elements=[
                    ProcessElement(
                        id="screen",
                        type="task",
                        label="Screen customer against sanctions",
                        source_refs=[],
                        confidence="high",
                    )
                ],
            ),
            document_id=None,
        )
        session.commit()
        element_id = repository.get_process_schema(session, process["id"]).elements[0].id
    finally:
        session.close()

    fake_llm.complete.return_value = _llm_json(
        {
            "findings": [
                {
                    "kind": "policy",
                    "question": "AML-4 says potential matches need MLRO review -- who reviews them here?",
                    "target_element_ids": [element_id],
                    "citations": ["C1"],
                    "options": [{"label": "Add an MLRO review step", "diff": None}],
                },
                {
                    "kind": "policy",
                    "question": "Uncited rule from general knowledge",
                    "target_element_ids": [],
                    "citations": [],
                    "options": [],
                },
            ]
        }
    )
    findings = client.post(f"/api/processes/{process['id']}/gap-findings/analyze").json()
    prompt = fake_llm.complete.call_args.args[0][0].content
    assert '"policy"' in prompt and "[C1] Control AML-4" in prompt
    policy = [f for f in findings if f["kind"] == "policy"]
    assert len(policy) == 1
    assert policy[0]["citations"][0]["clause_ref"] == "AML-4"
    assert policy[0]["context_pack_id"]

    # Re-running doesn't duplicate it.
    findings = client.post(f"/api/processes/{process['id']}/gap-findings/analyze").json()
    assert len([f for f in findings if f["kind"] == "policy"]) == 1


def test_unscoped_process_prompt_is_unchanged(client, fake_llm):
    _review_control(client)
    process = client.post("/api/processes", json={"name": "Legacy"}).json()
    _finalize(client, process["id"])
    fake_llm.complete.return_value = _llm_json(
        {
            "nodes": [
                _node_result("Event_start", verdict="not_automatable", agent_spec=None, not_automatable_reason="entry"),
                _node_result("Task_a"),
                _node_result("Event_end", verdict="not_automatable", agent_spec=None, not_automatable_reason="exit"),
            ]
        }
    )
    overlay = client.post(f"/api/processes/{process['id']}/blueprint/generate", json={}).json()
    prompt = fake_llm.complete.call_args.args[0][0].content
    assert "Organization context" not in prompt and "##CONTEXT_RULES##" not in prompt
    assert next(n for n in overlay["nodes"] if n["node_id"] == "Task_a")["verdict"] == "automatable"


def test_chat_gets_vocabulary(client, fake_llm):
    _create(client, kind="application", name="Salesforce", data={"aliases": ["the CRM"]})
    process = client.post("/api/processes", json={"name": "P"}).json()
    session = get_session_factory()()
    from app.db import repository
    from app.schemas.common import ProcessElement, ProcessSchema

    try:
        repository.merge_process_schema(
            session,
            process["id"],
            ProcessSchema(
                process_name="P",
                elements=[
                    ProcessElement(id="a", type="task", label="Update record", source_refs=[], confidence="high")
                ],
            ),
            document_id=None,
        )
        session.commit()
    finally:
        session.close()
    fake_llm.complete.return_value = _llm_json({"kind": "explain", "reply_text": "ok", "diff": None})
    client.post(f"/api/processes/{process['id']}/chat/messages", json={"text": "make it update the CRM"})
    prompt = fake_llm.complete.call_args.args[0][0].content
    assert "## Organization vocabulary" in prompt
    assert "Application Salesforce. Also known as: the CRM." in prompt
    assert json  # keep import used


def test_labels_in_prose_become_citations_and_are_humanized(client, fake_llm):
    _review_control(client)
    process = _scoped_process(client)
    _finalize(client, process["id"])
    fake_llm.complete.return_value = _llm_json(
        {
            "nodes": [
                _node_result("Event_start", verdict="not_automatable", agent_spec=None, not_automatable_reason="entry"),
                # Cites nothing in the field, but names C1 in the rationale.
                _node_result("Task_a", verdict="partial", rationale="Needs review per [C1]; priority P1 stays."),
                _node_result("Event_end", verdict="not_automatable", agent_spec=None, not_automatable_reason="exit"),
            ]
        }
    )
    overlay = client.post(f"/api/processes/{process['id']}/blueprint/generate", json={}).json()
    task = next(n for n in overlay["nodes"] if n["node_id"] == "Task_a")
    assert task["rationale"] == "Needs review per AML-4; priority P1 stays."
    assert task["applicable_rules"] == ["C1"]
    assert task["citations"][0]["clause_ref"] == "AML-4"
