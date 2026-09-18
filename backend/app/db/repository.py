"""DB-backed persistence for what Epic 2 owns (processes, documents, the
extracted process schema, embeddings) plus Epic 3's draft BPMN. Chat,
finalized versions, and the blueprint overlay stay in app/store.py's
in-memory store until their own epics are implemented -- see
app/db/models.py's module docstring.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ids import new_id, utcnow
from app.ingestion.embeddings import embed_texts
from app.ingestion.extractors import ExtractedBlock
from app.ingestion.merge import merge_process_schemas
from app.schemas.common import Actor, ProcessElement, ProcessFlow, ProcessSchema, SourceRef
from app.schemas.documents import IngestionStatus
from app.store import NotFoundError

from .models import (
    ActorModel,
    BPMNDraftModel,
    DocumentEmbeddingModel,
    DocumentModel,
    ProcessElementModel,
    ProcessFlowModel,
    ProcessModel,
    ProcessSchemaChangeModel,
    SourceRefModel,
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


def update_document_status(
    session: Session, process_id: str, document_id: str, status: IngestionStatus, error_message: str | None = None
) -> None:
    document = get_document(session, process_id, document_id)
    document.status = status
    document.error_message = error_message
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
