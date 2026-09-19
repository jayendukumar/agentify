"""SQLAlchemy ORM models for Epic 2 (Business Process Knowledge Store) --
processes, documents, the extracted process schema (actors/elements/
source_refs/flows), embeddings, a lightweight change log -- plus Epic 3's
draft BPMN (BPMNDraftModel), Epic 5's chat messages (ChatMessageModel),
Epic 6's finalized versions (VersionModel), Epic 7's blueprint overlay
(BlueprintOverlayModel), and Epic 11's gap findings (GapFindingModel),
each promoted out of the in-memory store (app/store.py) once its own epic
made the data real. app/store.py now only defines NotFoundError --
nothing left to hold in memory.
"""

from __future__ import annotations

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import ForeignKey, Index, JSON, String, Text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func

# all-MiniLM-L6-v2 (the default embedding_model_name in Settings) outputs
# 384-dim vectors -- see planning/document-ingestion-strategy.md.
EMBEDDING_DIM = 384


class Base(DeclarativeBase):
    pass


class ProcessModel(Base):
    __tablename__ = "processes"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
    # Epic 11: set every time a gap-analysis run completes (0 or more
    # findings -- "completed" is the signal, not "found something"). Null
    # means gap analysis has never successfully run for this process --
    # Finalize (app/api/versions.py) treats that as a hard block distinct
    # from "there are open findings", since a never-run process was never
    # actually checked at all.
    gap_analysis_completed_at: Mapped[datetime | None] = mapped_column(nullable=True)

    documents: Mapped[list["DocumentModel"]] = relationship(back_populates="process", cascade="all, delete-orphan")
    actors: Mapped[list["ActorModel"]] = relationship(back_populates="process", cascade="all, delete-orphan")
    elements: Mapped[list["ProcessElementModel"]] = relationship(
        back_populates="process", cascade="all, delete-orphan"
    )
    flows: Mapped[list["ProcessFlowModel"]] = relationship(back_populates="process", cascade="all, delete-orphan")
    schema_changes: Mapped[list["ProcessSchemaChangeModel"]] = relationship(
        back_populates="process", cascade="all, delete-orphan"
    )
    draft_bpmn: Mapped["BPMNDraftModel | None"] = relationship(
        back_populates="process", cascade="all, delete-orphan", uselist=False
    )
    chat_messages: Mapped[list["ChatMessageModel"]] = relationship(
        back_populates="process", cascade="all, delete-orphan"
    )
    versions: Mapped[list["VersionModel"]] = relationship(back_populates="process", cascade="all, delete-orphan")
    blueprint_overlay: Mapped["BlueprintOverlayModel | None"] = relationship(
        back_populates="process", cascade="all, delete-orphan", uselist=False
    )
    gap_findings: Mapped[list["GapFindingModel"]] = relationship(
        back_populates="process", cascade="all, delete-orphan"
    )


class DocumentModel(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    process_id: Mapped[str] = mapped_column(ForeignKey("processes.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String, nullable=False)
    content_type: Mapped[str] = mapped_column(String, nullable=False)
    size_bytes: Mapped[int] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="queued")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    process: Mapped[ProcessModel] = relationship(back_populates="documents")
    embeddings: Mapped[list["DocumentEmbeddingModel"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class ActorModel(Base):
    __tablename__ = "actors"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    process_id: Mapped[str] = mapped_column(ForeignKey("processes.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False)

    process: Mapped[ProcessModel] = relationship(back_populates="actors")


class ProcessElementModel(Base):
    __tablename__ = "process_elements"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    process_id: Mapped[str] = mapped_column(ForeignKey("processes.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String, nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("actors.id", ondelete="SET NULL"), nullable=True)
    inputs: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    outputs: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    systems_touched: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    confidence: Mapped[str] = mapped_column(String, nullable=False, default="medium")

    process: Mapped[ProcessModel] = relationship(back_populates="elements")
    actor: Mapped[ActorModel | None] = relationship()
    source_refs: Mapped[list["SourceRefModel"]] = relationship(
        back_populates="element", cascade="all, delete-orphan"
    )


class SourceRefModel(Base):
    __tablename__ = "source_refs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    element_id: Mapped[str] = mapped_column(ForeignKey("process_elements.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    location: Mapped[str] = mapped_column(String, nullable=False)
    excerpt: Mapped[str] = mapped_column(Text, nullable=False)

    element: Mapped[ProcessElementModel] = relationship(back_populates="source_refs")


class ProcessFlowModel(Base):
    __tablename__ = "process_flows"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    process_id: Mapped[str] = mapped_column(ForeignKey("processes.id", ondelete="CASCADE"), index=True)
    from_element_id: Mapped[str] = mapped_column(ForeignKey("process_elements.id", ondelete="CASCADE"))
    to_element_id: Mapped[str] = mapped_column(ForeignKey("process_elements.id", ondelete="CASCADE"))
    condition: Mapped[str | None] = mapped_column(Text, nullable=True)

    process: Mapped[ProcessModel] = relationship(back_populates="flows")


class DocumentEmbeddingModel(Base):
    """US2.3: one row per deterministic-extraction unit (a paragraph, a
    table, a Visio shape/connector text) -- NOT one row per document, and
    NOT the same grain as the LLM-structured elements above. See
    planning/document-ingestion-strategy.md for why embeddings are a
    separate, finer-grained concern from extraction.
    """

    __tablename__ = "document_embeddings"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    location: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM))

    document: Mapped[DocumentModel] = relationship(back_populates="embeddings")

    __table_args__ = (
        Index(
            "ix_document_embeddings_embedding_cosine",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class ProcessSchemaChangeModel(Base):
    """US2.6: a lightweight append-only log of each merge into a process's
    schema -- not full event-sourcing/temporal versioning, just enough to
    answer "how did my understanding of this process change over time"."""

    __tablename__ = "process_schema_changes"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    process_id: Mapped[str] = mapped_column(ForeignKey("processes.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[str | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"), nullable=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    process: Mapped[ProcessModel] = relationship(back_populates="schema_changes")


class BPMNDraftModel(Base):
    """US3.3: the current as-is BPMN diagram for a process -- one row per
    process (regenerating or manually editing replaces it in place; real
    history of prior drafts is Epic 6's finalized-versions story, not this).
    """

    __tablename__ = "bpmn_drafts"

    process_id: Mapped[str] = mapped_column(ForeignKey("processes.id", ondelete="CASCADE"), primary_key=True)
    xml: Mapped[str] = mapped_column(Text, nullable=False)
    low_confidence_element_ids: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    generated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    process: Mapped[ProcessModel] = relationship(back_populates="draft_bpmn")


class ChatMessageModel(Base):
    """US5.5: one row per chat turn -- the audit trail of chat-driven
    diagram edits (what was asked, what was proposed, whether/when it was
    applied or declined). `proposed_diff` stores the DiagramDiff (see
    app/schemas/chat.py) as JSON; null for a plain explain/clarify reply
    that never proposed a change.
    """

    __tablename__ = "chat_messages"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    process_id: Mapped[str] = mapped_column(ForeignKey("processes.id", ondelete="CASCADE"), index=True)
    request_text: Mapped[str] = mapped_column(Text, nullable=False)
    selected_element_id: Mapped[str | None] = mapped_column(String, nullable=True)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    reply_text: Mapped[str] = mapped_column(Text, nullable=False)
    proposed_diff: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    needs_confirmation: Mapped[bool] = mapped_column(nullable=False, default=False)
    applied: Mapped[bool] = mapped_column(nullable=False, default=False)
    declined: Mapped[bool] = mapped_column(nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    decided_at: Mapped[datetime | None] = mapped_column(nullable=True)

    process: Mapped[ProcessModel] = relationship(back_populates="chat_messages")


class VersionModel(Base):
    """US6.1: an immutable snapshot of a finalized BPMN diagram -- one row
    per finalize, kept forever (restore reads it, never mutates it). Only
    the XML is snapshotted, not a copy of the process schema tables --
    app/api/versions.py's restore_version is XML-only on purpose, since
    the draft XML can already legitimately diverge from the schema tables
    (a manual canvas edit via PUT /bpmn never touches them either).
    """

    __tablename__ = "versions"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    process_id: Mapped[str] = mapped_column(ForeignKey("processes.id", ondelete="CASCADE"), index=True)
    label: Mapped[str | None] = mapped_column(String, nullable=True)
    xml: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    process: Mapped[ProcessModel] = relationship(back_populates="versions")


class BlueprintOverlayModel(Base):
    """US7.6: the agentic blueprint overlay -- one row per process,
    replaced in place on regenerate (mirrors BPMNDraftModel; US7.7's
    "re-run after baseline changes" is just calling generate again, no
    separate history needed here the way finalized versions need one).
    `nodes` stores the full list of per-node results (BlueprintNodeResult,
    including any nested agent_spec) as a single JSON blob rather than
    normalized per-node/per-agent-spec tables -- this is a generated,
    whole-diagram artifact that's always read and regenerated as a unit,
    and the only per-field mutation (override_blueprint_node) already
    replaces one node's dict wholesale, so normalizing would add
    relational complexity with no real query benefit.
    """

    __tablename__ = "blueprint_overlays"

    process_id: Mapped[str] = mapped_column(ForeignKey("processes.id", ondelete="CASCADE"), primary_key=True)
    baseline_version_id: Mapped[str] = mapped_column(String, nullable=False)
    nodes: Mapped[list[dict]] = mapped_column(JSON, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    process: Mapped[ProcessModel] = relationship(back_populates="blueprint_overlay")


class GapFindingModel(Base):
    """Epic 11: one row per detected process gap (structural or
    cross-document), and the permanent audit trail of what the user did
    about it -- never mutated back to "open" once resolved/dismissed, same
    lifecycle shape as ChatMessageModel. `options` stores the LLM-proposed
    resolutions as JSON (each a {label, diff} pair, `diff` shaped like
    DiagramDiff -- see app/schemas/chat.py -- and applied through the same
    apply_diagram_diff/apply_diff_and_persist path chat-ops uses, so
    resolving a finding is not a separate apply mechanism). "Dismiss" is
    not one of `options` -- it's a status a finding can always move to
    regardless of what the LLM proposed, handled by
    mark_gap_finding_dismissed rather than an option index.
    """

    __tablename__ = "gap_findings"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    process_id: Mapped[str] = mapped_column(ForeignKey("processes.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    target_element_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    options: Mapped[list[dict]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="open")
    chosen_option_label: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    decided_at: Mapped[datetime | None] = mapped_column(nullable=True)

    process: Mapped[ProcessModel] = relationship(back_populates="gap_findings")
