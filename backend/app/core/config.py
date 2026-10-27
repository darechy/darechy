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
    # 1.7b — осознанный выбор под 8 ГБ RAM (dev-машины команды): 4b/8b вместе
    # с эмбеддингами уходят в своп. Задача модели узкая — сформулировать ответ
    # строго по присланным фрагментам, а не «знать» право.
    llm_model: str = "qwen3:1.7b"
    # Эмбеддинги: bge-m3 — официальная модель в библиотеке Ollama, 1024 измерения,
    # многоязычная. `multilingual-e5-large` в Ollama нет (404 в реестре).
    embedding_model: str = "bge-m3"
    embedding_dim: int = 1024
    # Ollama держит модель в памяти после запроса. Чат выгружаем сразу: на 8 ГБ
    # одновременно с эмбеддингами ему места нет. Эмбеддинги греются чаще — дольше.
    chat_keep_alive: str = "30s"
    embed_keep_alive: str = "5m"

    # RAG
    corpus_dir: Path = REPO_DIR / "kb_corpus" / "curated"
    retrieval_top_k: int = 6
    retrieval_score_threshold: float = 0.05
    use_qdrant: bool = True
    # Какие статусы чанков идут в индекс. Правило конвейера — только «проверен».
    # На dev/демо сюда добавляют «разобран», иначе неотревьюенный корпус
    # не индексируется вообще и ассистенту нечего отвечать.
    indexed_statuses: str = "проверен"

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
