"""SQLAlchemy ORM models for Epic 2 (Business Process Knowledge Store) --
processes, documents, the extracted process schema (actors/elements/
source_refs/flows), embeddings, a lightweight change log -- plus Epic 3's
draft BPMN (BPMNDraftModel), Epic 5's chat messages (ChatMessageModel),
Epic 6's finalized versions (VersionModel), Epic 7's blueprint overlay
(BlueprintOverlayModel), Epic 11's gap findings (GapFindingModel), Epic
12's generated agent artifacts (AgentArtifactModel), Epic 13's local
registry entries (RegistryEntryModel), Epic 14's digital twin scenarios/
runs/baselines (TwinToolSchemaModel/TwinScenarioModel/TwinRunModel/
TwinBaselineModel), Epic 15's publish history (AgentPublicationModel), and
Epic 9/10's users/sessions (UserModel/SessionModel), each promoted out of
the in-memory store (app/store.py) once its own epic made the data real.
app/store.py now only defines NotFoundError -- nothing left to hold in
memory.
"""

from __future__ import annotations

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import ForeignKey, Index, JSON, String, Text, UniqueConstraint
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
    definition: Mapped[dict] = mapped_column(JSON, nullable=False)
    source_baseline_version_id: Mapped[str] = mapped_column(String, nullable=False)
    source_node_snapshot: Mapped[list[dict]] = mapped_column(JSON, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
    # Epic 9/10, US9.9: who triggered (re)generation.
    generated_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

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


class TwinScenarioModel(Base):
    """Epic 14 (core slice), US14.1: one defined test scenario for an
    agent artifact -- start-event `inputs`, per-system simulation
    overrides (`system_stubs`), how to resolve the agent's human
    checkpoint if it has one (`human_checkpoint_config`), and the expected
    tool-call/checkpoint path plus expected final output used to grade a
    run (US14.3)."""

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
