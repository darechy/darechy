"""Бенчмарк retrieval: BM25 vs dense vs гибрид, с настраиваемым фьюжном.

Зачем: гибрид с RRF давал 3/4 там, где dense в одиночку давал 4/4 — BM25 тянул
вверх мусорную статью, а RRF складывает только ранги и не различает «слегка
лучше» и «в разы лучше». Плюс сырые скоры плеч несравнимы (BM25 5.1 против
косинуса 0.70), поэтому их нельзя складывать без нормировки.

Разметка: `FACT_CASES` — ожидаемая статья для вопроса, размечено вручную по
тексту корпуса (в комментарии к каждому кейсу — почему именно эта статья).
Это не «модель сама себя проверила»: ожидание задано человеком и не меняется
от конфигурации поиска.

Запуск из backend/:
    python -m scripts.bench_retrieval            # все стратегии
    python -m scripts.bench_retrieval --verbose  # с топ-3 по каждому вопросу
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import REPO_DIR, get_settings  # noqa: E402
from app.kb.ingest import collect_chunks, load_curated  # noqa: E402
from app.kb.models import RetrievedChunk  # noqa: E402
from app.llm.client import LlmClient  # noqa: E402
from app.retrieval.fusion import fuse_rankings  # noqa: E402
from app.retrieval.store import ChunkStore  # noqa: E402


@dataclass(frozen=True)
class FactCase:
    question: str
    article: str  # ожидаемый chunk_id
    why: str  # почему именно эта статья — чтобы разметку можно было перепроверить


FACT_CASES: list[FactCase] = [
    FactCase(
        "в каком размере пособие при рождении первого ребенка",
        "7-Z_st-11",
        "ст. 11 «Пособие в связи с рождением ребенка», п. 2.1 — десятикратный БПМ",
    ),
    FactCase(
        "в каком размере пособие при рождении второго ребенка",
        "7-Z_st-11",
        "ст. 11 п. 2.2 — четырнадцатикратный БПМ на второго и последующих",
    ),
    FactCase(
        "сколько платят по уходу за ребенком до 3 лет",
        "7-Z_st-13",
        "ст. 13 «Размеры и условия выплаты пособия по уходу за ребенком до 3 лет»",
    ),
    FactCase(
        "до какого срока обратиться за пособием",
        "7-Z_st-22",
        "ст. 22 «Сроки обращения за назначением государственных пособий»",
    ),
    FactCase(
        "когда пересчитывают размеры пособий",
        "7-Z_st-27",
        "ст. 27 «Перерасчет размеров государственных пособий»",
    ),
    FactCase(
        "право на пособие женщинам ставшим на учет до 12 недель",
        "7-Z_st-10",
        "ст. 10 — пособие женщинам, ставшим на учет до 12-недельного срока",
    ),
    FactCase(
        "на какой период назначается пособие по беременности и родам",
        "7-Z_st-8",
        "ст. 8 «Период, на который назначается пособие по беременности и родам»",
    ),
    FactCase(
        "какие виды государственных пособий существуют",
        "7-Z_st-3",
        "ст. 3 «Виды государственных пособий»",
    ),
    FactCase(
        "кто имеет право на пособие по уходу за ребенком до 3 лет",
        "7-Z_st-12",
        "ст. 12 п. 1 — круг лиц, имеющих право на пособие",
    ),
    FactCase(
        "прекращение выплаты государственных пособий",
        "7-Z_st-24",
        "ст. 24 «Прекращение выплаты государственных пособий»",
    ),
    FactCase(
        "в каком размере пособие на ребенка-инвалида до 18 лет",
        "7-Z_st-18",
        "ст. 18 п. 5 — размеры пособия по уходу за ребенком-инвалидом",
    ),
    FactCase(
        "пособие на детей старше 3 лет из отдельных категорий семей",
        "7-Z_st-15",
        "ст. 15 — право и размеры для отдельных категорий семей",
    ),
    FactCase(
        "обжалование решения по назначению пособия",
        "7-Z_st-23",
        "ст. 23 «Обжалование решений по назначению государственных пособий»",
    ),
    FactCase(
        "пособие на ребенка инфицированного вирусом иммунодефицита",
        "7-Z_st-17",
        "ст. 17 — пособие на ребенка до 18 лет с ВИЧ",
    ),
]


def _norm(scores: dict[str, float]) -> dict[str, float]:
    """Min-max нормировка скоров плеча в [0, 1]."""
    if not scores:
        return {}
    lo, hi = min(scores.values()), max(scores.values())
    if hi == lo:
        return {key: 1.0 for key in scores}
    return {key: (value - lo) / (hi - lo) for key, value in scores.items()}


def fuse(
    bm25: list[RetrievedChunk],
    dense: list[RetrievedChunk],
    strategy: str,
    rrf_k: int = 60,
    dense_weight: float = 1.0,
) -> list[str]:
    """Возвращает chunk_id в порядке убывания релевантности для заданной стратегии."""
    if strategy == "bm25":
        return [h.chunk.chunk_id for h in bm25]
    if strategy == "dense":
        return [h.chunk.chunk_id for h in dense]

    scores: dict[str, float] = {}

    if strategy == "rrf":
        for rank, hit in enumerate(bm25):
            scores[hit.chunk.chunk_id] = scores.get(hit.chunk.chunk_id, 0.0) + 1 / (
                rrf_k + rank + 1
            )
        for rank, hit in enumerate(dense):
            scores[hit.chunk.chunk_id] = scores.get(hit.chunk.chunk_id, 0.0) + 1 / (
                rrf_k + rank + 1
            )
    elif strategy == "rrf_norm":
        bm25_norm = _norm({h.chunk.chunk_id: h.score for h in bm25})
        dense_norm = _norm({h.chunk.chunk_id: h.score for h in dense})
        for rank, hit in enumerate(bm25):
            weight = 1 / (rrf_k + rank + 1)
            scores[hit.chunk.chunk_id] = scores.get(hit.chunk.chunk_id, 0.0) + weight * (
                1 + bm25_norm[hit.chunk.chunk_id]
            )
        for rank, hit in enumerate(dense):
            weight = 1 / (rrf_k + rank + 1)
            scores[hit.chunk.chunk_id] = scores.get(hit.chunk.chunk_id, 0.0) + (
                weight * (1 + dense_norm[hit.chunk.chunk_id]) * dense_weight
            )
    elif strategy == "score":
        bm25_norm = _norm({h.chunk.chunk_id: h.score for h in bm25})
        dense_norm = _norm({h.chunk.chunk_id: h.score for h in dense})
        for chunk_id, value in bm25_norm.items():
            scores[chunk_id] = scores.get(chunk_id, 0.0) + value
        for chunk_id, value in dense_norm.items():
            scores[chunk_id] = scores.get(chunk_id, 0.0) + value * dense_weight
    elif strategy == "hybrid":
        # Боевая стратегия из app/retrieval/fusion.py
        return [h.chunk.chunk_id for h in fuse_rankings(bm25, dense, top_k=len(bm25) + len(dense))]
    else:
        raise ValueError(f"неизвестная стратегия: {strategy}")

    return sorted(scores, key=lambda cid: scores[cid], reverse=True)


def evaluate(strategy: str, store: ChunkStore, llm: LlmClient, top_k: int, verbose: bool) -> float:
    hits = 0
    for case in FACT_CASES:
        bm25 = store.search_bm25(case.question, top_k=top_k)
        dense = store.search_dense(case.question, llm, top_k=top_k)
        ranked = fuse(bm25, dense, strategy)
        top1 = ranked[0] if ranked else "-"
        ok = top1 == case.article
        hits += ok
        if verbose:
            mark = "OK " if ok else "МИМО"
            print(f"    {mark} ждали {case.article:12} получили {top1:12} {case.question}")
    return hits / len(FACT_CASES)


def write_golden_facts() -> None:
    """Дописывает факт-кейсы бенчмарка в eval/golden_set.json.

    Ожидание — «статья N»: eval/run_eval.py ищет подстроку в склейке
    doc_title + location цитаты, а location у нас «статья 22, пункт 3».
    Существующие кейсы (navigation/refusal) не трогаем.
    """
    import json
    import re as _re

    golden = REPO_DIR / "eval" / "golden_set.json"
    payload = json.loads(golden.read_text(encoding="utf-8"))
    cases = payload.setdefault("cases", [])

    existing = {case.get("id") for case in cases}
    added = 0
    for index, case in enumerate(FACT_CASES, start=1):
        case_id = f"fact-{index:03d}"
        article = _re.search(r"st-(\d+)", case.article)
        if not article:
            continue
        if case_id in existing:
            continue
        cases.append(
            {
                "id": case_id,
                "type": "fact",
                "question": case.question,
                "expected_citation_contains": f"статья {article.group(1)}",
                "expected_refusal": False,
                "_why": case.why,
            }
        )
        added += 1

    golden.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"дописано факт-кейсов: {added}; всего кейсов: {len(cases)}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--top-k", type=int, default=6)
    parser.add_argument(
        "--update-golden",
        action="store_true",
        help="дописать факт-кейсы в eval/golden_set.json (та же разметка, что в бенчмарке)",
    )
    args = parser.parse_args()

    if args.update_golden:
        write_golden_facts()
        return

    settings = get_settings()
    llm = LlmClient(settings)
    docs, _ = load_curated(settings.corpus_dir)
    store = ChunkStore(
        qdrant_url=settings.qdrant_url if settings.use_qdrant else None,
        embedding_dim=settings.embedding_dim,
    )
    chunks = collect_chunks(docs)
    store.rebuild(chunks)
    print(f"корпус: {len(chunks)} чанков, dense доступен: {store.dense_available}\n")

    strategies = ["bm25", "dense", "rrf", "score", "hybrid"]
    results: dict[str, float] = {}
    for strategy in strategies:
        if args.verbose:
            print(f"  {strategy}:")
        results[strategy] = evaluate(strategy, store, llm, args.top_k, args.verbose)

    print(f"{'стратегия':12} точность top-1")
    for strategy, value in sorted(results.items(), key=lambda kv: -kv[1]):
        print(f"{strategy:12} {value * 100:5.1f}%   ({round(value * len(FACT_CASES))}/{len(FACT_CASES)})")


if __name__ == "__main__":
    main()
