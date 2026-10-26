"""Клиент локальной LLM (Ollama REST) — chat + embeddings.

Все методы возвращают None при недоступности сервиса: оркестратор обязан
работать в деградированном режиме (экстрактивный ответ), а не падать.
Подменяется фейком в тестах.
"""

import json

import httpx

from app.core.config import Settings


class LlmClient:
    def __init__(self, settings: Settings) -> None:
        self.base_url = settings.ollama_base_url
        self.chat_model = settings.llm_model
        self.embedding_model = settings.embedding_model
        self.embedding_dim = settings.embedding_dim

    # --- эмбеддинги ---------------------------------------------------------

    def embed(self, text: str) -> list[float] | None:
        try:
            resp = httpx.post(
                f"{self.base_url}/api/embed",
                json={"model": self.embedding_model, "input": text},
                timeout=30.0,
            )
            resp.raise_for_status()
        except httpx.HTTPError:
            return None
        embeddings = resp.json().get("embeddings") or []
        vector = embeddings[0] if embeddings else None
        if vector is None:
            return None
        return [float(x) for x in vector]

    # --- генерация ----------------------------------------------------------

    def chat(self, system_prompt: str, user_prompt: str) -> str | None:
        try:
            resp = httpx.post(
                f"{self.base_url}/api/chat",
                json={
                    "model": self.chat_model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "stream": False,
                    "options": {"temperature": 0.1},
                },
                timeout=120.0,
            )
            resp.raise_for_status()
        except httpx.HTTPError:
            return None
        try:
            return resp.json()["message"]["content"]
        except (KeyError, json.JSONDecodeError):
            return None

    def ping(self) -> bool:
        try:
            resp = httpx.get(f"{self.base_url}/api/tags", timeout=2.0)
            return resp.status_code == 200
        except httpx.HTTPError:
            return False
