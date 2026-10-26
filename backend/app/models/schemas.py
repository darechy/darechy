"""Схемы API — единый контракт для портала (frontend/src/api.ts), бота и киоска.

Каждое изменение здесь — синхронное изменение в api.ts у кента.
"""

from pydantic import BaseModel, Field


class ChecklistItem(BaseModel):
    item: str
    where: str


class Source(BaseModel):
    """Источник на странице услуги (каталог)."""

    title: str
    url: str | None = None
    point: str | None = None


class Service(BaseModel):
    id: str
    title: str
    situation: str
    summary: str
    who: str
    amount: str
    deadline: str
    checklist: list[ChecklistItem]
    sources: list[Source]


class Situation(BaseModel):
    """«Жизненная ситуация» для главной портала: маршрут из услуг."""

    id: str
    title: str
    service_ids: list[str]


class Citation(BaseModel):
    """Цитата нормативного акта под ответом ассистента.

    `quote` — дословный фрагмент чанка корпуса; `location` — точное место
    в акте (статья/пункт). Без источника ответ не отдаём — guardrail.
    """

    chunk_id: str
    doc_title: str
    doc_number: str | None = None
    doc_date: str | None = None
    location: str | None = None
    url: str | None = None
    quote: str


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    session_id: str | None = None
    # id услуги со страницы, с которой задан вопрос («спросить ассистента»)
    service_id: str | None = None
    top_k: int = Field(default=6, ge=1, le=20)


class ChatResponse(BaseModel):
    answer: str
    citations: list[Citation]
    refusal: bool = False
    suggested_services: list[str] = Field(default_factory=list)
    latency_ms: int = 0


class RefusalDetail(BaseModel):
    reason: str
