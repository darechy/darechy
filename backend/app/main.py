import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.news import news_router
from app.api.admin_routes import admin_router
from app.api.integration_routes import integration_router
from app.api.routes import api_router
from app.chat.orchestrator import Orchestrator
from app.chat.router import ServiceRouter
from app.chat.taught import TaughtStore
from app.core.config import get_settings
from app.kb.ingest import collect_chunks, load_curated
from app.llm.client import LlmClient
from app.retrieval.store import ChunkStore
from app.services.catalog import build_catalog
from app.services.draft_store import DraftStore
from app.voice.gateway import VoiceGateway

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Поднимает каталог и BM25, затем готовит локальные модели и dense-индекс."""
    settings = get_settings()
    app.state.settings = settings
    app.state.catalog = build_catalog(settings)
    app.state.voice = VoiceGateway(settings)

    store = ChunkStore(
        qdrant_url=settings.qdrant_url if settings.use_qdrant else None,
        embedding_dim=settings.embedding_dim,
        allowed_statuses=settings.allowed_statuses,
        embedding_model=settings.embedding_model,
    )
    llm = _make_llm(settings)
    docs, errors = load_curated(settings.corpus_dir)
    for error in errors:
        logger.warning("Кривой файл корпуса: %s", error)
    chunks = collect_chunks(docs)
    indexed = store.rebuild(chunks)
    logger.info("Индекс корпуса: %d чанков; Qdrant dense: %s", indexed, store.dense_available)

    # Админка работает с теми же объектами, что и API: индекс, корпус, LLM.
    app.state.store = store
    app.state.llm = llm
    app.state.corpus_chunks = chunks
    app.state.taught = TaughtStore(settings.taught_file, chunks)
    app.state.drafts = DraftStore(settings.drafts_file)
    draft_stats = app.state.drafts.stats()
    if draft_stats["total"]:
        logger.info("Черновиков карточек услуг: %d (ждут ревью)", draft_stats["total"])
    broken = app.state.taught.unknown_sources()
    if broken:
        logger.warning("Выученные ответы со ссылками вне корпуса: %s", broken)

    app.state.orchestrator = Orchestrator(
        settings=settings,
        store=store,
        llm=llm,
        router=ServiceRouter(app.state.catalog.all(), llm=llm),
        taught=app.state.taught,
    )

    startup = asyncio.create_task(
        asyncio.to_thread(_prepare_models, store, llm, settings.warmup_on_start)
    )
    try:
        yield
    finally:
        startup.cancel()
        await asyncio.gather(startup, return_exceptions=True)


def _prepare_models(store: ChunkStore, llm: LlmClient, warmup: bool) -> None:
    status = llm.model_status()
    if not status["ollama_reachable"]:
        logger.warning("Ollama недоступна. Поиск работает на BM25.")
        return
    if status["embedding_installed"]:
        count = store.dense_index(llm)
        logger.info("Векторов в индексе: %d", count)
    else:
        logger.warning("Модель эмбеддингов не найдена: %s", llm.embedding_model)
    if not status["chat_installed"]:
        logger.warning("Модель чата не найдена: %s", llm.chat_model)
    elif warmup:
        result = llm.chat("Ответь одним словом.", "Привет", max_tokens=4)
        if result is None:
            logger.warning("Модель чата не ответила на запрос прогрева.")


def _make_llm(settings):
    return LlmClient(settings)


app = FastAPI(
    title="ДаРэчы API",
    version="0.2.0",
    description="ИИ-проводник по государственным услугам: портал, Telegram-бот, киоск.",
    lifespan=lifespan,
)

# Dev-режим: фронтенд на Vite (:5173) ходит в API напрямую через свой прокси,
# но CORS оставляем на случай, когда стучимся без прокси.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)
app.include_router(integration_router)
# Админка: правка базы знаний, каталога услуг и доучивание — без изменения кода.
# Включается только если задан ADMIN_TOKEN.
app.include_router(admin_router, prefix="/api")
app.include_router(news_router)


@app.get("/health")
def health() -> dict:
    """Живость API: docker healthcheck и мониторинг."""
    settings = getattr(app.state, "settings", None) or get_settings()
    return {"status": "ok", "env": settings.app_env, "version": app.version}


@app.get("/health/models")
def model_health() -> dict:
    """Показывает доступность Ollama, моделей и векторного индекса."""
    settings = getattr(app.state, "settings", None) or get_settings()
    llm = getattr(app.state, "llm", None) or _make_llm(settings)
    store = getattr(app.state, "store", None)
    return {
        **llm.model_status(),
        "chat_model": llm.chat_model,
        "embedding_model": settings.embedding_model,
        "dense_enabled": settings.use_qdrant,
        "dense_ready": bool(store and store.dense_available),
    }
