"""Клиент локальной LLM (Ollama REST) — chat + embeddings.

Все методы возвращают None при недоступности сервиса: оркестратор обязан
работать в деградированном режиме (экстрактивный ответ), а не падать.
Подменяется фейком в тестах.
"""

import math

import httpx

from app.core.config import Settings


class LlmClient:
    def __init__(self, settings: Settings) -> None:
        self.base_url = settings.ollama_base_url.rstrip("/")
        self.chat_model = settings.llm_model
        self.embedding_model = settings.embedding_model
        self.embedding_dim = settings.embedding_dim
        self.chat_keep_alive = settings.chat_keep_alive
        self.embed_keep_alive = settings.embed_keep_alive
        self.max_new_tokens = settings.max_new_tokens

    # --- эмбеддинги ---------------------------------------------------------

    def embed(self, text: str) -> list[float] | None:
        vectors = self.embed_batch([text])
        return vectors[0] if vectors else None

    def embed_batch(self, texts: list[str]) -> list[list[float]] | None:
        """Эмбеддинги пачкой. Порядок ответа = порядок входа (гарантия Ollama).

        Проверяем форму и числовые значения до передачи в Qdrant.
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
            payload = resp.json()
        except (httpx.HTTPError, ValueError):
            return None
        if not isinstance(payload, dict):
            return None
        embeddings = payload.get("embeddings")
        if not isinstance(embeddings, list) or len(embeddings) != len(texts):
            return None
        for vector in embeddings:
            if not isinstance(vector, list) or len(vector) != self.embedding_dim:
                return None
            if not all(type(value) in (float, int) and math.isfinite(value) for value in vector):
                return None
        return embeddings

    # --- генерация ----------------------------------------------------------

    def chat(
        self, system_prompt: str, user_prompt: str, max_tokens: int | None = None
    ) -> str | None:
        """Генерация. `max_tokens` — свой потолок длины (роутеру хватает ~20)."""
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
                    # num_predict — потолок длины ответа. Без него модель на
                    # 1.7b охотно пишет 1500 токенов (~70 с) вместо трёх
                    # предложений: замерено, ответ про пособие занимал 72 с.
                    "options": {
                        "temperature": 0.1,
                        "num_predict": max_tokens or self.max_new_tokens,
                    },
                },
                timeout=120.0,
            )
            resp.raise_for_status()
            payload = resp.json()
            if payload.get("done_reason") == "length":
                return None
            content = payload["message"]["content"]
            if isinstance(content, str) and "<think>" in content:
                if "</think>" not in content:
                    return None
                content = content.rsplit("</think>", 1)[1]
        except (httpx.HTTPError, KeyError, TypeError, ValueError):
            return None
        return content.strip() if isinstance(content, str) and content.strip() else None

    # --- доступность --------------------------------------------------------

    def _installed_models(self) -> list[str] | None:
        try:
            resp = httpx.get(f"{self.base_url}/api/tags", timeout=3.0)
            resp.raise_for_status()
            payload = resp.json()
        except (httpx.HTTPError, ValueError):
            return None
        models = payload.get("models") if isinstance(payload, dict) else None
        if not isinstance(models, list):
            return None
        return [
            model["name"]
            for model in models
            if isinstance(model, dict) and isinstance(model.get("name"), str)
        ]

    def available_models(self) -> list[str]:
        return self._installed_models() or []

    def ping(self) -> bool:
        """Ollama отвечает (не значит, что модели скачаны)."""
        return self._installed_models() is not None

    def chat_ready(self) -> bool:
        """Скачана ли именно наша чат-модель — иначе будет экстрактивный режим."""
        return self.model_status()["chat_installed"]

    def model_status(self) -> dict[str, bool]:
        models = self._installed_models()

        def tagged(name: str) -> str:
            return name if ":" in name.rsplit("/", 1)[-1] else f"{name}:latest"

        installed = {tagged(name) for name in models or []}
        return {
            "ollama_reachable": models is not None,
            "chat_installed": tagged(self.chat_model) in installed,
            "embedding_installed": tagged(self.embedding_model) in installed,
        }

