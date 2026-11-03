"""Тесты слоя «доучивания»: эталонные ответы поверх корпуса.

Ключевое, что здесь зафиксировано:
1. Выучить ответ без источника из корпуса нельзя — иначе это обход guardrail.
2. «Пустое» слово (есть почти во всех пунктах) не включает ответ само по себе.
   Реальный случай: триггер «ребёнка» с режимом «достаточно одного» утаскивал
   на выученный ответ любой вопрос про детей, включая «какие документы нужны».
"""

from pathlib import Path

import pytest

from app.chat.taught import TaughtStore
from app.models.schemas import TaughtAnswerCreate, TaughtSource
from tests.fakes import make_chunk


def _chunks():
    """Маленький «корпус»: слово «ребёнок» есть везде, «второй» — только в одном."""
    return [
        make_chunk("a1", "Пособие на ребёнка назначается со дня рождения ребёнка."),
        make_chunk("a2", "Документы о рождении ребёнка представляет родитель ребёнка."),
        make_chunk("a3", "Пособие на второго ребёнка выплачивается в повышенном размере."),
        make_chunk("a4", "Выплата пособия ребёнку прекращается при достижении возраста."),
    ]


@pytest.fixture()
def store(tmp_path: Path) -> TaughtStore:
    return TaughtStore(tmp_path / "answers.json", _chunks())


def _payload(keywords, mode="any", sources=("a3",), question="сколько дают за второго ребёнка"):
    return TaughtAnswerCreate(
        question=question,
        answer="14-кратная величина БПМ.",
        keywords=keywords,
        match_mode=mode,
        sources=[TaughtSource(chunk_id=s) for s in sources],
    )


def test_create_and_match(store: TaughtStore) -> None:
    store.create(_payload(["второго", "сколько"]))
    match = store.match("сколько дают за второго ребенка")
    assert match is not None
    assert match.taught.answer == "14-кратная величина БПМ."


def test_generic_keyword_alone_does_not_trigger(store: TaughtStore) -> None:
    """«ребёнок» есть в каждом пункте → сам по себе ответ не включает."""
    store.create(_payload(["ребенка", "сколько"]))
    assert store.match("какие документы нужны для ребенка") is None


def test_distinctive_keyword_does_trigger(store: TaughtStore) -> None:
    store.create(_payload(["второго"]))
    assert store.match("сколько дают за второго ребенка") is not None


def test_is_distinctive_marks_common_word(store: TaughtStore) -> None:
    assert store.is_distinctive("ребенка") is False
    assert store.is_distinctive("второго") is True


def test_match_mode_all_requires_every_keyword(store: TaughtStore) -> None:
    store.create(_payload(["второго", "повышенном"], mode="all"))
    assert store.match("сколько дают за второго ребенка") is None  # «повышенном» нет
    assert store.match("в каком размере за второго, повышенном") is not None


def test_sources_missing_in_corpus_are_reported(store: TaughtStore) -> None:
    store.create(_payload(["второго"], sources=("a3",)))
    # корпус пересобрали и пункта не стало
    store.set_corpus(_chunks()[:2])
    assert store.unknown_sources() == [(store.all()[0].id, "a3")]


def test_sources_ok_validates_chunk_ids(store: TaughtStore) -> None:
    assert store.sources_ok([TaughtSource(chunk_id="a3")]) == []
    assert store.sources_ok([TaughtSource(chunk_id="nope")]) == ["nope"]


def test_citations_come_from_corpus_not_from_admin_text(store: TaughtStore) -> None:
    """Цитата — дословный текст корпуса, а не то, что написал администратор."""
    created = store.create(_payload(["второго"]))
    chunks = {c.chunk_id: c for c in _chunks()}
    citations = store.citations_for(created, chunks)
    assert len(citations) == 1
    assert citations[0].quote.startswith("Пособие на второго ребёнка")


def test_disabled_answer_is_ignored(store: TaughtStore) -> None:
    created = store.create(_payload(["второго"]))
    store.update(created.id, __import__("app.models.schemas", fromlist=["TaughtAnswerUpdate"]).TaughtAnswerUpdate(enabled=False))
    assert store.match("сколько дают за второго ребенка") is None


def test_persistence_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "answers.json"
    first = TaughtStore(path, _chunks())
    first.create(_payload(["второго"]))
    second = TaughtStore(path, _chunks())
    assert len(second.all()) == 1
    assert second.match("сколько дают за второго ребенка") is not None


def test_empty_corpus_does_not_block_matching(tmp_path: Path) -> None:
    """Без корпуса судить о редкости не по чему — не блокируем (но источник всё равно проверяется)."""
    store = TaughtStore(tmp_path / "a.json", [])
    assert store.is_distinctive("ребенка") is True

# --- защита от перехвата чужих вопросов -------------------------------------


def test_service_bound_answer_requires_routed_service(store: TaughtStore) -> None:
    """Привязанный ответ не срабатывает, если вопрос не про эту услугу.

    Реальный баг: «какие документы нужны для получения паспорта» отдавало
    документы для пособия при рождении — только потому, что в вопросе была
    фраза «какие документы».
    """
    store.create(
        _payload(["какие документы"], question="какие документы для пособия")
        .model_copy(update={"service_id": "posobie-rozhdenie"})
    )
    # Вопрос распознан как вопрос другой услуги — не наше дело.
    assert store.match("какие документы нужны для паспорта", routed_services=["drugoe"]) is None
    # Маршрут пуст (вопрос вообще не про наши услуги) — тоже не срабатываем.
    assert store.match("какие документы нужны для паспорта") is None
    # А вопрос про свою услугу — срабатывает.
    assert store.match("какие документы", routed_services=["posobie-rozhdenie"]) is not None


def test_generic_keywords_without_service_never_trigger(store: TaughtStore) -> None:
    """Общий триггер без привязки к услуге не срабатывает вообще."""
    store.create(_payload(["какие документы", "где получить"]))
    assert store.match("какие документы нужны", routed_services=["chto-ugodno"]) is None


def test_keywords_are_generic_detection() -> None:
    assert TaughtStore.keywords_are_generic(["какие документы", "где получить"]) is True
    assert TaughtStore.keywords_are_generic(["какие документы", "погребение"]) is False
    assert TaughtStore.keywords_are_generic(["второго ребёнка"]) is False
    assert TaughtStore.keywords_are_generic([]) is True


def test_multiphrase_distinctiveness_uses_words_not_whole_string(store: TaughtStore) -> None:
    """Фраза «какие документы» не должна считаться редкой из-за стемминга целиком."""
    # Слов, которых в корпусе нет вовсе, недостаточно для «редкости»:
    # отсутствие слова — это не доказательство специфичности.
    assert store.is_distinctive("какие нужно") is False
