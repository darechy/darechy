"""Тесты парсера актов: docx (stdlib) → чанки по статьям/пунктам."""

import zipfile
from pathlib import Path

import pytest

from app.kb.parsers import parse_act

_DOCX_XML = """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>УКАЗ ПРЕЗИДЕНТА РЕСПУБЛИКИ БЕЛАРУСЬ</w:t></w:r></w:p>
    <w:p><w:r><w:t>30 апреля 2010 г. № 200</w:t></w:r></w:p>
    <w:p><w:r><w:t>О государственной поддержке семей</w:t></w:r></w:p>
    <w:p><w:r><w:t>Статья 1. Настоящий Указ определяет порядок государственной поддержки семей, воспитывающих детей.</w:t></w:r></w:p>
    <w:p><w:r><w:t>1. Государственная поддержка предоставляется семьям с детьми.</w:t></w:r></w:p>
    <w:p><w:r><w:t>Статья 2. Условия назначения пособий</w:t></w:r></w:p>
    <w:p><w:r><w:t>Пособие назначается с даты обращения.</w:t></w:r></w:p>
  </w:body>
</w:document>
"""


@pytest.fixture()
def docx_act(tmp_path: Path) -> Path:
    path = tmp_path / "2010_200_test.docx"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml", _DOCX_XML)
    return path


def test_docx_parses_articles(docx_act: Path) -> None:
    doc = parse_act(docx_act)
    articles = [c.location.article for c in doc.chunks]
    assert "статья 1" in articles
    assert "статья 2" in articles


def test_docx_chunks_are_verbatim(docx_act: Path) -> None:
    doc = parse_act(docx_act)
    chunk1 = next(c for c in doc.chunks if c.location.article == "статья 1")
    assert "Статья 1." in chunk1.text
    assert "1. Государственная поддержка предоставляется" in chunk1.text


def test_docx_meta_from_header(docx_act: Path) -> None:
    doc = parse_act(docx_act)
    assert doc.doc.number == "200"
    assert "30 апреля 2010" in doc.doc.date
    assert doc.chunks[0].doc_title == doc.doc.title


def test_unknown_format_raises(tmp_path: Path) -> None:
    path = tmp_path / "act.txt"
    path.write_text("Статья 1. Тест")
    with pytest.raises(ValueError, match="формат"):
        parse_act(path)


def test_pdf_with_pypdf(tmp_path: Path) -> None:
    pytest.importorskip("pypdf")
    from pypdf import PdfWriter

    path = tmp_path / "act.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    # Пустая страница без статей → парсер обязан честно упасть, не отдать пустоту.
    with path.open("wb") as fh:
        writer.write(fh)
    with pytest.raises(ValueError, match="не нашлось"):
        parse_act(path)
