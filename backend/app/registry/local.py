"""Epic 13, US13.2: the default, always-available registry connector -- a
plain table in this product's own Postgres, so publish/browse/search can
be built and demonstrated end-to-end without a specific vendor registry
chosen or available yet (see the epic's Notes on that open question).
"""

from __future__ import annotations

from sqlalchemy import select

from app.db.models import RegistryEntryModel
from app.db.session import get_session_factory
from app.ids import new_id

from .base import RegistryConnector, RegistryEntry, RegistryHealth


def _to_entry(row: RegistryEntryModel) -> RegistryEntry:
    return RegistryEntry(
        id=row.id,
        registry_name=row.registry_name,
        agent_name=row.agent_name,
        tags=row.tags,
        definition=row.definition,
        pushed_at=row.pushed_at,
        source_process_id=row.source_process_id,
        source_node_ids=row.source_node_ids,
        pushed_by=row.pushed_by,
    )


class LocalRegistryConnector(RegistryConnector):
    """Opens its own short-lived session per call (like app/ingestion/
    pipeline.py's background tasks) rather than accepting a request-scoped
    one, since a real vendor connector (an HTTP client) has no DB session
    at all -- keeping RegistryConnector's interface free of a `session`
    parameter is what lets both kinds of connector implement it
    identically. In practice check_health/push/pull/search essentially
    never fail here (it's the same trusted Postgres instance as everything
    else) -- the unreachable/auth-error paths (US13.6) are real for the
    interface (and are exercised in tests via a fake connector), just not
    reachable through this particular implementation.
    """

    def check_health(self) -> RegistryHealth:
        try:
            session = get_session_factory()()
            try:
                session.execute(select(1))
            finally:
                session.close()
        except Exception as exc:  # pragma: no cover -- only if the shared DB itself is down
            return RegistryHealth(name=self.name, reachable=False, authenticated=False, message=str(exc))
        return RegistryHealth(name=self.name, reachable=True, authenticated=True)

    def push(
        self,
        *,
        agent_name: str,
        definition: dict,
        tags: list[str] | None = None,
        source_process_id: str | None = None,
        source_node_ids: list[str] | None = None,
        pushed_by: str | None = None,
    ) -> RegistryEntry:
        session = get_session_factory()()
        try:
            row = RegistryEntryModel(
                id=new_id("regentry"),
                registry_name=self.name,
                agent_name=agent_name,
                tags=tags or [],
                definition=definition,
                source_process_id=source_process_id,
                source_node_ids=source_node_ids or [],
                pushed_by=pushed_by,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return _to_entry(row)
        finally:
            session.close()

    def pull(self, entry_id: str) -> RegistryEntry | None:
        session = get_session_factory()()
        try:
            row = session.get(RegistryEntryModel, entry_id)
            return _to_entry(row) if row is not None and row.registry_name == self.name else None
        finally:
            session.close()

    def search(self, query: str) -> list[RegistryEntry]:
        session = get_session_factory()()
        try:
            rows = list(
                session.scalars(
                    select(RegistryEntryModel)
                    .where(RegistryEntryModel.registry_name == self.name)
                    .order_by(RegistryEntryModel.pushed_at.desc())
                )
            )
        finally:
            session.close()

        # Filtered in Python, not SQL -- this project's data volumes don't
        # warrant JSON-array-containment SQL for the tags match (same
        # "smallest number of moving parts" default as elsewhere, e.g.
        # repository.py's _resolve_user_name).
        needle = query.strip().lower()
        if not needle:
            return [_to_entry(row) for row in rows]
        return [
            _to_entry(row)
            for row in rows
            if needle in row.agent_name.lower() or any(needle in tag.lower() for tag in row.tags)
        ]
