"""DB-backed persistence for what Epic 2 owns (processes, documents, the
extracted process schema, embeddings) plus Epic 3's draft BPMN, Epic 5's
chat messages, Epic 6's finalized versions, Epic 7's blueprint overlay,
Epic 12's generated agent artifacts, Epic 14's digital twin scenarios/runs,
and Epic 15's publish history -- see app/db/models.py's module docstring.
"""

from __future__ import annotations

import secrets
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.generation import build_agent_definition, group_key_for, resolve_group
from app.config import get_settings
from app.ids import new_id, utcnow
from app.ingestion.embeddings import embed_texts
from app.ingestion.extractors import ExtractedBlock
from app.ingestion.merge import merge_process_schemas
from app.llm import LLMClient
from app.registry.base import RegistryConnector
from app.schemas.agents import AgentArtifact, AgentDefinition
from app.schemas.blueprint import BlueprintNodeResult, BlueprintOverlay
from app.schemas.chat import ChatMessageResult
from app.schemas.common import Actor, ProcessElement, ProcessFlow, ProcessSchema, SourceRef
from app.schemas.documents import IngestionStatus
from app.schemas.gap_analysis import GapFinding, GapFindingOption
from app.schemas.publish import AgentPublication, AgentPublishStatus
from app.schemas.twin import (
    InferredToolSchema,
    TwinBaseline,
    TwinBaselineComparison,
    TwinBaselineInput,
    TwinRun,
    TwinScenario,
    TwinScenarioCreate,
    TwinSummary,
)
from app.schemas.versions import VersionDetail
from app.store import NotFoundError
from app.twin.engine import run_scenario
from app.twin.schema_inference import infer_tool_schemas

from .models import (
    ActorModel,
    AgentArtifactModel,
    AgentPublicationModel,
    BlueprintOverlayModel,
    BPMNDraftModel,
    ChatMessageModel,
    DocumentEmbeddingModel,
    DocumentModel,
    GapFindingModel,
    ProcessElementModel,
    ProcessFlowModel,
    ProcessModel,
    ProcessSchemaChangeModel,
    SessionModel,
    SourceRefModel,
    TwinBaselineModel,
    TwinRunModel,
    TwinScenarioModel,
    TwinToolSchemaModel,
    UserModel,
    VersionModel,
)

# -- processes ------------------------------------------------------------


def create_process(session: Session, name: str) -> ProcessModel:
    process = ProcessModel(id=new_id("proc"), name=name)
    session.add(process)
    session.flush()
    return process


def list_processes(session: Session) -> list[ProcessModel]:
    return list(session.scalars(select(ProcessModel).order_by(ProcessModel.created_at)))


def get_process(session: Session, process_id: str) -> ProcessModel:
    process = session.get(ProcessModel, process_id)
    if process is None:
        raise NotFoundError("process", process_id)
    return process


def delete_process(session: Session, process_id: str) -> None:
    process = get_process(session, process_id)
    session.delete(process)
    session.flush()


# -- documents --------------------------------------------------------------


def add_document(
    session: Session, process_id: str, document_id: str, filename: str, content_type: str, size_bytes: int
) -> DocumentModel:
    get_process(session, process_id)  # 404s if missing
    document = DocumentModel(
        id=document_id,
        process_id=process_id,
        filename=filename,
        content_type=content_type,
        size_bytes=size_bytes,
        status="queued",
    )
    session.add(document)
    session.flush()
    return document


def get_document(session: Session, process_id: str, document_id: str) -> DocumentModel:
    document = session.get(DocumentModel, document_id)
    if document is None or document.process_id != process_id:
        raise NotFoundError("document", document_id)
    return document


def list_documents(session: Session, process_id: str) -> list[DocumentModel]:
    get_process(session, process_id)  # 404s if missing
    return list(
        session.scalars(
            select(DocumentModel).where(DocumentModel.process_id == process_id).order_by(DocumentModel.created_at)
        )
    )


def delete_document(session: Session, process_id: str, document_id: str) -> None:
    session.delete(get_document(session, process_id, document_id))
    session.flush()


def update_document_status(
    session: Session, process_id: str, document_id: str, status: IngestionStatus, error_message: str | None = None
) -> None:
    document = get_document(session, process_id, document_id)
    document.status = status
    document.error_message = error_message
    session.flush()


def update_document_validation(
    session: Session, process_id: str, document_id: str, confidence: int, message: str
) -> None:
    document = get_document(session, process_id, document_id)
    document.process_definition_confidence = confidence
    document.validation_message = message
    session.flush()


# -- process schema (US2.1, US2.5) -------------------------------------------


def get_process_schema(session: Session, process_id: str) -> ProcessSchema | None:
    process = get_process(session, process_id)
    if not process.actors and not process.elements:
        return None
    return _to_pydantic_schema(process)


def _to_pydantic_schema(process: ProcessModel) -> ProcessSchema:
    actors = [Actor(id=a.id, name=a.name, type=a.type) for a in process.actors]
    elements = [
        ProcessElement(
            id=e.id,
            type=e.type,
            label=e.label,
            actor_id=e.actor_id,
            inputs=list(e.inputs or []),
            outputs=list(e.outputs or []),
            systems_touched=list(e.systems_touched or []),
            source_refs=[
                SourceRef(document_id=r.document_id, location=r.location, excerpt=r.excerpt)
                for r in e.source_refs
            ],
            confidence=e.confidence,
        )
        for e in process.elements
    ]
    flows = [
        ProcessFlow(id=f.id, **{"from": f.from_element_id}, to=f.to_element_id, condition=f.condition)
        for f in process.flows
    ]
    return ProcessSchema(process_name=process.name, actors=actors, elements=elements, flows=flows)


def _replace_schema_rows(session: Session, process_id: str, schema: ProcessSchema) -> None:
    """Read-modify-write: the merge algorithm (app/ingestion/merge.py) runs
    against plain ProcessSchema objects (already tested independently of
    any storage backend); this just persists its output by fully replacing
    the process's schema rows rather than reimplementing the merge in SQL.
    Adequate at this scale (one process's schema is small)."""
    session.query(ProcessFlowModel).filter_by(process_id=process_id).delete()
    element_ids = select(ProcessElementModel.id).where(ProcessElementModel.process_id == process_id)
    session.query(SourceRefModel).filter(SourceRefModel.element_id.in_(element_ids)).delete(
        synchronize_session=False
    )
    session.query(ProcessElementModel).filter_by(process_id=process_id).delete()
    session.query(ActorModel).filter_by(process_id=process_id).delete()
    session.flush()

    for actor in schema.actors:
        session.add(ActorModel(id=actor.id, process_id=process_id, name=actor.name, type=actor.type))
    session.flush()  # actors must exist before elements reference them (FK)

    for element in schema.elements:
        session.add(
            ProcessElementModel(
                id=element.id,
                process_id=process_id,
                type=element.type,
                label=element.label,
                actor_id=element.actor_id,
                inputs=list(element.inputs),
                outputs=list(element.outputs),
                systems_touched=list(element.systems_touched),
                confidence=element.confidence,
            )
        )
    session.flush()  # elements must exist before source_refs/flows reference them (FK)

    for element in schema.elements:
        for ref in element.source_refs:
            session.add(
                SourceRefModel(
                    element_id=element.id, document_id=ref.document_id, location=ref.location, excerpt=ref.excerpt
                )
            )

    for flow in schema.flows:
        session.add(
            ProcessFlowModel(
                id=flow.id,
                process_id=process_id,
                from_element_id=flow.from_,
                to_element_id=flow.to,
                condition=flow.condition,
            )
        )

    session.flush()


def merge_process_schema(
    session: Session, process_id: str, new_schema: ProcessSchema, document_id: str | None = None
) -> None:
    process = get_process(session, process_id)
    existing_schema = _to_pydantic_schema(process) if (process.actors or process.elements) else None

    if existing_schema is None:
        merged = new_schema
        summary = f"Initial extraction: {len(new_schema.elements)} step(s), {len(new_schema.actors)} actor(s)."
    else:
        before = len(existing_schema.elements)
        merged = merge_process_schemas(existing_schema, new_schema)
        summary = f"Merged {len(new_schema.elements)} new step(s) from this document; total steps {before} -> {len(merged.elements)}."

    _replace_schema_rows(session, process_id, merged)
    session.add(ProcessSchemaChangeModel(process_id=process_id, document_id=document_id, summary=summary))
    process.updated_at = utcnow()
    session.flush()


def set_process_schema(session: Session, process_id: str, schema: ProcessSchema) -> None:
    """Epic 5: persist a chat-diff-mutated schema directly, bypassing
    merge_process_schema's re-merge algorithm -- a chat edit is a direct
    replacement of specific elements/flows (already resolved by
    app/bpmn/chat_ops.apply_diagram_diff), not a merge of freshly-extracted
    document content."""
    get_process(session, process_id)  # 404s if missing
    _replace_schema_rows(session, process_id, schema)
    process = get_process(session, process_id)
    process.updated_at = utcnow()
    session.flush()


def list_schema_changes(session: Session, process_id: str) -> list[ProcessSchemaChangeModel]:
    get_process(session, process_id)  # 404s if missing
    return list(
        session.scalars(
            select(ProcessSchemaChangeModel)
            .where(ProcessSchemaChangeModel.process_id == process_id)
            .order_by(ProcessSchemaChangeModel.created_at)
        )
    )


# -- embeddings (US2.3) -------------------------------------------------------


def add_document_embeddings(session: Session, document_id: str, blocks: list[ExtractedBlock]) -> None:
    if not blocks:
        return
    vectors = embed_texts([block.content for block in blocks])
    for block, vector in zip(blocks, vectors):
        session.add(
            DocumentEmbeddingModel(document_id=document_id, location=block.location, content=block.content, embedding=vector)
        )
    session.flush()


# -- draft BPMN (US3.3) -------------------------------------------------------


def get_draft_bpmn(session: Session, process_id: str) -> BPMNDraftModel | None:
    get_process(session, process_id)  # 404s if missing
    return session.get(BPMNDraftModel, process_id)


def set_draft_bpmn(
    session: Session, process_id: str, xml: str, low_confidence_element_ids: list[str]
) -> BPMNDraftModel:
    get_process(session, process_id)  # 404s if missing
    draft = session.get(BPMNDraftModel, process_id)
    if draft is None:
        draft = BPMNDraftModel(process_id=process_id, xml=xml, low_confidence_element_ids=low_confidence_element_ids)
        session.add(draft)
    else:
        draft.xml = xml
        draft.low_confidence_element_ids = low_confidence_element_ids
        draft.generated_at = utcnow()
    session.flush()
    return draft


# -- chat messages (Epic 5, US5.5 audit trail) --------------------------------


def _to_pydantic_chat_message(message: ChatMessageModel) -> ChatMessageResult:
    return ChatMessageResult(
        id=message.id,
        process_id=message.process_id,
        request_text=message.request_text,
        selected_element_id=message.selected_element_id,
        kind=message.kind,
        reply_text=message.reply_text,
        proposed_diff=message.proposed_diff,
        needs_confirmation=message.needs_confirmation,
        applied=message.applied,
        declined=message.declined,
        created_at=message.created_at,
        decided_at=message.decided_at,
    )


def add_chat_message(
    session: Session,
    process_id: str,
    *,
    request_text: str,
    selected_element_id: str | None,
    kind: str,
    reply_text: str,
    proposed_diff: dict | None,
    needs_confirmation: bool,
) -> ChatMessageResult:
    get_process(session, process_id)  # 404s if missing
    message = ChatMessageModel(
        id=new_id("msg"),
        process_id=process_id,
        request_text=request_text,
        selected_element_id=selected_element_id,
        kind=kind,
        reply_text=reply_text,
        proposed_diff=proposed_diff,
        needs_confirmation=needs_confirmation,
    )
    session.add(message)
    session.flush()
    return _to_pydantic_chat_message(message)


def list_chat_messages(session: Session, process_id: str) -> list[ChatMessageResult]:
    get_process(session, process_id)  # 404s if missing
    messages = session.scalars(
        select(ChatMessageModel).where(ChatMessageModel.process_id == process_id).order_by(ChatMessageModel.created_at)
    )
    return [_to_pydantic_chat_message(m) for m in messages]


def get_chat_message(session: Session, process_id: str, message_id: str) -> ChatMessageModel:
    message = session.get(ChatMessageModel, message_id)
    if message is None or message.process_id != process_id:
        raise NotFoundError("chat message", message_id)
    return message


def mark_chat_message_decided(session: Session, message: ChatMessageModel, *, applied: bool) -> ChatMessageResult:
    message.applied = applied
    message.declined = not applied
    message.decided_at = utcnow()
    session.flush()
    return _to_pydantic_chat_message(message)


# -- versions (Epic 6, US6.1/US6.2) -------------------------------------------


def _resolve_user_name(session: Session, user_id: str | None) -> str | None:
    """Epic 9/10, US9.9: resolves an audit-trail user id (VersionModel/
    GapFindingModel's created_by/decided_by, or a blueprint node's
    overridden_by) to a display name for API responses -- a per-lookup
    query, not an eager join, since this project's data volumes don't
    warrant the complexity (same "smallest number of moving parts"
    default as everywhere else in this codebase)."""
    if user_id is None:
        return None
    user = session.get(UserModel, user_id)
    return user.name if user is not None else None


def _to_pydantic_version(session: Session, version: VersionModel) -> VersionDetail:
    return VersionDetail(
        id=version.id,
        process_id=version.process_id,
        label=version.label,
        created_at=version.created_at,
        xml=version.xml,
        created_by=version.created_by,
        created_by_name=_resolve_user_name(session, version.created_by),
    )


def add_version(
    session: Session, process_id: str, xml: str, label: str | None = None, created_by: str | None = None
) -> VersionDetail:
    get_process(session, process_id)  # 404s if missing
    version = VersionModel(id=new_id("ver"), process_id=process_id, label=label, xml=xml, created_by=created_by)
    session.add(version)
    session.flush()
    return _to_pydantic_version(session, version)


def list_versions(session: Session, process_id: str) -> list[VersionDetail]:
    get_process(session, process_id)  # 404s if missing
    versions = session.scalars(
        select(VersionModel).where(VersionModel.process_id == process_id).order_by(VersionModel.created_at)
    )
    return [_to_pydantic_version(session, v) for v in versions]


def get_version(session: Session, process_id: str, version_id: str) -> VersionDetail:
    version = session.get(VersionModel, version_id)
    if version is None or version.process_id != process_id:
        raise NotFoundError("version", version_id)
    return _to_pydantic_version(session, version)


def get_latest_version(session: Session, process_id: str) -> VersionDetail | None:
    versions = list_versions(session, process_id)
    return versions[-1] if versions else None


# -- blueprint overlay (Epic 7, US7.6/US7.7) ----------------------------------


def _to_pydantic_blueprint_overlay(session: Session, overlay: BlueprintOverlayModel) -> BlueprintOverlay:
    nodes = []
    for node in overlay.nodes:
        result = BlueprintNodeResult.model_validate(node)
        result.overridden_by_name = _resolve_user_name(session, result.overridden_by)
        nodes.append(result)
    return BlueprintOverlay(
        process_id=overlay.process_id,
        baseline_version_id=overlay.baseline_version_id,
        nodes=nodes,
        generated_at=overlay.generated_at,
    )


def set_blueprint_overlay(
    session: Session, process_id: str, baseline_version_id: str, nodes: list[BlueprintNodeResult]
) -> BlueprintOverlay:
    get_process(session, process_id)  # 404s if missing
    node_dicts = [node.model_dump() for node in nodes]
    overlay = session.get(BlueprintOverlayModel, process_id)
    if overlay is None:
        overlay = BlueprintOverlayModel(process_id=process_id, baseline_version_id=baseline_version_id, nodes=node_dicts)
        session.add(overlay)
    else:
        overlay.baseline_version_id = baseline_version_id
        overlay.nodes = node_dicts
        overlay.generated_at = utcnow()
    session.flush()
    return _to_pydantic_blueprint_overlay(session, overlay)


def get_blueprint_overlay(session: Session, process_id: str) -> BlueprintOverlay | None:
    get_process(session, process_id)  # 404s if missing
    overlay = session.get(BlueprintOverlayModel, process_id)
    return _to_pydantic_blueprint_overlay(session, overlay) if overlay is not None else None


def update_blueprint_node(
    session: Session,
    process_id: str,
    node_id: str,
    *,
    verdict: str,
    justification: str,
    overridden_by: str | None = None,
) -> BlueprintOverlay:
    get_process(session, process_id)  # 404s if missing
    overlay = session.get(BlueprintOverlayModel, process_id)
    if overlay is None:
        raise NotFoundError("blueprint", process_id)

    # Reassign a NEW list (not mutate overlay.nodes in place) so SQLAlchemy's
    # plain JSON column change-tracking (identity-based) actually notices
    # the update and flushes it -- mutating overlay.nodes[i] in place would
    # silently not persist.
    updated_nodes = [dict(node) for node in overlay.nodes]
    match = next((node for node in updated_nodes if node.get("node_id") == node_id), None)
    if match is None:
        raise NotFoundError("blueprint node", node_id)
    match["verdict"] = verdict
    match["overridden"] = True
    match["override_justification"] = justification
    match["overridden_by"] = overridden_by
    overlay.nodes = updated_nodes
    session.flush()
    return _to_pydantic_blueprint_overlay(session, overlay)


# -- agent artifacts (Epic 12) ------------------------------------------------


def _is_agent_artifact_stale(overlay: BlueprintOverlayModel | None, artifact: AgentArtifactModel) -> bool:
    """US12.4: computed on every read rather than a stored flag -- see
    AgentArtifactModel's docstring for why. Stale if the blueprint was
    regenerated against a different baseline, or if any node in this
    artifact's group no longer matches the exact blueprint entry it was
    generated from (an override, or a fresh per-node result from a
    same-baseline regenerate)."""
    if overlay is None or overlay.baseline_version_id != artifact.source_baseline_version_id:
        return True
    current_by_id = {node.get("node_id"): node for node in overlay.nodes}
    for snapshot_node in artifact.source_node_snapshot:
        if current_by_id.get(snapshot_node.get("node_id")) != snapshot_node:
            return True
    return False


def _to_pydantic_agent_artifact(session: Session, artifact: AgentArtifactModel, *, is_stale: bool) -> AgentArtifact:
    return AgentArtifact(
        id=artifact.id,
        process_id=artifact.process_id,
        group_key=artifact.group_key,
        node_ids=artifact.node_ids,
        primary_node_id=artifact.primary_node_id,
        status="stale" if is_stale else "generated",
        definition=AgentDefinition.model_validate(artifact.definition),
        baseline_version_id=artifact.source_baseline_version_id,
        generated_at=artifact.generated_at,
        generated_by=artifact.generated_by,
        generated_by_name=_resolve_user_name(session, artifact.generated_by),
    )


def generate_agent_artifact(
    session: Session, process_id: str, node_id: str, *, generated_by: str | None = None
) -> AgentArtifact:
    get_process(session, process_id)  # 404s if missing
    overlay_model = session.get(BlueprintOverlayModel, process_id)
    if overlay_model is None:
        raise NotFoundError("blueprint", process_id)

    overlay = _to_pydantic_blueprint_overlay(session, overlay_model)
    group = resolve_group(overlay.nodes, node_id)
    node_ids = sorted(node.node_id for node in group)
    group_key = group_key_for(node_ids)
    definition = build_agent_definition(group, primary_node_id=node_id)
    snapshot = [dict(node) for node in overlay_model.nodes if node.get("node_id") in node_ids]

    artifact = session.scalar(
        select(AgentArtifactModel).where(
            AgentArtifactModel.process_id == process_id, AgentArtifactModel.group_key == group_key
        )
    )
    if artifact is None:
        artifact = AgentArtifactModel(
            id=new_id("agent"),
            process_id=process_id,
            group_key=group_key,
            node_ids=node_ids,
            primary_node_id=node_id,
            definition=definition.model_dump(),
            source_baseline_version_id=overlay.baseline_version_id,
            source_node_snapshot=snapshot,
            generated_by=generated_by,
        )
        session.add(artifact)
    else:
        # US12.4: regenerating updates the same row in place (matches
        # BlueprintOverlayModel's own "replace wholesale" convention) --
        # node_ids/group_key never change for an existing row since the
        # group_key IS derived from node_ids.
        artifact.primary_node_id = node_id
        artifact.definition = definition.model_dump()
        artifact.source_baseline_version_id = overlay.baseline_version_id
        artifact.source_node_snapshot = snapshot
        artifact.generated_by = generated_by
        artifact.generated_at = utcnow()

    session.flush()
    return _to_pydantic_agent_artifact(session, artifact, is_stale=False)


def list_agent_artifacts(session: Session, process_id: str) -> list[AgentArtifact]:
    get_process(session, process_id)  # 404s if missing
    overlay_model = session.get(BlueprintOverlayModel, process_id)
    artifacts = session.scalars(
        select(AgentArtifactModel)
        .where(AgentArtifactModel.process_id == process_id)
        .order_by(AgentArtifactModel.generated_at)
    )
    return [
        _to_pydantic_agent_artifact(session, artifact, is_stale=_is_agent_artifact_stale(overlay_model, artifact))
        for artifact in artifacts
    ]


def get_agent_artifact(session: Session, process_id: str, artifact_id: str) -> AgentArtifactModel:
    artifact = session.get(AgentArtifactModel, artifact_id)
    if artifact is None or artifact.process_id != process_id:
        raise NotFoundError("agent artifact", artifact_id)
    return artifact


# -- digital twin (Epic 14, core slice: US14.1-14.4) ---------------------------


def _to_pydantic_twin_scenario(session: Session, scenario: TwinScenarioModel) -> TwinScenario:
    return TwinScenario(
        id=scenario.id,
        agent_artifact_id=scenario.agent_artifact_id,
        name=scenario.name,
        inputs=scenario.inputs,
        system_stubs=scenario.system_stubs,
        human_checkpoint_config=scenario.human_checkpoint_config,
        expected_steps=scenario.expected_steps,
        expected_outputs=scenario.expected_outputs,
        created_at=scenario.created_at,
        created_by=scenario.created_by,
        created_by_name=_resolve_user_name(session, scenario.created_by),
    )


def _to_pydantic_twin_run(session: Session, run: TwinRunModel) -> TwinRun:
    return TwinRun(
        id=run.id,
        scenario_id=run.scenario_id,
        agent_artifact_id=run.agent_artifact_id,
        status=run.status,
        trace=run.trace,
        final_output=run.final_output,
        deviations=run.deviations,
        total_cost_usd=run.total_cost_usd,
        total_tokens=run.total_tokens,
        turns_used=run.turns_used,
        started_at=run.started_at,
        completed_at=run.completed_at,
        run_by=run.run_by,
        run_by_name=_resolve_user_name(session, run.run_by),
    )


def create_twin_scenario(
    session: Session, process_id: str, artifact_id: str, data: TwinScenarioCreate, *, created_by: str | None = None
) -> TwinScenario:
    get_process(session, process_id)  # 404s if missing
    artifact = get_agent_artifact(session, process_id, artifact_id)  # 404s if missing/wrong process
    scenario = TwinScenarioModel(
        id=new_id("twinsc"),
        agent_artifact_id=artifact.id,
        name=data.name,
        inputs=data.inputs,
        system_stubs={name: stub.model_dump() for name, stub in data.system_stubs.items()},
        human_checkpoint_config=data.human_checkpoint_config.model_dump(),
        expected_steps=[step.model_dump() for step in data.expected_steps],
        expected_outputs=data.expected_outputs,
        created_by=created_by,
    )
    session.add(scenario)
    session.flush()
    return _to_pydantic_twin_scenario(session, scenario)


def list_twin_scenarios(session: Session, process_id: str, artifact_id: str) -> list[TwinScenario]:
    get_process(session, process_id)  # 404s if missing
    get_agent_artifact(session, process_id, artifact_id)  # 404s if missing/wrong process
    scenarios = session.scalars(
        select(TwinScenarioModel)
        .where(TwinScenarioModel.agent_artifact_id == artifact_id)
        .order_by(TwinScenarioModel.created_at)
    )
    return [_to_pydantic_twin_scenario(session, scenario) for scenario in scenarios]


def get_twin_scenario(session: Session, process_id: str, scenario_id: str) -> TwinScenarioModel:
    scenario = session.get(TwinScenarioModel, scenario_id)
    if scenario is None:
        raise NotFoundError("scenario", scenario_id)
    get_agent_artifact(session, process_id, scenario.agent_artifact_id)  # verifies ownership, 404s otherwise
    return scenario


def delete_twin_scenario(session: Session, process_id: str, scenario_id: str) -> None:
    scenario = get_twin_scenario(session, process_id, scenario_id)
    session.delete(scenario)
    session.flush()


async def _get_or_infer_tool_schemas(
    llm: LLMClient, session: Session, artifact: AgentArtifactModel
) -> dict[str, InferredToolSchema]:
    """Caches the LLM-inferred tool schema per artifact (TwinToolSchemaModel,
    one row per artifact, replaced wholesale) -- regenerated only when the
    artifact's own `tools_systems_needed` set has changed since the schema
    was last inferred, same "stale on drift" spirit as US12.4, computed by
    comparing the cached schema's keys against the artifact's current
    definition rather than a separate stored flag."""
    definition = AgentDefinition.model_validate(artifact.definition)
    cached = session.get(TwinToolSchemaModel, artifact.id)
    if cached is not None and set(cached.schemas) == set(definition.tools_systems_needed):
        return {name: InferredToolSchema.model_validate(value) for name, value in cached.schemas.items()}

    schemas = await infer_tool_schemas(llm, definition)
    dumped = {name: schema.model_dump() for name, schema in schemas.items()}
    if cached is None:
        session.add(TwinToolSchemaModel(agent_artifact_id=artifact.id, schemas=dumped))
    else:
        cached.schemas = dumped
        cached.generated_at = utcnow()
    session.flush()
    return schemas


async def execute_twin_run(
    llm: LLMClient, session: Session, process_id: str, scenario_id: str, *, run_by: str | None = None
) -> TwinRun:
    scenario_model = get_twin_scenario(session, process_id, scenario_id)
    artifact = get_agent_artifact(session, process_id, scenario_model.agent_artifact_id)
    definition = AgentDefinition.model_validate(artifact.definition)
    tool_schemas = await _get_or_infer_tool_schemas(llm, session, artifact)
    scenario = _to_pydantic_twin_scenario(session, scenario_model)

    started_at = utcnow()
    outcome = await run_scenario(
        llm,
        artifact=definition,
        tool_schemas=tool_schemas,
        scenario=scenario,
        max_turns=get_settings().twin_max_loop_turns,
    )
    completed_at = utcnow()

    run = TwinRunModel(
        id=new_id("twinrun"),
        scenario_id=scenario_model.id,
        agent_artifact_id=artifact.id,
        status=outcome.status,
        trace=[step.model_dump() for step in outcome.trace],
        final_output=outcome.final_output,
        deviations=[deviation.model_dump() for deviation in outcome.deviations],
        total_cost_usd=outcome.total_cost_usd,
        total_tokens=outcome.total_tokens,
        turns_used=outcome.turns_used,
        started_at=started_at,
        completed_at=completed_at,
        run_by=run_by,
    )
    session.add(run)
    session.flush()
    return _to_pydantic_twin_run(session, run)


def list_twin_runs(session: Session, process_id: str, artifact_id: str) -> list[TwinRun]:
    get_process(session, process_id)  # 404s if missing
    get_agent_artifact(session, process_id, artifact_id)  # 404s if missing/wrong process
    runs = session.scalars(
        select(TwinRunModel).where(TwinRunModel.agent_artifact_id == artifact_id).order_by(TwinRunModel.started_at.desc())
    )
    return [_to_pydantic_twin_run(session, run) for run in runs]


def _to_pydantic_twin_baseline(session: Session, baseline: TwinBaselineModel) -> TwinBaseline:
    return TwinBaseline(
        agent_artifact_id=baseline.agent_artifact_id,
        typical_time_seconds=baseline.typical_time_seconds,
        error_rate=baseline.error_rate,
        notes=baseline.notes,
        recorded_at=baseline.recorded_at,
        recorded_by=baseline.recorded_by,
        recorded_by_name=_resolve_user_name(session, baseline.recorded_by),
    )


def set_twin_baseline(
    session: Session, process_id: str, artifact_id: str, data: TwinBaselineInput, *, recorded_by: str | None = None
) -> TwinBaseline:
    """US14.5: replaced wholesale on every save (same convention as
    TwinToolSchemaModel) -- there's no history of past baselines, just the
    architect's current best manual estimate."""
    get_process(session, process_id)  # 404s if missing
    get_agent_artifact(session, process_id, artifact_id)  # 404s if missing/wrong process
    baseline = session.get(TwinBaselineModel, artifact_id)
    if baseline is None:
        baseline = TwinBaselineModel(
            agent_artifact_id=artifact_id,
            typical_time_seconds=data.typical_time_seconds,
            error_rate=data.error_rate,
            notes=data.notes,
            recorded_by=recorded_by,
        )
        session.add(baseline)
    else:
        baseline.typical_time_seconds = data.typical_time_seconds
        baseline.error_rate = data.error_rate
        baseline.notes = data.notes
        baseline.recorded_by = recorded_by
        baseline.recorded_at = utcnow()
    session.flush()
    return _to_pydantic_twin_baseline(session, baseline)


def delete_twin_baseline(session: Session, process_id: str, artifact_id: str) -> None:
    get_process(session, process_id)  # 404s if missing
    get_agent_artifact(session, process_id, artifact_id)  # 404s if missing/wrong process
    baseline = session.get(TwinBaselineModel, artifact_id)
    if baseline is not None:
        session.delete(baseline)
        session.flush()


def get_twin_summary(session: Session, process_id: str, artifact_id: str) -> TwinSummary:
    get_process(session, process_id)  # 404s if missing
    get_agent_artifact(session, process_id, artifact_id)  # 404s if missing/wrong process
    runs = list(session.scalars(select(TwinRunModel).where(TwinRunModel.agent_artifact_id == artifact_id)))
    baseline_model = session.get(TwinBaselineModel, artifact_id)
    baseline = _to_pydantic_twin_baseline(session, baseline_model) if baseline_model is not None else None

    run_count = len(runs)
    if run_count == 0:
        return TwinSummary(agent_artifact_id=artifact_id, run_count=0, baseline=baseline)

    pass_rate = sum(1 for run in runs if run.status == "passed") / run_count
    # A single run with an unpriced model (see estimate_cost_usd) makes the
    # whole aggregate cost unknown rather than silently under-reporting it --
    # same "don't show a wrong partial number" principle as
    # UsageTracker.OperationTotals.cost_estimate_incomplete.
    cost_incomplete = any(run.total_cost_usd is None for run in runs)
    total_cost = None if cost_incomplete else sum(run.total_cost_usd or 0.0 for run in runs)
    average_cost = None if cost_incomplete else total_cost / run_count

    reason_counts: dict[str, int] = {}
    for run in runs:
        for deviation in run.deviations:
            reason = deviation.get("reason", "")
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
    common_failure_reasons = sorted(reason_counts, key=lambda reason: reason_counts[reason], reverse=True)[:5]

    average_duration = sum((run.completed_at - run.started_at).total_seconds() for run in runs) / run_count
    comparison = None
    if baseline is not None:
        # US14.5: only compare a side that actually has both values -- a
        # baseline that only recorded error_rate (not typical_time_seconds)
        # gets an error_rate_delta and a None time_delta_seconds, not a
        # fabricated zero.
        comparison = TwinBaselineComparison(
            average_run_duration_seconds=average_duration,
            time_delta_seconds=(
                average_duration - baseline.typical_time_seconds if baseline.typical_time_seconds is not None else None
            ),
            error_rate_delta=((1 - pass_rate) - baseline.error_rate) if baseline.error_rate is not None else None,
        )

    return TwinSummary(
        agent_artifact_id=artifact_id,
        run_count=run_count,
        pass_rate=pass_rate,
        total_cost_usd=total_cost,
        average_cost_usd=average_cost,
        common_failure_reasons=common_failure_reasons,
        baseline=baseline,
        baseline_comparison=comparison,
    )


# -- publishing (Epic 15) ------------------------------------------------------


def _to_pydantic_agent_publication(
    session: Session, publication: AgentPublicationModel, *, version: int
) -> AgentPublication:
    return AgentPublication(
        id=publication.id,
        agent_artifact_id=publication.agent_artifact_id,
        registry_name=publication.registry_name,
        registry_entry_id=publication.registry_entry_id,
        version=version,
        status=publication.status,
        published_at=publication.published_at,
        published_by=publication.published_by,
        published_by_name=_resolve_user_name(session, publication.published_by),
        deployed_at=publication.deployed_at,
        deployed_by=publication.deployed_by,
        deployed_by_name=_resolve_user_name(session, publication.deployed_by),
    )


def _list_agent_publication_models(session: Session, artifact_id: str) -> list[AgentPublicationModel]:
    """Oldest first -- this order IS the version numbering (US15.4's "new
    version" is just "next row"), so callers rank off this list's index
    rather than a stored version column (see AgentPublicationModel's
    docstring)."""
    return list(
        session.scalars(
            select(AgentPublicationModel)
            .where(AgentPublicationModel.agent_artifact_id == artifact_id)
            .order_by(AgentPublicationModel.published_at)
        )
    )


def publish_agent_artifact(
    connector: RegistryConnector,
    session: Session,
    process_id: str,
    artifact_id: str,
    *,
    published_by: str | None = None,
) -> AgentPublication:
    """US15.1: pushes the artifact's current definition through `connector`
    (the same RegistryConnector.push app/api/registries.py's plain push
    endpoint uses) and only records a new AgentPublicationModel row once
    that push actually succeeds -- a RegistryConnectorError (unreachable/
    auth, US13.6) propagates straight to the caller with no row written, so
    a failed publish never changes what's tracked here (US15.5). Tags with
    the node's step_type, same as the artifact's manual-push predecessor
    (frontend's old RegistryPushAction) used to pass by hand."""
    get_process(session, process_id)  # 404s if missing
    artifact = get_agent_artifact(session, process_id, artifact_id)  # 404s if missing/wrong process
    definition = AgentDefinition.model_validate(artifact.definition)

    overlay_model = session.get(BlueprintOverlayModel, process_id)
    primary_node = None
    if overlay_model is not None:
        primary_node = next(
            (node for node in overlay_model.nodes if node.get("node_id") == artifact.primary_node_id), None
        )
    tags = [primary_node["step_type"]] if primary_node else []

    entry = connector.push(
        agent_name=definition.name,
        definition=artifact.definition,
        tags=tags,
        source_process_id=process_id,
        source_node_ids=artifact.node_ids,
        pushed_by=published_by,
    )

    publication = AgentPublicationModel(
        id=new_id("pub"),
        agent_artifact_id=artifact.id,
        registry_name=entry.registry_name,
        registry_entry_id=entry.id,
        source_artifact_generated_at=artifact.generated_at,
        published_by=published_by,
    )
    session.add(publication)
    session.flush()

    version = len(_list_agent_publication_models(session, artifact_id))  # the row just added ranks last
    return _to_pydantic_agent_publication(session, publication, version=version)


def get_agent_publish_status(session: Session, process_id: str, artifact_id: str) -> AgentPublishStatus:
    get_process(session, process_id)  # 404s if missing
    artifact = get_agent_artifact(session, process_id, artifact_id)  # 404s if missing/wrong process
    models = _list_agent_publication_models(session, artifact_id)

    if not models:
        return AgentPublishStatus(agent_artifact_id=artifact_id, lifecycle_status="generated", needs_republish=False)

    publications = [
        _to_pydantic_agent_publication(session, model, version=i + 1) for i, model in enumerate(models)
    ]
    latest = publications[-1]
    # US15.2's "deployed" is a fact about how far this agent has ever
    # gotten, not a per-version flag -- see AgentPublicationModel's
    # docstring on why this stays "deployed" even once a newer,
    # not-yet-deployed version is published on top of it.
    lifecycle_status = "deployed" if any(p.status == "deployed" for p in publications) else "published"
    needs_republish = models[-1].source_artifact_generated_at != artifact.generated_at

    return AgentPublishStatus(
        agent_artifact_id=artifact_id,
        lifecycle_status=lifecycle_status,
        needs_republish=needs_republish,
        latest_publication=latest,
        publications=list(reversed(publications)),  # newest first, matching list_twin_runs
    )


def mark_agent_publication_deployed(
    session: Session, process_id: str, artifact_id: str, publication_id: str, *, deployed_by: str | None = None
) -> AgentPublication:
    """US15.2's Notes: for the local connector there's no registry API to
    sync deployment state from, so this is the manual path -- an
    Automation Architect telling the system they deployed it elsewhere."""
    get_process(session, process_id)  # 404s if missing
    get_agent_artifact(session, process_id, artifact_id)  # 404s if missing/wrong process
    publication = session.get(AgentPublicationModel, publication_id)
    if publication is None or publication.agent_artifact_id != artifact_id:
        raise NotFoundError("agent publication", publication_id)

    publication.status = "deployed"
    publication.deployed_at = utcnow()
    publication.deployed_by = deployed_by
    session.flush()

    models = _list_agent_publication_models(session, artifact_id)
    version = next(i + 1 for i, model in enumerate(models) if model.id == publication.id)
    return _to_pydantic_agent_publication(session, publication, version=version)


# -- gap findings (Epic 11) ----------------------------------------------------


def _to_pydantic_gap_finding(session: Session, finding: GapFindingModel) -> GapFinding:
    return GapFinding(
        id=finding.id,
        process_id=finding.process_id,
        kind=finding.kind,
        question=finding.question,
        target_element_ids=finding.target_element_ids,
        options=[GapFindingOption.model_validate(option) for option in finding.options],
        status=finding.status,
        chosen_option_label=finding.chosen_option_label,
        created_at=finding.created_at,
        decided_at=finding.decided_at,
        decided_by=finding.decided_by,
        decided_by_name=_resolve_user_name(session, finding.decided_by),
    )


def add_gap_finding(
    session: Session,
    process_id: str,
    *,
    kind: str,
    question: str,
    target_element_ids: list[str],
    options: list[GapFindingOption],
) -> GapFinding:
    get_process(session, process_id)  # 404s if missing
    finding = GapFindingModel(
        id=new_id("gap"),
        process_id=process_id,
        kind=kind,
        question=question,
        target_element_ids=target_element_ids,
        options=[option.model_dump() for option in options],
    )
    session.add(finding)
    session.flush()
    return _to_pydantic_gap_finding(session, finding)


def list_gap_findings(session: Session, process_id: str, status: str | None = None) -> list[GapFinding]:
    get_process(session, process_id)  # 404s if missing
    stmt = select(GapFindingModel).where(GapFindingModel.process_id == process_id)
    if status is not None:
        stmt = stmt.where(GapFindingModel.status == status)
    stmt = stmt.order_by(GapFindingModel.created_at)
    return [_to_pydantic_gap_finding(session, f) for f in session.scalars(stmt)]


def get_gap_finding(session: Session, process_id: str, finding_id: str) -> GapFindingModel:
    finding = session.get(GapFindingModel, finding_id)
    if finding is None or finding.process_id != process_id:
        raise NotFoundError("gap finding", finding_id)
    return finding


def mark_gap_finding_resolved(
    session: Session, finding: GapFindingModel, *, option_label: str, decided_by: str | None = None
) -> GapFinding:
    finding.status = "resolved"
    finding.chosen_option_label = option_label
    finding.decided_at = utcnow()
    finding.decided_by = decided_by
    session.flush()
    return _to_pydantic_gap_finding(session, finding)


def mark_gap_finding_dismissed(
    session: Session, finding: GapFindingModel, *, decided_by: str | None = None
) -> GapFinding:
    finding.status = "dismissed"
    finding.decided_at = utcnow()
    finding.decided_by = decided_by
    session.flush()
    return _to_pydantic_gap_finding(session, finding)


def mark_gap_analysis_completed(session: Session, process_id: str) -> None:
    process = get_process(session, process_id)  # 404s if missing
    process.gap_analysis_completed_at = utcnow()
    session.flush()


# -- auth (Epic 9/10, US9.9/US10.4) --------------------------------------------


def get_or_create_user(session: Session, *, name: str, role: str) -> UserModel:
    """Create or reuse a local-team user, applying the selected login role."""
    existing = session.scalar(select(UserModel).where(UserModel.name == name))
    if existing is not None:
        existing.role = role
        session.flush()
        return existing
    user = UserModel(id=new_id("user"), name=name, role=role)
    session.add(user)
    session.flush()
    return user


def create_session(session: Session, user_id: str, *, ttl_days: int = 30) -> SessionModel:
    # SessionModel.expires_at is a plain (naive) DateTime column, same as
    # every other timestamp column in this project (Postgres/psycopg
    # returns them naive on read) -- utcnow() is tz-aware, so its tzinfo
    # is stripped here rather than mixing naive/aware datetimes, which
    # raises a TypeError the moment expires_at is compared after a
    # round-trip through the DB (get_user_for_session below).
    expires_at = (utcnow() + timedelta(days=ttl_days)).replace(tzinfo=None)
    record = SessionModel(id=secrets.token_urlsafe(32), user_id=user_id, expires_at=expires_at)
    session.add(record)
    session.flush()
    return record


def get_user_for_session(session: Session, session_id: str) -> UserModel | None:
    record = session.get(SessionModel, session_id)
    if record is None or record.expires_at < utcnow().replace(tzinfo=None):
        return None
    return session.get(UserModel, record.user_id)


def delete_session(session: Session, session_id: str) -> None:
    record = session.get(SessionModel, session_id)
    if record is not None:
        session.delete(record)
        session.flush()
