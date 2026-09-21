"""Epic 15: publishing an agent artifact (Epic 12) to a connected registry
(Epic 13) and tracking its lifecycle -- US15.1-15.6. See planning/epics/
15-agent-publishing-lifecycle.md.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

PublicationStatus = Literal["published", "deployed"]
# "draft" (no artifact generated yet) is never returned here -- this status
# only exists once an AgentArtifact does, so it's a frontend-only concept
# (no artifact object to show a publish status for in the first place).
AgentLifecycleStatus = Literal["generated", "published", "deployed"]


class PublishRequest(BaseModel):
    registry_name: str


class AgentPublication(BaseModel):
    id: str
    agent_artifact_id: str
    registry_name: str
    registry_entry_id: str
    # 1-based rank among this artifact's publications by push order --
    # computed on read, not stored (AgentPublicationModel's docstring).
    version: int
    status: PublicationStatus
    published_at: datetime
    published_by: str | None = None
    published_by_name: str | None = None
    deployed_at: datetime | None = None
    deployed_by: str | None = None
    deployed_by_name: str | None = None


class AgentPublishStatus(BaseModel):
    agent_artifact_id: str
    lifecycle_status: AgentLifecycleStatus
    # US15.4: true once the artifact has been regenerated since its most
    # recent publication -- prompts "publish a new version" rather than
    # assuming the registry still reflects the current definition.
    needs_republish: bool
    latest_publication: AgentPublication | None = None
    publications: list[AgentPublication] = []
