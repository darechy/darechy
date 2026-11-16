"""Гибридное хранилище чанков: локальный BM25 + опциональный dense-поиск Qdrant.

Дизайн под реалити MVP:
- BM25 — своя реализация на stdlib: ноль зависимостей, работает в тестах и
  даже если Docker не поднят;
- dense — Qdrant по REST через httpx (нативный гибрид в планах, сейчас
  ручной RRF-фьюжн); если Qdrant недоступен — честно деградируем в BM25-only,
  молча не остаёмся: флаг `dense_available`.
"""

import hashlib
import math
import re
import threading
import uuid
from collections import Counter

import httpx

from app.kb.models import Chunk, RetrievedChunk
from app.llm.client import LlmClient
from app.retrieval.fusion import DEFAULT_DENSE_MARGIN, fuse_rankings
from app.retrieval.grouping import article_key, group_by_article

_COLLECTION = "darechy"
# Qdrant принимает только unsigned int или UUID как id точки — маппим chunk_id.
_UUID_NS = uuid.UUID("6f1d2a3b-4c5d-4e6f-8a9b-0c1d2e3f4a5b")
_EMBED_BATCH = 16
_TITLE_WEIGHT = 1.5

# Пороги релевантности = guardrail «нет источника → отказ».
# Числа подобраны свипом (`python -m scripts.tune_gate`) на 14 размеченных
# вопросах и 7 заведомо внешних. Порог 0.65 был подобран на корпусе из одного
# закона и перестал работать с добавлением перечня позиций: у позиций косинус
# 0.56–0.59, и порог срезал верные ответы. Проверять на текущем корпусе, а не
# переносить числа с прошлого — свип для этого и лежит в репозитории.
# BM25-порог держит внешние вопросы: они дают ровно 0.00.
DEFAULT_MIN_BM25 = 1.0
DEFAULT_MIN_COSINE = 0.55

_TOKEN_RE = re.compile(r"[\w]+", re.UNICODE)

def _scope_by_articles(
    bm25: list[RetrievedChunk],
    dense: list[RetrievedChunk],
    prefer_chunk_ids: set[str],
) -> tuple[list[RetrievedChunk], list[RetrievedChunk]] | None:
    """Оставляет хиты, чья статья входит в статьи-источники услуги.

    None — в скоупе пусто, скоуп не применяем (мягкое ограничение).
    """
    allowed = {article_key(cid) for cid in prefer_chunk_ids}
    if not allowed:
        return None
    bm25_scoped = [h for h in bm25 if article_key(h.chunk.chunk_id) in allowed]
    dense_scoped = [h for h in dense if article_key(h.chunk.chunk_id) in allowed]
    if not bm25_scoped and not dense_scoped:
        return None
    return bm25_scoped, dense_scoped


# Минимальный стоп-набор: предлоги/союзы, убивающие BM25 на длинных вопросах.
_STOP_WORDS = {
    "и", "в", "во", "не", "что", "он", "на", "я", "с", "со", "как", "а", "то",
    "все", "она", "так", "его", "но", "да", "ты", "к", "у", "же", "вы", "за",
    "бы", "по", "только", "ее", "мне", "было", "вот", "от", "меня", "еще", "нет",
    "о", "из", "ему", "теперь", "когда", "даже", "ну", "ли", "если", "или", "ни",
    "быть", "был", "него", "до", "вас", "нибудь", "уже", "вам", "ведь", "там",
    "для", "какой", "какая", "какие", "это", "этом", "есть", "при", "об", "их",
}


def tokenize(text: str) -> list[str]:
    tokens = _TOKEN_RE.findall(text.lower().replace("ё", "е"))
    return [t for t in tokens if t not in _STOP_WORDS and len(t) > 1]


class _Bm25Index:
    """Okapi BM25 (k1=1.5, b=0.75) поверх токенизированных чанков."""

    def __init__(self, docs_tokens: list[list[str]]) -> None:
        self.docs_tokens = docs_tokens
        self.doc_count = len(docs_tokens)
        self.doc_freqs: list[Counter[str]] = [Counter(toks) for toks in docs_tokens]
        self.doc_lengths = [len(toks) for toks in docs_tokens]
        self.avg_len = (sum(self.doc_lengths) / self.doc_count) if self.doc_count else 0.0
        self.idf: dict[str, float] = {}
        df: Counter[str] = Counter()
        for counter in self.doc_freqs:
            for term in counter:
                df[term] += 1
        for term, freq in df.items():
            self.idf[term] = math.log(1 + (self.doc_count - freq + 0.5) / (freq + 0.5))

    def scores(self, query_tokens: list[str]) -> list[float]:
        k1, b = 1.5, 0.75
        query_terms = set(query_tokens)
        result = [0.0] * self.doc_count
        for i, counter in enumerate(self.doc_freqs):
            length = self.doc_lengths[i]
            if not length:
                continue
            norm = 1 - b + b * length / self.avg_len
            score = 0.0
            for term in query_terms:
                freq = counter.get(term, 0)
                if not freq:
                    continue
                score += self.idf.get(term, 0.0) * freq * (k1 + 1) / (freq + k1 * norm)
            result[i] = score
        return result


class ChunkStore:
    """Индекс чанков корпуса. Thread-safe на чтение/перестройку."""

    def __init__(
        self,
        qdrant_url: str | None = None,
        embedding_dim: int = 1024,
        allowed_statuses: frozenset[str] | None = None,
        embedding_model: str = "bge-m3",
    ) -> None:
        self._lock = threading.Lock()
        self._chunks: list[Chunk] = []
        self._bm25: _Bm25Index | None = None
        self._titles: _Bm25Index | None = None
        self._chunks_by_id: dict[str, Chunk] = {}
        self._dense_collection: str | None = None
        self._embedding_model = embedding_model
        self._qdrant_url = qdrant_url
        self._embedding_dim = embedding_dim
        # По умолчанию индексируем только проверенное — правило конвейера.
        # На dev/демо допускаем расширение через настройку, чтобы корпус,
        # помеченный «разобран», можно было прогнать целиком.
        self.allowed_statuses = allowed_statuses or frozenset({"проверен"})
        self.dense_available: bool = False

    # --- построение индекса -------------------------------------------------

    def rebuild(self, chunks: list[Chunk]) -> int:
        by_id = {chunk.chunk_id: chunk for chunk in chunks if chunk.status in self.allowed_statuses}
        indexed = list(by_id.values())
        bm25 = _Bm25Index([tokenize(chunk.text) for chunk in indexed])
        titles = _Bm25Index([tokenize(chunk.title or "") for chunk in indexed])
        with self._lock:
            if indexed != self._chunks:
                self.dense_available = False
                self._dense_collection = None
            self._chunks = indexed
            self._chunks_by_id = by_id
            self._bm25 = bm25
            self._titles = titles
        return len(indexed)

    def size(self) -> int:
        return len(self._chunks)

    def get(self, chunk_id: str) -> RetrievedChunk | None:
        chunk = self._chunks_by_id.get(chunk_id)
        if chunk is None:
            return None
        return RetrievedChunk(chunk=chunk, score=0.0, via="direct")

    # --- BM25-плечо ---------------------------------------------------------

    def search_bm25(self, query: str, top_k: int = 6) -> list[RetrievedChunk]:
        with self._lock:
            chunks, bm25, titles = self._chunks, self._bm25, self._titles
        if bm25 is None or titles is None or not chunks:
            return []
        tokens = tokenize(query)
        scores = [
            body + _TITLE_WEIGHT * title
            for body, title in zip(bm25.scores(tokens), titles.scores(tokens), strict=True)
        ]
        ranked = sorted(range(len(chunks)), key=scores.__getitem__, reverse=True)[:top_k]
        return [
            RetrievedChunk(chunk=chunks[i], score=scores[i], via="bm25")
            for i in ranked
            if scores[i] > 0
        ]

    # --- dense-плечо (Qdrant REST) -----------------------------------------

    def _prepare_collection(self, url: str, dimension: int) -> bool:
        """Возвращает True, если коллекция уже есть, False, если создана сейчас."""
        existing = httpx.get(url, timeout=5.0)
        if existing.status_code == 404:
            httpx.put(
                url,
                json={"vectors": {"size": dimension, "distance": "Cosine"}},
                timeout=15.0,
            ).raise_for_status()
            return False
        existing.raise_for_status()
        return True

    def dense_index(self, llm: LlmClient, batch_size: int = _EMBED_BATCH) -> int:
        """Подключает готовый индекс либо строит новый, сохраняя предыдущие версии."""
        if not self._qdrant_url:
            return 0
        with self._lock:
            chunks = self._chunks
        if not chunks:
            return 0

        probe = llm.embed("проверка размерности")
        if not probe:
            return 0
        dimension = len(probe)
        fingerprint = hashlib.sha256()
        fingerprint.update(f"v1:{self._embedding_model}:{dimension}\n".encode())
        for chunk in sorted(chunks, key=lambda value: value.chunk_id):
            fingerprint.update(chunk.model_dump_json().encode())
            fingerprint.update(b"\n")
        collection = f"{_COLLECTION}_{fingerprint.hexdigest()[:24]}"
        url = f"{self._qdrant_url}/collections/{collection}"

        try:
            exists = self._prepare_collection(url, dimension)
            count = 0
            if exists:
                response = httpx.post(
                    f"{url}/points/count", json={"exact": True}, timeout=5.0,
                )
                response.raise_for_status()
                count = response.json()["result"]["count"]
            if count != len(chunks):
                for start in range(0, len(chunks), batch_size):
                    batch = chunks[start : start + batch_size]
                    texts = [f"{chunk.title}\n{chunk.text}" if chunk.title else chunk.text for chunk in batch]
                    vectors = llm.embed_batch(texts)
                    if (
                        not vectors
                        or len(vectors) != len(batch)
                        or any(len(vector) != dimension for vector in vectors)
                    ):
                        return 0
                    points = [
                        {
                            "id": str(uuid.uuid5(_UUID_NS, chunk.chunk_id)),
                            "vector": vector,
                            "payload": {"chunk_id": chunk.chunk_id},
                        }
                        for chunk, vector in zip(batch, vectors, strict=True)
                    ]
                    httpx.put(
                        f"{url}/points",
                        params={"wait": "true"},
                        json={"points": points},
                        timeout=60.0,
                    ).raise_for_status()
        except (httpx.HTTPError, ValueError, TypeError, KeyError):
            return 0

        with self._lock:
            if self._chunks == chunks:
                self._dense_collection = collection
                self.dense_available = True
                return len(chunks)
        return 0

    def search_dense(self, query: str, llm: LlmClient, top_k: int = 6) -> list[RetrievedChunk]:
        with self._lock:
            collection, chunks_by_id = self._dense_collection, self._chunks_by_id
        if not self._qdrant_url or not collection:
            return []
        vector = llm.embed(query)
        if not vector:
            self.dense_available = False
            return []
        try:
            resp = httpx.post(
                f"{self._qdrant_url}/collections/{collection}/points/search",
                json={"vector": vector, "limit": top_k, "with_payload": True},
                timeout=5.0,
            )
            resp.raise_for_status()
            hits = [
                RetrievedChunk(chunk=chunk, score=float(point["score"]), via="dense")
                for point in resp.json()["result"]
                if (chunk := chunks_by_id.get(point["payload"]["chunk_id"])) is not None
            ]
        except (httpx.HTTPError, ValueError, TypeError, KeyError):
            self.dense_available = False
            return []
        with self._lock:
            if self._dense_collection != collection:
                return []
            self.dense_available = True
        return hits

    # --- гибрид -------------------------------------------------------------

    def search(
        self,
        query: str,
        llm: LlmClient,
        top_k: int = 6,
        score_threshold: float = 0.0,
        dense_margin: float = DEFAULT_DENSE_MARGIN,
        min_bm25: float = DEFAULT_MIN_BM25,
        min_cosine: float = DEFAULT_MIN_COSINE,
        group_articles: bool = False,
        prefer_chunk_ids: set[str] | None = None,
    ) -> list[RetrievedChunk]:
        """Гибрид BM25 + dense с порогом релевантности.

        Порог — это и есть guardrail «нет источника → отказ». Без него поиск
        всегда возвращает top-k, и на вопрос «какой сегодня курс доллара»
        ассистент бодро пересказывает статьи закона о пособиях (проверено:
        0 из 5 отказных кейсов проходили).

        Замер на корпусе показал, что классы разделяются чисто:
        - нерелевантные вопросы: BM25 = 0.00 (нет ни одного общего слова),
          косинус 0.355–0.449;
        - релевантные: BM25 3.77–14.51, косинус 0.633–0.747.

        Поэтому: вопрос проходит, только если сработало **хотя бы одно** плечо —
        либо лексическое совпадение (BM25 ≥ min_bm25), либо смысловое
        (косинус ≥ min_cosine). Пустой результат → оркестратор отдаёт отказ.

        `score_threshold` оставлен как более грубый фильтр для совместимости.

        `group_articles`: сворачивать ли пункты в статьи (см. `grouping.py`).
        По умолчанию ВЫКЛЮЧЕНО — по замеру на текущем корпусе
        (`scripts/tune_gate.py`, 14 размеченных вопросов): без группировки
        71,4% против 64,3% с ней. На корпусе из одного закона группировка
        помогала, с добавлением перечня позиций и других актов — перестала:
        параметр оставлен, чтобы это можно было перепроверить, а не верить.

        `prefer_chunk_ids`: скоуп по услуге. Если вопрос распознан как вопрос
        о конкретной услуге, искать в первую очередь надо в её актах — иначе
        «какие документы нужны» тянет документы из чужих процедур. Скоуп
        мягкий: если в нём ничего не нашлось, возвращаемся к поиску по всему
        корпусу, чтобы не отдать отказ на нормальный вопрос.
        """
        bm25_hits = [
            h
            for h in self.search_bm25(query, top_k=top_k * 2)
            if h.score >= max(score_threshold, min_bm25)
        ]
        dense_hits = [
            h
            for h in self.search_dense(query, llm, top_k=top_k * 2)
            if h.score >= max(score_threshold, min_cosine)
        ]
        if prefer_chunk_ids:
            scoped = _scope_by_articles(bm25_hits, dense_hits, prefer_chunk_ids)
            if scoped is not None:
                bm25_hits, dense_hits = scoped

        fused = fuse_rankings(bm25_hits, dense_hits, top_k=0, margin=dense_margin)
        if group_articles:
            return group_by_article(fused)[:top_k]
        return fused[:top_k]
