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
    # Куда обращаться (орган) и сколько стоит. Поля появились, когда каталог
    # начал собираться из перечня административных процедур (Указ № 200):
    # в позиции перечня эти два блока заполнены всегда, терять их нельзя.
    # Синхронно с frontend/src/api.ts.
    authority: str = ""
    cost: str = ""
    # Статус карточки: черновик (сгенерирована, ждёт ревью) | проверена.
    # На портал черновики не отдаются.
    status: str = "проверена"
    # Триггеры для ИИ-роутера: по ним вопрос сопоставляется с услугой.
    keywords: list[str] = Field(default_factory=list)


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
    # Каким слоем получен ответ: "taught" — выученный администратором,
    # "corpus" — сгенерирован по корпусу, "extractive" — дословный фрагмент
    # без модели, "refusal" — честный отказ. Портал помечает выученные ответы.
    source: str = "corpus"


class RefusalDetail(BaseModel):
    reason: str


# --- «Доучивание»: ответы, подтверждённые администратором -------------------


class TaughtSource(BaseModel):
    """Ссылка на норму в выученном ответе.

    `chunk_id` обязателен и проверяется по корпусу: выучить ответ без
    подтверждённого источника нельзя — иначе рушится главный принцип проекта.
    """

    chunk_id: str
    note: str | None = None


class TaughtAnswer(BaseModel):
    id: str
    question: str
    answer: str
    # Триггеры срабатывания. match_mode=any — достаточно одного слова.
    keywords: list[str] = Field(min_length=1)
    match_mode: str = "all"  # "all" | "any"
    service_id: str | None = None
    sources: list[TaughtSource] = Field(min_length=1)
    enabled: bool = True
    author: str | None = None
    created_at: str
    updated_at: str


class TaughtAnswerCreate(BaseModel):
    question: str = Field(min_length=3)
    answer: str = Field(min_length=1)
    keywords: list[str] = Field(min_length=1)
    match_mode: str = "all"
    service_id: str | None = None
    sources: list[TaughtSource] = Field(min_length=1)
    enabled: bool = True
    author: str | None = None


class TaughtAnswerUpdate(BaseModel):
    question: str | None = None
    answer: str | None = None
    keywords: list[str] | None = None
    match_mode: str | None = None
    service_id: str | None = None
    sources: list[TaughtSource] | None = None
    enabled: bool | None = None


class TaughtMatch(BaseModel):
    """Результат проверки: сработал ли выученный ответ на запрос."""

    taught: TaughtAnswer
    matched_keywords: list[str]
    score: float
