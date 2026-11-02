"""Свип по порогам релевантности: не сломает ли отказ улучшение поиска.

Компромисс, который здесь замеряется:
- группировка пунктов в статьи помогает отсекать вопросы вне базы
  (лучший пункт статьи набирает больше, чем одиночный пункт),
- но она же тянет вниз точность: статья, чей лучший пункт оказался не тем,
  обгоняет верную статью (dense 92.9% → 85.7% на 14 размеченных вопросах).

Скрипт считает для каждого варианта (с группировкой и без) два числа:
точность по размеченным вопросам и сколько вопросов ВНЕ базы проскочило.
Решение принимается по цифрам, а не по вкусу.

Запуск из backend/:  python -m scripts.tune_gate
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import get_settings  # noqa: E402
from app.kb.ingest import collect_chunks, load_curated  # noqa: E402
from app.llm.client import LlmClient  # noqa: E402
from app.retrieval.store import ChunkStore  # noqa: E402
from scripts.bench_retrieval import FACT_CASES, article_of  # noqa: E402

# Вопросы вне базы: юридические (похожи лексикой, но ответа в корпусе нет)
# и посторонние (лексика вообще не пересекается).
OUT_OF_SCOPE = [
    "как выиграть в лотерею и потратить приз на подгузники",
    "какой сегодня курс доллара",
    "как получить паспорт в россии",
    "посоветуй коляску для новорожденного",
    "напиши стихотворение про очередь в исполкоме",
    "какие документы нужны для пособия при рождении ребенка",
    "нужна ли справка с места работы второго родителя",
]

BM25_GATES = [0.0, 0.5, 1.0, 2.0, 3.0]
COSINE_GATES = [0.0, 0.45, 0.55, 0.60, 0.65]


def evaluate(store: ChunkStore, llm: LlmClient, group: bool, bm25_gate: float, cos_gate: float) -> tuple[float, int]:
    hits = 0
    for case in FACT_CASES:
        results = store.search(
            case.question, llm, top_k=6, min_bm25=bm25_gate, min_cosine=cos_gate,
            group_articles=group,
        )
        top1 = results[0].chunk.chunk_id if results else "-"
        hits += article_of(top1) == case.article

    leaked = sum(
        1
        for question in OUT_OF_SCOPE
        if store.search(question, llm, top_k=6, min_bm25=bm25_gate, min_cosine=cos_gate,
                        group_articles=group)
    )
    return hits / len(FACT_CASES), leaked


def main() -> None:
    settings = get_settings()
    llm = LlmClient(settings)
    docs, _ = load_curated(settings.corpus_dir)
    store = ChunkStore(
        qdrant_url=settings.qdrant_url if settings.use_qdrant else None,
        embedding_dim=settings.embedding_dim,
        allowed_statuses=settings.allowed_statuses,
    )
    store.rebuild(collect_chunks(docs))
    print(f"корпус: {store.size()} чанков; вопросов вне базы: {len(OUT_OF_SCOPE)}\n")

    for group in (True, False):
        print(f"=== группировка статей: {'вкл' if group else 'выкл'} ===")
        print(f"{'bm25>=':>8} {'cos>=':>7} {'точность':>9} {'проскочило':>11}")
        for bm25_gate in BM25_GATES:
            for cos_gate in COSINE_GATES:
                accuracy, leaked = evaluate(store, llm, group, bm25_gate, cos_gate)
                flag = "  <-- баланс" if accuracy >= 0.9 and leaked <= 1 else ""
                print(f"{bm25_gate:8.1f} {cos_gate:7.2f} {accuracy * 100:8.1f}% {leaked:10}/{len(OUT_OF_SCOPE)}{flag}")
        print()


if __name__ == "__main__":
    main()
