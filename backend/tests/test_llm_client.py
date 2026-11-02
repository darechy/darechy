"""Тесты клиента Ollama: доступность модели vs доступность сервиса.

`ping()` отвечает на вопрос «Ollama запущена», а не «модель скачана».
Разница критичная: сервис жив, а модель не скачана — и пользователь получает
экстрактивный ответ, недоумевая, почему «модель недоступна».
"""

import httpx

from app.core.config import Settings
from app.llm.client import LlmClient


def _client(**overrides) -> LlmClient:
    return LlmClient(Settings(**overrides))


class _Resp:
    def __init__(self, payload, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=None)  # type: ignore[arg-type]


def test_available_models_lists_downloaded(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.llm.client.httpx.get",
        lambda url, timeout=None: _Resp({"models": [{"name": "bge-m3"}, {"name": "qwen3:1.7b"}]}),
    )
    assert _client().available_models() == ["bge-m3", "qwen3:1.7b"]


def test_available_models_empty_when_service_down(monkeypatch) -> None:
    def boom(url, timeout=None):
        raise httpx.ConnectError("нет соединения")

    monkeypatch.setattr("app.llm.client.httpx.get", boom)
    assert _client().available_models() == []
    assert _client().ping() is False
    assert _client().chat_ready() is False


def test_chat_ready_true_when_model_present(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.llm.client.httpx.get",
        lambda url, timeout=None: _Resp({"models": [{"name": "qwen3:1.7b"}]}),
    )
    client = _client()
    assert client.chat_ready() is True


def test_ping_true_but_chat_not_ready_when_model_missing(monkeypatch) -> None:
    """Сервис жив, модели нет — честная разница между ping и chat_ready."""
    monkeypatch.setattr(
        "app.llm.client.httpx.get",
        lambda url, timeout=None: _Resp({"models": [{"name": "bge-m3"}]}),
    )
    client = _client()
    assert client.ping() is True
    assert client.chat_ready() is False


def test_chat_sends_keep_alive_and_disables_thinking(monkeypatch) -> None:
    """keep_alive и think=False уезжают в запрос — это и есть управление памятью."""
    sent: dict = {}

    def fake_post(url, json=None, timeout=None):
        sent.update(json or {})
        return _Resp({"message": {"content": "ответ [chunk_1]"}})

    monkeypatch.setattr("app.llm.client.httpx.post", fake_post)
    client = _client(chat_keep_alive="30s")
    assert client.chat("система", "вопрос") == "ответ [chunk_1]"
    assert sent["think"] is False
    assert sent["keep_alive"] == "30s"
    assert sent["model"] == client.chat_model


def test_embed_batch_sends_keep_alive(monkeypatch) -> None:
    sent: dict = {}

    def fake_post(url, json=None, timeout=None):
        sent.update(json or {})
        return _Resp({"embeddings": [[0.1, 0.2]]})

    monkeypatch.setattr("app.llm.client.httpx.post", fake_post)
    client = _client(embed_keep_alive="5m")
    assert client.embed_batch(["текст"]) == [[0.1, 0.2]]
    assert sent["keep_alive"] == "5m"
    assert sent["input"] == ["текст"]


def test_embed_batch_rejects_short_response(monkeypatch) -> None:
    """Число векторов не совпало с числом текстов — лучше None, чем сдвиг."""
    monkeypatch.setattr(
        "app.llm.client.httpx.post",
        lambda url, json=None, timeout=None: _Resp({"embeddings": [[0.1]]}),
    )
    assert _client().embed_batch(["a", "b"]) is None


# --- маркеры ссылок в ответе модели -----------------------------------------
# Регресс на реальный баг: id пунктов содержат кириллицу («п-1» из «пункт 1»),
# а класс символов в регекспе её не включал. Маркеры терялись молча, и цитата
# подставлялась «лучшим чанком» — то есть не та статья, о которой модель
# говорила. Тесты фиксируют, что кириллица в маркерах читается.


def test_markers_with_cyrillic_are_extracted() -> None:
    from app.chat.orchestrator import _extract_markers

    assert _extract_markers("Пособие [7-Z_st-12-п-1] лицам.") == ["7-Z_st-12-п-1"]


def test_multiple_markers_and_no_markers() -> None:
    from app.chat.orchestrator import _extract_markers

    assert _extract_markers("[7-Z_st-11-п-2] и [7-Z_st-13-п-9]") == [
        "7-Z_st-11-п-2",
        "7-Z_st-13-п-9",
    ]
    assert _extract_markers("ответ без ссылок") == []


def test_markers_are_stripped_from_user_visible_answer() -> None:
    from app.chat.orchestrator import _strip_markers

    cleaned = _strip_markers("Пособие назначается [7-Z_st-12-п-1] лицам.")
    assert "[" not in cleaned and "7-Z" not in cleaned
    assert "Пособие назначается" in cleaned
