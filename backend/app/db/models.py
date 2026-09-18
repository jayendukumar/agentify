"""SQLAlchemy ORM models for Epic 2 (Business Process Knowledge Store) --
processes, documents, the extracted process schema (actors/elements/
source_refs/flows), embeddings, a lightweight change log -- plus Epic 3's
draft BPMN (BPMNDraftModel), added once BPMN generation was real to
generate something.

Still deliberately does NOT model chat messages, finalized diagram
versions, or the blueprint overlay -- those belong to Epics 5/6/7/8, still
unimplemented (501 stubs), and stay in the in-memory store (app/store.py)
until their own stories are built. Persisting placeholder data for
features that don't exist yet would be scope creep.
"""

from __future__ import annotations

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import ForeignKey, Index, String, Text
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
