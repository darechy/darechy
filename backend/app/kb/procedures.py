"""Разбор перечня административных процедур (Указ № 200) в структуру.

Перечень — это каталог госуслуг: 480 процедур в 19 тематических разделах, и у
каждой уже разложено по полочкам: орган, документы, стоимость, срок. Мы брали
из него только пособийные позиции руками; этот модуль разбирает перечень
целиком, чтобы каталог услуг рос данными, а не кодом.

Формат позиции (проверено на файле):

    2.6. Назначение пособия в связи с рождением ребенка

    организация по месту работы, ...          ← орган (первый абзац)
    заявление                                  ← документы (абзацы)
    паспорт или иной документ, ...
    бесплатно                                  ← стоимость
    5 дней со дня обращения                    ← срок
    бессрочно                                  ← срок действия документа

Текст не переписывается: заголовок, орган и документы берутся как есть.
Человеческие формулировки делает отдельный слой (черновик карточки), и его
обязательно проверяет человек.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_POSITION_START = re.compile(r"(?m)^(\d{1,2}\.\d{1,2}(?:-\d)?)\.\s+(?=\S)")
# Заголовок тематического раздела: капс, без номера позиции.
_SECTION_RE = re.compile(r"^(?P<name>[А-ЯЁ][А-ЯЁ ,.\-()/]{11,})$")
_SECTION_STOPWORDS = ("ГЛАВА", "УКАЗ", "ПЕРЕЧЕНЬ", "ПРИЛОЖЕНИЕ", "ПОСТАНОВЛЯЕТ", "ЛУКАШЕНКО")
_COST_RE = re.compile(r"^(бесплатно|платно|\d+[,.]?\d*\s*(руб|процент|%))", re.IGNORECASE)
_TERM_RE = re.compile(r"(дн(я|ей|ь)|месяц|год|бессрочно|немедленно)", re.IGNORECASE)
_FREE = "бесплатно"


@dataclass
class Procedure:
    """Одна позиция перечня, разобранная на поля."""

    number: str  # «2.6» или «2.9-1»
    section: str  # тематический раздел («ТРУД И СОЦИАЛЬНАЯ ЗАЩИТА»)
    title: str  # заголовок без номера
    authority: str  # первый абзац — куда обращаться
    documents: list[str] = field(default_factory=list)
    cost: str = ""
    deadline: str = ""
    validity: str = ""
    raw: str = ""  # исходный текст позиции — для сверки человеком

    @property
    def chunk_id_hint(self) -> str:
        """id пункта в корпусе, если позиция уже проиндексирована."""
        return f"_поз-{self.number.replace('.', '-')}"


def _blocks(text: str) -> list[str]:
    """Абзацы позиции: текст между пустыми строками."""
    return [block.strip() for block in re.split(r"\n\s*\n", text) if block.strip()]


def parse_procedure(number: str, section: str, text: str) -> Procedure:
    """Разбирает текст позиции. Непонятное не выдумывает — оставляет пустым."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    heading = lines[0] if lines else ""
    title = re.sub(r"^\d{1,2}\.\d{1,2}(?:-\d)?\.\s*", "", heading).strip()

    blocks = _blocks(text)
    body = blocks[1:] if blocks and blocks[0] == heading else blocks
    # Заголовок мог склеиться с первым абзацем — тогда режем по строкам.
    if not body:
        body = lines[1:]

    authority = ""
    documents: list[str] = []
    cost = ""
    deadline = ""
    validity = ""

    index = 0
    if body and not _COST_RE.match(body[0]) and not _TERM_RE.search(body[0][:40]):
        authority = body[0]
        index = 1

    while index < len(body):
        block = body[index]
        if not cost and _COST_RE.match(block):
            cost = block
            index += 1
            continue
        if cost and not deadline and _TERM_RE.search(block):
            deadline = block
            index += 1
            continue
        if deadline and not validity:
            validity = block
            index += 1
            continue
        documents.append(block)
        index += 1

    return Procedure(
        number=number,
        section=section,
        title=title,
        authority=authority,
        documents=documents,
        cost=cost or _FREE,
        deadline=deadline,
        validity=validity,
        raw=text.strip(),
    )


def parse_sections(raw: str) -> list[tuple[str, list[Procedure]]]:
    """Все разделы перечня с позициями: [(раздел, [процедуры])]."""
    lines = raw.splitlines()
    # Границы разделов — капс-заголовки.
    marks: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        candidate = line.strip()
        match = _SECTION_RE.match(candidate)
        if not match:
            continue
        if any(word in candidate for word in _SECTION_STOPWORDS):
            continue
        if len(candidate) > 90:
            continue
        marks.append((index, candidate))

    if not marks:
        return [("БЕЗ РАЗДЕЛА", _parse_range(raw, ""))]

    sections: list[tuple[str, list[Procedure]]] = []
    for order, (start, name) in enumerate(marks):
        end = marks[order + 1][0] if order + 1 < len(marks) else len(lines)
        procedures = _parse_range("\n".join(lines[start:end]), name)
        if procedures:
            sections.append((name, procedures))
    return sections


def _parse_range(text: str, section: str) -> list[Procedure]:
    matches = list(_POSITION_START.finditer(text))
    procedures: list[Procedure] = []
    for order, match in enumerate(matches):
        end = matches[order + 1].start() if order + 1 < len(matches) else len(text)
        procedures.append(parse_procedure(match.group(1), section, text[match.start() : end]))
    return procedures


def all_procedures(raw: str) -> list[Procedure]:
    return [proc for _name, procs in parse_sections(raw) for proc in procs]


def section_names(raw: str) -> list[str]:
    return [name for name, _procs in parse_sections(raw)]
