"""DB-backed persistence for what Epic 2 owns (processes, documents, the
extracted process schema, embeddings) plus Epic 3's draft BPMN, Epic 5's
chat messages, Epic 6's finalized versions, Epic 7's blueprint overlay,
Epic 12's generated agent artifacts, Epic 14's digital twin scenarios/runs,
and Epic 15's publish history -- see app/db/models.py's module docstring.
"""

from __future__ import annotations

import contextlib
import secrets
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.generation import build_agent_definition, group_key_for, resolve_group
from app.agents.validation import compute_checklist
from app.bpmn.nodes import FlowNodeInfo, extract_flow_nodes
from app.catalog.service import apply_catalog_to_definition, spec_tool_schemas
from app.config import Settings, get_settings
from app.datasets.runtime import apply_dataset_row
from app.datasets.service import row_at
from app.context.consumers import apply_agent_context
from app.context.resolver import is_pack_stale
from app.ids import new_id, utcnow
from app.ingestion.embeddings import embed_texts
from app.ingestion.extractors import ExtractedBlock
from app.ingestion.merge import merge_process_schemas
from app.llm import LLMClient
from app.llm.routing import current_route as current_llm_route
from app.llm.routing import route_for_scope
from app.registry.base import RegistryConnector
from app.schemas.agents import AgentArtifact, AgentDefinition, AgentGovernanceUpdate, RuntimeGuardrails
from app.schemas.blueprint import BlueprintNodeResult, BlueprintOverlay
from app.schemas.chat import ChatMessageResult
from app.schemas.common import Actor, ProcessElement, ProcessFlow, ProcessSchema, SourceRef
from app.schemas.documents import IngestionStatus
from app.schemas.environment import TwinEnvironment, TwinEnvironmentCreate, TwinEnvironmentUpdate
from app.schemas.evaluation import (
    EvaluationResult,
    RegressionSuite,
    RegressionSuiteCreate,
    RegressionSuiteRun,
    RegressionSuiteScenarioResult,
    RunComparison,
)
from app.schemas.context import Citation
from app.schemas.gap_analysis import GapFinding, GapFindingOption
from app.schemas.orchestration import (
    NodeRun,
    OrchestrationRun,
    OrchestrationScenario,
    OrchestrationScenarioCreate,
)
from app.schemas.publish import AgentPublication, AgentPublishStatus
from app.schemas.reproducibility import RunSnapshotView
from app.schemas.run import NON_TERMINAL_RUN_STATUSES, RunStatus, RunStep
from app.schemas.twin import (
    InferredToolSchema,
    TwinBaseline,
    TwinBaselineComparison,
    TwinBaselineInput,
    TwinDeviation,
    TwinRun,
    TwinScenario,
    TwinScenarioCreate,
    TwinSummary,
)
from app.schemas.versions import VersionDetail
from app.store import NotFoundError
from app.twin.engine import RunCancelled, run_scenario
from app.twin.errors import TwinServiceError
from app.twin.evaluation import evaluate_run
from app.twin.orchestrator import NodeArtifact, run_orchestration
from app.twin.schema_inference import infer_tool_schemas
from app.twin.snapshot import build_run_snapshot

from .models import (
    ActorModel,
    AgentArtifactModel,
    AgentPublicationModel,
    BlueprintOverlayModel,
    BPMNDraftModel,
    BatchRunModel,
    ChatMessageModel,
    ContextPackModel,
    LabelledDatasetModel,
    DocumentEmbeddingModel,
    DocumentModel,
    GapFindingModel,
    OrchestrationRunModel,
    OrchestrationScenarioModel,
    ProcessElementModel,
    ProcessFlowModel,
    ProcessModel,
    ProcessSchemaChangeModel,
    RegressionSuiteModel,
    RegressionSuiteRunModel,
    SessionModel,
    SimulationRunModel,
    SourceRefModel,
    TwinBaselineModel,
    TwinEnvironmentModel,
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
        context_pack_id=overlay.context_pack_id,
        context_stale=is_pack_stale(session, overlay.context_pack_id),
    )


def set_blueprint_overlay(
    session: Session,
    process_id: str,
    baseline_version_id: str,
    nodes: list[BlueprintNodeResult],
    *,
    context_pack_id: str | None = None,
) -> BlueprintOverlay:
    get_process(session, process_id)  # 404s if missing
    node_dicts = [node.model_dump() for node in nodes]
    overlay = session.get(BlueprintOverlayModel, process_id)
    if overlay is None:
        overlay = BlueprintOverlayModel(
            process_id=process_id,
            baseline_version_id=baseline_version_id,
            nodes=node_dicts,
            context_pack_id=context_pack_id,
        )
        session.add(overlay)
    else:
        overlay.baseline_version_id = baseline_version_id
        overlay.nodes = node_dicts
        overlay.generated_at = utcnow()
        overlay.context_pack_id = context_pack_id
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


def _compute_agent_lifecycle_status(
    session: Session, artifact: AgentArtifactModel, *, checklist_complete: bool
) -> str:
    """Epic 23, US23.1: derives the DRAFT..DEPLOYED lifecycle from data
    that already exists -- see AgentArtifact schema's `LifecycleStatus`
    docstring and the 2026-09-29 decision-log entry for why `VALIDATED`
    collapses into `SIMULATION_READY` here (this implementation has no
    signal that distinguishes "checklist just passed" from "ready to
    simulate" -- they're the same fact until a real intermediate step is
    added)."""
    if any(p.status == "deployed" for p in _list_agent_publication_models(session, artifact.id)):
        return "DEPLOYED"
    if artifact.approved_at is not None:
        return "APPROVED"
    if artifact.marked_release_candidate_at is not None:
        return "RELEASE_CANDIDATE"
    if not checklist_complete:
        return "DRAFT"
    runs = list(session.scalars(select(SimulationRunModel).where(SimulationRunModel.agent_artifact_id == artifact.id)))
    if any(r.status in NON_TERMINAL_RUN_STATUSES for r in runs):
        return "SIMULATING"
    if any(r.evaluation is not None for r in runs):
        return "EVALUATED"
    return "SIMULATION_READY"


def _to_pydantic_agent_artifact(session: Session, artifact: AgentArtifactModel, *, is_stale: bool) -> AgentArtifact:
    definition = AgentDefinition.model_validate(artifact.definition)
    has_scenarios = (
        session.scalar(
            select(TwinScenarioModel.id).where(TwinScenarioModel.agent_artifact_id == artifact.id).limit(1)
        )
        is not None
    )
    checklist = compute_checklist(definition, has_scenarios=has_scenarios)
    checklist_complete = all(item.satisfied for item in checklist)
    context_stale = is_pack_stale(session, artifact.context_pack_id)
    return AgentArtifact(
        id=artifact.id,
        process_id=artifact.process_id,
        group_key=artifact.group_key,
        node_ids=artifact.node_ids,
        primary_node_id=artifact.primary_node_id,
        status="stale" if is_stale or context_stale else "generated",
        version=artifact.version,
        definition=definition,
        baseline_version_id=artifact.source_baseline_version_id,
        context_pack_id=artifact.context_pack_id,
        context_stale=context_stale,
        lifecycle_status=_compute_agent_lifecycle_status(session, artifact, checklist_complete=checklist_complete),
        checklist=checklist,
        marked_release_candidate_at=artifact.marked_release_candidate_at,
        approved_at=artifact.approved_at,
        approved_by=artifact.approved_by,
        approved_by_name=_resolve_user_name(session, artifact.approved_by),
        generated_at=artifact.generated_at,
        generated_by=artifact.generated_by,
        generated_by_name=_resolve_user_name(session, artifact.generated_by),
    )


_LIFECYCLE_ORDER = [
    "DRAFT", "VALIDATED", "SIMULATION_READY", "SIMULATING", "EVALUATED", "RELEASE_CANDIDATE", "APPROVED", "DEPLOYED",
]


def _require_simulation_ready(session: Session, artifact: AgentArtifactModel) -> None:
    """US23.3: refuses to start a run against an artifact that hasn't
    reached SIMULATION_READY, rather than letting a run start and only
    surfacing missing definition pieces mid-run."""
    pydantic_artifact = _to_pydantic_agent_artifact(session, artifact, is_stale=False)
    if _LIFECYCLE_ORDER.index(pydantic_artifact.lifecycle_status) < _LIFECYCLE_ORDER.index("SIMULATION_READY"):
        unmet = [item.label for item in pydantic_artifact.checklist if not item.satisfied]
        raise TwinServiceError(
            f"Agent artifact '{artifact.id}' hasn't reached SIMULATION_READY yet -- missing: {', '.join(unmet)}"
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
    # Epic 28, US28.6: the overlay's pinned pack is what this agent's rules
    # come from, and what it pins in turn.
    pack = session.get(ContextPackModel, overlay_model.context_pack_id) if overlay_model.context_pack_id else None
    definition = apply_agent_context(session, definition, group, pack)
    # Epic 29, US29.3/US29.7: tool contracts link to catalog applications and
    # take their permissions from the data those applications hold.
    primary_spec = next(n for n in group if n.node_id == node_id).agent_spec
    if primary_spec is not None:
        definition = apply_catalog_to_definition(session, process_id, definition, primary_spec)
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
            context_pack_id=overlay_model.context_pack_id,
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
        artifact.context_pack_id = overlay_model.context_pack_id
        # Epic 19, US19.5: every regenerate of an existing row is a new
        # version to key runs/suites/comparisons against.
        artifact.version += 1

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


def update_agent_governance(
    session: Session, process_id: str, artifact_id: str, update: AgentGovernanceUpdate
) -> AgentArtifact:
    """Epic 20, US20.1: lets an Automation Architect widen/narrow an
    already-generated artifact's governance fields (permissions,
    guardrails, escalation policy, tool contracts, knowledge sources,
    model policy) without a full blueprint regenerate -- regenerating via
    US12.1/generate_agent_artifact would silently reset these back to
    generation.py's defaults, so this is a targeted, additive update
    instead. Deliberately never reassigns generated_by, and doesn't affect
    staleness (US12.4) -- a governance edit doesn't change what the
    definition's content was generated from, even though the row's
    generated_at timestamp does still advance (AgentArtifactModel's own
    onupdate=func.now() convention, shared with every other "replace
    wholesale" model in this codebase -- it tracks "last touched", not
    specifically "last regenerated")."""
    artifact = get_agent_artifact(session, process_id, artifact_id)
    updated_fields = update.model_dump(exclude_unset=True)
    # Re-validate through AgentDefinition rather than BaseModel.model_copy
    # (which assigns the update's raw dicts without re-running nested
    # model validation) so the stored definition's nested governance
    # fields are always real ToolContract/ResourcePermission/etc.
    # instances, never plain dicts left over from the partial update.
    definition = AgentDefinition.model_validate({**artifact.definition, **updated_fields})
    artifact.definition = definition.model_dump()
    session.flush()

    overlay_model = session.get(BlueprintOverlayModel, process_id)
    return _to_pydantic_agent_artifact(
        session, artifact, is_stale=_is_agent_artifact_stale(overlay_model, artifact)
    )


def mark_agent_release_candidate(session: Session, process_id: str, artifact_id: str) -> AgentArtifact:
    """Epic 23, US23.4: an explicit human action -- nothing else can
    produce this state (see LifecycleStatus's docstring)."""
    artifact = get_agent_artifact(session, process_id, artifact_id)
    artifact.marked_release_candidate_at = utcnow()
    session.flush()
    overlay_model = session.get(BlueprintOverlayModel, process_id)
    return _to_pydantic_agent_artifact(session, artifact, is_stale=_is_agent_artifact_stale(overlay_model, artifact))


def approve_agent_artifact(
    session: Session, process_id: str, artifact_id: str, *, approved_by: str | None = None
) -> AgentArtifact:
    artifact = get_agent_artifact(session, process_id, artifact_id)
    artifact.approved_at = utcnow()
    artifact.approved_by = approved_by
    session.flush()
    overlay_model = session.get(BlueprintOverlayModel, process_id)
    return _to_pydantic_agent_artifact(session, artifact, is_stale=_is_agent_artifact_stale(overlay_model, artifact))


# -- digital twin environments & fault injection (Epic 21) --------------------


def _to_pydantic_twin_environment(session: Session, environment: TwinEnvironmentModel) -> TwinEnvironment:
    return TwinEnvironment(
        id=environment.id,
        process_id=environment.process_id,
        name=environment.name,
        version=environment.version,
        systems=environment.systems,
        datasets=environment.datasets,
        personas=environment.personas,
        events=environment.events,
        faults=environment.faults,
        created_at=environment.created_at,
        created_by=environment.created_by,
        created_by_name=_resolve_user_name(session, environment.created_by),
        updated_at=environment.updated_at,
    )


def create_twin_environment(
    session: Session, process_id: str, data: TwinEnvironmentCreate, *, created_by: str | None = None
) -> TwinEnvironment:
    get_process(session, process_id)  # 404s if missing
    environment = TwinEnvironmentModel(
        id=new_id("twinenv"),
        process_id=process_id,
        name=data.name,
        systems=list(data.systems),
        datasets=data.datasets,
        personas=data.personas,
        events=data.events,
        faults={name: [f.model_dump() for f in faults] for name, faults in data.faults.items()},
        created_by=created_by,
    )
    session.add(environment)
    session.flush()
    return _to_pydantic_twin_environment(session, environment)


def list_twin_environments(session: Session, process_id: str) -> list[TwinEnvironment]:
    get_process(session, process_id)  # 404s if missing
    environments = session.scalars(
        select(TwinEnvironmentModel)
        .where(TwinEnvironmentModel.process_id == process_id)
        .order_by(TwinEnvironmentModel.created_at)
    )
    return [_to_pydantic_twin_environment(session, environment) for environment in environments]


def get_twin_environment_model(session: Session, process_id: str, environment_id: str) -> TwinEnvironmentModel:
    environment = session.get(TwinEnvironmentModel, environment_id)
    if environment is None or environment.process_id != process_id:
        raise NotFoundError("twin environment", environment_id)
    return environment


def update_twin_environment(
    session: Session, process_id: str, environment_id: str, data: TwinEnvironmentUpdate
) -> TwinEnvironment:
    """US21.1: replaces the environment's fields in place and bumps
    `version` -- same convention as `generate_agent_artifact`'s version
    bump (Epic 19, US19.5). Scenarios that already pinned an earlier
    `environment_version` keep reading their own snapshot at run time
    (Epic 22 records it); this only affects new scenarios/runs created
    after the update."""
    environment = get_twin_environment_model(session, process_id, environment_id)
    environment.name = data.name
    environment.systems = list(data.systems)
    environment.datasets = data.datasets
    environment.personas = data.personas
    environment.events = data.events
    environment.faults = {name: [f.model_dump() for f in faults] for name, faults in data.faults.items()}
    environment.version += 1
    session.flush()
    return _to_pydantic_twin_environment(session, environment)


def delete_twin_environment(session: Session, process_id: str, environment_id: str) -> None:
    environment = get_twin_environment_model(session, process_id, environment_id)
    session.delete(environment)
    session.flush()


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
        environment_id=scenario.environment_id,
        environment_version=scenario.environment_version,
        faults=scenario.faults or {},
        step_match=scenario.step_match or "exact",
        story=scenario.story,
        dataset_id=scenario.dataset_id,
        decision_field=scenario.decision_field or "decision",
        created_at=scenario.created_at,
        created_by=scenario.created_by,
        created_by_name=_resolve_user_name(session, scenario.created_by),
    )


def _legacy_run_status(status: str) -> tuple[RunStatus, bool | None]:
    """Epic 17: maps a pre-Epic-17 TwinRunModel/OrchestrationRunModel
    `status` string onto the new (status, graded_passed) split -- historical
    rows are never re-executed, only re-displayed through the new shape.
    "passed"/"failed" both mean the run *executed* to completion (only
    grading differed); "error" (or a leftover "running" from a crashed
    background task predating this migration) means it did not."""
    if status == "passed":
        return "COMPLETED", True
    if status == "failed":
        return "COMPLETED", False
    return "FAILED", None


def _legacy_trace_to_steps(trace: list[dict], *, occurred_at) -> list[RunStep]:
    """Epic 17: maps a pre-Epic-17 flat two-kind trace (`tool_call`/
    `human_checkpoint`) onto the new typed RunStep taxonomy for display.
    Old trace entries never recorded a per-step timestamp, so every mapped
    step shares the run's own `occurred_at` (its completion time) --
    approximate, but this is historical read-only data, never re-graded."""
    steps: list[RunStep] = []
    for i, entry in enumerate(trace):
        if entry.get("kind") == "human_checkpoint":
            step_type = "HUMAN_APPROVED" if entry.get("decision") == "approve" else "HUMAN_REJECTED"
        else:
            step_type = "TOOL_FAILED" if entry.get("denied") else "TOOL_EXECUTED"
        steps.append(
            RunStep(
                seq=i,
                type=step_type,
                target=entry.get("target"),
                arguments=entry.get("arguments") or {},
                result=entry.get("result"),
                decision=entry.get("decision"),
                denied=bool(entry.get("denied")),
                static_fallback=bool(entry.get("static_fallback")),
                occurred_at=occurred_at,
            )
        )
    return steps


def _to_pydantic_twin_run(session: Session, run: TwinRunModel) -> TwinRun:
    """Epic 17: projects a pre-Epic-17 TwinRunModel row (kept forever, never
    migrated -- see app/db/models.py's SimulationRunModel docstring) through
    the new TwinRun shape, so it still displays correctly alongside new
    SimulationRunModel-backed runs (see `list_twin_runs`/`get_twin_summary`'s
    merge)."""
    status, graded_passed = _legacy_run_status(run.status)
    return TwinRun(
        id=run.id,
        kind="twin",
        scenario_id=run.scenario_id,
        agent_artifact_id=run.agent_artifact_id,
        status=status,
        steps=_legacy_trace_to_steps(run.trace, occurred_at=run.completed_at),
        final_output=run.final_output,
        deviations=[TwinDeviation.model_validate(d) for d in run.deviations],
        graded_passed=graded_passed,
        total_cost_usd=run.total_cost_usd,
        total_tokens=run.total_tokens,
        turns_used=run.turns_used,
        started_at=run.started_at,
        completed_at=run.completed_at,
        run_by=run.run_by,
        run_by_name=_resolve_user_name(session, run.run_by),
    )


def _to_pydantic_sim_twin_run(session: Session, run: SimulationRunModel) -> TwinRun:
    return TwinRun(
        id=run.id,
        kind="twin",
        scenario_id=run.scenario_id,
        agent_artifact_id=run.agent_artifact_id or "",
        status=run.status,
        steps=[RunStep.model_validate(s) for s in run.steps],
        final_output=run.final_output,
        deviations=[TwinDeviation.model_validate(d) for d in run.deviations],
        graded_passed=run.graded_passed,
        evaluation=EvaluationResult.model_validate(run.evaluation) if run.evaluation else None,
        total_cost_usd=run.total_cost_usd,
        total_tokens=run.total_tokens,
        turns_used=run.turns_used,
        started_at=run.started_at,
        completed_at=run.completed_at,
        run_by=run.run_by,
        run_by_name=_resolve_user_name(session, run.run_by),
        dataset_id=run.dataset_id,
        dataset_version=run.dataset_version,
        row_key=run.row_key,
        trial=run.trial,
        batch_id=run.batch_id,
    )


def create_twin_scenario(
    session: Session, process_id: str, artifact_id: str, data: TwinScenarioCreate, *, created_by: str | None = None
) -> TwinScenario:
    get_process(session, process_id)  # 404s if missing
    artifact = get_agent_artifact(session, process_id, artifact_id)  # 404s if missing/wrong process
    # Epic 21, US21.3: pinned to the environment's current version at
    # creation time, same reasoning as source_baseline_version_id -- a
    # scenario shouldn't silently start reading a different environment
    # revision just because the environment was edited later.
    environment_version = None
    if data.environment_id is not None:
        environment_version = get_twin_environment_model(session, process_id, data.environment_id).version
    scenario = TwinScenarioModel(
        id=new_id("twinsc"),
        agent_artifact_id=artifact.id,
        name=data.name,
        inputs=data.inputs,
        system_stubs={name: stub.model_dump() for name, stub in data.system_stubs.items()},
        human_checkpoint_config=data.human_checkpoint_config.model_dump(),
        expected_steps=[step.model_dump() for step in data.expected_steps],
        expected_outputs=data.expected_outputs,
        environment_id=data.environment_id,
        environment_version=environment_version,
        faults={name: [f.model_dump() for f in faults] for name, faults in data.faults.items()},
        step_match=data.step_match,
        story=data.story.model_dump() if data.story is not None else None,
        dataset_id=data.dataset_id,
        decision_field=data.decision_field,
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
    # Epic 29, US29.4: systems linked to a catalog application with an
    # OpenAPI spec use the real operations, rebuilt on every call (no LLM,
    # and a re-attached spec takes effect immediately); only the rest are
    # inferred and cached.
    from_specs = spec_tool_schemas(session, artifact.process_id, definition.tools_systems_needed)
    to_infer = [name for name in definition.tools_systems_needed if name not in from_specs]
    cached = session.get(TwinToolSchemaModel, artifact.id)
    if cached is not None and set(cached.schemas) == set(to_infer):
        inferred = {name: InferredToolSchema.model_validate(value) for name, value in cached.schemas.items()}
        return {**inferred, **from_specs}

    schemas = await infer_tool_schemas(llm, definition.model_copy(update={"tools_systems_needed": to_infer}))
    dumped = {name: schema.model_dump() for name, schema in schemas.items()}
    if cached is None:
        session.add(TwinToolSchemaModel(agent_artifact_id=artifact.id, schemas=dumped))
    else:
        cached.schemas = dumped
        cached.generated_at = utcnow()
    session.flush()
    return {**schemas, **from_specs}


def start_twin_run(
    session: Session,
    process_id: str,
    scenario_id: str,
    *,
    run_by: str | None = None,
    row_key: str | None = None,
    dataset_version: int | None = None,
    trial: int | None = None,
    batch_id: str | None = None,
) -> TwinRun:
    """Epic 17, US17.6: creates the run row in status="CREATED" and returns
    immediately -- the caller (app/api/twin.py) commits this and enqueues
    `execute_twin_run_background` as a FastAPI BackgroundTask, the same
    two-step handoff app/api/orchestration.py's `start_run` already uses
    (itself modeled on app/api/documents.py's ingestion upload). Twin runs
    used to execute synchronously inside one request -- this closes that
    gap so a twin run can be polled, cancelled, and (via a later manual
    human checkpoint) suspended/resumed exactly like an orchestration run."""
    scenario_model = get_twin_scenario(session, process_id, scenario_id)
    artifact = get_agent_artifact(session, process_id, scenario_model.agent_artifact_id)
    _require_simulation_ready(session, artifact)
    environment_model = (
        session.get(TwinEnvironmentModel, scenario_model.environment_id) if scenario_model.environment_id else None
    )
    run = SimulationRunModel(
        id=new_id("simrun"),
        kind="twin",
        process_id=process_id,
        scenario_id=scenario_model.id,
        agent_artifact_id=artifact.id,
        status="CREATED",
        # Epic 22, US22.1: everything this needs is already resolved here,
        # before any execution starts.
        snapshot=build_run_snapshot(
            kind="twin", scenario_id=scenario_model.id, agent_artifacts=[artifact], environment=environment_model
        ),
        run_by=run_by,
        trial=trial,
        batch_id=batch_id,
    )
    if row_key is not None:
        # Epic 32, US32.2/US32.6: a data-driven run pins the dataset version.
        if not scenario_model.dataset_id:
            raise TwinServiceError("This scenario has no dataset, so it can't run a dataset row")
        dataset = session.get(LabelledDatasetModel, scenario_model.dataset_id)
        if dataset is None:
            raise TwinServiceError("This scenario's dataset no longer exists")
        version = dataset_version or dataset.version
        row_at(session, dataset.id, row_key, version)  # 404s if the row isn't in that version
        run.dataset_id = dataset.id
        run.dataset_version = version
        run.row_key = row_key
        run.snapshot = {**(run.snapshot or {}), "dataset": {"dataset_id": dataset.id, "version": version, "row_key": row_key}}
    session.add(run)
    session.flush()
    return _to_pydantic_sim_twin_run(session, run)


@contextlib.contextmanager
def _llm_route_for_run(run_id: str):
    """Epic 31, US31.8: a background run applies its process's business-unit
    model policy to every LLM call it makes (agent turns, simulated
    systems, schema inference)."""
    from app.db.session import get_session_factory

    session = get_session_factory()()
    try:
        run_model = session.get(SimulationRunModel, run_id)
        route = route_for_scope(session, _scope_unit(session, run_model.process_id)) if run_model else None
    finally:
        session.close()
    token = current_llm_route.set(route)
    try:
        yield
    finally:
        current_llm_route.reset(token)


def _scope_unit(session: Session, process_id: str) -> str | None:
    process = session.get(ProcessModel, process_id)
    return process.business_unit_id if process is not None else None


async def execute_twin_run_background(
    run_id: str, llm: LLMClient, settings: Settings | None = None, human_decision: str | None = None
) -> None:
    with _llm_route_for_run(run_id):
        await _execute_twin_run(run_id, llm, settings, human_decision)


async def _execute_twin_run(
    run_id: str, llm: LLMClient, settings: Settings | None = None, human_decision: str | None = None
) -> None:
    """US17.6: the FastAPI BackgroundTask entry point for a twin run.
    Opens its own DB session -- same reasoning as app/ingestion/
    pipeline.py's process_document, since a BackgroundTask can outlive the
    request-scoped session's teardown. Also the resume entry point (Epic
    17, US17.3): called again after `resume_run` flips a WAITING_FOR_HUMAN
    row back toward RUNNING -- `run_model.resume_state` (persisted by the
    prior leg) and the caller-supplied `human_decision` are read/consumed
    here rather than threaded through BackgroundTasks.add_task."""
    from app.db.session import get_session_factory

    settings = settings or get_settings()
    session = get_session_factory()()
    try:
        run_model = session.get(SimulationRunModel, run_id)
        if run_model is None:
            return
        scenario_model = session.get(TwinScenarioModel, run_model.scenario_id)
        artifact = session.get(AgentArtifactModel, run_model.agent_artifact_id) if run_model.agent_artifact_id else None
        if scenario_model is None or artifact is None:
            run_model.status = "FAILED"
            run_model.deviations = [{"reason": "The scenario or agent artifact this run was started from no longer exists"}]
            run_model.completed_at = utcnow()
            session.commit()
            return

        definition = AgentDefinition.model_validate(artifact.definition)
        try:
            tool_schemas = await _get_or_infer_tool_schemas(llm, session, artifact)
        except TwinServiceError as exc:
            run_model.status = "FAILED"
            run_model.deviations = [{"reason": str(exc)}]
            run_model.completed_at = utcnow()
            session.commit()
            return
        scenario = _to_pydantic_twin_scenario(session, scenario_model)
        if run_model.row_key and run_model.dataset_id:
            scenario = apply_dataset_row(
                scenario, row_at(session, run_model.dataset_id, run_model.row_key, run_model.dataset_version or 1)
            )
        # Epic 34, US34.6/US34.10: a batch run's own seed, and simulated
        # system latency when it's part of a load batch.
        if run_model.seed is not None:
            scenario.human_checkpoint_config = scenario.human_checkpoint_config.model_copy(update={"seed": run_model.seed})
        if run_model.batch_id:
            batch_model = session.get(BatchRunModel, run_model.batch_id)
            if batch_model is not None and batch_model.mode == "load":
                scenario.stub_latency_ms = int((batch_model.load_settings or {}).get("stub_latency_ms", 0))
        # Epic 21, US21.3: resolved at execution time (not pinned to the
        # scenario's own environment_version) -- the environment's own
        # fields are already "current state, version bumped in place"
        # (see TwinEnvironmentModel's docstring), so a run reads whatever
        # the environment looks like right now, exactly like an agent
        # artifact's own definition is always read fresh, not pinned.
        environment_model = (
            session.get(TwinEnvironmentModel, scenario_model.environment_id) if scenario_model.environment_id else None
        )
        environment = _to_pydantic_twin_environment(session, environment_model) if environment_model is not None else None
        resume_state = run_model.resume_state
        run_model.resume_state = None
        if resume_state is None:
            # Epic 34: batch runs are created up front and queued, so the
            # insert time would count queue wait as run time.
            run_model.started_at = utcnow()
        run_model.status = "RUNNING"
        session.commit()

        async def on_step(step: RunStep) -> None:
            run_model.steps = [*run_model.steps, step.model_dump(mode="json")]
            session.commit()
            session.refresh(run_model, attribute_names=["cancel_requested"])
            if run_model.cancel_requested:
                raise RunCancelled()

        async def on_phase(status: RunStatus) -> None:
            run_model.status = status
            session.commit()

        outcome = await run_scenario(
            llm,
            artifact=definition,
            tool_schemas=tool_schemas,
            scenario=scenario,
            max_turns=settings.twin_max_loop_turns,
            environment=environment,
            resume_state=resume_state,
            human_decision=human_decision,
            on_step=on_step,
            on_phase=on_phase,
        )

        run_model.steps = [s.model_dump(mode="json") for s in outcome.steps]
        run_model.status = outcome.status
        run_model.final_output = outcome.final_output
        run_model.deviations = [d.model_dump(mode="json") for d in outcome.deviations]
        run_model.graded_passed = outcome.graded_passed
        run_model.total_cost_usd = outcome.total_cost_usd
        run_model.total_tokens = outcome.total_tokens
        run_model.turns_used = outcome.turns_used
        run_model.resume_state = outcome.resume_state
        if outcome.status != "WAITING_FOR_HUMAN":
            run_model.completed_at = utcnow()
            # Epic 19, US19.1: computed once the run reaches a terminal
            # state, using the artifact's own guardrails as each dimension's
            # threshold reference (efficiency/latency/token_usage/cost).
            run_model.evaluation = evaluate_run(
                steps=outcome.steps,
                final_output=outcome.final_output,
                deviations=outcome.deviations,
                graded_passed=outcome.graded_passed,
                status=outcome.status,
                guardrails=definition.guardrails,
                total_cost_usd=outcome.total_cost_usd,
                total_tokens=outcome.total_tokens,
                started_at=run_model.started_at,
                completed_at=run_model.completed_at,
            ).model_dump(mode="json")
        session.commit()
    finally:
        session.close()


def _get_simulation_run(session: Session, process_id: str, run_id: str, *, kind: str) -> SimulationRunModel:
    run = session.get(SimulationRunModel, run_id)
    if run is None or run.process_id != process_id or run.kind != kind:
        raise NotFoundError(f"{kind} run", run_id)
    return run


def cancel_run(session: Session, process_id: str, run_id: str, *, kind: str) -> None:
    """Epic 17, US17.4: `kind` ("twin" or "orchestration") guards against
    cancelling a run through the wrong kind's endpoint. A running leg is
    checked cooperatively by its own `on_step` callback (see
    `execute_twin_run_background`/`execute_orchestration_run_background`)
    rather than forcibly killed, since a background task has no safe way to
    be interrupted mid-await -- but a WAITING_FOR_HUMAN run has no active
    background task to notice that flag at all (nothing runs again until a
    resume), so cancelling one resolves immediately instead of waiting for
    a wake-up that would otherwise never come."""
    run = _get_simulation_run(session, process_id, run_id, kind=kind)
    if run.status == "WAITING_FOR_HUMAN":
        run.status = "CANCELLED"
        run.resume_state = None
        run.completed_at = utcnow()
    else:
        run.cancel_requested = True
    session.commit()


def resume_run(session: Session, process_id: str, run_id: str, decision: str, *, kind: str) -> SimulationRunModel:
    """Epic 17, US17.3: flips a WAITING_FOR_HUMAN row back toward RUNNING --
    the caller (app/api/twin.py or app/api/orchestration.py) commits this
    and enqueues the matching `execute_*_run_background` with
    `human_decision=decision`."""
    run = _get_simulation_run(session, process_id, run_id, kind=kind)
    if run.status != "WAITING_FOR_HUMAN":
        raise TwinServiceError(f"Run '{run_id}' is not waiting for a human decision (status: {run.status})")
    run.status = "RUNNING"
    session.commit()
    return run


def get_twin_run(session: Session, process_id: str, run_id: str) -> TwinRun:
    run = session.get(SimulationRunModel, run_id)
    if run is not None and run.kind == "twin" and run.process_id == process_id:
        return _to_pydantic_sim_twin_run(session, run)
    legacy = session.get(TwinRunModel, run_id)
    if legacy is not None:
        get_agent_artifact(session, process_id, legacy.agent_artifact_id)  # 404s if wrong process
        return _to_pydantic_twin_run(session, legacy)
    raise NotFoundError("twin run", run_id)


def list_twin_runs(session: Session, process_id: str, artifact_id: str) -> list[TwinRun]:
    get_process(session, process_id)  # 404s if missing
    get_agent_artifact(session, process_id, artifact_id)  # 404s if missing/wrong process
    legacy_runs = session.scalars(
        select(TwinRunModel).where(TwinRunModel.agent_artifact_id == artifact_id)
    )
    sim_runs = session.scalars(
        select(SimulationRunModel).where(
            SimulationRunModel.kind == "twin", SimulationRunModel.agent_artifact_id == artifact_id
        )
    )
    runs = [_to_pydantic_twin_run(session, run) for run in legacy_runs] + [
        _to_pydantic_sim_twin_run(session, run) for run in sim_runs
    ]
    runs.sort(key=lambda run: run.started_at, reverse=True)
    return runs


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
    legacy_runs = list(session.scalars(select(TwinRunModel).where(TwinRunModel.agent_artifact_id == artifact_id)))
    sim_runs = list(
        session.scalars(
            select(SimulationRunModel).where(
                SimulationRunModel.kind == "twin", SimulationRunModel.agent_artifact_id == artifact_id
            )
        )
    )
    baseline_model = session.get(TwinBaselineModel, artifact_id)
    baseline = _to_pydantic_twin_baseline(session, baseline_model) if baseline_model is not None else None

    # Epic 17: normalizes both the legacy TwinRunModel rows and the new
    # SimulationRunModel rows into one (passed, cost, deviations, duration)
    # shape so the aggregate math below doesn't need to know which table a
    # row came from. A SimulationRunModel row still in flight (not yet
    # completed) has nothing to grade yet, so it's excluded here.
    rows: list[tuple[bool, float | None, list[dict], float]] = [
        (run.status == "passed", run.total_cost_usd, run.deviations, (run.completed_at - run.started_at).total_seconds())
        for run in legacy_runs
    ] + [
        (bool(run.graded_passed), run.total_cost_usd, run.deviations, (run.completed_at - run.started_at).total_seconds())
        for run in sim_runs
        if run.completed_at is not None
    ]

    run_count = len(rows)
    if run_count == 0:
        return TwinSummary(agent_artifact_id=artifact_id, run_count=0, baseline=baseline)

    pass_rate = sum(1 for passed, _, _, _ in rows if passed) / run_count
    # A single run with an unpriced model (see estimate_cost_usd) makes the
    # whole aggregate cost unknown rather than silently under-reporting it --
    # same "don't show a wrong partial number" principle as
    # UsageTracker.OperationTotals.cost_estimate_incomplete.
    cost_incomplete = any(cost is None for _, cost, _, _ in rows)
    total_cost = None if cost_incomplete else sum(cost or 0.0 for _, cost, _, _ in rows)
    average_cost = None if cost_incomplete else total_cost / run_count

    reason_counts: dict[str, int] = {}
    for _, _, deviations, _ in rows:
        for deviation in deviations:
            reason = deviation.get("reason", "")
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
    common_failure_reasons = sorted(reason_counts, key=lambda reason: reason_counts[reason], reverse=True)[:5]

    average_duration = sum(duration for _, _, _, duration in rows) / run_count
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


# -- orchestration rehearsal (Epic 16) -----------------------------------------


def _to_pydantic_orchestration_scenario(session: Session, scenario: OrchestrationScenarioModel) -> OrchestrationScenario:
    return OrchestrationScenario(
        id=scenario.id,
        process_id=scenario.process_id,
        baseline_version_id=scenario.baseline_version_id,
        name=scenario.name,
        inputs=scenario.inputs,
        gateway_decisions=scenario.gateway_decisions,
        manual_node_config=scenario.manual_node_config,
        system_stubs=scenario.system_stubs,
        human_checkpoint_config=scenario.human_checkpoint_config,
        data_mapping_mode=scenario.data_mapping_mode,
        expected_path=scenario.expected_path,
        expected_final_output=scenario.expected_final_output,
        environment_id=scenario.environment_id,
        environment_version=scenario.environment_version,
        created_at=scenario.created_at,
        created_by=scenario.created_by,
        created_by_name=_resolve_user_name(session, scenario.created_by),
    )


def create_orchestration_scenario(
    session: Session, process_id: str, data: OrchestrationScenarioCreate, *, created_by: str | None = None
) -> OrchestrationScenario:
    """US16.1. Pinned to the process's current blueprint baseline version
    (same reasoning as AgentArtifactModel's source_baseline_version_id) --
    a scenario authored against one finalized diagram shouldn't silently
    start walking a different one if the blueprint is later regenerated."""
    get_process(session, process_id)  # 404s if missing
    overlay = get_blueprint_overlay(session, process_id)
    if overlay is None:
        raise NotFoundError("blueprint", process_id)
    scenario = OrchestrationScenarioModel(
        id=new_id("orchsc"),
        process_id=process_id,
        baseline_version_id=overlay.baseline_version_id,
        name=data.name,
        inputs=data.inputs,
        gateway_decisions={node_id: decision.model_dump() for node_id, decision in data.gateway_decisions.items()},
        manual_node_config={node_id: config.model_dump() for node_id, config in data.manual_node_config.items()},
        system_stubs={
            node_id: {name: stub.model_dump() for name, stub in stubs.items()}
            for node_id, stubs in data.system_stubs.items()
        },
        human_checkpoint_config={
            node_id: config.model_dump() for node_id, config in data.human_checkpoint_config.items()
        },
        data_mapping_mode=dict(data.data_mapping_mode),
        expected_path=list(data.expected_path),
        expected_final_output=data.expected_final_output,
        environment_id=data.environment_id,
        environment_version=(
            get_twin_environment_model(session, process_id, data.environment_id).version
            if data.environment_id is not None
            else None
        ),
        created_by=created_by,
    )
    session.add(scenario)
    session.flush()
    return _to_pydantic_orchestration_scenario(session, scenario)


def list_orchestration_scenarios(session: Session, process_id: str) -> list[OrchestrationScenario]:
    get_process(session, process_id)  # 404s if missing
    scenarios = session.scalars(
        select(OrchestrationScenarioModel)
        .where(OrchestrationScenarioModel.process_id == process_id)
        .order_by(OrchestrationScenarioModel.created_at)
    )
    return [_to_pydantic_orchestration_scenario(session, scenario) for scenario in scenarios]


def get_orchestration_scenario_model(session: Session, process_id: str, scenario_id: str) -> OrchestrationScenarioModel:
    scenario = session.get(OrchestrationScenarioModel, scenario_id)
    if scenario is None or scenario.process_id != process_id:
        raise NotFoundError("orchestration scenario", scenario_id)
    return scenario


def delete_orchestration_scenario(session: Session, process_id: str, scenario_id: str) -> None:
    scenario = get_orchestration_scenario_model(session, process_id, scenario_id)
    session.delete(scenario)
    session.flush()


def _to_pydantic_orchestration_run(session: Session, run: OrchestrationRunModel) -> OrchestrationRun:
    """Epic 17: projects a pre-Epic-17 OrchestrationRunModel row (kept
    forever, never migrated) through the new OrchestrationRun shape --
    `handoffs` has no equivalent any more (Epic 17 folded that into
    AGENT_DELEGATED steps) so it's simply not shown for these historical
    rows, same spirit as _to_pydantic_twin_run's trace-to-steps mapping."""
    status, graded_passed = _legacy_run_status(run.status)
    node_runs = []
    for nr in run.node_runs:
        nr_status, _ = _legacy_run_status(nr.get("status", "error"))
        occurred_at = nr.get("completed_at") or run.completed_at or run.started_at
        node_runs.append(
            NodeRun(
                node_id=nr["node_id"],
                node_label=nr["node_label"],
                kind=nr["kind"],
                status=nr_status,
                agent_artifact_id=nr.get("agent_artifact_id"),
                steps=_legacy_trace_to_steps(nr.get("trace") or [], occurred_at=occurred_at),
                output=nr.get("output"),
                error_message=nr.get("error_message"),
                total_cost_usd=nr.get("total_cost_usd"),
                total_tokens=nr.get("total_tokens", 0),
                started_at=nr["started_at"],
                completed_at=nr.get("completed_at"),
            )
        )
    return OrchestrationRun(
        id=run.id,
        kind="orchestration",
        scenario_id=run.scenario_id,
        process_id=run.process_id,
        status=status,
        steps=[],
        node_runs=node_runs,
        visited_path=run.visited_path,
        final_output=run.final_output,
        deviations=[TwinDeviation.model_validate(deviation) for deviation in run.deviations],
        graded_passed=graded_passed,
        total_cost_usd=run.total_cost_usd,
        total_tokens=run.total_tokens,
        started_at=run.started_at,
        completed_at=run.completed_at,
        run_by=run.run_by,
        run_by_name=_resolve_user_name(session, run.run_by),
    )


def _to_pydantic_sim_orchestration_run(session: Session, run: SimulationRunModel) -> OrchestrationRun:
    return OrchestrationRun(
        id=run.id,
        kind="orchestration",
        scenario_id=run.scenario_id,
        process_id=run.process_id,
        status=run.status,
        steps=[RunStep.model_validate(s) for s in run.steps],
        node_runs=[NodeRun.model_validate(nr) for nr in run.node_runs],
        visited_path=run.visited_path,
        final_output=run.final_output,
        deviations=[TwinDeviation.model_validate(d) for d in run.deviations],
        graded_passed=run.graded_passed,
        evaluation=EvaluationResult.model_validate(run.evaluation) if run.evaluation else None,
        total_cost_usd=run.total_cost_usd,
        total_tokens=run.total_tokens,
        started_at=run.started_at,
        completed_at=run.completed_at,
        run_by=run.run_by,
        run_by_name=_resolve_user_name(session, run.run_by),
    )


def start_orchestration_run(
    session: Session, process_id: str, scenario_id: str, *, run_by: str | None = None
) -> OrchestrationRun:
    """US16.7: creates the run row in status="CREATED" with no node_runs
    yet and returns immediately -- the caller (app/api/orchestration.py)
    commits this and enqueues `execute_orchestration_run_background` as a
    FastAPI BackgroundTask, the same two-step handoff
    app/api/documents.py uses for Epic 1's ingestion pipeline. Epic 17:
    now a `kind="orchestration"` SimulationRunModel row, the same unified
    table `start_twin_run` writes to."""
    scenario_model = get_orchestration_scenario_model(session, process_id, scenario_id)
    run = SimulationRunModel(
        id=new_id("simrun"),
        kind="orchestration",
        scenario_id=scenario_model.id,
        process_id=process_id,
        status="CREATED",
        run_by=run_by,
    )
    session.add(run)
    session.flush()
    return _to_pydantic_sim_orchestration_run(session, run)


def get_orchestration_run(session: Session, process_id: str, run_id: str) -> OrchestrationRun:
    run = session.get(SimulationRunModel, run_id)
    if run is not None and run.kind == "orchestration" and run.process_id == process_id:
        return _to_pydantic_sim_orchestration_run(session, run)
    legacy = session.get(OrchestrationRunModel, run_id)
    if legacy is not None and legacy.process_id == process_id:
        return _to_pydantic_orchestration_run(session, legacy)
    raise NotFoundError("orchestration run", run_id)


def list_orchestration_runs(
    session: Session, process_id: str, scenario_id: str | None = None
) -> list[OrchestrationRun]:
    get_process(session, process_id)  # 404s if missing
    legacy_stmt = select(OrchestrationRunModel).where(OrchestrationRunModel.process_id == process_id)
    sim_stmt = select(SimulationRunModel).where(
        SimulationRunModel.kind == "orchestration", SimulationRunModel.process_id == process_id
    )
    if scenario_id is not None:
        legacy_stmt = legacy_stmt.where(OrchestrationRunModel.scenario_id == scenario_id)
        sim_stmt = sim_stmt.where(SimulationRunModel.scenario_id == scenario_id)
    runs = [_to_pydantic_orchestration_run(session, run) for run in session.scalars(legacy_stmt)] + [
        _to_pydantic_sim_orchestration_run(session, run) for run in session.scalars(sim_stmt)
    ]
    runs.sort(key=lambda run: run.started_at, reverse=True)
    return runs


async def _build_node_artifacts(
    llm: LLMClient, session: Session, process_id: str, flow_nodes: list[FlowNodeInfo]
) -> dict[str, NodeArtifact]:
    """Maps every flow node with a generated agent to a runnable
    `NodeArtifact`, reusing `_get_or_infer_tool_schemas`'s per-artifact
    cache so a consolidated group's artifact only has its tools inferred
    once even though it covers multiple node ids."""
    artifact_models = session.scalars(select(AgentArtifactModel).where(AgentArtifactModel.process_id == process_id))
    artifact_by_node_id: dict[str, AgentArtifactModel] = {}
    for artifact_model in artifact_models:
        for node_id in artifact_model.node_ids:
            artifact_by_node_id[node_id] = artifact_model

    tool_schema_cache: dict[str, dict[str, InferredToolSchema]] = {}
    node_artifacts: dict[str, NodeArtifact] = {}
    for node in flow_nodes:
        artifact_model = artifact_by_node_id.get(node.id)
        if artifact_model is None:
            continue
        if artifact_model.id not in tool_schema_cache:
            # US23.3: same precondition start_twin_run enforces, checked
            # once per distinct artifact before its tools are even inferred.
            _require_simulation_ready(session, artifact_model)
            tool_schema_cache[artifact_model.id] = await _get_or_infer_tool_schemas(llm, session, artifact_model)
        node_artifacts[node.id] = NodeArtifact(
            artifact_id=artifact_model.id,
            definition=AgentDefinition.model_validate(artifact_model.definition),
            tool_schemas=tool_schema_cache[artifact_model.id],
        )
    return node_artifacts


async def execute_orchestration_run_background(
    run_id: str, llm: LLMClient, settings: Settings | None = None, human_decision: str | None = None
) -> None:
    with _llm_route_for_run(run_id):
        await _execute_orchestration_run(run_id, llm, settings, human_decision)


async def _execute_orchestration_run(
    run_id: str, llm: LLMClient, settings: Settings | None = None, human_decision: str | None = None
) -> None:
    """US16.2-16.7: the FastAPI BackgroundTask entry point for a rehearsal
    run. Opens its own DB session/connection -- same reasoning as
    app/ingestion/pipeline.py's process_document, since a BackgroundTask
    can outlive the request-scoped session's teardown. Persists steps/
    node_runs/visited_path incrementally (via the orchestrator's on_step/
    on_node_run callbacks) so a concurrent GET on this run id shows live
    progress, then writes the final status/deviations/cost once the walk
    ends. Epic 17: also the resume entry point (US17.3) -- see
    execute_twin_run_background's docstring for why `resume_state` is read
    off the row rather than threaded through BackgroundTasks.add_task."""
    from app.db.session import get_session_factory

    settings = settings or get_settings()
    session = get_session_factory()()
    try:
        run_model = session.get(SimulationRunModel, run_id)
        if run_model is None:
            return
        scenario_model = session.get(OrchestrationScenarioModel, run_model.scenario_id)
        if scenario_model is None:
            run_model.status = "FAILED"
            run_model.deviations = [{"reason": "The scenario this run was started from no longer exists"}]
            run_model.completed_at = utcnow()
            session.commit()
            return

        resume_state = run_model.resume_state
        run_model.resume_state = None
        run_model.status = "RUNNING"
        session.commit()

        try:
            version = get_version(session, run_model.process_id, scenario_model.baseline_version_id)
            flow_nodes = extract_flow_nodes(version.xml)
            scenario = _to_pydantic_orchestration_scenario(session, scenario_model)
            environment_model = (
                session.get(TwinEnvironmentModel, scenario_model.environment_id)
                if scenario_model.environment_id
                else None
            )
            environment = (
                _to_pydantic_twin_environment(session, environment_model) if environment_model is not None else None
            )
            node_artifacts = await _build_node_artifacts(llm, session, run_model.process_id, flow_nodes)
            # Epic 22, US22.1: built once node artifacts are actually known
            # (unlike a twin run, they can't be resolved synchronously at
            # start_orchestration_run -- schema inference is an LLM call).
            distinct_artifact_ids = {na.artifact_id for na in node_artifacts.values()}
            artifact_models = [
                m for m in (session.get(AgentArtifactModel, aid) for aid in distinct_artifact_ids) if m is not None
            ]
            run_model.snapshot = build_run_snapshot(
                kind="orchestration", scenario_id=scenario_model.id, agent_artifacts=artifact_models,
                environment=environment_model,
            )
            session.commit()

            async def on_node_run(node_run: NodeRun) -> None:
                run_model.node_runs = [*run_model.node_runs, node_run.model_dump(mode="json")]
                run_model.visited_path = [*run_model.visited_path, node_run.node_id]
                session.commit()

            async def on_step(step: RunStep) -> None:
                run_model.steps = [*run_model.steps, step.model_dump(mode="json")]
                session.commit()
                session.refresh(run_model, attribute_names=["cancel_requested"])
                if run_model.cancel_requested:
                    raise RunCancelled()

            async def on_phase(status: RunStatus) -> None:
                run_model.status = status
                session.commit()

            outcome = await run_orchestration(
                llm,
                flow_nodes=flow_nodes,
                node_artifacts=node_artifacts,
                scenario=scenario,
                max_turns_per_node=settings.twin_max_loop_turns,
                max_total_steps=settings.orchestration_max_total_steps,
                environment=environment,
                resume_state=resume_state,
                human_decision=human_decision,
                on_node_run=on_node_run,
                on_step=on_step,
                on_phase=on_phase,
            )
        except (TwinServiceError, NotFoundError) as exc:
            run_model.status = "FAILED"
            run_model.deviations = [{"reason": str(exc)}]
            run_model.completed_at = utcnow()
            session.commit()
            return

        run_model.node_runs = [node_run.model_dump(mode="json") for node_run in outcome.node_runs]
        run_model.steps = [s.model_dump(mode="json") for s in outcome.steps]
        run_model.status = outcome.status
        run_model.visited_path = outcome.visited_path
        run_model.final_output = outcome.final_output
        run_model.deviations = [deviation.model_dump(mode="json") for deviation in outcome.deviations]
        run_model.graded_passed = outcome.graded_passed
        run_model.total_cost_usd = outcome.total_cost_usd
        run_model.total_tokens = outcome.total_tokens
        run_model.resume_state = outcome.resume_state
        if outcome.status != "WAITING_FOR_HUMAN":
            run_model.completed_at = utcnow()
            # Epic 19, US19.1: an orchestration run has no single artifact
            # to pull guardrails from -- default RuntimeGuardrails() is the
            # threshold reference for efficiency/latency/token_usage/cost,
            # same "no single owner" reasoning RegressionSuiteModel's
            # nullable agent_artifact_id already documents for this kind.
            run_model.evaluation = evaluate_run(
                steps=outcome.steps,
                final_output=outcome.final_output,
                deviations=outcome.deviations,
                graded_passed=outcome.graded_passed,
                status=outcome.status,
                guardrails=RuntimeGuardrails(),
                total_cost_usd=outcome.total_cost_usd,
                total_tokens=outcome.total_tokens,
                started_at=run_model.started_at,
                completed_at=run_model.completed_at,
            ).model_dump(mode="json")
        session.commit()
    finally:
        session.close()


# -- evaluation, regression suites & comparison (Epic 19) ---------------------


def get_any_run(session: Session, process_id: str, run_id: str) -> TwinRun | OrchestrationRun:
    """US19.4/US19.3: resolves a run id regardless of kind -- tries twin
    first, then orchestration, the same ambiguity every other cross-kind
    run id already has to handle (see `_get_simulation_run`'s `kind`
    parameter). Raises NotFoundError (from the orchestration lookup) if
    neither matches."""
    try:
        return get_twin_run(session, process_id, run_id)
    except NotFoundError:
        return get_orchestration_run(session, process_id, run_id)


def _to_pydantic_regression_suite(session: Session, suite: RegressionSuiteModel) -> RegressionSuite:
    return RegressionSuite(
        id=suite.id,
        process_id=suite.process_id,
        agent_artifact_id=suite.agent_artifact_id,
        kind=suite.kind,
        name=suite.name,
        scenario_ids=suite.scenario_ids,
        created_at=suite.created_at,
        created_by=suite.created_by,
        created_by_name=_resolve_user_name(session, suite.created_by),
    )


def create_regression_suite(
    session: Session, process_id: str, data: RegressionSuiteCreate, *, created_by: str | None = None
) -> RegressionSuite:
    """US19.2. `scenario_ids` are validated by existing, kind-matching
    lookups (`get_twin_scenario`/`get_orchestration_scenario_model`) so a
    suite can never reference a scenario that doesn't exist or belongs to
    another process."""
    get_process(session, process_id)  # 404s if missing
    if data.kind == "twin":
        if data.agent_artifact_id is None:
            raise TwinServiceError("A twin regression suite must declare agent_artifact_id")
        get_agent_artifact(session, process_id, data.agent_artifact_id)  # 404s if missing/wrong process
        for scenario_id in data.scenario_ids:
            get_twin_scenario(session, process_id, scenario_id)
    else:
        for scenario_id in data.scenario_ids:
            get_orchestration_scenario_model(session, process_id, scenario_id)

    suite = RegressionSuiteModel(
        id=new_id("regsuite"),
        process_id=process_id,
        agent_artifact_id=data.agent_artifact_id,
        kind=data.kind,
        name=data.name,
        scenario_ids=list(data.scenario_ids),
        created_by=created_by,
    )
    session.add(suite)
    session.flush()
    return _to_pydantic_regression_suite(session, suite)


def list_regression_suites(session: Session, process_id: str) -> list[RegressionSuite]:
    get_process(session, process_id)  # 404s if missing
    suites = session.scalars(
        select(RegressionSuiteModel)
        .where(RegressionSuiteModel.process_id == process_id)
        .order_by(RegressionSuiteModel.created_at)
    )
    return [_to_pydantic_regression_suite(session, suite) for suite in suites]


def get_regression_suite_model(session: Session, process_id: str, suite_id: str) -> RegressionSuiteModel:
    suite = session.get(RegressionSuiteModel, suite_id)
    if suite is None or suite.process_id != process_id:
        raise NotFoundError("regression suite", suite_id)
    return suite


def delete_regression_suite(session: Session, process_id: str, suite_id: str) -> None:
    suite = get_regression_suite_model(session, process_id, suite_id)
    session.delete(suite)
    session.flush()


def start_regression_suite_run(
    session: Session, process_id: str, suite_id: str, *, run_by: str | None = None
) -> tuple[RegressionSuiteRun, list[str], str]:
    """US19.3: starts one SimulationRunModel per scenario in the suite
    (through the existing `start_twin_run`/`start_orchestration_run` -- no
    duplicated execution path), wraps the resulting run ids in a
    RegressionSuiteRunModel, and returns (suite_run, run_ids, kind) so the
    caller (app/api/evaluation.py) can enqueue the right background task
    (execute_twin_run_background vs execute_orchestration_run_background)
    per run, the same two-step create-then-enqueue handoff every other run
    start already uses."""
    suite = get_regression_suite_model(session, process_id, suite_id)
    run_ids: list[str] = []
    for scenario_id in suite.scenario_ids:
        if suite.kind == "twin":
            run = start_twin_run(session, process_id, scenario_id, run_by=run_by)
        else:
            run = start_orchestration_run(session, process_id, scenario_id, run_by=run_by)
        run_ids.append(run.id)

    suite_run = RegressionSuiteRunModel(
        id=new_id("regrun"), suite_id=suite.id, run_ids=run_ids, status="running", started_by=run_by
    )
    session.add(suite_run)
    session.flush()
    return _to_pydantic_regression_suite_run(session, suite_run), run_ids, suite.kind


def _to_pydantic_regression_suite_run(session: Session, suite_run: RegressionSuiteRunModel) -> RegressionSuiteRun:
    results: list[RegressionSuiteScenarioResult] = []
    all_terminal = True
    for run_id in suite_run.run_ids:
        run_model = session.get(SimulationRunModel, run_id)
        if run_model is None:
            continue
        scenario_id = run_model.scenario_id
        if run_model.completed_at is None:
            all_terminal = False
        results.append(
            RegressionSuiteScenarioResult(
                scenario_id=scenario_id, run_id=run_id, status=run_model.status, graded_passed=run_model.graded_passed
            )
        )
    if all_terminal and suite_run.status != "completed":
        suite_run.status = "completed"
        suite_run.completed_at = utcnow()
        session.flush()

    pass_count = sum(1 for r in results if r.graded_passed)
    return RegressionSuiteRun(
        id=suite_run.id,
        suite_id=suite_run.suite_id,
        status=suite_run.status,  # type: ignore[arg-type]
        run_ids=suite_run.run_ids,
        pass_count=pass_count,
        total_count=len(results),
        scenario_results=results,
        started_at=suite_run.started_at,
        completed_at=suite_run.completed_at,
    )


def get_regression_suite_run(session: Session, process_id: str, suite_run_id: str) -> RegressionSuiteRun:
    suite_run = session.get(RegressionSuiteRunModel, suite_run_id)
    if suite_run is None:
        raise NotFoundError("regression suite run", suite_run_id)
    get_regression_suite_model(session, process_id, suite_run.suite_id)  # 404s if wrong process
    return _to_pydantic_regression_suite_run(session, suite_run)


def compare_runs(session: Session, process_id: str, run_a_id: str, run_b_id: str) -> RunComparison:
    run_a = get_any_run(session, process_id, run_a_id)
    run_b = get_any_run(session, process_id, run_b_id)
    from app.twin.comparison import compare_runs as _compare_runs

    return _compare_runs(run_a, run_b)


# -- reproducibility snapshots (Epic 22) ---------------------------------------


def get_run_snapshot(session: Session, process_id: str, run_id: str) -> RunSnapshotView:
    """US22.1/US22.2: reads the snapshot recorded at run time verbatim --
    no live re-resolution of any versioned component, since the whole
    point is showing what a *past* run used even after later edits (see
    this function's schema's module docstring)."""
    run = session.get(SimulationRunModel, run_id)
    if run is not None and run.process_id == process_id:
        return RunSnapshotView(run_id=run_id, snapshot=run.snapshot)
    # Legacy pre-Epic-17 rows (TwinRunModel/OrchestrationRunModel) never
    # recorded a snapshot -- confirm the run exists at all (404s otherwise
    # via the existing merged lookup) and report snapshot=None honestly
    # rather than fabricating one after the fact.
    get_any_run(session, process_id, run_id)
    return RunSnapshotView(run_id=run_id, snapshot=None)


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
        citations=[Citation.model_validate(c) for c in finding.citations or []],
        context_pack_id=finding.context_pack_id,
    )


def add_gap_finding(
    session: Session,
    process_id: str,
    *,
    kind: str,
    question: str,
    target_element_ids: list[str],
    options: list[GapFindingOption],
    citations: list[Citation] | None = None,
    context_pack_id: str | None = None,
) -> GapFinding:
    get_process(session, process_id)  # 404s if missing
    finding = GapFindingModel(
        id=new_id("gap"),
        process_id=process_id,
        kind=kind,
        question=question,
        target_element_ids=target_element_ids,
        options=[option.model_dump() for option in options],
        citations=[c.model_dump() for c in citations or []],
        context_pack_id=context_pack_id,
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
