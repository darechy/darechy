"""Корпус: разбор акта в чанки, импорт, инвентаризация.

Один и тот же код используют:
- `scripts/rechunk_points.py` (разовый прогон из терминала),
- админка (`POST /api/admin/corpus/import`) — чтобы добавлять акты без кода.

Инвариант честности: разбор чисто механический, текст не переписывается.
Склейка пунктов обязана совпасть с исходной статьёй (`verify_roundtrip`) —
если не совпала, чанк не создаётся и импорт рапортует об ошибке.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from app.kb.models import Chunk, ChunkLocation, CuratedDoc

_POINT_START = re.compile(r"(?:(?<=\s)|^)(\d{1,2}(?:\.\d{1,2}){0,3}(?:-\d)?)\.\s+(?=[А-ЯЁA-Z«\"(])")
_ARTICLE_HEAD = re.compile(r"^\s*Статья\s+(\d+)\.\s*([^\n]*)")
_META_LINE = re.compile(r"^-\s*([\w_]+):\s*(.+)$", re.MULTILINE)
_ACT_HEAD = re.compile(
    r"^#\s*(?P<title>.+?)\s*(?:от\s+(?P<date>\d{1,2}\s+\w+\s+\d{4}))?\s*[«\"](?P<name>.+?)[»\"]?\s*$"
)

# «No 569», «№ 7-З» — приводим к одному виду в реквизитах.
_NUMBER_RE = re.compile(r"(?:No|№)\s*([0-9]+(?:-[А-ЯA-Z]+)?)")


@dataclass
class ChunkingReport:
    """Что получилось при разборе статьи: чанки + ошибки целостности."""

    chunks: list[Chunk]
    errors: list[str]


def _is_next_point(previous: str, candidate: str) -> bool:
    """Проверяет, что candidate — следующий номер в последовательности пунктов.

    Без этой проверки «Статья 11.» даёт кандидата «11» и статья режется на
    огрызок «Статья» — так и появлялись 6-символьные чанки.
    """
    prev = previous.split(".")
    cand = candidate.split(".")
    if len(cand) == len(prev) + 1 and cand[:-1] == prev:
        return True  # 1 → 1.1
    if len(cand) == len(prev) and cand[:-1] == prev[:-1]:
        try:
            return int(cand[-1]) == int(prev[-1]) + 1
        except ValueError:
            return False
    return False


def _valid_points(text: str) -> list[re.Match[str]]:
    accepted: list[re.Match[str]] = []
    previous: str | None = None
    for match in _POINT_START.finditer(text):
        number = match.group(1)
        if number == "1" or (previous is not None and _is_next_point(previous, number)):
            accepted.append(match)
            previous = number
    return accepted


def split_points(text: str) -> list[tuple[str | None, str]]:
    """Режет статью на пункты, не меняя текст. Первый элемент может быть None — преамбула.

    Заголовок «Статья N.» сначала вынимается отдельно. Иначе «Статья 1. Право…»
    распознавалось как пункт «1» (единица стоит после пробела и перед заглавной),
    и рядом появлялся настоящий пункт 1 — два чанка с одним chunk_id. Для
    «Статья 11.» этого не происходило, поэтому баг и вылез только на статье 1.
    """
    text = text.strip()
    head = ""
    body = text
    article_head = _ARTICLE_HEAD.match(text)
    if article_head:
        newline = text.find("\n")
        if newline == -1:
            return [(None, text)]
        head = text[:newline].strip()
        body = text[newline:].strip()

    matches = _valid_points(body)
    if not matches:
        # Нет нумерованных пунктов — статья остаётся цельным чанком.
        # Возвращать только заголовок нельзя: текст статьи потеряется,
        # а проверка целостности это поймает и отменит импорт.
        return [(None, text)]

    pieces: list[tuple[str | None, str]] = []
    preamble = f"{head}\n{body[: matches[0].start()]}".strip() if head else body[: matches[0].start()].strip()
    if preamble:
        pieces.append((None, preamble))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        pieces.append((match.group(1), body[match.start() : end].strip()))
    return pieces


def verify_roundtrip(pieces: list[tuple[str | None, str]], original: str) -> bool:
    """Склейка пунктов должна воспроизводить статью: ни один символ не потерян."""
    joined = re.sub(r"\s+", " ", " ".join(piece for _, piece in pieces)).strip()
    source = re.sub(r"\s+", " ", original).strip()
    return joined == source


def chunk_article(
    text: str,
    doc_prefix: str,
    article_hint: str | None = None,
    status: str = "восстановлен",
) -> ChunkingReport:
    """Превращает текст статьи в чанки-пункты с проверкой целостности."""
    pieces = split_points(text)
    if not verify_roundtrip(pieces, text):
        return ChunkingReport(
            chunks=[],
            errors=["склейка пунктов не совпала с исходной статьёй — импорт отменён"],
        )

    head = _ARTICLE_HEAD.match(text)
    if head:
        article_label = f"статья {head.group(1)}"
        article_title = head.group(2).strip()
    else:
        article_label = article_hint or "статья"
        article_title = ""

    chunks: list[Chunk] = []
    point_pieces = [(point, piece) for point, piece in pieces if point is not None]

    if not point_pieces:
        # Статья без нумерованных пунктов (например, Закон о погребении):
        # по конвенции kb_corpus/README.md чанк = «статья ИЛИ пункт», поэтому
        # здесь чанк — статья целиком. Раньше такие статьи терялись полностью.
        number = article_label.split()[-1] if article_label.split() else "0"
        return ChunkingReport(
            chunks=[
                Chunk(
                    chunk_id=f"{doc_prefix}_st-{number}",
                    title=(f"{article_label.capitalize()}. {article_title}"
                           if article_title else article_label.capitalize()),
                    location=ChunkLocation(article=article_label, point=None),
                    text=text.strip(),
                    status=status,
                )
            ],
            errors=[],
        )

    for point, piece in point_pieces:
        chunks.append(
            Chunk(
                chunk_id=f"{doc_prefix}_st-{article_label.split()[-1]}_п-{point.replace('.', '-')}",
                title=(f"{article_label.capitalize()}. {article_title}" if article_title
                       else article_label.capitalize()),
                location=ChunkLocation(article=article_label, point=f"пункт {point}"),
                text=piece,
                status=status,
            )
        )
    return ChunkingReport(chunks=chunks, errors=[])


_TOC_LINE_RE = re.compile(r"(?m)^\s*(СОДЕРЖАНИЕ|ОГЛАВЛЕНИЕ)\s*$")


def _drop_table_of_contents(raw: str) -> str:
    """Убирает оглавление акта: это не нормы, а список статей.

    В выгрузках портала (Кодекс о браке и семье) есть блок «СОДЕРЖАНИЕ» —
    однострочные упоминания статей. Разборщик принимал их за статьи, и в
    корпусе появлялись дубли: «статья 193» из оглавления и настоящая.
    Тело начинается там, где после строки «Статья N.» идёт ещё текст.
    """
    match = _TOC_LINE_RE.search(raw)
    if not match:
        return raw
    lines = raw[match.end() :].splitlines()
    for index, line in enumerate(lines):
        if not re.match(r"^\s*Статья\s+\d+", line):
            continue
        rest = [next_line for next_line in lines[index + 1 : index + 4] if next_line.strip()]
        if rest:
            # Настоящая статья: за заголовком идёт текст нормы.
            return raw[: match.start()] + "\n".join(lines[index:])
    return raw[: match.start()]


def dedupe_chunk_ids(chunks: list[Chunk]) -> list[Chunk]:
    """Делает chunk_id уникальными, сохраняя порядок и текст.

    Нумерация пунктов в постановлениях начинается заново в каждом приложении,
    поэтому одинаковые номера дают одинаковые id. Валидатор это ловит; здесь
    добавляем суффикс, а не выбрасываем норму.
    """
    seen: dict[str, int] = {}
    result: list[Chunk] = []
    for chunk in chunks:
        count = seen.get(chunk.chunk_id, 0) + 1
        seen[chunk.chunk_id] = count
        if count == 1:
            result.append(chunk)
        else:
            result.append(
                chunk.model_copy(
                    update={
                        "chunk_id": f"{chunk.chunk_id}-{count}",
                        "title": f"{chunk.title or chunk.chunk_id} (повтор {count})",
                    }
                )
            )
    return result


def parse_act_markdown(raw: str) -> tuple[dict, str, list[str]]:
    """Разбирает markdown-выгрузку акта на реквизиты и статьи.

    Возвращает (реквизиты, заголовок, список текстов статей).
    Формат — тот, что кладёт конвейер в `kb_corpus/raw/`: шапка с полями
    `- type:`, `- number:` и далее текст акта с заголовками «Статья N.».
    """
    meta: dict[str, str] = {}
    for key, value in _META_LINE.findall(raw):
        meta[key] = value.strip()

    title = meta.get("title", "")
    if not title:
        first = raw.splitlines()[0] if raw.splitlines() else ""
        match = _ACT_HEAD.match(first.lstrip("# ").strip())
        title = match.group("name") if match else first.strip("# ").strip()

    body = _drop_table_of_contents(raw)
    marker = "\n---\n"
    if marker in raw:  # шапка отделена от текста разделителем
        body = raw.split(marker, 1)[1]
    else:
        lines = raw.splitlines()
        body = "\n".join(line for line in lines if not _META_LINE.match(line) and not line.startswith("# "))

    # Статьи режем по заголовкам «Статья N.»
    articles: list[str] = []
    starts = [m.start() for m in re.finditer(r"(?m)^\s*Статья\s+\d+\.", body)]
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(body)
        articles.append(body[start:end].strip())

    number_match = _NUMBER_RE.search(meta.get("number", "")) or _NUMBER_RE.search(title)
    doc = {
        "type": meta.get("type", ""),
        "number": number_match.group(1) if number_match else meta.get("number", ""),
        "date": meta.get("date", ""),
        "title": title,
        "source_url": meta.get("source_url", ""),
        "edition_date": meta.get("edition_date") or None,
    }
    return doc, title, articles


def build_curated_doc(raw: str, doc_prefix: str, status: str = "восстановлен") -> tuple[CuratedDoc | None, list[str]]:
    """Собирает CuratedDoc из markdown-выгрузки акта."""
    doc_meta, _title, articles = parse_act_markdown(raw)
    if not articles:
        return None, ["в файле не найдено ни одной статьи («Статья N.»)"]

    chunks: list[Chunk] = []
    errors: list[str] = []
    for article in articles:
        report = chunk_article(article, doc_prefix=doc_prefix, status=status)
        errors.extend(report.errors)
        chunks.extend(report.chunks)

    if errors:
        return None, errors
    if not chunks:
        return None, ["ни одного пункта не разобрано"]

    try:
        return CuratedDoc.model_validate(
            {"doc": doc_meta, "chunks": [c.model_dump() for c in dedupe_chunk_ids(chunks)]}
        ), []
    except ValueError as exc:
        return None, [f"схема не приняла документ: {exc}"]


def build_flat_act(
    raw: str,
    doc_prefix: str,
    status: str = "восстановлен",
) -> tuple[CuratedDoc | None, list[str]]:
    """Акт без структуры «Статья N.»: режем по нумерованным пунктам.

    Так устроены постановления министерств (№ 88 о БПМ): «1. Установить…»,
    «2. Настоящее постановление вступает в силу…». Если нумерации нет вообще —
    весь текст одним чанком, чтобы не потерять акт из базы.
    """
    doc_meta, _title, _articles = parse_act_markdown(raw)
    pieces = split_points(raw)
    if not verify_roundtrip(pieces, raw):
        return None, ["склейка пунктов не совпала с исходным текстом — импорт отменён"]

    chunks: list[Chunk] = []
    point_pieces = [(point, piece) for point, piece in pieces if point is not None]

    for point, piece in point_pieces:
        chunks.append(
            Chunk(
                chunk_id=f"{doc_prefix}_п-{point.replace('.', '-')}",
                title=f"Пункт {point}",
                location=ChunkLocation(article=None, point=f"пункт {point}"),
                text=piece,
                status=status,
            )
        )

    if not chunks:
        # Нумерации нет — акт целиком одним чанком. Место цитаты обязательно:
        # без него валидатор справедливо не пускает чанк в индекс.
        body = raw.split("\n---\n", 1)[-1].strip()
        chunks = [
            Chunk(
                chunk_id=doc_prefix,
                title=doc_meta.get("title") or "Акт",
                location=ChunkLocation(article=None, point="весь акт"),
                text=body,
                status=status,
            )
        ]

    try:
        return CuratedDoc.model_validate(
            {"doc": doc_meta, "chunks": [c.model_dump() for c in dedupe_chunk_ids(chunks)]}
        ), []
    except ValueError as exc:
        return None, [f"схема не приняла документ: {exc}"]


def corpus_inventory(corpus_dir: Path, raw_dir: Path) -> list[dict]:
    """Что уже в корпусе: акты, число чанков, статусы, объём. Для админки."""
    items: list[dict] = []
    for path in sorted(corpus_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            chunks = payload.get("chunks", [])
            doc = payload.get("doc", {})
        except (json.JSONDecodeError, AttributeError) as exc:
            items.append({"file": path.name, "error": str(exc)})
            continue

        statuses: dict[str, int] = {}
        for chunk in chunks:
            # У старых выгрузок поля status нет — модель считает такие чанки
            # «проверен» (значение по умолчанию), поэтому и здесь тот же дефолт.
            status = chunk.get("status") or "проверен"
            statuses[status] = statuses.get(status, 0) + 1

        items.append(
            {
                "file": path.name,
                "title": doc.get("title", ""),
                "number": doc.get("number", ""),
                "date": doc.get("date", ""),
                "source_url": doc.get("source_url", ""),
                "chunks": len(chunks),
                "statuses": statuses,
                "chars": sum(len(c.get("text", "")) for c in chunks),
                "has_raw": (raw_dir / f"{path.stem}.md").exists(),
            }
        )
    return items
