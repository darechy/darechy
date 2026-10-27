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
        self.chat_keep_alive = settings.chat_keep_alive
        self.embed_keep_alive = settings.embed_keep_alive

    # --- эмбеддинги ---------------------------------------------------------

    def embed(self, text: str) -> list[float] | None:
        vectors = self.embed_batch([text])
        return vectors[0] if vectors else None

    def embed_batch(self, texts: list[str]) -> list[list[float]] | None:
        """Эмбеддинги пачкой. Порядок ответа = порядок входа (гарантия Ollama).

        `keep_alive` короткий: на 8 ГБ RAM держать эмбеддинги и чат-модель
        в памяти одновременно нельзя — уходим в своп.
        """
        if not texts:
            return []
        try:
            resp = httpx.post(
                f"{self.base_url}/api/embed",
                json={
                    "model": self.embedding_model,
                    "input": texts,
                    "keep_alive": self.embed_keep_alive,
                },
                timeout=120.0,
            )
            resp.raise_for_status()
        except httpx.HTTPError:
            return None
        embeddings = resp.json().get("embeddings")
        if not embeddings or len(embeddings) != len(texts):
            return None
        return [[float(x) for x in vector] for vector in embeddings]

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
                    # Thinking-режим Qwen3 не нужен: у нас ответ строго по
                    # фрагментам, а размышления только жгут время на слабой машине.
                    "think": False,
                    "keep_alive": self.chat_keep_alive,
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

    # --- доступность --------------------------------------------------------

    def available_models(self) -> list[str]:
        try:
            resp = httpx.get(f"{self.base_url}/api/tags", timeout=3.0)
            resp.raise_for_status()
        except httpx.HTTPError:
            return []
        return [m["name"] for m in resp.json().get("models", [])]

    def ping(self) -> bool:
        """Ollama отвечает (не значит, что модели скачаны)."""
        try:
            resp = httpx.get(f"{self.base_url}/api/tags", timeout=2.0)
            return resp.status_code == 200
        except httpx.HTTPError:
            return False

    def chat_ready(self) -> bool:
        """Скачана ли именно наша чат-модель — иначе будет экстрактивный режим."""
        models = self.available_models()
        if not models:
            return False
        # Ollama отдаёт имена с тегом (`qwen3:1.7b`), сверяем и без тега.
        wanted = {self.chat_model, self.chat_model.split(":")[0]}
        return any(name in wanted for name in models)

