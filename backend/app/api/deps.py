from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.llm import LLMClient, get_llm_client
from app.store import InMemoryStore, get_store

StoreDep = Annotated[InMemoryStore, Depends(get_store)]
LLMDep = Annotated[LLMClient, Depends(get_llm_client)]
DbDep = Annotated[Session, Depends(get_db)]
