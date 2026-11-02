"""Тесты разбора акта на пункты — конвейер корпуса.

Главная гарантия: нарезка не имеет права переписать или потерять текст акта.
Именно поэтому здесь проверяется целостность (склейка == исходник), а не
«красивость» чанков.
"""

from app.kb.corpus import (
    build_curated_doc,
    chunk_article,
    parse_act_markdown,
    split_points,
    verify_roundtrip,
)

ARTICLE_1 = """Статья 1. Право на государственные пособия семьям, воспитывающим детей

1. Право на государственные пособия семьям, воспитывающим детей (далее – государственные пособия), в соответствии с настоящим Законом имеют:
1.1. постоянно проживающие в Республике Беларусь граждане;
2. Государственные пособия назначаются на детей, зарегистрированных по месту жительства.
3. Государственные пособия не назначаются и не выплачиваются:
3.1. на детей, проживающих за пределами Республики Беларусь.
"""


def test_article_heading_is_not_a_point() -> None:
    """«Статья 1. Право…» не должна распознаваться как пункт «1».

    Регресс на реальный баг: единица в заголовке стоит после пробела и перед
    заглавной, поэтому проходила как начало пункта. Рядом появлялся настоящий
    пункт 1 — два чанка с одинаковым chunk_id. На «Статья 11.» не проявлялось,
    поэтому вылезло только на первой статье.
    """
    pieces = split_points(ARTICLE_1)
    points = [point for point, _ in pieces if point is not None]
    assert points.count("1") == 1, f"пункт 1 продублирован: {points}"
    assert points == ["1", "2", "3"]


def test_heading_goes_to_preamble() -> None:
    pieces = split_points(ARTICLE_1)
    assert pieces[0][0] is None
    assert pieces[0][1].startswith("Статья 1.")


def test_roundtrip_keeps_full_text() -> None:
    """Склейка пунктов обязана воспроизвести статью символ в символ."""
    pieces = split_points(ARTICLE_1)
    assert verify_roundtrip(pieces, ARTICLE_1)


def test_chunk_ids_are_unique_for_article() -> None:
    report = chunk_article(ARTICLE_1, doc_prefix="7-Z")
    ids = [chunk.chunk_id for chunk in report.chunks]
    assert len(ids) == len(set(ids)), f"дубли: {ids}"
    assert report.chunks[0].chunk_id == "7-Z_st-1_п-1"
    assert report.chunks[0].location.article == "статья 1"
    assert report.chunks[0].location.point == "пункт 1"


def test_subpoints_stay_with_parent_point() -> None:
    """1.1 — часть пункта 1, отдельным чанком не становится (конвенция корпуса)."""
    report = chunk_article(ARTICLE_1, doc_prefix="7-Z")
    first = next(c for c in report.chunks if c.location.point == "пункт 1")
    assert "1.1." in first.text


def test_text_is_not_rewritten() -> None:
    """В чанках — дословный текст акта, без пересказа и правок."""
    report = chunk_article(ARTICLE_1, doc_prefix="7-Z")
    joined = " ".join(c.text for c in report.chunks)
    assert "постоянно проживающие в Республике Беларусь граждане" in joined


def test_parse_act_markdown_extracts_requisites() -> None:
    raw = """# Закон Республики Беларусь от 29 декабря 2012 г. No 7-З «О государственных пособиях семьям, воспитывающим детей»

- type: Закон Республики Беларусь
- number: No 7-З
- date: 29.12.2012
- title: О государственных пособиях семьям, воспитывающим детей
- source_url: https://pravo.by/document/?guid=3871&p0=H11200007

---

""" + ARTICLE_1
    doc, title, articles = parse_act_markdown(raw)
    assert doc["number"] == "7-З"
    assert doc["date"] == "29.12.2012"
    assert doc["source_url"].startswith("https://pravo.by")
    assert "пособиях" in title
    assert len(articles) == 1


def test_build_curated_doc_rejects_file_without_articles() -> None:
    doc, errors = build_curated_doc("просто текст без статей", doc_prefix="x")
    assert doc is None
    assert errors


def test_build_curated_doc_produces_valid_schema() -> None:
    raw = """- type: Закон
- number: No 1
- date: 01.01.2020
- title: Тестовый акт
- source_url: https://pravo.by/test

---

""" + ARTICLE_1
    doc, errors = build_curated_doc(raw, doc_prefix="test")
    assert errors == []
    assert doc is not None
    assert len(doc.chunks) == 3
    assert all(chunk.status == "восстановлен" for chunk in doc.chunks)