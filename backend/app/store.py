"""Historically an in-memory scratch store for per-process side data
(finalized versions, chat messages, the blueprint overlay) that hadn't
been promoted to real DB persistence yet -- Epics 5/6/7 each promoted
their piece to app/db/repository.py in turn, and Epic 7's blueprint
overlay (the last one) means there's nothing left to hold in memory.

Only `NotFoundError` remains: a domain-generic "resource not found"
exception raised by app/db/repository.py's lookups, mapped to a 404 by a
single exception handler in app/main.py.
"""

from __future__ import annotations

__all__ = ["NotFoundError"]


class NotFoundError(Exception):
    def __init__(self, resource: str, resource_id: str) -> None:
        super().__init__(f"{resource} '{resource_id}' not found")
        self.resource = resource
        self.resource_id = resource_id
