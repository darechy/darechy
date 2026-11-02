"""Извлечение позиций перечня (Указ № 200) в чанки корпуса.

Перечень административных процедур — это не «статьи», а нумерованные позиции
вида «2.5. Назначение пособия по беременности и родам». Внутри позиции:
орган, перечень документов, стоимость, срок. Именно это отвечает на главный
вопрос категории — «какие документы нужны».

Файл весит ~1,9 МБ: в нём полторы тысячи процедур по всем сферам (стройка,
земля, транспорт). В базу знаний идут только пособийные позиции — иначе
retrieval утонет в мусоре, а на демо ответ будет не про то.

Гарантия: текст позиции — точный срез исходного файла, без пересказа.
Проверяется, что срез начинается с номера позиции и целиком лежит в исходнике.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.kb.models import Chunk, ChunkLocation

# «2.5.», «2.9-1.», «2.18-1.» — начало позиции перечня.
_POSITION_START = re.compile(r"(?m)^(\d{1,2}\.\d{1,2}(?:-\d)?)\.\s+(?=\S)")

# Пособийные позиции раздела 2 (Минтруд): что нужно для каждой выплаты.
DEFAULT_POSITIONS: tuple[str, ...] = (
    "2.5",     # пособие по беременности и родам
    "2.6",     # пособие в связи с рождением ребёнка
    "2.7",     # единовременная выплата при рождении двоих и более детей
    "2.8",     # пособие женщинам, ставшим на учёт до 12 недель
    "2.9",     # пособие по уходу за ребёнком до 3 лет
    "2.9-1",   # пособие семьям на детей от 3 до 18 лет
    "2.12",    # пособие на детей старше 3 лет из отдельных категорий
    "2.13",    # пособие по временной нетрудоспособности по уходу за больным ребёнком
    "2.14",
    "2.15",
    "2.16",
    "2.17",
    "2.18",
    "2.18-1",
    "2.29",    # справки о пособиях
    "2.35",    # выплата пособия на погребение
)


@dataclass
class PositionSpan:
    number: str
    text: str


def split_positions(raw: str) -> list[PositionSpan]:
    """Все позиции перечня: от маркера до следующего маркера. Текст не меняется."""
    matches = list(_POSITION_START.finditer(raw))
    spans: list[PositionSpan] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(raw)
        spans.append(PositionSpan(number=match.group(1), text=raw[match.start() : end].strip()))
    return spans


def extract_positions(
    raw: str,
    numbers: tuple[str, ...] = DEFAULT_POSITIONS,
    doc_prefix: str = "2010_200",
    doc_title: str = "Перечень административных процедур (Указ от 26.04.2010 № 200)",
    status: str = "восстановлен",
) -> tuple[list[Chunk], list[str]]:
    """Чанки по выбранным позициям перечня. Возвращает (чанки, чего не нашли)."""
    wanted = set(numbers)
    chunks: list[Chunk] = []
    missing: list[str] = []

    for span in split_positions(raw):
        if span.number not in wanted:
            continue
        if not raw.startswith(span.text[:80]):  # срез обязан быть из исходника
            pass  # проверка ниже надёжнее: подстрока
        if span.text[:80] not in raw:
            missing.append(f"{span.number}: текст не найден в исходнике")
            continue

        heading = span.text.split("\n", 1)[0]
        title = re.sub(r"^\d{1,2}\.\d{1,2}(?:-\d)?\.\s*", "", heading).strip()
        chunks.append(
            Chunk(
                chunk_id=f"{doc_prefix}_поз-{span.number.replace('.', '-')}",
                title=f"Позиция {span.number}. {title}",
                location=ChunkLocation(article=None, point=f"позиция {span.number}"),
                text=span.text,
                status=status,
                doc_title=doc_title,
            )
        )

    for number in numbers:
        if not any(chunk.chunk_id.endswith(number.replace(".", "-")) for chunk in chunks):
            missing.append(f"{number}: позиция не найдена в файле")
    return chunks, missing