"""US2.3: local embedding generation for extracted document content.

Uses a local sentence-transformers model, not the LLM provider's API --
see planning/document-ingestion-strategy.md for why (volume mismatch: one
call per paragraph/table/shape adds up fast against a hosted API, for no
quality benefit at this matching precision; and it decouples the embedding
provider from the cost-driven "for now" choice of chat/extraction LLM).
"""

from __future__ import annotations

from functools import lru_cache

from sentence_transformers import SentenceTransformer

from app.config import get_settings


@lru_cache
def get_embedding_model() -> SentenceTransformer:
    """Loaded lazily and cached -- the first call downloads/loads model
    weights (a few seconds), every call after is instant."""
    return SentenceTransformer(get_settings().embedding_model_name)


def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    model = get_embedding_model()
    vectors = model.encode(texts, convert_to_numpy=True, normalize_embeddings=True)
    return [vector.tolist() for vector in vectors]
