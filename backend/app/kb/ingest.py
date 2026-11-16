"""Конвейер индексации: kb_corpus/curated/*.json → ChunkStore (+ Qdrant, если поднят).

Запуск из backend/:  python -m app.kb.ingest
Индексируем только чанки со статусом «проверен» (правило конвейера).
"""

import json
import logging
import sys

from app.core.config import Settings, get_settings
from app.kb.models import Chunk, CuratedDoc
from app.retrieval.store import ChunkStore

logger = logging.getLogger(__name__)


def load_curated(corpus_dir) -> tuple[list[CuratedDoc], list[str]]:
    """Читает все curated/*.json. Возвращает (документы, список ошибок)."""
    docs: list[CuratedDoc] = []
    errors: list[str] = []
    for path in sorted(corpus_dir.glob("*.json")):
        try:
            docs.append(CuratedDoc.model_validate(json.loads(path.read_text(encoding="utf-8"))))
        except (json.JSONDecodeError, ValueError) as exc:
            errors.append(f"{path.name}: {exc}")
    return docs, errors


def collect_chunks(docs: list[CuratedDoc]) -> list[Chunk]:
    chunks: list[Chunk] = []
    for doc in docs:
        for chunk in doc.chunks:
            chunk.doc_title = chunk.doc_title or doc.doc.title
            chunk.doc_number = chunk.doc_number or doc.doc.number
            chunk.doc_date = chunk.doc_date or doc.doc.date
            chunk.doc_url = chunk.doc_url or doc.doc.source_url
            chunks.append(chunk)
    return chunks


def ingest(settings: Settings, llm=None) -> tuple[int, int]:
    """BM25-индекс + (если доступны Qdrant и эмбеддинги) dense-плечо.

    Возвращает (чанков в BM25-индексе, векторов загружено в Qdrant).
    """
    docs, errors = load_curated(settings.corpus_dir)
    for error in errors:
        logger.warning("Кривой файл корпуса: %s", error)
    chunks = collect_chunks(docs)
    store = ChunkStore(
        qdrant_url=settings.qdrant_url if settings.use_qdrant else None,
        embedding_dim=settings.embedding_dim,
        allowed_statuses=settings.allowed_statuses,
        embedding_model=settings.embedding_model,
    )
    indexed = store.rebuild(chunks)

    if llm is None:
        from app.llm.client import LlmClient

        llm = LlmClient(settings)
    uploaded = store.dense_index(llm)
    if not uploaded:
        logger.info(
            "Dense-плечо не загружено (нет Qdrant или нет модели эмбеддингов %s) — "
            "поиск работает на BM25",
            settings.embedding_model,
        )
    logger.info("Проиндексировано %d чанков из %d актов", indexed, len(docs))
    return indexed, uploaded


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    indexed, uploaded = ingest(get_settings())
    print(f"OK: {indexed} чанков в BM25-индексе, {uploaded} векторов в Qdrant")
    sys.exit(0)


if __name__ == "__main__":
    main()
