"""Тесты RAG-оркестратора: цитаты, guardrail-отказ, деградация без LLM."""

import pytest

from app.chat.orchestrator import REFUSAL_TEMPLATE, Orchestrator
from app.core.config import Settings
from app.models.schemas import ChatRequest
from tests.fakes import FakeLlm
from tests.test_retrieval import _store_with_chunks


@pytest.fixture()
def env():
    settings = Settings(app_env="test")
    store = _store_with_chunks()
    return settings, store


def test_answer_with_citations(env) -> None:
    settings, store = env
    llm = FakeLlm(
        chat_script=[
            "Размер пособия определяется базовой величиной [a_p2]."
        ]
    )
    orch = Orchestrator(settings, store, llm)
    response = orch.answer(ChatRequest(message="какой размер пособия?"))
    assert not response.refusal
    assert response.answer == "Размер пособия определяется базовой величиной."
    assert len(response.citations) == 1
    citation = response.citations[0]
    assert citation.chunk_id == "a_p2"
    assert citation.doc_title == "Тестовый акт"
    assert citation.quote.startswith("Размер пособия")


def test_refusal_when_no_relevant_chunks(env) -> None:
    settings, store = env
    orch = Orchestrator(settings, store, FakeLlm())
    response = orch.answer(ChatRequest(message="Какой закон регулирует добычу полезных ископаемых на Марсе?"))
    assert response.refusal
    assert response.answer == REFUSAL_TEMPLATE
    assert response.citations == []


def test_llm_refusal_keyword_maps_to_refusal(env) -> None:
    settings, store = env
    llm = FakeLlm(chat_script=["ОТКАЗ"])
    orch = Orchestrator(settings, store, llm)
    response = orch.answer(ChatRequest(message="какой размер пособия?"))
    assert response.refusal


def test_trailing_refusal_marker_is_stripped_not_shown(env) -> None:
    """Модель 1.7b дописывает ОТКАЗ после верного абзаца — маркер не должен
    попадать пользователю, а ответ обязан остаться."""
    settings, store = env
    llm = FakeLlm(
        chat_script=["Пособие назначается с даты обращения [a_p1].\n\nОТКАЗ"]
    )
    orch = Orchestrator(settings, store, llm)
    response = orch.answer(ChatRequest(message="когда назначается пособие?"))
    assert not response.refusal
    assert "ОТКАЗ" not in response.answer
    assert "Пособие назначается с даты обращения" in response.answer


def test_refusal_marker_alone_still_refuses(env) -> None:
    """Если кроме маркера ничего нет — это настоящий отказ, а не ответ."""
    settings, store = env
    llm = FakeLlm(chat_script=["ОТКАЗ."])
    orch = Orchestrator(settings, store, llm)
    response = orch.answer(ChatRequest(message="какой размер пособия?"))
    assert response.refusal
    assert response.answer == REFUSAL_TEMPLATE
    assert response.citations == []


def test_extractive_fallback_when_llm_down(env) -> None:
    """LLM недоступна → дословная цитата лучшего чанка, отказ-лжи быть не должно."""
    settings, store = env
    orch = Orchestrator(settings, store, FakeLlm())  # chat_script пуст → None
    response = orch.answer(ChatRequest(message="размер пособия базовая величина"))
    assert not response.refusal
    assert "Размер пособия определяется базовой величиной" in response.answer
    assert len(response.citations) == 1


def test_unreferenced_marker_falls_back_to_top_chunk(env) -> None:
    """Модель не поставила маркеры → цитируем лучший чанк, а не верим на слово."""
    settings, store = env
    llm = FakeLlm(chat_script=["Пособие назначается с даты обращения."])
    orch = Orchestrator(settings, store, llm)
    response = orch.answer(ChatRequest(message="когда назначается пособие?"))
    assert response.citations[0].chunk_id == "a_p2" or response.citations[0].chunk_id == "a_p1"


# --- ответ про документы из карточки услуги ---------------------------------


def _service(sid: str, title: str, *, checklist=None, authority="", cost="", point=None):
    from app.models.schemas import ChecklistItem, Service, Source

    return Service(
        id=sid,
        title=title,
        situation="registraciya-aktov",
        summary=title,
        who="",
        amount="",
        deadline="",
        authority=authority,
        cost=cost,
        checklist=[ChecklistItem(item=i, where="") for i in (checklist or [])],
        sources=[Source(title="Указ № 200, позиция 5.2", point=point)] if point else [],
        keywords=["регистрация брака"],
    )


def test_documents_question_answered_from_checklist(env) -> None:
    """Список документов берётся из карточки услуги, а не из пересказа модели.

    Реальный случай: на «какие документы нужны для регистрации брака» модель
    отдала одну строку из перечня вместо всего списка. Чек-лист услуги собран
    из того же пункта акта, поэтому ответ ассистента и портал не расходятся.
    """
    from app.chat.router import ServiceRouter

    settings, store = env
    service = _service(
        "p5-2-brak",
        "Регистрация заключения брака",
        checklist=["совместное заявление", "паспорта лиц, вступающих в брак"],
        authority="орган загса",
        cost="бесплатно",
        point="a_p1",
    )
    router = ServiceRouter([service])
    llm = FakeLlm(chat_script=["этого не должно быть в ответе"])
    orch = Orchestrator(settings, store, llm, router=router)

    response = orch.answer(ChatRequest(message="какие документы нужны для регистрации брака"))
    assert response.source == "checklist"
    assert "совместное заявление" in response.answer
    assert "паспорта лиц, вступающих в брак" in response.answer
    assert "орган загса" in response.answer
    assert response.citations, "ответ обязан ссылаться на пункт акта"
    assert llm.chat_calls == [], "для списка документов модель не нужна"


def test_documents_question_without_service_asks_to_clarify(env) -> None:
    """Услуга не распознана — уточняем, а не угадываем перечень."""
    from app.chat.router import ServiceRouter

    settings, store = env
    router = ServiceRouter([_service("p5-2-brak", "Регистрация заключения брака",
                                     checklist=["заявление"], point="a_p1")])
    orch = Orchestrator(settings, store, FakeLlm(), router=router)
    response = orch.answer(ChatRequest(message="какие документы нужны для получения паспорта"))
    assert response.source == "clarify"
    assert "Уточните" in response.answer


def test_checklist_answer_disabled_falls_back(env) -> None:
    """Флаг выключен — работает обычный путь, ответ не ломается."""
    from app.chat.router import ServiceRouter

    settings, store = env
    settings.checklist_answers = False
    router = ServiceRouter([_service("p5-2-brak", "Регистрация заключения брака",
                                     checklist=["заявление"], point="a_p1")])
    orch = Orchestrator(settings, store, FakeLlm(chat_script=["обычный ответ [a_p1]"]),
                        router=router)
    response = orch.answer(ChatRequest(message="какие документы нужны для регистрации брака"))
    assert response.source != "checklist"


@pytest.mark.parametrize("message", [
    "У меня умерла кошка. Что делать?",
    "Меня уволили, мне очень тяжело",
    "Я потерял маму, мне одиноко",
])
def test_personal_distress_bypasses_retrieval(env, monkeypatch, message):
    settings, store = env
    def unexpected_search(*args, **kwargs):
        pytest.fail("Personal support should not retrieve legal documents")
    monkeypatch.setattr(store, "search", unexpected_search)
    orch = Orchestrator(settings, store, FakeLlm(chat_script=["Мне очень жаль. Расскажите, чем помочь."]))
    response = orch.answer(ChatRequest(message=message, session_id="support"))
    assert response.source == "conversation"
    assert response.suggested_services == []
    assert not response.refusal
    assert orch.sessions.history("support")[-1]["content"] == response.answer


def test_distress_with_legal_question_keeps_citations(env):
    settings, store = env
    orch = Orchestrator(settings, store, FakeLlm(
        chat_script=["Размер пособия определяется базовой величиной [a_p2]."]
    ))
    response = orch.answer(ChatRequest(message="У меня умер муж, какой размер пособия?"))
    assert response.answer.startswith("Мне жаль")
    assert response.citations
