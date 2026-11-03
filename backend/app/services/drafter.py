"""Черновики карточек услуг из перечня административных процедур.

Идея модуля: каталог услуг растёт **данными**, а не кодом. Из позиции перечня
структурно достаются орган, документы, стоимость и срок — это факты из акта.
Чего в перечне нет (кто имеет право, сумма в рублях) — остаётся пустым и
помечается: выдумывать это нельзя, карточку утверждает человек.

Роль ИИ здесь прикладная: переписать канцелярит («Назначение пособия в связи
с рождением ребенка») в человеческий заголовок и краткое описание. ИИ работает
ТОЛЬКО по тексту позиции и не добавляет фактов; черновик всё равно идёт на
ревью, поэтому это безопасно, а не «дообучение».
"""

from __future__ import annotations

import json
import logging
import re

from app.kb.procedures import Procedure
from app.models.schemas import ChecklistItem, Service, Source
from app.services.admin_store import slug

logger = logging.getLogger(__name__)

PERECHEN_URL = "https://pravo.by/document/?guid=3871&p0=P31000200"
PERECHEN_PREFIX = "2010_200_ukaz-perechen-administrativnykh-procedur"

# Раздел перечня → жизненная ситуация. Разделы, которых нет в карте, получают
# ситуацию по своему названию — ничего не теряется.
SECTION_SITUATIONS: dict[str, str] = {
    "ЖИЛИЩНЫЕ ПРАВООТНОШЕНИЯ": "zhilyo",
    "ТРУД И СОЦИАЛЬНАЯ ЗАЩИТА": "trud-i-soczaschita",
    "ДОКУМЕНТЫ, ПОДТВЕРЖДАЮЩИЕ ПРАВО НА СОЦИАЛЬНЫЕ ЛЬГОТЫ": "socialnye-lgoty",
    "УСЫНОВЛЕНИЕ (УДОЧЕРЕНИЕ). ОПЕКА, ПОПЕЧИТЕЛЬСТВО, ПАТРОНАЖ. ЭМАНСИПАЦИЯ": "usynovlenie",
    "РЕГИСТРАЦИЯ АКТОВ ГРАЖДАНСКОГО СОСТОЯНИЯ": "registraciya-aktov",
    "ЗДРАВООХРАНЕНИЕ": "zdravoohranenie",
    "ДОКУМЕНТИРОВАНИЕ НАСЕЛЕНИЯ РЕСПУБЛИКИ БЕЛАРУСЬ": "dokumenty-naseleniya",
    "АРХИТЕКТУРА И СТРОИТЕЛЬСТВО": "stroitelstvo",
    "ГАЗО-, ЭЛЕКТРО-, ТЕПЛО- И ВОДОСНАБЖЕНИЕ. СВЯЗЬ": "kommunikacii",
    "ПРИРОДОПОЛЬЗОВАНИЕ": "prirodopolzovanie",
    "СЕЛЬСКОЕ ХОЗЯЙСТВО": "selskoe-hozyaystvo",
    "ВОИНСКАЯ ОБЯЗАННОСТЬ, ПРОХОЖДЕНИЕ АЛЬТЕРНАТИВНОЙ СЛУЖБЫ. ОБОРОНА": "voinskaya-obyazannost",
    "ОБОРОТ ОРУЖИЯ": "oruzhie",
    "ГОСУДАРСТВЕННАЯ РЕГИСТРАЦИЯ НЕДВИЖИМОГО ИМУЩЕСТВА, ПРАВ НА НЕГО И СДЕЛОК С НИМ": "nedvizhimost",
    "ФИЗИЧЕСКАЯ КУЛЬТУРА И СПОРТ, ТУРИЗМ, КУЛЬТУРА": "sport-i-kultura",
    "ОЦЕНОЧНАЯ ДЕЯТЕЛЬНОСТЬ": "ocenochnaya-deyatelnost",
    "ЛИЦЕНЗИРОВАНИЕ АДВОКАТСКОЙ ДЕЯТЕЛЬНОСТИ": "advokatura",
}

# Порог, после которого заголовок позиции уже читаемый: не трогаем.
_MAX_TITLE_CHARS = 70

_POLISH_PROMPT = (
    "Ты редактор портала госуслуг Беларуси. Тебе дан заголовок административной "
    "процедуры и её текст. Перепиши заголовок человеческим языком (до 70 знаков, "
    "без канцелярита, глагол в начале: «получить», «зарегистрировать») и дай "
    "краткое описание в 1–2 предложениях. Опирайся ТОЛЬКО на присланный текст: "
    "не добавляй суммы, сроки и условия, которых там нет. "
    'Ответь строго JSON: {"title": "...", "summary": "..."}'
)


def situation_for(section: str) -> str:
    return SECTION_SITUATIONS.get(section.strip().upper(), slug(section)[:40] or "prochee")


def draft_id(procedure: Procedure) -> str:
    """Стабильный id черновика: номер позиции + короткий смысловой хвост."""
    return f"p{procedure.number.replace('.', '-')}-{slug(procedure.title)[:38]}"


def _keywords(procedure: Procedure) -> list[str]:
    """Триггеры для роутера: значимые слова заголовка, без служебных."""
    stop = {"назначение", "выдача", "принятие", "и", "в", "на", "по", "о", "об", "для"}
    words = [w for w in re.findall(r"[а-яёa-z0-9\-]+", procedure.title.lower()) if w not in stop]
    phrases = [" ".join(words[:3])] if len(words) >= 3 else []
    return list(dict.fromkeys([*phrases, *words[:8]]))[:8]


def draft_from_procedure(
    procedure: Procedure,
    llm=None,
    polish: bool = False,
    position_chunk_id: str | None = None,
) -> Service:
    """Собирает черновик карточки. Факты — из позиции, формулировки — по желанию от ИИ."""
    title = procedure.title
    summary = (
        f"{procedure.title.capitalize()}. "
        f"Обращаться: {procedure.authority[:160] or 'орган не указан в позиции'}. "
        f"Стоимость: {procedure.cost}. "
        f"Срок: {procedure.deadline or 'не указан'}."
    )

    if polish and llm is not None and (len(title) > _MAX_TITLE_CHARS or True):
        polished = _polish(procedure, llm)
        if polished:
            title, summary = polished

    return Service(
        id=draft_id(procedure),
        title=title,
        situation=situation_for(procedure.section),
        summary=summary,
        # Из перечня неизвестно, кто имеет право: оставляем пустым и не врём.
        who="",
        amount="",
        deadline=procedure.deadline,
        authority=procedure.authority,
        cost=procedure.cost,
        checklist=[
            ChecklistItem(item=document, where=procedure.authority[:120])
            for document in procedure.documents
        ],
        sources=[
            Source(
                title=f"Указ № 200, позиция {procedure.number}",
                url=PERECHEN_URL,
                point=position_chunk_id,
            )
        ],
        keywords=_keywords(procedure),
        status="черновик",
    )


def _polish(procedure: Procedure, llm) -> tuple[str, str] | None:
    """Просит ИИ переписать заголовок и описание. None — не получилось, не страшно."""
    payload = (
        f"Заголовок: {procedure.title}\n"
        f"Орган: {procedure.authority}\n"
        f"Документы: {'; '.join(procedure.documents[:8])}\n"
        f"Стоимость: {procedure.cost}\nСрок: {procedure.deadline}"
    )
    answer = llm.chat(_POLISH_PROMPT, payload, max_tokens=180)
    if not answer:
        return None
    match = re.search(r"\{.*\}", answer, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    title = str(data.get("title", "")).strip()
    summary = str(data.get("summary", "")).strip()
    if len(title) < 5 or len(summary) < 20:
        return None
    return title, summary
