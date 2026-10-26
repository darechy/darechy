"""Модели базы знаний — формат `kb_corpus/README.md`, без отклонений.

Один акт = один json в `kb_corpus/curated/`. Чанк = статья или пункт.
"""

from pydantic import BaseModel, Field


class ActDoc(BaseModel):
    type: str
    number: str
    date: str
    title: str
    source_url: str
    edition_date: str | None = None


class ChunkLocation(BaseModel):
    article: str | None = None
    point: str | None = None

    def label(self) -> str:
        parts = [p for p in (self.article, self.point) if p]
        return ", ".join(parts)


class Chunk(BaseModel):
    chunk_id: str
    location: ChunkLocation
    text: str
    status: str = "проверен"  # индексируем только «проверен»
    # Заполняется на этапе индексации:
    doc_title: str | None = None
    doc_number: str | None = None
    doc_date: str | None = None
    doc_url: str | None = None


class CuratedDoc(BaseModel):
    doc: ActDoc
    chunks: list[Chunk] = Field(min_length=1)


class RetrievedChunk(BaseModel):
    """Чанк после поиска: с скором и источником релевантности."""

    chunk: Chunk
    score: float
    via: str  # "bm25" | "dense" | "both"
