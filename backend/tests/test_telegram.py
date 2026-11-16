import asyncio
import json
from unittest.mock import AsyncMock

import httpx
from aiogram import Bot
from aiogram.types import File, Update

from app.bot.telegram import KnowledgeApi, build_dispatcher


def update(index: int, sender_id: int, **content) -> Update:
    return Update.model_validate({
        "update_id": index,
        "message": {
            "message_id": index,
            "date": 0,
            "from": {"id": sender_id, "is_bot": False, "first_name": "Test"},
            "chat": {"id": -100, "type": "supergroup"},
            **content,
        },
    })


def test_text_uses_authenticated_chat_and_keeps_sessions_separate(monkeypatch) -> None:
    request_bodies = []
    sent_texts = []

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/chat"
        assert request.headers["Authorization"] == "Bearer test-key"
        request_bodies.append(json.loads(request.content))
        return httpx.Response(200, json={
            "answer": "Ответ с символами < и &",
            "citations": [{
                "chunk_id": "test-law-1",
                "doc_title": "Тестовый акт",
                "location": "п. 1",
                "url": "https://example.invalid/law",
                "quote": "Цитата нормы.",
            }],
        })

    async def telegram_request(self, bot, method, timeout=None):
        sent_texts.append(method.text)

    monkeypatch.setattr("aiogram.client.session.aiohttp.AiohttpSession.make_request", telegram_request)

    async def run() -> None:
        async with (
            Bot("123456789:test") as bot,
            httpx.AsyncClient(
                base_url="http://api.test", headers={"Authorization": "Bearer test-key"},
                transport=httpx.MockTransport(handle),
            ) as client,
        ):
            dp = build_dispatcher(KnowledgeApi(client))
            await dp.feed_update(bot, update(1, 10, text="Первый вопрос"))
            await dp.feed_update(bot, update(2, 10, text="Уточнение"))
            await dp.feed_update(bot, update(3, 11, text="Другой вопрос"))

    asyncio.run(run())
    assert [body["message"] for body in request_bodies] == [
        "Первый вопрос", "Уточнение", "Другой вопрос",
    ]
    assert request_bodies[0]["session_id"] == request_bodies[1]["session_id"]
    assert request_bodies[0]["session_id"] != request_bodies[2]["session_id"]
    assert all(len(body["session_id"]) == 64 for body in request_bodies)
    assert sent_texts == [
        "Ответ с символами < и &\n\nИсточники\n1. Тестовый акт, п. 1\n"
        "«Цитата нормы.»\nhttps://example.invalid/law",
    ] * 3


def test_voice_preserves_text_when_synthesis_is_unavailable(monkeypatch) -> None:
    paths = []
    telegram_methods = []

    def handle(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.endswith("transcribe"):
            assert b'filename="audio.ogg"' in request.content
            assert b"audio-content" in request.content
            return httpx.Response(200, json={"text": "Распознанный вопрос"})
        if request.url.path.endswith("chat"):
            assert json.loads(request.content)["message"] == "Распознанный вопрос"
            return httpx.Response(200, json={"answer": "Ответ из базы", "citations": []})
        return httpx.Response(503)

    async def download(self, file_path, *, destination):
        assert file_path == "voice/audio.oga"
        destination.write(b"audio-content")

    async def run() -> None:
        async with (
            Bot("123456789:test") as bot,
            httpx.AsyncClient(
                base_url="http://api.test", transport=httpx.MockTransport(handle),
            ) as client,
        ):
            async def request(session, bot, method, timeout=None):
                telegram_methods.append(method.__api_method__)
                if method.__api_method__ == "sendMessage":
                    return update(1, 10, text=method.text).message.as_(bot)

            monkeypatch.setattr(type(bot.session), "make_request", request)
            monkeypatch.setattr(Bot, "get_file", AsyncMock(return_value=File(
                file_id="test", file_unique_id="test", file_path="voice/audio.oga",
            )))
            monkeypatch.setattr(Bot, "download_file", download)
            dp = build_dispatcher(KnowledgeApi(client))
            await dp.feed_update(bot, update(1, 10, voice={
                "file_id": "test", "file_unique_id": "test", "duration": 1,
            }))

    asyncio.run(run())
    assert paths == ["/api/v1/voice/transcribe", "/api/v1/chat", "/api/v1/voice/synthesize"]
    assert telegram_methods == ["sendMessage", "editMessageText", "sendMessage"]
