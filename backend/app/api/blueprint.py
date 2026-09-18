from fastapi import APIRouter, HTTPException, Query, Response, status

from app.blueprint.service import BlueprintServiceError, evaluate_blueprint
from app.bpmn.nodes import extract_flow_nodes
from app.db import repository
from app.schemas.blueprint import BlueprintGenerateRequest, BlueprintOverlay, BlueprintOverrideRequest

from .deps import DbDep, LLMDep

router = APIRouter(prefix="/api/processes/{process_id}/blueprint", tags=["blueprint"])


@router.post("/generate", response_model=BlueprintOverlay)
async def generate_blueprint(process_id: str, body: BlueprintGenerateRequest, db: DbDep, llm: LLMDep) -> BlueprintOverlay:
    repository.get_process(db, process_id)  # 404s if missing
    version = (
        repository.get_version(db, process_id, body.version_id)
        if body.version_id
        else repository.get_latest_version(db, process_id)
    )
    if version is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="Process has no finalized version yet -- finalize a baseline first"
        )

    flow_nodes = extract_flow_nodes(version.xml)
    if not flow_nodes:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Finalized diagram has no steps to evaluate")

    try:
        nodes = await evaluate_blueprint(llm, flow_nodes=flow_nodes)
    except BlueprintServiceError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    return repository.set_blueprint_overlay(db, process_id, version.id, nodes)


@router.get("", response_model=BlueprintOverlay)
def get_blueprint(process_id: str, db: DbDep) -> BlueprintOverlay:
    repository.get_process(db, process_id)  # 404s if missing
    overlay = repository.get_blueprint_overlay(db, process_id)
    if overlay is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No blueprint yet -- call /generate first")
    return overlay


@router.patch("/nodes/{node_id}", response_model=BlueprintOverlay)
def override_blueprint_node(
    process_id: str, node_id: str, body: BlueprintOverrideRequest, db: DbDep
) -> BlueprintOverlay:
    repository.get_process(db, process_id)  # 404s if missing
    # update_blueprint_node raises NotFoundError (-> 404 via the global
    # handler in app/main.py) for an unknown process or node id.
    return repository.update_blueprint_node(
        db, process_id, node_id, verdict=body.verdict, justification=body.justification
    )


@router.get("/export")
def export_blueprint(
    process_id: str, db: DbDep, format: str = Query("markdown", pattern="^(markdown)$")
) -> Response:
    process = repository.get_process(db, process_id)  # 404s if missing
    overlay = repository.get_blueprint_overlay(db, process_id)
    if overlay is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No blueprint yet -- call /generate first")

    lines = [f"# Agentic Blueprint -- {process.name}", ""]
    automatable = [n for n in overlay.nodes if n.verdict != "not_automatable"]
    not_automatable = [n for n in overlay.nodes if n.verdict == "not_automatable"]

    lines.append(f"**{len(automatable)}/{len(overlay.nodes)} steps automatable or partially automatable.**")
    lines.append("")
    lines.append("## Automatable steps")
    for node in automatable:
        lines.append(f"### {node.node_id} -- {node.verdict}")
        lines.append(node.rationale)
        if node.agent_spec:
            lines.append(f"- Agent: **{node.agent_spec.name}** -- {node.agent_spec.purpose}")
        lines.append("")

    lines.append("## Not automatable")
    for node in not_automatable:
        lines.append(f"### {node.node_id}")
        lines.append(node.not_automatable_reason or node.rationale)
        lines.append("")

    return Response(content="\n".join(lines), media_type="text/markdown")
