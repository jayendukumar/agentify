"""In-memory scratch store for what's not persisted yet: chat messages
(Epic 5), finalized versions (Epic 6), and the blueprint overlay
(Epic 7/8). Still lost on every process restart -- each gets real
persistence when its own epic is implemented.

Process/document/extracted-schema persistence is real (app/db/repository.py,
Epic 2); draft BPMN persistence is also real now (same module, Epic 3) --
this store no longer holds it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

from app.ids import new_id, utcnow  # re-exported -- other modules import these from here
from app.schemas.blueprint import BlueprintOverlay
from app.schemas.chat import ChatMessageResult
from app.schemas.versions import VersionDetail

__all__ = [
    "new_id",
    "utcnow",
    "NotFoundError",
    "InMemoryStore",
    "get_store",
]


class NotFoundError(Exception):
    def __init__(self, resource: str, resource_id: str) -> None:
        super().__init__(f"{resource} '{resource_id}' not found")
        self.resource = resource
        self.resource_id = resource_id


@dataclass
class ProcessSideData:
    """Everything about a process that isn't persisted to the DB yet."""

    chat_messages: list[ChatMessageResult] = field(default_factory=list)
    versions: dict[str, VersionDetail] = field(default_factory=dict)
    version_order: list[str] = field(default_factory=list)
    blueprint: BlueprintOverlay | None = None


class InMemoryStore:
    """Does not know whether a process_id actually exists -- callers must
    check that against app/db/repository.py first (it raises NotFoundError
    the same way this module's own methods below do, so a single exception
    handler in app/main.py covers both). This store will happily create an
    empty side-record for any id it's asked about.
    """

    def __init__(self) -> None:
        self._data: dict[str, ProcessSideData] = {}

    def _side_data(self, process_id: str) -> ProcessSideData:
        return self._data.setdefault(process_id, ProcessSideData())

    # -- chat (Epic 5) -----------------------------------------------------
    def add_chat_message(self, process_id: str, message: ChatMessageResult) -> None:
        self._side_data(process_id).chat_messages.append(message)

    def list_chat_messages(self, process_id: str) -> list[ChatMessageResult]:
        return self._side_data(process_id).chat_messages

    def get_chat_message(self, process_id: str, message_id: str) -> ChatMessageResult:
        for message in self._side_data(process_id).chat_messages:
            if message.id == message_id:
                return message
        raise NotFoundError("chat message", message_id)

    # -- versions (Epic 6) -------------------------------------------------
    def add_version(self, process_id: str, version: VersionDetail) -> None:
        data = self._side_data(process_id)
        data.versions[version.id] = version
        data.version_order.append(version.id)

    def get_version(self, process_id: str, version_id: str) -> VersionDetail:
        version = self._side_data(process_id).versions.get(version_id)
        if version is None:
            raise NotFoundError("version", version_id)
        return version

    def list_versions(self, process_id: str) -> list[VersionDetail]:
        data = self._side_data(process_id)
        return [data.versions[version_id] for version_id in data.version_order]

    def latest_version(self, process_id: str) -> VersionDetail | None:
        data = self._side_data(process_id)
        if not data.version_order:
            return None
        return data.versions[data.version_order[-1]]

    # -- blueprint (Epic 7/8) ----------------------------------------------
    def set_blueprint(self, process_id: str, blueprint: BlueprintOverlay) -> None:
        self._side_data(process_id).blueprint = blueprint

    def get_blueprint(self, process_id: str) -> BlueprintOverlay | None:
        return self._side_data(process_id).blueprint


@lru_cache
def get_store() -> InMemoryStore:
    return InMemoryStore()
