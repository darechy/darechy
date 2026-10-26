"""Парсеры оригиналов актов из `kb_corpus/raw/` в формат `kb_corpus/curated/`.

Правило конвейера: чанк = статья или пункт (не абзац), текст — ДОСЛОВНО.
Парсер только экономит время: 100% чанков всё равно кросс-проверяет второй
человек (см. kb_corpus/README.md, шаг 4).

Зависимости сознательно минимальные:
- .docx — stdlib (zipfile + xml.etree): docx это zip с word/document.xml;
- .pdf  — pypdf (чистый Python, ставится без системных пакетов).
"""

import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from app.kb.models import ActDoc, Chunk, ChunkLocation, CuratedDoc

_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

# «Статья 3», «Ст. 5», «Пункт 2.1» и сочетания в начале абзаца.
_ARTICLE_RE = re.compile(
    r"^\s*(?:статья|ст\.|стаття)\s+(?P<num>\d+(?:\.\d+)?)\.?\s*(?P<body>.*)$",
    re.IGNORECASE,
)
_POINT_RE = re.compile(
    r"^\s*(?:пункт|п\.)\s+(?P<num>\d+(?:\.\d+)*)\.?\s*(?P<body>.*)$",
    re.IGNORECASE,
)


def _docx_paragraphs(path: Path) -> list[str]:
    paragraphs: list[str] = []
    with zipfile.ZipFile(path) as zf:
        xml = zf.read("word/document.xml")
    root = ElementTree.fromstring(xml)
    for p in root.iter(f"{_W_NS}p"):
        texts = [t.text or "" for t in p.iter(f"{_W_NS}t")]
        text = "".join(texts).strip()
        if text:
            paragraphs.append(text)
    return paragraphs


def _pdf_paragraphs(path: Path) -> list[str]:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    lines: list[str] = []
    for page in reader.pages:
        text = page.extract_text() or ""
        # PDF-экстракторы ломают абзацы переносами: склеиваем строки без
        # дефисов на конце и разрываем по маркерам статей/пунктов.
        merged = re.sub(r"(\w)-\n(\w)", r"\1\2", text)
        for raw_line in merged.split("\n"):
            line = raw_line.strip()
            if line:
                lines.append(line)
    return lines


def _split_chunks(paragraphs: list[str]) -> list[Chunk]:
    """Склеивает абзацы в чанки по маркерам «статья N» / «пункт N».

    Строка с маркером начинает новый чанк; всё остальное приклеивается
    к текущему чанку (дословно, переводы строк сохраняем как есть).
    """
    chunks: list[Chunk] = []
    current: Chunk | None = None
    counter = 0

    def push() -> None:
        nonlocal current
        if current is not None and current.text.strip():
            chunks.append(current)

    for line in paragraphs:
        article_match = _ARTICLE_RE.match(line)
        point_match = _POINT_RE.match(line)
        if article_match:
            push()
            counter += 1
            current = Chunk(
                chunk_id=f"chunk-{counter:03d}",
                location=ChunkLocation(article=f"статья {article_match['num'].strip()}"),
                text=line,
            )
        elif point_match and current is not None and current.location.article:
            # Пункт внутри статьи — отдельный чанк с уточнённым location.
            push()
            counter += 1
            current = Chunk(
                chunk_id=f"chunk-{counter:03d}",
                location=ChunkLocation(
                    article=current.location.article,
                    point=f"пункт {point_match['num']}",
                ),
                text=line,
            )
        elif point_match:
            push()
            counter += 1
            current = Chunk(
                chunk_id=f"chunk-{counter:03d}",
                location=ChunkLocation(point=f"пункт {point_match['num']}"),
                text=line,
            )
        elif current is not None:
            current.text += "\n" + line
        # Абзацы до первой статьи (преамбула) отбрасываем — не цитируем.
    push()
    return chunks


def _guess_doc_meta(path: Path, paragraphs: list[str]) -> ActDoc:
    """Мета акта: из первых абзацев; заглушки ❗ заполнит человек при сверке."""
    head = "\n".join(paragraphs[:6])
    number = "❗№"
    date = "❗дата"
    number_match = re.search(r"№\s*([\dА-Яа-я/]+)", head)
    if number_match:
        number = number_match.group(1)
    date_match = re.search(r"(\d{1,2}[.\-/]\d{1,2}[.\-/]\d{4}|\d{1,2}\s+\w+\s+\d{4})", head)
    if date_match:
        date = date_match.group(1)
    title = paragraphs[0][:200] if paragraphs else path.stem
    return ActDoc(
        type="❗тип акта",
        number=number,
        date=date,
        title=title,
        source_url="https://pravo.by",
        edition_date=None,
    )


def parse_act(path: Path, doc_meta: ActDoc | None = None) -> CuratedDoc:
    """Разбирает оригинал акта в CuratedDoc. Мета — из sidecar-словаря, если есть."""
    suffix = path.suffix.lower()
    if suffix == ".docx":
        paragraphs = _docx_paragraphs(path)
    elif suffix == ".pdf":
        paragraphs = _pdf_paragraphs(path)
    else:
        msg = f"Неизвестный формат акта: {path.name} (ожидались .docx/.pdf)"
        raise ValueError(msg)

    meta = doc_meta or _guess_doc_meta(path, paragraphs)
    chunks = _split_chunks(paragraphs)
    for chunk in chunks:
        chunk.chunk_id = f"{path.stem}_{chunk.chunk_id}"
        chunk.doc_title = meta.title
        chunk.doc_number = meta.number
        chunk.doc_date = meta.date
        chunk.doc_url = meta.source_url
    if not chunks:
        msg = f"В {path.name} не нашлось ни одной статьи/пункта — проверить парсер"
        raise ValueError(msg)
    return CuratedDoc(doc=meta, chunks=chunks)
