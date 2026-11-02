"""Тесты слияния плеч: правило «уверенный dense решает, иначе RRF».

Числа в тестах взяты с реального корпуса — это не выдумка под реализацию,
а зафиксированный случай, который ломал чистый RRF.
"""

from app.retrieval.fusion import (
    DEFAULT_DENSE_MARGIN,
    dense_is_confident,
    fuse_rankings,
    rrf_fuse,
)
from tests.fakes import make_chunk, make_hit


def _hits(pairs: list[tuple[str, float]], via: str):
    return [make_hit(make_chunk(chunk_id, f"текст {chunk_id}"), score=score, via=via)
            for chunk_id, score in pairs]


def test_dense_confident_when_gap_above_margin() -> None:
    dense = _hits([("st-22", 0.704), ("st-16", 0.644)], "dense")
    assert dense_is_confident(dense) is True


def test_dense_not_confident_when_scores_tight() -> None:
    dense = _hits([("st-3", 0.607), ("st-14", 0.606)], "dense")
    assert dense_is_confident(dense) is False


def test_dense_with_single_hit_counts_as_confident() -> None:
    assert dense_is_confident(_hits([("st-11", 0.7)], "dense")) is True
    assert dense_is_confident([]) is False


def test_confident_dense_overrides_bm25_ranking() -> None:
    """Реальный кейс: BM25 ставит ст. 22 четвёртой, dense видит её первой."""
    bm25 = _hits([("st-16", 5.10), ("st-10", 3.20), ("st-24", 2.68), ("st-22", 2.33)], "bm25")
    dense = _hits([("st-22", 0.704), ("st-16", 0.644)], "dense")

    ranked = fuse_rankings(bm25, dense, top_k=4)
    assert ranked[0].chunk.chunk_id == "st-22"
    assert ranked[0].via == "dense"


def test_unsure_dense_falls_back_to_rrf_agreement() -> None:
    """Плотные скоры → работает согласие плеч: найденный обоими выходит вперёд."""
    bm25 = _hits([("st-16", 5.10), ("st-22", 2.33)], "bm25")
    dense = _hits([("st-16", 0.645), ("st-22", 0.640)], "dense")  # разрыв 0.005 < margin

    ranked = fuse_rankings(bm25, dense, top_k=4)
    assert ranked[0].chunk.chunk_id == "st-16"
    assert ranked[0].via == "both"


def test_rrf_marks_chunks_found_by_both_arms() -> None:
    bm25 = _hits([("a", 3.0)], "bm25")
    dense = _hits([("a", 0.9), ("b", 0.8)], "dense")
    fused = {hit.chunk.chunk_id: hit for hit in rrf_fuse(bm25, dense)}
    assert fused["a"].via == "both"
    assert fused["b"].via == "dense"


def test_fuse_handles_missing_arm() -> None:
    """Одно плечо пустое: не падаем и не теряем результаты второго."""
    bm25 = _hits([("a", 3.0)], "bm25")
    assert [h.chunk.chunk_id for h in fuse_rankings(bm25, [], top_k=3)] == ["a"]
    assert [h.chunk.chunk_id for h in fuse_rankings([], bm25, top_k=3)] == ["a"]
    assert fuse_rankings([], [], top_k=3) == []


def test_fuse_respects_top_k() -> None:
    bm25 = _hits([("a", 3.0), ("b", 2.0), ("c", 1.0)], "bm25")
    assert len(fuse_rankings(bm25, [], top_k=2)) == 2


def test_margin_is_configurable() -> None:
    """Порог вынесен в параметр: его можно подстроить под другую модель эмбеддингов."""
    dense = _hits([("a", 0.70), ("b", 0.69)], "dense")  # разрыв 0.01
    assert dense_is_confident(dense, margin=DEFAULT_DENSE_MARGIN) is False
    assert dense_is_confident(dense, margin=0.005) is True
