"""Выученные ответы: слой, который администратор добавляет поверх корпуса.

Про «доучить модель»: дообучать локальную модель на 1.7b под правила — плохая
идея (дорого, невоспроизводимо, ломает основной принцип «ответ только по акту»).
Здесь доучивание сделано как слой подтверждённых людьми ответов: эталонная
формулировка + триггеры срабатывания + ссылки на нормы корпуса. Слой отвечает
мгновенно, воспроизводим, и его можно отключить одним флагом.

Жёсткое правило: у выученного ответа обязательно есть источник, и `chunk_id`
проверяется по корпусу. Выучить «ответ без нормы» нельзя — иначе это обход
guardrail, а не доучивание.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from app.kb.models import Chunk
from app.models.schemas import (
    Citation,
    TaughtAnswer,
    TaughtAnswerCreate,
    TaughtAnswerUpdate,
    TaughtMatch,
    TaughtSource,
)

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[\w]+", re.UNICODE)
_MIN_STEM = 5  # «рожден» ловит «рождения»; более короткие стемы дают ложные срабатывания
# Слово, которое встречается в корпусе чаще этой доли пунктов, считается
# «пустым» триггером и само по себе ответ не включает.
_MAX_DF_SHARE = 0.25

# Слова-намерения: «какие документы», «где получить», «как оформить». Сами по
# себе они не привязаны к услуге, поэтому не могут включать выученный ответ.
# Реальный баг: вопрос «какие документы нужны для получения паспорта» отдавал
# документы для пособия при рождении — только потому, что в нём есть фраза
# «какие документы». Лечится не частотностью (слово «документы» редкое), а
# запретом: общий триггер обязан быть привязан к услуге.
_GENERIC_TRIGGER_TOKENS = {
    "как", "какие", "какой", "какая", "где", "куда", "когда", "сколько",
    "нужно", "нужны", "надо", "получить", "оформить", "подать", "сделать",
    "документ", "документы", "справка", "справки", "заявление", "перечень",
    "порядок", "срок", "сроки", "условия", "размер", "выплата", "пособие",
}


def _tokens(text: str) -> list[str]:
    """Токены в нижнем регистре с ё→е.

    Без нормализации ё основа слова не совпадает: в акте «ребёнка» даёт стем
    «ребён», а пользователь пишет «ребенка» — «ребен». Из-за этого не работал
    ни отсев «пустых» триггеров, ни само сопоставление.
    """
    normalized = text.lower().replace("ё", "е")
    return [t for t in _TOKEN_RE.findall(normalized) if len(t) > 1]


def _stem(token: str) -> str:
    """Грубая основа слова: «рождения» → «рожден».

    Полноценная морфология тут не нужна и потянула бы зависимость: у нас
    не поиск по корпусу, а сопоставление заранее выбранных человеком триггеров.
    """
    return token[: max(_MIN_STEM, len(token) - 2)] if len(token) > _MIN_STEM else token


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class TaughtStore:
    """Файловое хранилище выученных ответов. Потокобезопасно.

    JSON, а не Postgres: слой маленький, нужен на демо, и его не должно
    блокировать отсутствие Docker. Переезд в Postgres — отдельная задача.
    """

    def __init__(self, path: Path, chunks: list[Chunk] | None = None) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._answers: list[TaughtAnswer] = []
        self._corpus_ids: set[str] = set()
        self._token_df: Counter[str] = Counter()
        self._corpus_size = 0
        self.set_corpus(chunks or [])
        self._load()

    # --- корпус для проверки источников -------------------------------------

    def set_corpus(self, chunks: list[Chunk]) -> None:
        """Обновляет корпус: какие chunk_id существуют и насколько редки слова.

        Частотность нужна для отсева «пустых» триггеров: слово «ребёнок» есть
        почти в каждом пункте закона, и при режиме «достаточно одного» оно
        утаскивало на выученный ответ любой вопрос про детей. Проверено на
        живом API: вопрос «какие документы нужны» отдавал ответ про 14 БПМ.
        """
        self._corpus_ids = {chunk.chunk_id for chunk in chunks}
        self._corpus_size = len(chunks)
        token_df: Counter[str] = Counter()
        for chunk in chunks:
            stems = {_stem(token) for token in _tokens(chunk.text)}
            token_df.update(stems)
        self._token_df = token_df

    def is_distinctive(self, keyword: str) -> bool:
        """Редкое ли слово: встречается в корпусе, но меньше чем в 25% пунктов.

        Для многословной фразы смотрим по отдельным словам. Раньше фраза
        стеммилась целиком («какие документы» → «какие докумен»), такого стема
        в словаре нет — и фраза автоматически считалась редкой.
        """
        if self._corpus_size == 0:
            return True  # корпуса нет — судить не по чему, не блокируем
        tokens = _tokens(keyword) or [keyword.strip().lower()]
        for token in tokens:
            stem = _stem(token)
            count = self._token_df.get(stem, 0)
            # Слова нет в корпусе — это не «редкое совпадение», а отсутствие
            # доказательства: такое слово специфичности не даёт.
            if count and count / self._corpus_size <= _MAX_DF_SHARE:
                return True
        return False

    @staticmethod
    def keywords_are_generic(keywords: list[str]) -> bool:
        """Все триггеры — слова-намерения («какие документы», «где получить»).

        Такой ответ без привязки к услуге срабатывать не должен: он ответит
        не на тот вопрос. Админка это отклоняет, а match() — игнорирует.
        """
        if not keywords:
            return True
        for keyword in keywords:
            tokens = _tokens(keyword) or [keyword.strip().lower()]
            if any(token not in _GENERIC_TRIGGER_TOKENS for token in tokens):
                return False
        return True

    def unknown_sources(self) -> list[tuple[str, str]]:
        """Выученные ответы со ссылками на несуществующие чанки: (id ответа, chunk_id)."""
        broken: list[tuple[str, str]] = []
        for item in self._answers:
            for source in item.sources:
                if source.chunk_id not in self._corpus_ids:
                    broken.append((item.id, source.chunk_id))
        return broken

    # --- чтение/запись файла ------------------------------------------------

    def _load(self) -> None:
        if not self.path.exists():
            self._answers = []
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self._answers = [TaughtAnswer.model_validate(item) for item in raw]
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("Выученные ответы не прочитаны (%s): %s", self.path, exc)
            self._answers = []

    def _save(self) -> None:
        """Атомарная запись: сначала во временный файл, потом подмена."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = [item.model_dump(mode="json") for item in self._answers]
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    # --- CRUD ---------------------------------------------------------------

    def all(self, enabled_only: bool = False) -> list[TaughtAnswer]:
        with self._lock:
            items = list(self._answers)
        return [i for i in items if i.enabled] if enabled_only else items

    def get(self, answer_id: str) -> TaughtAnswer | None:
        with self._lock:
            return next((i for i in self._answers if i.id == answer_id), None)

    def create(self, payload: TaughtAnswerCreate) -> TaughtAnswer:
        stamp = _now()
        item = TaughtAnswer(
            id=uuid.uuid4().hex[:12],
            created_at=stamp,
            updated_at=stamp,
            **payload.model_dump(),
        )
        with self._lock:
            self._answers.append(item)
            self._save()
        return item

    def update(self, answer_id: str, payload: TaughtAnswerUpdate) -> TaughtAnswer | None:
        with self._lock:
            for index, item in enumerate(self._answers):
                if item.id != answer_id:
                    continue
                changes = payload.model_dump(exclude_none=True)
                updated = item.model_copy(update={**changes, "updated_at": _now()})
                self._answers[index] = updated
                self._save()
                return updated
        return None

    def delete(self, answer_id: str) -> bool:
        with self._lock:
            before = len(self._answers)
            self._answers = [i for i in self._answers if i.id != answer_id]
            if len(self._answers) == before:
                return False
            self._save()
            return True

    # --- срабатывание -------------------------------------------------------

    def match(
        self,
        question: str,
        service_id: str | None = None,
        routed_services: list[str] | None = None,
    ) -> TaughtMatch | None:
        """Ищет выученный ответ, который срабатывает на вопрос.

        Правило специфичности: совпадение по «пустому» слову (которое есть
        в корпусе чаще `_MAX_DF_SHARE`) само по себе ответ не включает — нужен
        хотя бы один редкий триггер. Иначе слово «ребёнок» угоняло любой
        вопрос про детей: проверено, «какие документы нужны» отдавал ответ
        про 14 БПМ вместо честного отказа.

        Приоритет: привязка к услуге (service_id) важнее общих ответов, затем
        больше совпавших триггеров.
        """
        tokens = {_stem(token) for token in _tokens(question)}
        best: TaughtMatch | None = None
        routed = set(routed_services or [])
        if service_id:
            routed.add(service_id)

        for item in self.all(enabled_only=True):
            bound = bool(item.service_id)
            if bound and item.service_id not in routed:
                # Ответ привязан к услуге и уместен ТОЛЬКО когда вопрос
                # распознан как вопрос об этой услуге. Проверка жёсткая: если
                # маршрут пуст (вопрос не про наши услуги) — тоже не срабатываем.
                # Раньше при пустом маршруте условие пропускало проверку, и
                # «какие документы для паспорта» получало документы на пособие.
                continue
            if not bound and self.keywords_are_generic(item.keywords):
                # Общий триггер без привязки — не срабатывает вовсе.
                continue
            matched = [
                keyword
                for keyword in item.keywords
                if self._keyword_hits(keyword, question, tokens)
            ]
            if not matched:
                continue
            if item.match_mode == "all" and len(matched) < len(item.keywords):
                continue
            # Привязка к услуге уже различает вопрос — проверка редкости для
            # таких ответов не нужна (иначе «какие документы» + привязка
            # блокировались бы дважды). Без привязки — нужен редкий триггер.
            if not bound and not any(self.is_distinctive(keyword) for keyword in matched):
                continue

            score = len(matched) / len(item.keywords)
            if item.service_id and item.service_id == service_id:
                score += 0.5  # ответ, привязанный к странице услуги, приоритетнее
            if best is None or score > best.score:
                best = TaughtMatch(taught=item, matched_keywords=matched, score=score)
        return best

    @staticmethod
    def _keyword_hits(keyword: str, question: str, tokens: set[str]) -> bool:
        keyword_lower = keyword.lower().strip()
        if not keyword_lower:
            return False
        # Короткие триггеры («срок», «дети») сверяем по основе, длинные фразы —
        # подстрокой: «до какого срока обратиться» должен ловиться целиком.
        if " " in keyword_lower:
            return keyword_lower in question.lower()
        return _stem(keyword_lower) in tokens

    # --- превращение в цитаты ----------------------------------------------

    def citations_for(self, item: TaughtAnswer, chunks_by_id: dict[str, Chunk]) -> list[Citation]:
        """Цитаты выученного ответа — дословные фрагменты корпуса, не текст админа."""
        citations: list[Citation] = []
        for source in item.sources:
            chunk = chunks_by_id.get(source.chunk_id)
            if chunk is None:
                # Источник пропал из корпуса — ответ отдавать нельзя.
                continue
            citations.append(
                Citation(
                    chunk_id=chunk.chunk_id,
                    doc_title=chunk.doc_title or "",
                    doc_number=chunk.doc_number,
                    doc_date=chunk.doc_date,
                    location=chunk.location.label() or None,
                    url=chunk.doc_url,
                    quote=chunk.text[:600],
                )
            )
        return citations

    def sources_ok(self, payload_sources: list[TaughtSource]) -> list[str]:
        """Возвращает chunk_id, которых нет в корпусе (валидация при создании)."""
        return [s.chunk_id for s in payload_sources if s.chunk_id not in self._corpus_ids]
