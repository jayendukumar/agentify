from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

Role = Literal["viewer", "editor"]


class LoginRequest(BaseModel):
    name: str
    role: Role = "viewer"


class UserOut(BaseModel):
    id: str
    name: str
    role: Role
    created_at: datetime
