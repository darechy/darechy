"""Тесты импортёра каталога из перечня административных процедур.

Проверяем три вещи, на которых держится «новая категория = новый раздел, а не
новый код»: разбор позиции на поля, сборка черновика без выдуманных фактов и
то, что черновик не попадает на портал до утверждения человеком.
"""

from pathlib import Path

import pytest

from app.kb.procedures import all_procedures, parse_procedure, parse_sections
from app.services.draft_store import DraftStore
from app.services.drafter import draft_from_procedure, situation_for
from tests.fakes import FakeLlm

PERECHEN = (
    Path(__file__).resolve().parent.parent.parent
    / "kb_corpus" / "raw" / "2010_200_ukaz-perechen-administrativnykh-procedur.md"
)

POSITION = """2.6. Назначение пособия в связи с рождением ребенка

организация по месту работы, службы, учебы, орган по труду, занятости и социальной защите

заявление

паспорт или иной документ, удостоверяющий личность

справка о рождении ребенка

бесплатно

10 дней со дня подачи заявления

бессрочно
"""


def test_parse_position_fields() -> None:
    procedure = parse_procedure("2.6", "ТРУД И СОЦИАЛЬНАЯ ЗАЩИТА", POSITION)
    assert procedure.title == "Назначение пособия в связи с рождением ребенка"
    assert procedure.authority.startswith("организация по месту работы")
    assert procedure.documents == [
        "заявление",
        "паспорт или иной документ, удостоверяющий личность",
        "справка о рождении ребенка",
    ]
    assert procedure.cost == "бесплатно"
    assert procedure.deadline == "10 дней со дня подачи заявления"
    assert procedure.validity == "бессрочно"


def test_parse_keeps_raw_text_for_review() -> None:
    """Исходный текст сохраняется: человек должен видеть, из чего собран черновик."""
    procedure = parse_procedure("2.6", "РАЗДЕЛ", POSITION)
    assert "справка о рождении ребенка" in procedure.raw


@pytest.mark.skipif(not PERECHEN.exists(), reason="нет файла перечня в raw/")
def test_real_perechen_sections_and_counts() -> None:
    raw = PERECHEN.read_text(encoding="utf-8")
    sections = parse_sections(raw)
    assert len(sections) >= 15, "разделов должно быть много: это каталог госуслуг"
    total = len(all_procedures(raw))
    assert total >= 400, f"процедур ожидалось около 480, разобрано {total}"
    names = [name for name, _ in sections]
    assert any("ГРАЖДАНСКОГО СОСТОЯНИЯ" in name for name in names)


@pytest.mark.skipif(not PERECHEN.exists(), reason="нет файла перечня в raw/")
def test_real_position_2_6_parsed() -> None:
    raw = PERECHEN.read_text(encoding="utf-8")
    procedure = next(p for p in all_procedures(raw) if p.number == "2.6")
    assert "рождением ребенка" in procedure.title
    assert procedure.documents, "у позиции 2.6 есть перечень документов"
    assert procedure.cost == "бесплатно"


def test_draft_has_no_invented_facts() -> None:
    """В перечне нет «кто имеет право» и суммы — значит и в черновике их нет."""
    procedure = parse_procedure("2.6", "ТРУД И СОЦИАЛЬНАЯ ЗАЩИТА", POSITION)
    draft = draft_from_procedure(procedure, position_chunk_id="act_поз-2-6")
    assert draft.who == "", "кто имеет право в перечне не указано — выдумывать нельзя"
    assert draft.amount == ""
    assert draft.status == "черновик"
    assert len(draft.checklist) == 3
    assert draft.sources[0].point == "act_поз-2-6"
    assert draft.situation == "trud-i-soczaschita"


def test_draft_title_and_deadline_from_act() -> None:
    procedure = parse_procedure("2.6", "ТРУД И СОЦИАЛЬНАЯ ЗАЩИТА", POSITION)
    draft = draft_from_procedure(procedure)
    assert draft.title == procedure.title
    assert draft.deadline.startswith("10 дней")
    assert draft.cost == "бесплатно"


def test_llm_polish_is_optional_and_validated() -> None:
    """ИИ может переписать заголовок, но мусорный ответ не принимается."""
    procedure = parse_procedure("2.6", "ТРУД И СОЦИАЛЬНАЯ ЗАЩИТА", POSITION)
    good = FakeLlm(chat_script=['{"title": "Получить пособие при рождении", '
                                '"summary": "Разовая выплата на каждого рождённого ребёнка."}'])
    draft = draft_from_procedure(procedure, llm=good, polish=True)
    assert draft.title == "Получить пособие при рождении"
    assert "Разовая выплата" in draft.summary

    bad = FakeLlm(chat_script=["я не понял задачу"])
    fallback = draft_from_procedure(procedure, llm=bad, polish=True)
    assert fallback.title == procedure.title, "мусор от ИИ не должен ломать карточку"


def test_situation_mapping_and_fallback() -> None:
    assert situation_for("РЕГИСТРАЦИЯ АКТОВ ГРАЖДАНСКОГО СОСТОЯНИЯ") == "registraciya-aktov"
    # Раздел, которого нет в карте, получает слаг из названия — не теряется.
    assert situation_for("НЕИЗВЕСТНЫЙ РАЗДЕЛ ПРО ЧТО-ТО") == "neizvestnyy-razdel-pro-chto-to"


# --- хранилище черновиков ----------------------------------------------------


def _draft(tmp_path, procedure_number="2.6"):
    procedure = parse_procedure(procedure_number, "ТРУД И СОЦИАЛЬНАЯ ЗАЩИТА", POSITION)
    return draft_from_procedure(procedure, position_chunk_id=f"act_поз-{procedure_number}")


def test_draft_store_merge_does_not_lose_reviewed(tmp_path) -> None:
    """Повторный импорт не затирает карточку, которую человек уже дополнил."""
    store = DraftStore(tmp_path / "drafts.json")
    fresh = _draft(tmp_path)
    edited = fresh.model_copy(update={"who": "мать или отец ребёнка"})

    store.merge([edited])
    assert store.get(fresh.id).who == "мать или отец ребёнка"

    # Тот же импорт ещё раз: черновик с пустым «кто имеет право» не должен
    # стереть то, что человек уже вписал.
    store.merge([fresh])
    assert store.get(fresh.id).who == "мать или отец ребёнка"


def test_draft_store_stats_and_delete(tmp_path) -> None:
    store = DraftStore(tmp_path / "drafts.json")
    draft = _draft(tmp_path)
    store.merge([draft, draft.model_copy(update={"id": "second"})])
    stats = store.stats()
    assert stats["total"] == 2
    assert stats["with_documents"] == 2
    assert stats["filled_who"] == 0
    assert store.delete("second") is True
    assert store.delete("second") is False
    assert store.stats()["total"] == 1


def test_draft_store_persists(tmp_path) -> None:
    path = tmp_path / "drafts.json"
    DraftStore(path).merge([_draft(tmp_path)])
    assert len(DraftStore(path).all()) == 1


# --- сквозные жизненные ситуации --------------------------------------------


def test_cross_cutting_situation_collects_services_from_different_acts(tmp_path) -> None:
    """Ситуация может собирать услуги из разных актов и категорий.

    «Родился ребёнок» = регистрация рождения в ЗАГСе (перечень, Указ № 200)
    + три пособия (Закон № 7-З). Это маршрут, который видит человек.
    """
    import json

    from app.services.catalog import Catalog

    services_file = tmp_path / "services.json"
    raw = [
        {"id": "p5-1", "title": "Регистрация рождения", "situation": "registraciya-aktov"},
        {"id": "posobie", "title": "Пособие при рождении", "situation": "rozhdenie-rebenka"},
        {"id": "chernovik", "title": "Черновик", "situation": "rozhdenie-rebenka",
         "status": "черновик"},
    ]
    payload = []
    for item in raw:
        base = {
            "situation": "x", "summary": "", "who": "", "amount": "", "deadline": "",
            "checklist": [], "sources": [],
        }
        payload.append({**base, **item})
    services_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    (tmp_path / "situations.json").write_text(json.dumps({
        "rozhdenie-rebenka": {
            "title": "Родился ребёнок",
            "service_ids": ["p5-1", "posobie"],
        }
    }, ensure_ascii=False), encoding="utf-8")

    situations = {s.id: s for s in Catalog(services_file).situations()}
    assert situations["rozhdenie-rebenka"].title == "Родился ребёнок"
    assert situations["rozhdenie-rebenka"].service_ids == ["p5-1", "posobie"]
    # Черновик не попадает ни в одну ситуацию: его ещё не проверил человек.
    for situation in situations.values():
        assert "chernovik" not in situation.service_ids
    # А обе утверждённые услуги на месте — из разных актов, в одной ситуации.
    assert set(situations["rozhdenie-rebenka"].service_ids) == {"p5-1", "posobie"}
