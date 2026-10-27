"""Тесты памяти диалога: уточнения («а какие документы?») с контекстом."""

from app.core.config import Settings
from tests.fakes import FakeLlm, make_chunk
from tests.fakes_store import make_store

SETTINGS = Settings(use_qdrant=False)


def _fake_store():
    store = make_store()
    store.rebuild(
        [
            make_chunk("act1_c01", "Единовременное пособие назначается при рождении ребёнка."),
            make_chunk("act1_c02", "Документы: заявление, паспорт, свидетельство о рождении."),
        ]
    )
    return store


class RecordingLlm(FakeLlm):
    """LLM-двойка, которая всегда отвечает с маркером чанка."""

    def chat(self, system_prompt: str, user_prompt: str) -> str | None:
        super().chat(system_prompt, user_prompt)
        return "Отвечаю по акту [act1_c02]."


def test_followup_question_extends_query():
    """«а какие документы?» после вопроса про пособие ищет пособие, а не «документы»."""
    from app.chat.orchestrator import Orchestrator
    from app.models.schemas import ChatRequest

    llm = RecordingLlm()
    orchestrator = Orchestrator(settings=SETTINGS, store=_fake_store(), llm=llm)
    session = "test-session-1"

    orchestrator.answer(ChatRequest(message="какое пособие при рождении ребёнка", session_id=session))
    orchestrator.answer(ChatRequest(message="а какие документы нужны", session_id=session))

    # Второй запрос к поиску должен содержать и первую тему, и уточнение.
    user_prompt = llm.chat_calls[-1][1]
    assert "Предыдущий диалог" in user_prompt
    assert "пособие при рождении" in user_prompt or "а какие документы нужны" in user_prompt


def test_history_passed_to_llm_prompt():
    from app.chat.orchestrator import Orchestrator
    from app.models.schemas import ChatRequest

    llm = RecordingLlm()
    orchestrator = Orchestrator(settings=SETTINGS, store=_fake_store(), llm=llm)
    session = "test-session-2"

    orchestrator.answer(ChatRequest(message="какое пособие при рождении ребёнка", session_id=session))
    assert "Предыдущий диалог" not in llm.chat_calls[0][1]  # у первого вопроса истории нет
    orchestrator.answer(ChatRequest(message="какие документы нужны", session_id=session))
    assert "Пользователь: какое пособие при рождении ребёнка" in llm.chat_calls[1][1]


def test_history_isolated_by_session():
    from app.chat.orchestrator import Orchestrator
    from app.models.schemas import ChatRequest

    llm = RecordingLlm()
    orchestrator = Orchestrator(settings=SETTINGS, store=_fake_store(), llm=llm)

    orchestrator.answer(ChatRequest(message="какое пособие при рождении ребёнка", session_id="A"))
    orchestrator.answer(ChatRequest(message="какие документы нужны", session_id="B"))

    assert "Пользователь: какое пособие" not in llm.chat_calls[1][1]


def test_session_memory_bounds():
    from app.chat.sessions import SessionMemory

    memory = SessionMemory(max_turns=4, max_sessions=2)
    for i in range(10):
        memory.append("s", "user", f"msg {i}")
    history = memory.history("s")
    assert len(history) == 4
    assert history[-1]["content"] == "msg 9"

    memory.append("t1", "user", "x")
    memory.append("t2", "user", "y")
    memory.append("t3", "user", "z")  # LRU вытесняет t1
    assert memory.history("t1") == []
    assert memory.history("t3") != []
