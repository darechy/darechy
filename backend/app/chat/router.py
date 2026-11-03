"""Роутер услуг: свободная фраза → услуги каталога.

Два режима, и это осознанно:

1. **Правила** — лексическое совпадение вопроса с названием, описанием и
   ключевыми словами услуги. Быстро (микросекунды) и предсказуемо.
2. **LLM** — включается только когда правила не уверены: ноль совпадений или
   несколько услуг с близкими скорами. Слабая модель на 1.7b годится для
   выбора из короткого списка, а вот «понять вопрос с нуля» — нет.

Зачем вообще LLM здесь: правила не понимают перефраз («сидим с малышом дома,
на что рассчитывать» → пособие по уходу). LLM ловит смысл, но стоит секунды,
поэтому он не на каждом запросе, а как арбитр.

Результат роутера используется ещё и для **скоупа поиска**: если вопрос про
конкретную услугу, искать имеет смысл в первую очередь в её актах, а не по
всему корпусу.
"""

from __future__ import annotations

import json
import logging
import re

from app.models.schemas import Service
from app.retrieval.store import tokenize

logger = logging.getLogger(__name__)

# Минимальный отрыв лидера от второго места, чтобы правила считались
# уверенными и LLM не звали. Числа с потолка не берём: 0.5 — это «лидер
# набрал заметно больше», при равенстве вызовем арбитра.
_RULE_CONFIDENCE_GAP = 0.5

ROUTER_PROMPT = (
    "Ты классификатор вопросов о государственных услугах Беларуси. "
    "Тебе дан список услуг и вопрос гражданина. Выбери услуги, о которых "
    "спрашивают. Ответь ТОЛЬКО JSON-массивом идентификаторов, без пояснений. "
    "Если вопрос не про эти услуги — верни []. Максимум 3 идентификатора."
)


class ServiceRouter:
    def __init__(self, services: list[Service], llm=None) -> None:
        self._services = [s for s in services if s.status != "черновик"]
        self._llm = llm
        # Словарь услуги: токены названия + summary + who + ключевые слова.
        # Ключевые слова (из админки) весим выше — их задал человек.
        self._vocab: dict[str, list[str]] = {}
        for service in self._services:
            self._vocab[service.id] = [
                *(t for _ in range(3) for t in tokenize(service.title)),
                *(t for _ in range(2) for t in tokenize(service.summary)),
                *tokenize(service.who),
                *(t for _ in range(4) for t in tokenize(" ".join(service.keywords))),
            ]

    # --- правила ------------------------------------------------------------

    def route_scored(self, message: str) -> list[tuple[str, float]]:
        """Услуги со скорами, по убыванию. Пустой список — совпадений нет."""
        query = tokenize(message)
        if not query:
            return []
        query_set = set(query)
        scored: list[tuple[str, float]] = []
        for service_id, vocab in self._vocab.items():
            overlap = len(query_set & set(vocab))
            scored.append((service_id, overlap / (1 + len(query_set) * 0.1)))
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return [(sid, score) for sid, score in scored if score > 0]

    def route(self, message: str, top_n: int = 3) -> list[str]:
        """Правиловый маршрут: совместим со старым поведением."""
        return [sid for sid, _ in self.route_scored(message)[:top_n]]

    def _rules_confident(self, scored: list[tuple[str, float]]) -> bool:
        if len(scored) != 1:
            return False
        return True  # один кандидат — уже однозначно; отрыв считаем ниже

    # --- LLM-арбитр ---------------------------------------------------------

    def _catalog_for_prompt(self) -> str:
        lines = []
        for service in self._services:
            keywords = ", ".join(service.keywords) or "—"
            lines.append(f"{service.id}: {service.title} (ключевые слова: {keywords})")
        return "\n".join(lines)

    def _route_llm(self, message: str) -> list[str]:
        """Спрашивает LLM. Возвращает [] при любой проблеме — вызывающий решает."""
        if self._llm is None or not self._services:
            return []
        prompt = f"Услуги:\n{self._catalog_for_prompt()}\n\nВопрос: {message}"
        # Роутеру нужен короткий ответ (список id), а не сочинение: иначе
        # арбитр стоит 6,5 с — замерено на живом сервере.
        answer = self._llm.chat(ROUTER_PROMPT, prompt, max_tokens=24)
        if not answer:
            return []
        known = {service.id for service in self._services}
        # Модель может завернуть JSON в текст или в ```-блок: вытаскиваем id по
        # словарю каталога, а не доверяем формату.
        found: list[str] = []
        for match in re.finditer(r"[a-z0-9][a-z0-9\-]{2,}", answer.lower()):
            candidate = match.group(0)
            if candidate in known and candidate not in found:
                found.append(candidate)
        if not found and "[]" not in answer:
            logger.debug("LLM-роутер вернул непонятное: %s", answer[:120])
        return found[:3]

    # --- итоговый маршрут ---------------------------------------------------

    def route_with_ai(self, message: str) -> tuple[list[str], str]:
        """Маршрут и признак, кто решил: "rules" или "llm".

        LLM зовём только при неоднозначности: ноль совпадений (правила не
        поняли перефраз) или несколько услуг с близкими скорами. Когда правила
        уверены, тратить секунды на модель незачем.
        """
        scored = self.route_scored(message)
        if self._rules_confident(scored):
            return [scored[0][0]], "rules"

        ambiguous = len(scored) > 1 and (
            scored[0][1] - scored[1][1] < _RULE_CONFIDENCE_GAP
        )
        if scored and not ambiguous:
            return [sid for sid, _ in scored[:3]], "rules"

        llm_ids = self._route_llm(message)
        if llm_ids:
            return llm_ids, "llm"

        # LLM не помог — отдаём то, что нашли правила (может быть пусто).
        return [sid for sid, _ in scored[:3]], "rules"

    # --- скоуп поиска -------------------------------------------------------

    def scope_chunk_ids(self, service_ids: list[str]) -> set[str]:
        """chunk_id, на которые опираются услуги: по ним ограничиваем поиск."""
        wanted = set(service_ids)
        chunk_ids: set[str] = set()
        for service in self._services:
            if service.id not in wanted:
                continue
            for source in service.sources:
                if source.point:
                    chunk_ids.add(source.point)
        return chunk_ids

    def keywords_json(self) -> str:
        """Для отладки/админки: что роутер реально видит."""
        return json.dumps(
            {s.id: s.keywords for s in self._services}, ensure_ascii=False, indent=2
        )
