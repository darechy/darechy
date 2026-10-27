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
    response = orch.answer(ChatRequest(message="как выиграть в лотерею?"))
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
    assert "дословно из нормативного акта" in response.answer
    assert len(response.citations) == 1


def test_unreferenced_marker_falls_back_to_top_chunk(env) -> None:
    """Модель не поставила маркеры → цитируем лучший чанк, а не верим на слово."""
    settings, store = env
    llm = FakeLlm(chat_script=["Пособие назначается с даты обращения."])
    orch = Orchestrator(settings, store, llm)
    response = orch.answer(ChatRequest(message="когда назначается пособие?"))
    assert response.citations[0].chunk_id == "a_p2" or response.citations[0].chunk_id == "a_p1"
