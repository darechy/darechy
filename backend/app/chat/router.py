"""Роутер услуг: свободная фраза → подходящие услуги каталога.

Неделя 3 здесь появится LLM-классификатор и 2–3 уточняющих вопроса. Сейчас —
честный rule-based: лексическое совпадение вопроса с названием/описанием
услуги. Он же служит fallback'ом для LLM-классификатора.
"""

from app.models.schemas import Service
from app.retrieval.store import tokenize


class ServiceRouter:
    def __init__(self, services: list[Service]) -> None:
        self._services = services
        # Словарь услуги: токены названия + summary, взвешенные выше.
        self._vocab: dict[str, list[str]] = {}
        for service in services:
            title_tokens = tokenize(service.title)
            summary_tokens = tokenize(service.summary)
            who_tokens = tokenize(service.who)
            self._vocab[service.id] = [
                *(t for _ in range(3) for t in title_tokens),
                *(t for _ in range(2) for t in summary_tokens),
                *who_tokens,
            ]

    def route(self, message: str, top_n: int = 3) -> list[str]:
        query = tokenize(message)
        if not query:
            return []
        query_set = set(query)
        scored: list[tuple[str, float]] = []
        for service_id, vocab in self._vocab.items():
            overlap = len(query_set & set(vocab))
            scored.append((service_id, overlap / (1 + len(query_set) * 0.1)))
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return [service_id for service_id, score in scored[:top_n] if score > 0]
