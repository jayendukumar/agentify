from fastapi import APIRouter, HTTPException, Query, Response, status

from app.db import repository
from app.schemas.blueprint import BlueprintGenerateRequest, BlueprintOverlay, BlueprintOverrideRequest

from .deps import DbDep, LLMDep, StoreDep

router = APIRouter(prefix="/api/processes/{process_id}/blueprint", tags=["blueprint"])


@router.post("/generate", response_model=BlueprintOverlay)
async def generate_blueprint(
    process_id: str, body: BlueprintGenerateRequest, db: DbDep, store: StoreDep, llm: LLMDep
) -> BlueprintOverlay:
    repository.get_process(db, process_id)  # 404s if missing
    version = store.get_version(process_id, body.version_id) if body.version_id else store.latest_version(process_id)
    if version is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Process has no finalized version yet -- finalize a baseline first")

    # TODO(Epic 7 / agentic-blueprint-evaluator skill): evaluate every node
    # in `version.xml` for automation feasibility and build the overlay.
    # `llm` and the finalized baseline are already resolved for that
    # implementation to use.
    raise HTTPException(
        status.HTTP_501_NOT_IMPLEMENTED,
        detail="Agentic blueprint evaluation is not implemented yet (Epic 7). This endpoint is scaffolded and wired to the LLM client and the finalized baseline version.",
    )


@router.get("", response_model=BlueprintOverlay)
def get_blueprint(process_id: str, db: DbDep, store: StoreDep) -> BlueprintOverlay:
    repository.get_process(db, process_id)  # 404s if missing
    blueprint = store.get_blueprint(process_id)
    if blueprint is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No blueprint yet -- call /generate first")
    return blueprint


@router.patch("/nodes/{node_id}", response_model=BlueprintOverlay)
def override_blueprint_node(
    process_id: str, node_id: str, body: BlueprintOverrideRequest, db: DbDep, store: StoreDep
) -> BlueprintOverlay:
    repository.get_process(db, process_id)  # 404s if missing
    blueprint = store.get_blueprint(process_id)
    if blueprint is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No blueprint yet -- call /generate first")

    for node in blueprint.nodes:
        if node.node_id == node_id:
            node.verdict = body.verdict
            node.overridden = True
            node.override_justification = body.justification
            return blueprint

    raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Blueprint has no node '{node_id}'")


@router.get("/export")
def export_blueprint(
    process_id: str, db: DbDep, store: StoreDep, format: str = Query("markdown", pattern="^(markdown)$")
) -> Response:
    process = repository.get_process(db, process_id)  # 404s if missing
    blueprint = store.get_blueprint(process_id)
    if blueprint is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No blueprint yet -- call /generate first")

    lines = [f"# Agentic Blueprint -- {process.name}", ""]
    automatable = [n for n in blueprint.nodes if n.verdict != "not_automatable"]
    not_automatable = [n for n in blueprint.nodes if n.verdict == "not_automatable"]

    lines.append(f"**{len(automatable)}/{len(blueprint.nodes)} steps automatable or partially automatable.**")
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
