"""SQLAlchemy ORM models for Epic 2 (Business Process Knowledge Store) --
processes, documents, the extracted process schema (actors/elements/
source_refs/flows), embeddings, a lightweight change log -- plus Epic 3's
draft BPMN (BPMNDraftModel), Epic 5's chat messages (ChatMessageModel),
Epic 6's finalized versions (VersionModel), Epic 7's blueprint overlay
(BlueprintOverlayModel), Epic 11's gap findings (GapFindingModel), Epic
12's generated agent artifacts (AgentArtifactModel), Epic 13's local
registry entries (RegistryEntryModel), Epic 14's digital twin scenarios/
runs/baselines (TwinToolSchemaModel/TwinScenarioModel/TwinRunModel/
TwinBaselineModel), Epic 15's publish history (AgentPublicationModel),
Epic 16's orchestration rehearsal scenarios/runs (OrchestrationScenarioModel/
OrchestrationRunModel), Epic 17's unified run store (SimulationRunModel --
additive, see its own docstring; TwinRunModel/OrchestrationRunModel are
kept for historical rows), Epic 19's regression suites
(RegressionSuiteModel/RegressionSuiteRunModel), Epic 21's digital twin
environments (TwinEnvironmentModel), and Epic 9/10's users/
sessions (UserModel/SessionModel), each promoted out of
the in-memory store (app/store.py) once its own epic made the data real.
app/store.py now only defines NotFoundError -- nothing left to hold in
memory.
"""

from __future__ import annotations

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import BigInteger, ForeignKey, Index, JSON, String, Text, UniqueConstraint
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
    # Epic 27, US27.2: which part of the organization this process belongs
    # to. Null business unit + empty jurisdictions = organization-wide
    # scope, which is what every pre-existing process gets.
    business_unit_id: Mapped[str | None] = mapped_column(
        ForeignKey("context_entities.id", ondelete="SET NULL"), nullable=True
    )
    jurisdictions: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list, server_default="[]")
    process_type: Mapped[str | None] = mapped_column(String, nullable=True)
    data_classes: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list, server_default="[]")

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
    agent_artifacts: Mapped[list["AgentArtifactModel"]] = relationship(
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
    process_definition_confidence: Mapped[int | None] = mapped_column(nullable=True)
    validation_message: Mapped[str | None] = mapped_column(Text, nullable=True)
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
    # Epic 9/10, US9.9: who finalized this version -- nullable since older
    # rows (and any future non-interactive finalize path) may have none.
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

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
    # Epic 28, US28.2: the Context Pack this overlay was evaluated with.
    context_pack_id: Mapped[str | None] = mapped_column(String, nullable=True)

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
    # Epic 28, US28.3/US28.4: policy-grounded findings cite the pack entries
    # behind "why we're asking".
    citations: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list, server_default="[]")
    context_pack_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # Epic 9/10, US9.9: who resolved/dismissed this finding.
    decided_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    process: Mapped[ProcessModel] = relationship(back_populates="gap_findings")


class UserModel(Base):
    """Epic 9/10, US9.9/US10.4: a lightweight named-user model -- no
    password, "log in" means selecting a name and role. Each login updates
    the user's role, which also applies to their existing sessions. This
    role selector is for trusted local-team use (see app/api/auth.py).
    Deliberately not a dead end: a real SSO login would
    only replace this table's population + app/api/auth.py's login
    endpoint, not the SessionModel/CurrentUserDep/EditorDep plumbing every
    other route already depends on.
    """

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class SessionModel(Base):
    """`id` is itself the opaque session token (a secrets.token_urlsafe(32)
    value, minted in app/api/auth.py) stored in an httponly cookie -- not
    a separate id+token pair, since nothing ever needs to look up a
    session except by presenting that exact token."""

    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(nullable=False)

    user: Mapped[UserModel] = relationship()


class AgentArtifactModel(Base):
    """Epic 12: a generated, portable agent definition for one blueprint
    node or a consolidated group of nodes (US7.5's grouping). One row per
    distinct *group* per process, keyed by `group_key` (the group's
    node_ids, sorted and pipe-joined) rather than by whichever node the
    user happened to click "Generate" on -- clicking from any node already
    in a consolidated group updates the same artifact instead of creating
    a duplicate. `primary_node_id` is just the node the definition's
    content (system prompt, I/O schema) was actually built from, kept for
    display/linking back to a specific canvas element.

    `source_baseline_version_id`/`source_node_snapshot` capture the exact
    blueprint state this was generated from. Staleness (US12.4) is
    deliberately *not* a stored, separately-mutable column -- it's computed
    on read (see repository.py) by comparing this snapshot against the
    process's current BlueprintOverlayModel, the same "derive, don't
    duplicate and hope it stays in sync" choice made elsewhere in this
    codebase (e.g. blueprint stats computed client-side in BlueprintPage
    rather than stored).
    """

    __tablename__ = "agent_artifacts"
    __table_args__ = (UniqueConstraint("process_id", "group_key", name="uq_agent_artifacts_process_group"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    process_id: Mapped[str] = mapped_column(ForeignKey("processes.id", ondelete="CASCADE"), index=True)
    group_key: Mapped[str] = mapped_column(String, nullable=False)
    node_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    primary_node_id: Mapped[str] = mapped_column(String, nullable=False)
    # Epic 19, US19.5: bumped by repository.generate_agent_artifact on every
    # regenerate of an existing row -- see AgentArtifact schema's docstring.
    version: Mapped[int] = mapped_column(nullable=False, default=1, server_default="1")
    definition: Mapped[dict] = mapped_column(JSON, nullable=False)
    source_baseline_version_id: Mapped[str] = mapped_column(String, nullable=False)
    source_node_snapshot: Mapped[list[dict]] = mapped_column(JSON, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
    # Epic 9/10, US9.9: who triggered (re)generation.
    generated_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    # Epic 23, US23.1: the only two lifecycle states nothing can derive --
    # everything else (DRAFT..EVALUATED, DEPLOYED) is computed on read from
    # the checklist/run history/publication status. See AgentArtifact
    # schema's LifecycleStatus docstring.
    marked_release_candidate_at: Mapped[datetime | None] = mapped_column(nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(nullable=True)
    approved_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    # Epic 28, US28.2/US28.6: the Context Pack this definition was built
    # from (the blueprint overlay's pack at generation time).
    context_pack_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # Epic 35, US35.7: statistical release criteria (app/batches/results.py's
    # ReleaseCriteria). None = no statistical gate for this agent.
    release_criteria: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    process: Mapped[ProcessModel] = relationship(back_populates="agent_artifacts")


class RegistryEntryModel(Base):
    """Epic 13: one row per agent definition pushed into the *local*
    reference registry connector (app/registry/local.py) -- other
    connector types (a real vendor registry, once one is chosen) would
    store entries on their own side entirely, never in this table.

    Deliberately has no relationship/cascade back to ProcessModel:
    RegistryConnector.push (app/registry/base.py) is a generic interface
    a real external registry would implement too, where "the source
    process was deleted locally" has no meaning -- source_process_id/
    source_node_ids here are informational provenance only (SET NULL on
    delete, not CASCADE), not an ownership relationship.
    """

    __tablename__ = "registry_entries"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    registry_name: Mapped[str] = mapped_column(String, index=True, nullable=False)
    agent_name: Mapped[str] = mapped_column(String, nullable=False)
    tags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    definition: Mapped[dict] = mapped_column(JSON, nullable=False)
    source_process_id: Mapped[str | None] = mapped_column(
        ForeignKey("processes.id", ondelete="SET NULL"), nullable=True
    )
    source_node_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    pushed_at: Mapped[datetime] = mapped_column(server_default=func.now())
    pushed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class TwinToolSchemaModel(Base):
    """Epic 14 (core slice): the synthesized, callable schema for every
    `tools_systems_needed` entry of one agent artifact -- one row per
    artifact, replaced wholesale on regenerate (same "replace, don't
    version" convention as BlueprintOverlayModel/BPMNDraftModel). This
    resolves the gap left by Epic 12, where `tools_systems_needed` is
    plain strings with nothing callable behind them. `schemas` is
    `{system_name: {tool_name, description, parameters, response_shape_description}}`
    (see app/schemas/twin.py's InferredToolSchema) -- both twin system
    simulation modes (Proxy, Static) use this same inferred schema, only
    how a call's *response* is produced differs by mode.
    """

    __tablename__ = "twin_tool_schemas"

    agent_artifact_id: Mapped[str] = mapped_column(
        ForeignKey("agent_artifacts.id", ondelete="CASCADE"), primary_key=True
    )
    schemas: Mapped[dict] = mapped_column(JSON, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class TwinBaselineModel(Base):
    """Epic 14, US14.5: an optional, explicitly-manual as-is baseline for
    one agent artifact -- one row per artifact, replaced wholesale on
    update (same convention as TwinToolSchemaModel). Both value columns
    are nullable and independent; there is no automatic extraction of
    this data anywhere in the system, so its presence always means a
    human recorded it, never something the system inferred or fabricated.
    """

    __tablename__ = "twin_baselines"

    agent_artifact_id: Mapped[str] = mapped_column(
        ForeignKey("agent_artifacts.id", ondelete="CASCADE"), primary_key=True
    )
    typical_time_seconds: Mapped[float | None] = mapped_column(nullable=True)
    error_rate: Mapped[float | None] = mapped_column(nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
    recorded_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class TwinEnvironmentModel(Base):
    """Epic 21, US21.1/US21.4: a named, versioned, reusable bundle of
    systems/datasets/personas/events for a process, plus its own typed
    fault-injection catalog per system (US21.2, `faults`). One row per
    environment, version bumped in place on update (see the schema's own
    docstring for why this doesn't keep full row history)."""

    __tablename__ = "twin_environments"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    process_id: Mapped[str] = mapped_column(ForeignKey("processes.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    version: Mapped[int] = mapped_column(nullable=False, default=1, server_default="1")
    systems: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    datasets: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    personas: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    events: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    faults: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # Epic 36, US36.1: system name -> SimulatedSystemData (sample records).
    system_data: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class TwinScenarioModel(Base):
    """Epic 14 (core slice), US14.1: one defined test scenario for an
    agent artifact -- start-event `inputs`, per-system simulation
    overrides (`system_stubs`), how to resolve the agent's human
    checkpoint if it has one (`human_checkpoint_config`), and the expected
    tool-call/checkpoint path plus expected final output used to grade a
    run (US14.3). Epic 21, US21.3: `environment_id`/`environment_version`
    optionally pin this scenario to a reusable TwinEnvironmentModel whose
    fault catalog intercepts calls before `system_stubs` resolution --
    additive and nullable, so every scenario created before this epic
    keeps working unchanged."""

    __tablename__ = "twin_scenarios"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    agent_artifact_id: Mapped[str] = mapped_column(
        ForeignKey("agent_artifacts.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    inputs: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    system_stubs: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    human_checkpoint_config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    expected_steps: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    expected_outputs: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    environment_id: Mapped[str | None] = mapped_column(
        ForeignKey("twin_environments.id", ondelete="SET NULL"), nullable=True
    )
    environment_version: Mapped[int | None] = mapped_column(nullable=True)
    # Epic 32, US32.2: a data-driven scenario runs once per row of this
    # dataset, each row graded against its own labels; a row's decision
    # label is compared with the agent output field `decision_field`.
    dataset_id: Mapped[str | None] = mapped_column(String, nullable=True)
    decision_field: Mapped[str] = mapped_column(String, nullable=False, default="decision", server_default="decision")
    # Epic 24, US24.4/US24.6: additive -- per-scenario faults (offered
    # before the environment's), the grading match mode, and the
    # business-readable story the guided builder authored it from.
    faults: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict, server_default="{}")
    step_match: Mapped[str] = mapped_column(String, nullable=False, default="exact", server_default="exact")
    story: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class AgentPublicationModel(Base):
    """Epic 15, US15.1/US15.4: one push of an agent artifact's definition to
    a registry -- kept forever, like VersionModel/TwinRunModel, so
    "republish as a new version" (US15.4) means *inserting* a new row, never
    mutating a prior one. `version` is deliberately not a stored column --
    it's the row's 1-based rank among this artifact's publications ordered
    by `published_at`, computed on read the same way AgentArtifactModel's
    staleness is (see repository._to_pydantic_agent_publication).

    `source_artifact_generated_at` snapshots AgentArtifactModel.generated_at
    at push time -- comparing it against the artifact's *current*
    generated_at on read is how "needs republish" (the artifact was
    regenerated since this was published) is derived, without a second
    mutable staleness flag to keep in sync.

    `status` starts "published" and can move to "deployed" -- see this
    epic's Notes on why "deployed" is a manual/synced status field only,
    not real deployment. Deliberately monotonic across an artifact's whole
    publication history (repository.get_agent_publish_status): once any
    publication is marked deployed, the artifact's lifecycle_status reads
    "deployed" even if a newer, not-yet-deployed version was since
    published -- a fact about how far this agent has ever gotten, not a
    per-version flag that can regress.
    """

    __tablename__ = "agent_publications"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    agent_artifact_id: Mapped[str] = mapped_column(
        ForeignKey("agent_artifacts.id", ondelete="CASCADE"), index=True
    )
    registry_name: Mapped[str] = mapped_column(String, nullable=False)
    registry_entry_id: Mapped[str] = mapped_column(String, nullable=False)
    source_artifact_generated_at: Mapped[datetime] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="published")
    published_at: Mapped[datetime] = mapped_column(server_default=func.now())
    published_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    deployed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    deployed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class TwinRunModel(Base):
    """Epic 14 (core slice), US14.2/US14.3/US14.4: one execution of a
    scenario against the real LLM (every tool/system call stubbed per the
    scenario's config, never live) -- kept forever, like VersionModel,
    since a run is evidence of what actually happened, not a value that's
    ever mutated in place. `agent_artifact_id` is denormalized from
    `scenario.agent_artifact_id` so artifact-level aggregate queries
    (US14.4's pass rate / cost) don't need a join through twin_scenarios.
    """

    __tablename__ = "twin_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    scenario_id: Mapped[str] = mapped_column(ForeignKey("twin_scenarios.id", ondelete="CASCADE"), index=True)
    agent_artifact_id: Mapped[str] = mapped_column(
        ForeignKey("agent_artifacts.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String, nullable=False)
    trace: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    final_output: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    deviations: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    total_cost_usd: Mapped[float | None] = mapped_column(nullable=True)
    total_tokens: Mapped[int] = mapped_column(nullable=False, default=0)
    turns_used: Mapped[int] = mapped_column(nullable=False, default=0)
    started_at: Mapped[datetime] = mapped_column(server_default=func.now())
    completed_at: Mapped[datetime] = mapped_column(server_default=func.now())
    run_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class OrchestrationScenarioModel(Base):
    """Epic 16, US16.1: one defined process-level rehearsal scenario --
    start-event inputs, plus config keyed by node_id (gateway decisions,
    manual-node resolution, per-node system stubs/checkpoints, per-node
    data-mapping mode override) needed to walk the whole blueprint's agents
    together. `baseline_version_id` pins the scenario to the exact
    finalized diagram it was authored against, same reasoning as
    AgentArtifactModel's `source_baseline_version_id`."""

    __tablename__ = "orchestration_scenarios"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    process_id: Mapped[str] = mapped_column(ForeignKey("processes.id", ondelete="CASCADE"), index=True)
    baseline_version_id: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    inputs: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    gateway_decisions: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    manual_node_config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    system_stubs: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    human_checkpoint_config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    data_mapping_mode: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    expected_path: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    expected_final_output: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # Epic 21, US21.3: one environment (its fault catalog) applies across
    # the whole rehearsal walk, not per-node -- see TwinScenarioModel's
    # docstring for the same additive/nullable reasoning.
    environment_id: Mapped[str | None] = mapped_column(
        ForeignKey("twin_environments.id", ondelete="SET NULL"), nullable=True
    )
    environment_version: Mapped[int | None] = mapped_column(nullable=True)
    # Epic 36, US36.2/US36.3: {"handoff", "default_gateway_routing",
    # "gateway_routing"} -- one JSON column rather than three, since they're
    # only ever read together by the walk.
    routing: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class OrchestrationRunModel(Base):
    """Epic 16, US16.2-16.7: one execution of a rehearsal scenario across
    every node the walk reaches. Kept forever once finished, like
    TwinRunModel -- but, deliberately unlike TwinRunModel, this row *is*
    mutated while `status == "running"` (node_runs/visited_path/etc.
    appended incrementally as each node completes) so the frontend can
    poll it for live progress (US16.7) the same way Epic 1's document
    processing status is polled -- see this epic's planning doc for why
    that's an intentional, scoped exception to the "runs are immutable
    evidence" convention. `process_id` is denormalized from
    `scenario.process_id` for the same reason TwinRunModel denormalizes
    `agent_artifact_id`."""

    __tablename__ = "orchestration_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    scenario_id: Mapped[str] = mapped_column(
        ForeignKey("orchestration_scenarios.id", ondelete="CASCADE"), index=True
    )
    process_id: Mapped[str] = mapped_column(ForeignKey("processes.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="running")
    node_runs: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    handoffs: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    visited_path: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    final_output: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    deviations: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    total_cost_usd: Mapped[float | None] = mapped_column(nullable=True)
    total_tokens: Mapped[int] = mapped_column(nullable=False, default=0)
    started_at: Mapped[datetime] = mapped_column(server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    run_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class SimulationRunModel(Base):
    """Epic 17: the unified run store both twin (Epic 14) and orchestration
    (Epic 16) runs write to going forward, replacing TwinRunModel/
    OrchestrationRunModel's two separate, ad hoc shapes with one
    RunStatus/RunStep taxonomy (app/schemas/run.py) shared by both --
    disambiguated by `kind`. Deliberately additive, not a data migration:
    `twin_runs`/`orchestration_runs` are left in place unaltered so no run
    history is lost or moved; app/db/repository.py's list/summary reads
    merge rows from both the old tables and this one. See
    planning/epics/17-simulation-run-lifecycle.md and this repo's
    decision-log for why.

    `process_id` is denormalized on both kinds (previously only
    OrchestrationRunModel had it) so a single cancel/resume/list query
    never needs a kind-specific join. `resume_state` is only ever
    populated while `status == "WAITING_FOR_HUMAN"` -- an opaque JSON blob
    app/twin/engine.py's `run_scenario`/app/twin/orchestrator.py's
    `run_orchestration` produce and consume, never read for any other
    purpose. Like OrchestrationRunModel (and unlike TwinRunModel), this row
    is mutated incrementally while non-terminal so a concurrent GET shows
    live progress."""

    __tablename__ = "simulation_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    kind: Mapped[str] = mapped_column(String, nullable=False, index=True)
    process_id: Mapped[str] = mapped_column(ForeignKey("processes.id", ondelete="CASCADE"), index=True)
    scenario_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    agent_artifact_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_artifacts.id", ondelete="CASCADE"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String, nullable=False, default="CREATED", index=True)
    steps: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    node_runs: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    visited_path: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    final_output: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    deviations: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    graded_passed: Mapped[bool | None] = mapped_column(nullable=True)
    # Epic 32/34: the dataset row (at a pinned dataset version) this run
    # executed, its trial number, and the batch it belongs to.
    dataset_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    dataset_version: Mapped[int | None] = mapped_column(nullable=True)
    row_key: Mapped[str | None] = mapped_column(String, nullable=True)
    trial: Mapped[int | None] = mapped_column(nullable=True)
    batch_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    # Epic 34, US34.6: the seed every random choice in this run used
    # (faults, probabilistic reviewers), so one row can be replayed exactly.
    seed: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Epic 19, US19.1: the twelve-dimension EvaluationResult (app/schemas/
    # evaluation.py), computed once alongside graded_passed/deviations --
    # None for a still-running or non-COMPLETED run, and for every run
    # persisted before this epic (never backfilled, same "historical rows
    # just don't have it" convention RunStep.tokens/cost_usd set in Epic 18).
    evaluation: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    total_cost_usd: Mapped[float | None] = mapped_column(nullable=True)
    total_tokens: Mapped[int] = mapped_column(nullable=False, default=0)
    turns_used: Mapped[int] = mapped_column(nullable=False, default=0)
    resume_state: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Epic 22, US22.1: every versioned component that influenced this run
    # (agent artifact version, model policy, tool contracts, guardrails,
    # escalation policy, knowledge sources, twin environment/dataset
    # versions), captured at run start -- see app/twin/snapshot.py. None
    # for a run persisted before this epic, or an orchestration run that
    # failed before node artifacts were resolved.
    snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(nullable=False, default=False)
    queued_at: Mapped[datetime | None] = mapped_column(nullable=True)
    started_at: Mapped[datetime] = mapped_column(server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    run_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class RegressionSuiteModel(Base):
    """Epic 19, US19.2: a named, reusable set of scenario ids (twin or
    orchestration) an Automation Architect wants to re-run together as one
    regression pass, instead of an ungrouped scenario list. `agent_artifact_id`
    is required for kind="twin" (a suite of one agent's scenarios) and left
    null for kind="orchestration" (a process-wide rehearsal suite has no
    single owning artifact)."""

    __tablename__ = "regression_suites"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    process_id: Mapped[str] = mapped_column(ForeignKey("processes.id", ondelete="CASCADE"), index=True)
    agent_artifact_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_artifacts.id", ondelete="CASCADE"), nullable=True, index=True
    )
    kind: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    scenario_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class RegressionSuiteRunModel(Base):
    """Epic 19, US19.3: one execution of a whole RegressionSuiteModel --
    `run_ids` are SimulationRunModel ids (one per scenario, started through
    the existing start_twin_run/start_orchestration_run + background-task
    path, never a duplicated execution mechanism). Aggregate pass/fail is
    computed on read from those runs' own graded_passed, not stored here,
    the same "derive, don't duplicate" convention AgentArtifactModel's
    staleness already follows."""

    __tablename__ = "regression_suite_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    suite_id: Mapped[str] = mapped_column(ForeignKey("regression_suites.id", ondelete="CASCADE"), index=True)
    run_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String, nullable=False, default="running")
    started_at: Mapped[datetime] = mapped_column(server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    started_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class OrganizationModel(Base):
    """Epic 27, US27.1: the one organization this deployment serves (hard
    multi-tenancy is out of scope -- see the epic's Notes). `profile`
    keeps the wizard's raw answers plus which starter packs were applied
    at which version."""

    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    industry: Mapped[str | None] = mapped_column(String, nullable=True)
    size: Mapped[str | None] = mapped_column(String, nullable=True)
    profile: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class ContextEntityModel(Base):
    """Epic 27: every organization-context entity -- business units,
    jurisdictions, roles, approval authorities, regulations, obligations,
    controls, policies, glossary terms (and Epic 29's applications/data
    classes) -- in one table keyed by `kind`, with kind-specific fields in
    `data` (validated by app/schemas/context.py). One table rather than
    ten because every kind shares the same lifecycle: `applies_to` scope,
    proposed -> confirmed confirmation, citations, source, and in-place
    versioning with history rows (ContextEntityVersionModel). See
    planning/decision-log.md (2026-10-11)."""

    __tablename__ = "context_entities"
    __table_args__ = (Index("ix_context_entities_kind_status", "kind", "status"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    code: Mapped[str | None] = mapped_column(String, nullable=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    data: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    applies_to: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # proposed | confirmed | dismissed | retired
    status: Mapped[str] = mapped_column(String, nullable=False, default="confirmed")
    confidence: Mapped[float | None] = mapped_column(nullable=True)
    citations: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    source: Mapped[str] = mapped_column(String, nullable=False, default="manual")
    parent_id: Mapped[str | None] = mapped_column(
        ForeignKey("context_entities.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # A proposal that would update an existing confirmed entity on confirm
    # (US27.6), and whether that update loosens it (Notes: never
    # bulk-confirmable).
    supersedes_id: Mapped[str | None] = mapped_column(
        ForeignKey("context_entities.id", ondelete="CASCADE"), nullable=True
    )
    relaxes: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
    # Epic 31, US31.7: the business unit that stewards this entity. Null =
    # group-wide.
    owner_business_unit_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # Epic 31, US31.2: the record's id in the system of record a connector
    # synced it from (with `source` = "connector:<name>").
    external_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    version: Mapped[int] = mapped_column(nullable=False, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    confirmed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(nullable=True)


class ContextEntityVersionModel(Base):
    """Epic 27 Notes: history rows for ContextEntityModel. One row per
    version ever confirmed, holding the full entity as it was -- Epic 28's
    packs pin (entity_id, version) and need to read the content back."""

    __tablename__ = "context_entity_versions"
    __table_args__ = (UniqueConstraint("entity_id", "version"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    entity_id: Mapped[str] = mapped_column(ForeignKey("context_entities.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    change_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    changed_at: Mapped[datetime] = mapped_column(server_default=func.now())
    changed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class ContextDocumentModel(Base):
    """Epic 27, US27.6: an uploaded policy document. Separate from
    DocumentModel, which belongs to one process -- a policy belongs to the
    organization."""

    __tablename__ = "context_documents"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    filename: Mapped[str] = mapped_column(String, nullable=False)
    content_type: Mapped[str] = mapped_column(String, nullable=False)
    size_bytes: Mapped[int] = mapped_column(nullable=False, default=0)
    # pending | processing | done | failed
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    policy_entity_id: Mapped[str | None] = mapped_column(
        ForeignKey("context_entities.id", ondelete="SET NULL"), nullable=True
    )
    proposal_count: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
    content_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class PolicyPassageModel(Base):
    """Epic 27 Notes: policy/regulation clause text in pgvector, with the
    scope metadata Epic 28's filtered retrieval matches against. `version`
    is the owning policy entity's version when the passage was written --
    re-ingesting a policy writes new passages rather than mutating old
    ones, so a pinned pack can still cite the clause text it used."""

    __tablename__ = "policy_passages"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    document_id: Mapped[str | None] = mapped_column(
        ForeignKey("context_documents.id", ondelete="CASCADE"), nullable=True, index=True
    )
    policy_entity_id: Mapped[str | None] = mapped_column(
        ForeignKey("context_entities.id", ondelete="CASCADE"), nullable=True, index=True
    )
    policy_version: Mapped[int] = mapped_column(nullable=False, default=1)
    ordinal: Mapped[int] = mapped_column(nullable=False, default=0)
    clause_ref: Mapped[str] = mapped_column(String, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM), nullable=False)
    applies_to: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    current: Mapped[bool] = mapped_column(nullable=False, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class ContextPackModel(Base):
    """Epic 28, US28.1/US28.2: one resolved, versioned Context Pack -- the
    exact entity versions and passages an LLM call was given. Immutable:
    identical content resolves to the same row (`content_hash`), so a pack
    id is a stable version identifier that blueprint overlays, agent
    artifacts, gap findings, and run snapshots can pin."""

    __tablename__ = "context_packs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    content_hash: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    purpose: Mapped[str] = mapped_column(String, nullable=False)
    scope: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    process_id: Mapped[str | None] = mapped_column(ForeignKey("processes.id", ondelete="SET NULL"), nullable=True)
    # [{label, entity_id, kind, code, name, version, text}]
    entries: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    # [{label, passage_id, policy_entity_id, policy_version, clause_ref, text, score}]
    passages: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    token_estimate: Mapped[int] = mapped_column(nullable=False, default=0)
    budget: Mapped[int] = mapped_column(nullable=False, default=0)
    truncated: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class ProcessSystemLinkModel(Base):
    """Epic 29, US29.3: which catalog application a free-text system name
    means within one process. One row per (process, system name) rather
    than an `application_id` field added to every JSON blob that holds a
    system string (process elements, blueprint agent specs, agent
    definitions): the strings stay as they are, and every consumer resolves
    them through this table. `auto` marks links made on an exact alias
    match; a person's choice is never overwritten by an auto-link."""

    __tablename__ = "process_system_links"

    process_id: Mapped[str] = mapped_column(ForeignKey("processes.id", ondelete="CASCADE"), primary_key=True)
    system_name: Mapped[str] = mapped_column(String, primary_key=True)
    application_id: Mapped[str | None] = mapped_column(
        ForeignKey("context_entities.id", ondelete="CASCADE"), nullable=True
    )
    auto: Mapped[bool] = mapped_column(nullable=False, default=False)
    linked_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    linked_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class JobModel(Base):
    """Epic 31, US31.1: a durable background job -- Postgres-backed so it
    survives restarts and needs no extra infrastructure. Workers claim jobs
    with SELECT ... FOR UPDATE SKIP LOCKED (app/jobs/queue.py)."""

    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_status_run_after", "status", "run_after"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    kind: Mapped[str] = mapped_column(String, nullable=False, index=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # queued | running | succeeded | failed | cancelled
    status: Mapped[str] = mapped_column(String, nullable=False, default="queued")
    priority: Mapped[int] = mapped_column(nullable=False, default=100, server_default="100")
    attempts: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
    max_attempts: Mapped[int] = mapped_column(nullable=False, default=3, server_default="3")
    run_after: Mapped[datetime] = mapped_column(server_default=func.now())
    locked_by: Mapped[str | None] = mapped_column(String, nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # At most one queued-or-running job per key (e.g. one sync per connector).
    dedupe_key: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)


class ConnectorModel(Base):
    """Epic 31, US31.2: a configured, read-only connector to a system of
    record. `config` never holds a secret -- only the *name* of the
    environment variable that does (`token_env`)."""

    __tablename__ = "connectors"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    type: Mapped[str] = mapped_column(String, nullable=False)
    config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    enabled: Mapped[bool] = mapped_column(nullable=False, default=True, server_default="true")
    schedule_minutes: Mapped[int | None] = mapped_column(nullable=True)
    owner_business_unit_id: Mapped[str | None] = mapped_column(String, nullable=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_status: Mapped[str | None] = mapped_column(String, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class ConnectorRunModel(Base):
    __tablename__ = "connector_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    connector_id: Mapped[str] = mapped_column(ForeignKey("connectors.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="running")
    stats: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    impacts: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)


class UserBusinessUnitModel(Base):
    """Epic 31, US31.7: which business units a user belongs to, and whether
    they steward (may confirm/edit) that unit's context. A user with no
    rows is unrestricted -- the single-unit default."""

    __tablename__ = "user_business_units"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    business_unit_id: Mapped[str] = mapped_column(
        ForeignKey("context_entities.id", ondelete="CASCADE"), primary_key=True
    )
    steward: Mapped[bool] = mapped_column(nullable=False, default=False)


class ContextAccessLogModel(Base):
    """Epic 31, US31.10: who and what read which context."""

    __tablename__ = "context_access_log"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    at: Mapped[datetime] = mapped_column(server_default=func.now(), index=True)
    actor_type: Mapped[str] = mapped_column(String, nullable=False)  # user | llm | connector
    actor: Mapped[str] = mapped_column(String, nullable=False)
    action: Mapped[str] = mapped_column(String, nullable=False)
    process_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    pack_id: Mapped[str | None] = mapped_column(String, nullable=True)
    entity_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    business_unit_id: Mapped[str | None] = mapped_column(String, nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)


class LabelledDatasetModel(Base):
    """Epic 32, US32.1: a labelled dataset -- the cases (inputs plus
    expected labels) a data-driven scenario is graded against, kept
    separate from the scenario's test logic. `version` bumps on every
    change to its rows (US32.6)."""

    __tablename__ = "labelled_datasets"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    process_id: Mapped[str] = mapped_column(ForeignKey("processes.id", ondelete="CASCADE"), index=True)
    agent_artifact_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_artifacts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    version: Mapped[int] = mapped_column(nullable=False, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class DatasetRowModel(Base):
    """Epic 32: one revision of one case. Rows are never edited in place:
    a change closes the current revision (`valid_to_version`) and opens a
    new one, so the dataset as it was at any version can be read back
    exactly -- what a run pins (US32.6) and what a diff compares."""

    __tablename__ = "dataset_rows"
    __table_args__ = (Index("ix_dataset_rows_dataset_key", "dataset_id", "row_key"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("labelled_datasets.id", ondelete="CASCADE"), index=True)
    row_key: Mapped[str] = mapped_column(String, nullable=False)
    valid_from_version: Mapped[int] = mapped_column(nullable=False)
    valid_to_version: Mapped[int | None] = mapped_column(nullable=True)
    inputs: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # {"outputs": {field: value}, "checkpoint_decision": "approve"|"reject"|null}
    labels: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    segment: Mapped[str] = mapped_column(String, nullable=False, default="typical")
    # historical | synthetic | expert | oracle
    provenance: Mapped[str] = mapped_column(String, nullable=False, default="expert")
    tags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    ambiguous: Mapped[bool] = mapped_column(nullable=False, default=False)
    ambiguity_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    second_labels: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    holdout: Mapped[bool] = mapped_column(nullable=False, default=False)
    critical: Mapped[bool] = mapped_column(nullable=False, default=False)
    source_run_id: Mapped[str | None] = mapped_column(String, nullable=True)
    confirmed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class DecisionRuleModel(Base):
    """Epic 33, US33.1: one gateway branch's condition as an executable
    rule over the process's input fields -- drafted by the LLM from the
    BPMN condition text, confirmed (or edited) by a person. `expression` is
    a small typed JSON AST (app/oracles/rules.py), never code."""

    __tablename__ = "decision_rules"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    process_id: Mapped[str] = mapped_column(ForeignKey("processes.id", ondelete="CASCADE"), index=True)
    gateway_id: Mapped[str] = mapped_column(String, nullable=False)
    gateway_label: Mapped[str] = mapped_column(String, nullable=False, default="")
    branch_target_id: Mapped[str] = mapped_column(String, nullable=False)
    branch_label: Mapped[str] = mapped_column(String, nullable=False, default="")
    condition_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    expression: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    outcome: Mapped[str | None] = mapped_column(String, nullable=True)  # approve | reject | escalate
    priority: Mapped[int] = mapped_column(nullable=False, default=100)
    is_default: Mapped[bool] = mapped_column(nullable=False, default=False)
    # proposed | confirmed | unstructurable
    status: Mapped[str] = mapped_column(String, nullable=False, default="proposed")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    control_id: Mapped[str | None] = mapped_column(String, nullable=True)
    version: Mapped[int] = mapped_column(nullable=False, default=1)
    confirmed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class MetamorphicRelationModel(Base):
    """Epic 33, US33.5: an invariant over a dataset's cases -- perturbing
    `field` must leave the decision unchanged (invariance) or move it in a
    stated direction (directional)."""

    __tablename__ = "metamorphic_relations"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("labelled_datasets.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)  # invariance | directional
    field: Mapped[str] = mapped_column(String, nullable=False)
    perturbation: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    direction: Mapped[str | None] = mapped_column(String, nullable=True)  # toward_approve | toward_reject | toward_escalate
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class JudgeRubricModel(Base):
    """Epic 33, US33.6: a rubric an LLM judge grades one free-text output
    field against. Gate-eligible only after calibration against human
    verdicts reaches `min_agreement`; any change to the rubric or judge
    model resets that."""

    __tablename__ = "judge_rubrics"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    process_id: Mapped[str] = mapped_column(ForeignKey("processes.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    field: Mapped[str] = mapped_column(String, nullable=False)
    rubric: Mapped[str] = mapped_column(Text, nullable=False)
    judge_model: Mapped[str | None] = mapped_column(String, nullable=True)
    version: Mapped[int] = mapped_column(nullable=False, default=1)
    min_agreement: Mapped[float] = mapped_column(nullable=False, default=0.85)
    min_samples: Mapped[int] = mapped_column(nullable=False, default=20)
    calibration: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class BatchRunModel(Base):
    """Epic 34, US34.2: one execution of a data-driven scenario over a
    dataset version -- rows x trials runs, executed through the job queue
    (correctness mode) or by an in-process load driver (load mode)."""

    __tablename__ = "batch_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    process_id: Mapped[str] = mapped_column(ForeignKey("processes.id", ondelete="CASCADE"), index=True)
    scenario_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    agent_artifact_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    agent_version: Mapped[int] = mapped_column(nullable=False, default=1)
    dataset_id: Mapped[str] = mapped_column(String, nullable=False)
    dataset_version: Mapped[int] = mapped_column(nullable=False)
    mode: Mapped[str] = mapped_column(String, nullable=False, default="correctness")  # correctness | load
    tier: Mapped[str] = mapped_column(String, nullable=False, default="custom")
    trials: Mapped[int] = mapped_column(nullable=False, default=1)
    sample: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    row_keys: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    # queued | running | paused | cancelled | completed | stopped_budget
    status: Mapped[str] = mapped_column(String, nullable=False, default="queued")
    budget_usd: Mapped[float | None] = mapped_column(nullable=True)
    estimate: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    load_settings: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    keep_traces: Mapped[str] = mapped_column(String, nullable=False, default="failed_and_sampled")
    schedule_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)


class BatchScheduleModel(Base):
    """Epic 34, US34.9: run a tier of a data-driven scenario on a schedule,
    and/or whenever its dataset or the agent's pinned context changes."""

    __tablename__ = "batch_schedules"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    process_id: Mapped[str] = mapped_column(ForeignKey("processes.id", ondelete="CASCADE"), index=True)
    scenario_id: Mapped[str] = mapped_column(String, nullable=False)
    tier: Mapped[str] = mapped_column(String, nullable=False, default="full")
    trials: Mapped[int] = mapped_column(nullable=False, default=1)
    every_minutes: Mapped[int | None] = mapped_column(nullable=True)
    on_dataset_change: Mapped[bool] = mapped_column(nullable=False, default=True)
    on_context_change: Mapped[bool] = mapped_column(nullable=False, default=True)
    budget_usd: Mapped[float | None] = mapped_column(nullable=True)
    enabled: Mapped[bool] = mapped_column(nullable=False, default=True)
    last_run_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_dataset_version: Mapped[int | None] = mapped_column(nullable=True)
    last_context_pack_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class ProcessOperatingModel(Base):
    """Epic 36, US36.4: how a process runs today -- per node, who does it,
    how long it takes and waits, what that time costs; per process, how
    many cases arrive. The as-is side of every process twin comparison.
    One row per process, `version` bumped on save; a process twin batch
    snapshots the model it ran with."""

    __tablename__ = "process_operating_models"

    process_id: Mapped[str] = mapped_column(ForeignKey("processes.id", ondelete="CASCADE"), primary_key=True)
    model: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    version: Mapped[int] = mapped_column(nullable=False, default=1, server_default="1")
    updated_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class ProcessTwinBatchModel(Base):
    """Epic 36, US36.5: the whole process run as a digital twin over a
    dataset -- one orchestration run per case (SimulationRunModel rows with
    `batch_id` = this id), executed through the job queue. `results` is the
    efficiency report (US36.7), computed once the last case finishes."""

    __tablename__ = "process_twin_batches"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    process_id: Mapped[str] = mapped_column(ForeignKey("processes.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String, nullable=False, default="")
    scenario_id: Mapped[str] = mapped_column(String, nullable=False)
    dataset_id: Mapped[str] = mapped_column(String, nullable=False)
    dataset_version: Mapped[int] = mapped_column(nullable=False)
    row_keys: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    # running | paused | cancelled | completed | stopped_budget
    status: Mapped[str] = mapped_column(String, nullable=False, default="running")
    budget_usd: Mapped[float | None] = mapped_column(nullable=True)
    seed: Mapped[int] = mapped_column(nullable=False, default=7)
    operating_model: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    operating_model_version: Mapped[int] = mapped_column(nullable=False, default=1)
    results: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)

