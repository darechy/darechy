"""Тесты гибридного хранилища: BM25-ранжирование и RRF-фьюжн."""

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


def test_hybrid_search_fuses_bm25_and_dense() -> None:
    """Dense-плечо подменяем вручную: RRF должен поднять чанк, найденный обоими."""
    store = _store_with_chunks()
    store.dense_available = True
    dense_hit = make_hit_like(store, "a_p2")
    store.search_dense = lambda query, llm, top_k=6: [dense_hit]  # type: ignore[method-assign]

    hits = store.search("какой размер пособия на ребёнка", FakeLlm(), top_k=3)
    assert hits[0].chunk.chunk_id == "a_p2"
    assert hits[0].via == "both"


def make_hit_like(store: ChunkStore, chunk_id: str):
    from tests.fakes import make_hit

    return make_hit(store.get(chunk_id).chunk, score=0.9, via="dense")  # type: ignore[union-attr]
