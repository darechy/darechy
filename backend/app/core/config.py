from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
REPO_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    """Конфиг приложения. Читается из backend/.env (образец — .env.example)."""

    app_env: str = "dev"
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # Telegram-бот (@BotFather). Пусто — бот не стартует, REST API работает.
    telegram_bot_token: str = ""

    # Хранилища
    database_url: str = "postgresql://darechy:darechy@localhost:5432/darechy"
    redis_url: str = "redis://localhost:6379/0"
    qdrant_url: str = "http://localhost:6333"

    # Локальная LLM
    ollama_base_url: str = "http://localhost:11434"
    llm_model: str = "qwen3:4b-instruct-2507-q4_K_M"
    # Эмбеддинги: bge-m3 — официальная модель в библиотеке Ollama, 1024 измерения,
    # многоязычная. `multilingual-e5-large` в Ollama нет (404 в реестре).
    embedding_model: str = "bge-m3"
    embedding_dim: int = 1024
    # Ollama держит модель в памяти после запроса. Чат выгружаем сразу: на 8 ГБ
    # одновременно с эмбеддингами ему места нет. Эмбеддинги греются чаще — дольше.
    chat_keep_alive: str = "30s"
    embed_keep_alive: str = "5m"
    # Потолок длины ответа модели. 400 токенов ≈ 17 с на 1.7b; без лимита
    # модель выдаёт до 1500 токенов и ответ идёт больше минуты.
    max_new_tokens: int = 180
    # Скоуп поиска по услуге и ИИ-роутер. Флаги нужны, чтобы можно было
    # честно сравнить качество с ними и без, а не верить на слово.
    service_scoping: bool = True
    ai_router: bool = False
    # Прогревать модели при старте: убирает «первый ответ 11 с» после простоя.
    warmup_on_start: bool = True
    # Переспрашивать услугу, если спросили про документы без привязки.
    clarify_doc_intent: bool = True
    # Отвечать про документы списком из карточки услуги, а не пересказом модели.
    checklist_answers: bool = True
    # Сворачивать пункты в статьи при поиске. По замеру на текущем корпусе
    # выключено (71,4% против 64,3%); см. scripts/tune_gate.py.
    group_articles: bool = False

    # RAG
    corpus_dir: Path = REPO_DIR / "kb_corpus" / "curated"
    raw_dir: Path = REPO_DIR / "kb_corpus" / "raw"
    retrieval_top_k: int = 6
    retrieval_score_threshold: float = 0.05
    use_qdrant: bool = True
    # Какие статусы чанков идут в индекс. Правило конвейера — только «проверен».
    # На dev/демо сюда добавляют «разобран»/«восстановлен», иначе неотревьюенный
    # корпус не индексируется вообще и ассистенту нечего отвечать.
    indexed_statuses: str = "проверен,восстановлен"

    # Админка: правка базы знаний и каталога услуг без изменения кода
    # (см. docs/админка.md). Токен — обязателен; пусто = админка выключена.
    admin_token: str = ""
    admin_accounts: dict[str, str] = {}
    news_db: str = "news.sqlite3"
    api_keys_db: Path = REPO_DIR / "infra" / "data" / "api_keys.sqlite3"
    services_file: Path = BACKEND_DIR / "app" / "data" / "services.json"
    taught_file: Path = REPO_DIR / "kb_corpus" / "taught" / "answers.json"
    # Черновики карточек услуг из перечня: лежат ОТДЕЛЬНО от рабочего каталога,
    # на портал попадает только утверждённое человеком.
    drafts_file: Path = BACKEND_DIR / "app" / "data" / "services_drafts.json"

    # Голос (неделя 3)
    whisper_model: str = "large-v3-turbo"
    tts_lang: str = "ru"

    # Langfuse, опционально
    langfuse_host: str = ""
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def allowed_statuses(self) -> frozenset[str]:
        """Статусы чанков, которые попадают в индекс (например «проверен,разобран»)."""
        statuses = {s.strip() for s in self.indexed_statuses.split(",") if s.strip()}
        return frozenset(statuses) or frozenset({"проверен"})


@lru_cache
def get_settings() -> Settings:
    return Settings()
