import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import api_router
from app.bot.telegram import build_bot
from app.chat.orchestrator import Orchestrator
from app.chat.router import ServiceRouter
from app.core.config import get_settings
from app.kb.ingest import collect_chunks, load_curated
from app.retrieval.store import ChunkStore
from app.services.catalog import build_catalog
from app.voice.gateway import VoiceGateway

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Сборка зависимостей при старте: каталог, корпус, оркестратор, бот.

    Ничего критичного здесь не падает: нет Docker/Qdrant/Ollama — API живёт
    в деградированном режиме (BM25 + экстрактивные ответы), что важно для
   dev-машин без инфры.
    """
    settings = get_settings()
    app.state.settings = settings
    app.state.catalog = build_catalog(settings)
    app.state.voice = VoiceGateway(settings)

    store = ChunkStore(
        qdrant_url=settings.qdrant_url if settings.use_qdrant else None,
        embedding_dim=settings.embedding_dim,
        allowed_statuses=settings.allowed_statuses,
    )
    llm = _make_llm(settings)
    docs, errors = load_curated(settings.corpus_dir)
    for error in errors:
        logger.warning("Кривой файл корпуса: %s", error)
    indexed = store.rebuild(collect_chunks(docs))
    logger.info("Индекс корпуса: %d чанков; Qdrant dense: %s", indexed, store.dense_available)

    app.state.orchestrator = Orchestrator(
        settings=settings,
        store=store,
        llm=llm,
        router=ServiceRouter(app.state.catalog.all()),
    )

    bot_result = build_bot(settings, app.state.orchestrator, app.state.voice)
    app.state.bot = None
    if bot_result:
        bot, dp = bot_result
        app.state.bot = bot
        app.state.dp = dp
    yield


def _make_llm(settings):
    from app.llm.client import LlmClient

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


@app.get("/health")
def health() -> dict:
    """Живость API: docker healthcheck и мониторинг."""
    settings = getattr(app.state, "settings", None) or get_settings()
    return {"status": "ok", "env": settings.app_env, "version": app.version}
