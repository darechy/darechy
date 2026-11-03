"""Группировка пунктов в статьи при поиске.

Зачем: корпус нарезан на пункты (требование `kb_corpus/README.md` — чанк = пункт),
это даёт точную цитату. Но поиск по коротким пунктам теряет полноту: статья про
сроки обращения была одним текстом на 12 400 знаков, а стала десятком кусков по
два предложения, и вопрос «до какого срока обратиться за пособием» перестаёт
совпадать с нужным куском. Замер: BM25 упал с 9/14 до 0/14 сразу после разбора.

Решение — классическая двухступенчатая схема: ищем по пунктам (точно), но
ранжируем **статьи**: статью представляет её лучший пункт, а соседние пункты
дают небольшой бонус за согласие. В ответ уходит конкретный пункт-победитель —
цитата остаётся точечной.
"""

from __future__ import annotations

import re
from collections import defaultdict

from app.kb.models import RetrievedChunk

_ARTICLE_RE = re.compile(r"^(?P<article>.*_st-\d+)", re.UNICODE)

# Насколько второй и последующий пункты той же статьи усиливают её скор.
# Вес относительный (доля от скора лучшего пункта): так бонус одинаково
# работает и для BM25, и для косинуса, и для рангового RRF.
_NEIGHBOUR_BONUS = 0.05


def article_key(chunk_id: str) -> str:
    """«7-Z_st-11_п-2» → «7-Z_st-11». Если разбора на пункты не было — сам id."""
    match = _ARTICLE_RE.match(chunk_id)
    return match.group("article") if match else chunk_id


def group_by_article(hits: list[RetrievedChunk]) -> list[RetrievedChunk]:
    """Сворачивает пункты в статьи: статью представляет её лучший пункт.

    Возвращает по одному хиту на статью (лучший пункт), отсортированных по
    агрегированному скору. `via` берётся у лучшего пункта.
    """
    if not hits:
        return []

    grouped: dict[str, list[RetrievedChunk]] = defaultdict(list)
    for hit in hits:
        grouped[article_key(hit.chunk.chunk_id)].append(hit)

    result: list[RetrievedChunk] = []
    for article_hits in grouped.values():
        article_hits.sort(key=lambda h: h.score, reverse=True)
        best = article_hits[0]
        # Бонус ПРОПОРЦИОНАЛЕН скору, а не фиксирован: BM25, косинус и RRF
        # живут в разных шкалах, и одна и та же константа 0.05 для RRF
        # (вклад плеча ≈ 0.016) весила больше, чем для BM25 — группировка
        # начинала искажать ранжирование.
        score = best.score * (1.0 + _NEIGHBOUR_BONUS * (len(article_hits) - 1))
        result.append(
            RetrievedChunk(chunk=best.chunk, score=score, via=best.via)
        )

    result.sort(key=lambda h: h.score, reverse=True)
    return result
