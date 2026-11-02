"""Тесты гибридного хранилища: BM25-ранжирование, RRF-фьюжн, индексация в Qdrant."""

import uuid

import httpx

from app.retrieval.store import ChunkStore, tokenize
from tests.fakes import FakeLlm, make_chunk


def _store_with_chunks() -> ChunkStore:
    store = ChunkStore(qdrant_url=None)
    store.rebuild(
        [
            make_chunk("a_p1", "Пособие на ребёнка назначается с даты обращения в орган соцзащиты."),
            make_chunk("a_p2", "Размер пособия определяется базовой величиной на день рождения ребёнка."),
            make_chunk("b_p1", "Свидетельство о рождении выдаётся органом записи актов гражданского состояния."),
        ]
    )
    return store


def test_tokenize_drops_stop_words() -> None:
    tokens = tokenize("Какие документы нужны для назначения пособия на ребёнка?")
    assert "документы" in tokens
    assert "пособия" in tokens
    assert "какие" not in tokens  # стоп-слово
    assert "для" not in tokens  # стоп-слово


def test_bm25_ranks_relevant_chunk_first() -> None:
    store = _store_with_chunks()
    hits = store.search_bm25("размер пособия базовая величина", top_k=2)
    assert hits, "BM25 обязан найти совпадения"
    assert hits[0].chunk.chunk_id == "a_p2"
    assert hits[0].via == "bm25"


def test_bm25_no_match_returns_empty() -> None:
    store = _store_with_chunks()
    assert store.search_bm25("лотерея подгузники выигрыш", top_k=3) == []


def test_store_get_returns_chunk() -> None:
    store = _store_with_chunks()
    hit = store.get("a_p1")
    assert hit is not None and hit.chunk.doc_number == "№ 57"
    assert store.get("нет такого") is None


def test_rebuild_filters_unverified() -> None:
    store = ChunkStore(qdrant_url=None)
    chunks = [
        make_chunk("ok_1", "Проверенный чанк про пособие."),
        make_chunk("raw_1", "Непроверенный черновик."),
    ]
    chunks[1].status = "разобран"
    assert store.rebuild(chunks) == 1
    assert store.get("raw_1") is None


def test_hybrid_prefers_confident_dense() -> None:
    """Уверенный dense решает: BM25 не должен перебивать его голосом рангов.

    Ровно этот сценарий ломал чистый RRF на корпусе: на «до какого срока
    обратиться за пособием» dense ставил ст. 22 первой, BM25 топил её на
    4-е место, и RRF отдавал неверную ст. 16.
    """
    store = _store_with_chunks()
    dense_hit = make_hit_like(store, "a_p2", score=0.9)
    runner_up = make_hit_like(store, "a_p1", score=0.5)  # разрыв 0.4 > margin
    store.search_dense = lambda query, llm, top_k=6: [dense_hit, runner_up]  # type: ignore[method-assign]

    hits = store.search("какой размер пособия на ребёнка", FakeLlm(), top_k=3)
    assert hits[0].chunk.chunk_id == "a_p2"
    assert hits[0].via == "dense"


def test_hybrid_falls_back_to_rrf_when_dense_unsure() -> None:
    """Плотные скоры dense = модель колеблется → решает согласие плеч (RRF)."""
    store = _store_with_chunks()
    # Разрыв 0.01 меньше порога: dense не уверен, слово за фьюжном.
    first = make_hit_like(store, "a_p1", score=0.70)
    second = make_hit_like(store, "a_p2", score=0.69)
    store.search_dense = lambda query, llm, top_k=6: [first, second]  # type: ignore[method-assign]

    hits = store.search("пособие назначается с даты обращения", FakeLlm(), top_k=3)
    # via="both" возможен только через RRF: он и есть признак фьюжна.
    assert any(h.via == "both" for h in hits)


def make_hit_like(store: ChunkStore, chunk_id: str, score: float = 0.9):
    from tests.fakes import make_hit

    return make_hit(store.get(chunk_id).chunk, score=score, via="dense")  # type: ignore[union-attr]


# --- индексация dense-плеча -------------------------------------------------


class _FakeResponse:
    def __init__(self, status_code: int = 200, payload=None) -> None:
        self.status_code = status_code
        self._payload = payload if payload is not None else {"result": True}

    def json(self):
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=None)  # type: ignore[arg-type]


class _RecordingQdrant:
    """Подменяет httpx-вызовы store: пишет, что и куда улетело."""

    def __init__(self, *, collection_exists: bool = False, vector_size: int = 8) -> None:
        self.collection_exists = collection_exists
        self.vector_size = vector_size
        self.created: list[dict] = []
        self.upserted: list[list[dict]] = []
        self.deleted: list[str] = []

    def get(self, url, timeout=None):
        if url.endswith("/collections/darechy") and self.collection_exists:
            return _FakeResponse(
                payload={
                    "result": {"config": {"params": {"vectors": {"size": self.vector_size}}}}
                }
            )
        return _FakeResponse(status_code=404, payload={"status": "error"})

    def put(self, url, json=None, timeout=None):
        if url.endswith("/points"):
            self.upserted.append(json["points"])
        else:
            self.created.append(json)
        return _FakeResponse()

    def delete(self, url, timeout=None):
        self.deleted.append(url)
        return _FakeResponse()


def _patch_qdrant(monkeypatch, fake: _RecordingQdrant) -> None:
    monkeypatch.setattr("app.retrieval.store.httpx.get", fake.get)
    monkeypatch.setattr("app.retrieval.store.httpx.put", fake.put)
    monkeypatch.setattr("app.retrieval.store.httpx.delete", fake.delete)


def test_dense_index_without_qdrant_is_noop() -> None:
    """Нет Qdrant — индексация не падает, просто возвращает 0."""
    store = _store_with_chunks()  # qdrant_url=None
    assert store.dense_index(FakeLlm()) == 0
    assert store.dense_available is False


def test_dense_index_creates_collection_and_upserts_points(monkeypatch) -> None:
    store = ChunkStore(qdrant_url="http://qdrant:6333")
    store.rebuild([make_chunk("a_p1", "Текст про пособие.")])
    fake = _RecordingQdrant(collection_exists=False)
    _patch_qdrant(monkeypatch, fake)

    assert store.dense_index(FakeLlm()) == 1
    assert fake.created == [{"vectors": {"size": 8, "distance": "Cosine"}}]
    point = fake.upserted[0][0]
    assert point["payload"]["chunk_id"] == "a_p1"
    # Qdrant принимает только unsigned int или UUID как id точки.
    assert uuid.UUID(point["id"])
    assert store.dense_available is True


def test_dense_index_recreates_collection_on_dimension_mismatch(monkeypatch) -> None:
    """Коллекция под старую модель — вектора молча не найдутся, поэтому пересоздаём."""
    store = ChunkStore(qdrant_url="http://qdrant:6333")
    store.rebuild([make_chunk("a_p1", "Текст про пособие.")])
    fake = _RecordingQdrant(collection_exists=True, vector_size=1024)
    _patch_qdrant(monkeypatch, fake)

    store.dense_index(FakeLlm())
    assert fake.deleted, "коллекция с чужой размерностью должна быть удалена"
    assert fake.created == [{"vectors": {"size": 8, "distance": "Cosine"}}]


def test_dense_index_keeps_collection_when_dimension_matches(monkeypatch) -> None:
    store = ChunkStore(qdrant_url="http://qdrant:6333")
    store.rebuild([make_chunk("a_p1", "Текст про пособие.")])
    fake = _RecordingQdrant(collection_exists=True, vector_size=8)
    _patch_qdrant(monkeypatch, fake)

    store.dense_index(FakeLlm())
    assert fake.deleted == []
    assert fake.created == []


def test_dense_index_bails_out_without_embeddings(monkeypatch) -> None:
    """Нет модели эмбеддингов — честный 0, BM25-плечо продолжает работать."""

    class NoEmbed(FakeLlm):
        def embed(self, text: str):
            return None

    store = ChunkStore(qdrant_url="http://qdrant:6333")
    store.rebuild([make_chunk("a_p1", "Текст про пособие.")])
    fake = _RecordingQdrant()
    _patch_qdrant(monkeypatch, fake)

    assert store.dense_index(NoEmbed()) == 0
    assert fake.upserted == []
