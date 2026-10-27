"""Общая фабрика ChunkStore для тестов (BM25-only, без сети и Docker)."""

from app.retrieval.store import ChunkStore


def make_store() -> ChunkStore:
    """Store без Qdrant: только BM25-плечо, детерминированное в тестах."""
    return ChunkStore(qdrant_url=None)
