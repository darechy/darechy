"""Трасса обработки вопроса: видно, ГДЕ произошёл промах.

Без трассы по конечному ответу нельзя сказать, что сломалось: поиск не нашёл
норму, нашёл не то, контекст потерял зависимый пункт или модель пересказала
неверно. Скрипт печатает каждый этап отдельно.

Запуск из backend/:
    python -m scripts.trace_question "какие документы нужны для пособия при рождении"
    python -m scripts.trace_question --no-llm "кто имеет право на пособие до 3 лет"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.chat.orchestrator import SYSTEM_PROMPT, Orchestrator, best_excerpt  # noqa: E402
from app.chat.router import ServiceRouter  # noqa: E402
from app.chat.taught import TaughtStore  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.kb.ingest import collect_chunks, load_curated  # noqa: E402
from app.llm.client import LlmClient  # noqa: E402
from app.models.schemas import ChatRequest  # noqa: E402
from app.retrieval.store import ChunkStore  # noqa: E402
from app.services.catalog import build_catalog  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("question")
    parser.add_argument("--no-llm", action="store_true", help="не звать модель (только поиск)")
    parser.add_argument("--top-k", type=int, default=6)
    args = parser.parse_args()

    settings = get_settings()
    llm = LlmClient(settings)
    docs, _ = load_curated(settings.corpus_dir)
    chunks = collect_chunks(docs)
    store = ChunkStore(
        qdrant_url=settings.qdrant_url if settings.use_qdrant else None,
        embedding_dim=settings.embedding_dim,
        allowed_statuses=settings.allowed_statuses,
    )
    store.rebuild(chunks)
    taught = TaughtStore(settings.taught_file, chunks)
    router = ServiceRouter(build_catalog(settings).all(), llm=None if args.no_llm else llm)
    orchestrator = Orchestrator(
        settings=settings, store=store, llm=llm, router=router, taught=taught
    )

    print(f"ВОПРОС: {args.question}\n")

    routed, by = orchestrator._route(args.question)
    print(f"1) МАРШРУТ: {routed or '—'} (решил: {by})")
    scope = router.scope_chunk_ids(routed) if routed else set()
    print(f"   скоуп по источникам услуги: {sorted(scope) or '—'}\n")

    print("2) ДОУЧИВАНИЕ:")
    hit = taught.match(args.question, routed_services=routed)
    if hit:
        print(f"   сработал ответ {hit.taught.id} (услуга: {hit.taught.service_id or '—'}),"
              f" триггеры: {hit.matched_keywords}")
    else:
        print("   выученного ответа нет")

    bm25 = store.search_bm25(args.question, top_k=args.top_k * 2)
    dense = store.search_dense(args.question, llm, top_k=args.top_k * 2)
    print("\n3) ПОИСК, плечи отдельно:")
    print("   BM25: ", [(h.chunk.chunk_id, round(h.score, 2)) for h in bm25[:5]] or "пусто")
    print("   dense:", [(h.chunk.chunk_id, round(h.score, 3)) for h in dense[:5]] or "пусто")

    hits = store.search(
        args.question, llm, top_k=args.top_k, prefer_chunk_ids=scope or None
    )
    print("\n4) ИТОГ ПОИСКА (после фьюжна, группировки и скоупа):")
    for position, h in enumerate(hits, start=1):
        print(f"   {position}. {h.chunk.chunk_id:52} {h.score:.4f} via={h.via}")
    if not hits:
        print("   пусто → будет честный отказ (порог релевантности)")

    if args.no_llm or not hits:
        return

    context = orchestrator._build_context(ChatRequest(message=args.question), hits, [])
    print(f"\n5) КОНТЕКСТ В МОДЕЛЬ: {len(context)} символов")
    for h in hits:
        print(f"   [{h.chunk.chunk_id}] {len(best_excerpt(h.chunk.text, args.question))} симв")

    raw = llm.chat(SYSTEM_PROMPT, context)
    print(f"\n6) СЫРОЙ ОТВЕТ МОДЕЛИ:\n{raw}\n")

    response = orchestrator.answer(ChatRequest(message=args.question))
    print(f"7) ИТОГ: source={response.source} отказ={response.refusal}"
          f" латентность={response.latency_ms} мс")
    print(f"   {response.answer[:400]}")
    for citation in response.citations:
        print(f"   цитата: {citation.location} | {citation.doc_title[:50]}")


if __name__ == "__main__":
    main()
