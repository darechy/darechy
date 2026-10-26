from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


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

    # Локальная LLM (неделя 2)
    ollama_base_url: str = "http://localhost:11434"
    llm_model: str = "qwen3:8b"

    # Голос (неделя 3)
    whisper_model: str = "large-v3-turbo"
    tts_lang: str = "ru"

    # Langfuse, опционально
    langfuse_host: str = ""
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
