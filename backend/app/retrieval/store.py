"""Гибридное хранилище чанков: локальный BM25 + опциональный dense-поиск Qdrant.

Дизайн под реалити MVP:
- BM25 — своя реализация на stdlib: ноль зависимостей, работает в тестах и
  даже если Docker не поднят;
- dense — Qdrant по REST через httpx (нативный гибрид в планах, сейчас
  ручной RRF-фьюжн); если Qdrant недоступен — честно деградируем в BM25-only,
  молча не остаёмся: флаг `dense_available`.
"""

import json
import math
import re
import threading
from collections import Counter

import httpx

from app.kb.models import Chunk, RetrievedChunk
from app.llm.client import LlmClient

_TOKEN_RE = re.compile(r"[\w]+", re.UNICODE)

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
    tokens = _TOKEN_RE.findall(text.lower())
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
        result = [0.0] * self.doc_count
        for i, counter in enumerate(self.doc_freqs):
            length = self.doc_lengths[i]
            if not length:
                continue
            norm = 1 - b + b * length / self.avg_len
            score = 0.0
            for term in query_tokens:
                freq = counter.get(term, 0)
                if not freq:
                    continue
                score += self.idf.get(term, 0.0) * freq * (k1 + 1) / (freq + k1 * norm)
            result[i] = score
        return result


class ChunkStore:
    """Индекс чанков корпуса. Thread-safe на чтение/перестройку."""

    def __init__(self, qdrant_url: str | None = None, embedding_dim: int = 1024) -> None:
        self._lock = threading.Lock()
        self._chunks: list[Chunk] = []
        self._bm25: _Bm25Index | None = None
        self._qdrant_url = qdrant_url
        self._embedding_dim = embedding_dim
        self.dense_available: bool = False

    # --- построение индекса -------------------------------------------------

    def rebuild(self, chunks: list[Chunk]) -> int:
        with self._lock:
            self._chunks = [c for c in chunks if c.status == "проверен"]
            self._bm25 = _Bm25Index([tokenize(c.text) for c in self._chunks])
        return len(self._chunks)

    def size(self) -> int:
        return len(self._chunks)

    def get(self, chunk_id: str) -> RetrievedChunk | None:
        for chunk in self._chunks:
            if chunk.chunk_id == chunk_id:
                return RetrievedChunk(chunk=chunk, score=0.0, via="direct")
        return None

    # --- BM25-плечо ---------------------------------------------------------

    def search_bm25(self, query: str, top_k: int = 6) -> list[RetrievedChunk]:
        if self._bm25 is None or not self._chunks:
            return []
        scores = self._bm25.scores(tokenize(query))
        ranked = sorted(
            range(len(self._chunks)), key=lambda i: scores[i], reverse=True
        )[:top_k]
        return [
            RetrievedChunk(chunk=self._chunks[i], score=scores[i], via="bm25")
            for i in ranked
            if scores[i] > 0
        ]

    # --- dense-плечо (Qdrant REST) -----------------------------------------

    def _qdrant_search(self, vector: list[float], top_k: int) -> list[RetrievedChunk]:
        if not self._qdrant_url:
            return []
        try:
            resp = httpx.post(
                f"{self._qdrant_url}/collections/darechy/points/search",
                json={"vector": vector, "limit": top_k, "with_payload": True},
                timeout=5.0,
            )
            resp.raise_for_status()
        except (httpx.HTTPError, json.JSONDecodeError):
            self.dense_available = False
            return []
        self.dense_available = True
        return [
            RetrievedChunk(
                chunk=Chunk.model_validate(point["payload"]),
                score=float(point["score"]),
                via="dense",
            )
            for point in resp.json()["result"]
        ]

    def search_dense(self, query: str, llm: LlmClient, top_k: int = 6) -> list[RetrievedChunk]:
        vector = llm.embed(query)
        if vector is None:
            self.dense_available = False
            return []
        return self._qdrant_search(vector, top_k)

    # --- гибрид -------------------------------------------------------------

    def search(
        self,
        query: str,
        llm: LlmClient,
        top_k: int = 6,
        score_threshold: float = 0.0,
    ) -> list[RetrievedChunk]:
        """Гибрид BM25 + dense с RRF-фьюжном (reciprocal rank fusion, k=60).

        `score_threshold` применяется к сырым скорам плеч (BM25 — униграммные
        веса, dense — косинус), НЕ к RRF: фьюжн-скор ранговый и мал по природе.
        """
        bm25_hits = [
            h for h in self.search_bm25(query, top_k=top_k * 2)
            if h.score >= score_threshold
        ]
        dense_hits = [
            h for h in self.search_dense(query, llm, top_k=top_k * 2)
            if h.score >= score_threshold
        ]

        fused: dict[str, list] = {}
        for hits in (bm25_hits, dense_hits):
            for rank, hit in enumerate(hits):
                rrf = 1.0 / (60 + rank + 1)
                entry = fused.get(hit.chunk.chunk_id)
                if entry:
                    entry[0] += rrf
                    entry[2].add(hit.via)
                else:
                    fused[hit.chunk.chunk_id] = [rrf, hit, {hit.via}]

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
        return merged[:top_k]
