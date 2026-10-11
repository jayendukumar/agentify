"""Epic 27: organization context foundation."""

import json

from app.context.ingestion import split_into_passages
from app.ingestion.extractors import ExtractedBlock
from app.llm.types import ChatCompletionResult, Usage


def _llm_json(payload: dict) -> ChatCompletionResult:
    return ChatCompletionResult(
        text=json.dumps(payload),
        finish_reason="stop",
        usage=Usage(input_tokens=1, output_tokens=1, total_tokens=2),
        model="test-model",
    )


def _create(client, **body):
    response = client.post("/api/organization/entities", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def test_organization_is_created_lazily_and_editable(client):
    org = client.get("/api/organization").json()
    assert org["name"] == "My organization"
    updated = client.put("/api/organization", json={"name": "Meridian Group", "industry": "Banking", "size": "large"})
    assert updated.json()["name"] == "Meridian Group"
    assert client.get("/api/organization").json()["id"] == org["id"]


def test_viewer_cannot_write_context(viewer_client):
    assert viewer_client.get("/api/organization").status_code == 200
    response = viewer_client.post("/api/organization/entities", json={"kind": "role", "name": "MLRO"})
    assert response.status_code == 403


def test_profile_wizard_creates_scope_and_is_idempotent(client):
    answers = {
        "name": "Corner Bakery",
        "industry": "Retail banking",
        "countries": ["DE", "UK"],
        "business_units": [],
        "applications": ["Core banking", "CRM"],
        "approval_rules": [{"role": "Head of Operations", "decision_type": "refund", "limit_amount": 5000, "currency": "EUR"}],
    }
    result = client.post("/api/organization/profile", json=answers).json()
    assert result["created"] == {
        "jurisdiction": 2,
        "business_unit": 1,
        "application": 2,
        "role": 1,
        "approval_authority": 1,
    }
    assert "eu-uk-aml-basics" in result["suggested_packs"]
    assert "eu-data-protection-basics" in result["suggested_packs"]
    assert "us-health-privacy-basics" not in result["suggested_packs"]

    jurisdictions = {j["code"]: j for j in client.get("/api/organization/entities?kind=jurisdiction").json()}
    # DE pulled in its parent EU automatically.
    assert set(jurisdictions) == {"DE", "EU", "UK"}
    assert jurisdictions["DE"]["data"]["parent_code"] == "EU"
    units = client.get("/api/organization/entities?kind=business_unit").json()
    assert [u["name"] for u in units] == ["Corner Bakery"]

    # Re-running with a changed limit updates rather than duplicates.
    answers["approval_rules"][0]["limit_amount"] = 8000
    again = client.post("/api/organization/profile", json=answers).json()
    assert again["created"] == {}
    authorities = client.get("/api/organization/entities?kind=approval_authority").json()
    assert len(authorities) == 1
    assert authorities[0]["data"]["limit_amount"] == 8000
    assert authorities[0]["version"] == 2


def test_starter_pack_apply_is_versioned_and_idempotent(client):
    packs = {p["id"]: p for p in client.get("/api/organization/starter-packs").json()}
    assert set(packs) >= {"eu-data-protection-basics", "eu-uk-aml-basics", "us-health-privacy-basics"}
    assert "not legal advice" in packs["eu-uk-aml-basics"]["disclaimer"]

    first = client.post("/api/organization/starter-packs/eu-uk-aml-basics/apply").json()
    assert first["created"] > 0 and first["updated"] == 0
    second = client.post("/api/organization/starter-packs/eu-uk-aml-basics/apply").json()
    assert second["created"] == 0 and second["updated"] == 0 and second["unchanged"] == first["created"]

    controls = {c["code"]: c for c in client.get("/api/organization/entities?kind=control").json()}
    sanctions = controls["AML-C-SANCTIONS-MATCH-REVIEW"]
    assert sanctions["data"]["requires_human_review"] is True
    assert sanctions["source"] == "starter_pack:eu-uk-aml-basics@1.0.0"
    assert set(sanctions["applies_to"]["jurisdictions"]) == {"EU", "UK"}
    assert client.get("/api/organization").json()["applied_packs"] == {"eu-uk-aml-basics": "1.0.0"}
    assert client.post("/api/organization/starter-packs/nope/apply").status_code == 404


def test_entity_versioning_history_and_retire(client):
    role = _create(client, kind="role", name="MLRO", data={"description": "AML reporting"})
    assert role["status"] == "confirmed" and role["version"] == 1

    same = client.put(f"/api/organization/entities/{role['id']}", json={"name": "MLRO"}).json()
    assert same["version"] == 1  # no-op save doesn't bump

    changed = client.put(
        f"/api/organization/entities/{role['id']}", json={"data": {"description": "Money laundering reporting officer"}, "change_note": "clarified"}
    ).json()
    assert changed["version"] == 2
    history = client.get(f"/api/organization/entities/{role['id']}/versions").json()
    assert [h["version"] for h in history] == [1, 2]
    assert history[1]["change_note"] == "clarified"
    assert history[0]["snapshot"]["data"]["description"] == "AML reporting"

    retired = client.delete(f"/api/organization/entities/{role['id']}").json()
    assert retired["status"] == "retired"
    assert client.get("/api/organization/entities?kind=role").json() == []
    assert client.put(f"/api/organization/entities/{role['id']}", json={"name": "x"}).status_code == 400


def test_entity_data_is_validated_per_kind(client):
    response = client.post(
        "/api/organization/entities",
        json={"kind": "approval_authority", "name": "bad", "data": {"limit_amount": "lots"}},
    )
    assert response.status_code == 400
    assert "approval_authority" in response.json()["detail"]


def test_process_scope_and_applicability(client):
    client.post("/api/organization/starter-packs/eu-uk-aml-basics/apply")
    client.post("/api/organization/starter-packs/us-health-privacy-basics/apply")
    banking = _create(client, kind="business_unit", name="Retail Banking")
    insurance = _create(client, kind="business_unit", name="Insurance")
    _create(client, kind="control", name="Banking-only control", applies_to={"business_units": [banking["id"]]})

    process = client.post(
        "/api/processes",
        json={"name": "Customer Onboarding", "scope": {"business_unit_id": banking["id"], "jurisdictions": ["de"]}},
    ).json()
    assert process["scope"]["jurisdictions"] == ["DE"]
    assert client.get(f"/api/processes/{process['id']}/scope").json()["business_unit_id"] == banking["id"]

    eu_banking = client.get(
        f"/api/organization/applicable?business_unit_id={banking['id']}&jurisdictions=DE"
    ).json()["entities"]
    codes = {c["code"] for c in eu_banking["control"]}
    assert "AML-C-SANCTIONS-MATCH-REVIEW" in codes  # EU covers DE
    assert not any(code.startswith("HIPAA") for code in codes if code)
    assert "Banking-only control" in {c["name"] for c in eu_banking["control"]}

    us_insurance = client.get(
        f"/api/organization/applicable?business_unit_id={insurance['id']}&jurisdictions=US-CA"
    ).json()["entities"]
    us_codes = {c["code"] for c in us_insurance["control"]}
    assert "HIPAA-C-ACCESS-LOG" in us_codes  # US covers US-CA
    assert "AML-C-SANCTIONS-MATCH-REVIEW" not in us_codes
    assert "Banking-only control" not in {c["name"] for c in us_insurance["control"]}

    # Organization-wide scope sees neither jurisdiction's rules.
    org_wide = client.get("/api/organization/applicable").json()["entities"]
    assert "control" not in org_wide or all(not c["applies_to"]["jurisdictions"] for c in org_wide["control"])

    # A non-business-unit id is rejected as a scope.
    role = _create(client, kind="role", name="MLRO")
    bad = client.put(f"/api/processes/{process['id']}/scope", json={"business_unit_id": role["id"]})
    assert bad.status_code == 400


def test_split_into_passages_respects_headings_and_size():
    blocks = [
        ExtractedBlock(kind="text", location="page 1", content="1. Purpose\nThis policy sets rules.\n\n2. Approvals\nRefunds over 500 EUR need the Head of Operations."),
        ExtractedBlock(kind="text", location="page 2", content="word " * 600),
    ]
    passages = split_into_passages(blocks)
    assert passages[0].text.startswith("1. Purpose")
    assert passages[1].text.startswith("2. Approvals")
    assert all(len(p.text) <= 1600 for p in passages)
    assert passages[-1].clause_ref == "page 2"


POLICY_TEXT = b"""Expense and Refund Policy

1. Refund approvals
Refunds above 500 EUR must be approved by the Head of Operations before they are paid.

2. Sanctions
Any potential sanctions match must be reviewed by the MLRO. It must never be cleared automatically.

3. Definitions
A Merchant is any business we settle card payments for.
"""


def _extraction_payload():
    return {
        "obligations": [
            {"code": "REF-1", "title": "Approve large refunds", "text": "Refunds above 500 EUR need approval.", "citations": ["P1"], "confidence": 0.95},
            {"code": "GHOST", "title": "Uncited rule", "text": "Not in the document.", "citations": ["P99"], "confidence": 0.9},
        ],
        "controls": [
            {
                "code": "SAN-1",
                "title": "Sanctions matches reviewed by MLRO",
                "control_type": "human_review",
                "requires_human_review": True,
                "required_guardrails": ["decision_logging"],
                "review_role": "MLRO",
                "citations": ["P2"],
                "confidence": 0.9,
            }
        ],
        "roles": [{"name": "Head of Operations", "description": "Approves refunds", "citations": ["P1"], "confidence": 0.8}],
        "approval_authorities": [
            {"role": "Head of Operations", "decision_type": "refund", "limit_amount": 500, "currency": "EUR", "citations": ["P1"], "confidence": 0.5}
        ],
        "glossary_terms": [{"term": "Merchant", "definition": "A business we settle card payments for.", "citations": ["P3"], "confidence": 0.9}],
    }


def _upload(client, text=POLICY_TEXT, **form):
    response = client.post(
        "/api/organization/documents",
        files={"file": ("refund-policy.txt", text, "text/plain")},
        data={"title": "Expense and Refund Policy", "code": "POL-7", "jurisdictions": "EU", **form},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_policy_upload_extracts_cited_proposals(client, fake_llm):
    fake_llm.complete.return_value = _llm_json(_extraction_payload())
    document = _upload(client)

    detail = client.get(f"/api/organization/documents/{document['id']}").json()
    assert detail["status"] == "done", detail
    passages = client.get(f"/api/organization/documents/{document['id']}/passages").json()
    # The title line joins the first clause rather than standing alone.
    assert len(passages) == 3
    assert passages[0]["text"].startswith("Expense and Refund Policy\n1. Refund approvals")
    assert passages[1]["clause_ref"] == "2. Sanctions (paragraph 3)"
    assert passages[0]["applies_to"]["jurisdictions"] == ["EU"]

    proposals = client.get("/api/organization/entities?status=proposed").json()
    by_name = {p["name"]: p for p in proposals}
    # The uncited "GHOST" obligation was dropped; everything else is proposed.
    assert "Uncited rule" not in by_name
    assert detail["proposal_count"] == len(proposals) == 5
    sanctions = by_name["Sanctions matches reviewed by MLRO"]
    assert sanctions["citations"][0]["passage_id"] == passages[1]["id"]
    assert sanctions["citations"][0]["policy_id"] == document["policy_entity_id"]
    assert sanctions["source"] == f"document:{document['id']}"

    # Nothing extracted takes effect until confirmed.
    assert client.get("/api/organization/entities?kind=control").json() == []
    prompt = fake_llm.complete.call_args.args[0][0].content
    assert "[P1]" in prompt and "Refunds above 500 EUR" in prompt


def test_bulk_confirm_skips_low_confidence_and_relaxations(client, fake_llm):
    _create(
        client,
        kind="control",
        code="SAN-1",
        name="Sanctions matches reviewed by MLRO",
        data={"control_type": "human_review", "requires_human_review": True, "required_guardrails": ["decision_logging"]},
    )
    payload = _extraction_payload()
    # The new document would drop human review from an existing control.
    payload["controls"][0]["requires_human_review"] = False
    payload["controls"][0]["required_guardrails"] = []
    fake_llm.complete.return_value = _llm_json(payload)
    _upload(client)

    proposals = {p["name"]: p for p in client.get("/api/organization/entities?status=proposed").json()}
    relaxing = proposals["Sanctions matches reviewed by MLRO"]
    assert relaxing["relaxes"] is True and relaxing["supersedes_id"] is not None

    result = client.post("/api/organization/entities/bulk-confirm", json={}).json()
    assert relaxing["id"] in result["skipped"]
    assert proposals["Head of Operations: refund"]["id"] in result["skipped"]  # confidence 0.5
    assert proposals["Approve large refunds"]["id"] in result["confirmed"]

    # A relaxation needs explicit acknowledgement...
    denied = client.post(f"/api/organization/entities/{relaxing['id']}/confirm", json={})
    assert denied.status_code == 400
    merged = client.post(
        f"/api/organization/entities/{relaxing['id']}/confirm", json={"acknowledge_relaxation": True}
    ).json()
    # ...and is merged into the existing control as its next version.
    assert merged["id"] == relaxing["supersedes_id"]
    assert merged["version"] == 2
    assert merged["data"]["requires_human_review"] is False
    assert client.get(f"/api/organization/entities/{relaxing['id']}").status_code == 404


def test_dismiss_and_reupload_new_policy_version(client, fake_llm):
    fake_llm.complete.return_value = _llm_json({"glossary_terms": _extraction_payload()["glossary_terms"]})
    first = _upload(client)
    proposal = client.get("/api/organization/entities?status=proposed").json()[0]
    assert client.post(f"/api/organization/entities/{proposal['id']}/dismiss").json()["status"] == "dismissed"
    assert client.post(f"/api/organization/entities/{proposal['id']}/confirm", json={}).status_code == 400

    second = _upload(client, text=POLICY_TEXT + b"\n4. New clause\nAll refunds are logged.\n")
    # Same policy code -> same policy entity, next version; old passages kept but not current.
    assert second["policy_entity_id"] == first["policy_entity_id"]
    policy = client.get(f"/api/organization/entities/{first['policy_entity_id']}").json()
    assert policy["version"] == 2
    old = client.get(f"/api/organization/documents/{first['id']}/passages").json()
    new = client.get(f"/api/organization/documents/{second['id']}/passages").json()
    assert all(not p["current"] for p in old)
    assert all(p["current"] and p["policy_version"] == 2 for p in new)


def test_reextraction_dedups_reworded_rules(client, fake_llm):
    control = {
        "title": "MLRO Review of Potential Sanctions Match",
        "description": "Potential sanctions matches are reviewed by the MLRO.",
        "control_type": "human_review",
        "requires_human_review": True,
        "review_role": "MLRO",
        "citations": ["P2"],
        "confidence": 0.9,
    }
    fake_llm.complete.return_value = _llm_json({"controls": [control]})
    _upload(client)
    # A revised policy; the model words the same control slightly differently.
    fake_llm.complete.return_value = _llm_json({"controls": [{**control, "title": "MLRO Review of Potential Sanctions Matches"}]})
    _upload(client, text=POLICY_TEXT + b"\n4. Revision\nMinor edits.\n")
    assert len(client.get("/api/organization/entities?status=proposed&kind=control").json()) == 1

    client.post("/api/organization/entities/bulk-confirm", json={})
    # Only reworded: no update proposal at all.
    reworded = {**control, "title": "MLRO review of sanctions matches", "description": "Potential sanctions matches get reviewed by the MLRO."}
    fake_llm.complete.return_value = _llm_json({"controls": [reworded]})
    _upload(client, text=POLICY_TEXT + b"\n4. Revision\nMore edits.\n")
    assert client.get("/api/organization/entities?status=proposed&kind=control").json() == []

    # A material change (a different reviewer) is an update proposal, not a new rule.
    fake_llm.complete.return_value = _llm_json({"controls": [{**reworded, "review_role": "Head of Compliance"}]})
    _upload(client, text=POLICY_TEXT + b"\n4. Revision\nNew reviewer.\n")
    proposals = client.get("/api/organization/entities?status=proposed&kind=control").json()
    assert len(proposals) == 1 and proposals[0]["supersedes_id"] is not None
    assert len(client.get("/api/organization/entities?kind=control").json()) == 1


def test_identical_reupload_is_refused(client, fake_llm):
    fake_llm.complete.return_value = _llm_json({})
    _upload(client)
    again = client.post(
        "/api/organization/documents",
        files={"file": ("refund-policy.txt", POLICY_TEXT, "text/plain")},
        data={"title": "Expense and Refund Policy", "code": "POL-7"},
    )
    assert again.status_code == 409 and "already the current version of POL-7" in again.json()["detail"]


def test_failed_extraction_marks_document_failed(client, fake_llm):
    fake_llm.complete.return_value = ChatCompletionResult(
        text="not json", finish_reason="stop", usage=Usage(input_tokens=1, output_tokens=1, total_tokens=2), model="m"
    )
    document = _upload(client)
    detail = client.get(f"/api/organization/documents/{document['id']}").json()
    assert detail["status"] == "failed"
    assert "invalid structured output" in detail["error_message"]


def test_unsupported_policy_type_rejected(client):
    response = client.post(
        "/api/organization/documents", files={"file": ("x.png", b"\x89PNG", "image/png")}, data={"title": "x"}
    )
    assert response.status_code == 400
