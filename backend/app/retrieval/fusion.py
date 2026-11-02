"""Слияние плеч поиска (BM25 + dense) в один ранжированный список.

Почему не чистый RRF, как было: RRF оперирует только рангами и потому не
различает «слегка лучше» и «в разы лучше». Замер на корпусе (14 размеченных
вопросов) дал: dense 14/14, BM25 9/14, RRF 13/14. Единственный провал RRF —
«до какого срока обратиться за пособием»: dense уверенно ставит ст. 22 первой
(0.704 против 0.644 у ст. 16), а BM25 топит ст. 22 на 4-е место (2.33 против
5.10). RRF награждает ст. 16 за согласие обоих плеч и перебивает верный ответ.

Отсюда правило: **dense решает, когда уверен**. Если его лидер отрывается от
второго больше чем на `dense_margin`, доверяем dense — он видит смысл вопроса,
а BM25 всего лишь совпадение слов. Если плотно (модель колеблется) — отдаём
слово RRF-фьюжну, где работает согласие плеч.
"""

from __future__ import annotations

from app.kb.models import RetrievedChunk

# Дефолт подобран по бенчмарку: на провальном кейсе разрыв dense = 0.06,
# поэтому порог 0.02 не мешает dense решать, но не даёт ему «продавливать»
# случайный лидер при плотных скорах.
DEFAULT_DENSE_MARGIN = 0.02


def rrf_fuse(
    bm25: list[RetrievedChunk],
    dense: list[RetrievedChunk],
    k: int = 60,
) -> list[RetrievedChunk]:
    """Reciprocal rank fusion: 1/(k + rank) от каждого плеча, суммы сортируются."""
    fused: dict[str, list] = {}
    for hits in (bm25, dense):
        for rank, hit in enumerate(hits):
            weight = 1.0 / (k + rank + 1)
            entry = fused.get(hit.chunk.chunk_id)
            if entry:
                entry[0] += weight
                entry[2].add(hit.via)
            else:
                fused[hit.chunk.chunk_id] = [weight, hit, {hit.via}]

    merged: list[RetrievedChunk] = []
    for score, hit, via in fused.values():
        merged.append(
            RetrievedChunk(
                chunk=hit.chunk,
                score=score,
                via="both" if len(via) > 1 else next(iter(via)),
            )
        )
    merged.sort(key=lambda h: h.score, reverse=True)
    return merged


def dense_is_confident(
    dense: list[RetrievedChunk],
    margin: float = DEFAULT_DENSE_MARGIN,
) -> bool:
    """Уверен ли dense: лидер отрывается от второго больше чем на margin.

    У dense всего один кандидат — сравнивать не с чем, считаем уверенным.
    """
    if len(dense) < 2:
        return bool(dense)
    return (dense[0].score - dense[1].score) > margin


def fuse_rankings(
    bm25: list[RetrievedChunk],
    dense: list[RetrievedChunk],
    top_k: int,
    margin: float = DEFAULT_DENSE_MARGIN,
) -> list[RetrievedChunk]:
    """Итоговое ранжирование: уверенный dense важнее фьюжна, иначе — RRF.

    `top_k <= 0` — без ограничения (вернуть всё). Раньше при `top_k=0` ветки
    «одно плечо пустое» возвращали пустой список, а основная ветка — всё:
    противоречие, на котором спотыкался бенчмарк.
    """
    limit = top_k if top_k > 0 else None

    if not dense:
        return bm25 if limit is None else bm25[:limit]
    if not bm25:
        return dense if limit is None else dense[:limit]

    if dense_is_confident(dense, margin):
        chosen = dense
    else:
        chosen = rrf_fuse(bm25, dense)

    return chosen if limit is None else chosen[:limit]
