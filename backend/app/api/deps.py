from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.llm import LLMClient, get_llm_client

LLMDep = Annotated[LLMClient, Depends(get_llm_client)]
DbDep = Annotated[Session, Depends(get_db)]
