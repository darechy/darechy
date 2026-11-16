"""Telegram-бот на aiogram. Запуск: python -m app.bot.telegram."""

import asyncio
import hashlib
import hmac
import io
import logging
import secrets
from collections.abc import Iterator

import httpx
from aiogram import Bot, Dispatcher, F
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import CommandStart
from aiogram.types import BufferedInputFile, Message
from aiohttp import ClientError
from pydantic import ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.models.schemas import ChatRequest, ChatResponse

logger = logging.getLogger(__name__)

MAX_AUDIO_BYTES = 10 * 1024 * 1024
MAX_QUESTION_LENGTH = 4096
# Даже если все символы занимают два элемента UTF-16, сообщение укладывается в лимит.
MAX_REPLY_LENGTH = 2000


class TelegramSettings(BaseSettings):
    telegram_bot_token: str = ""
    telegram_api_key: str = ""
    telegram_api_base_url: str = "http://127.0.0.1:8000"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


class AudioTooLarge(ValueError):
    pass


class AudioBuffer(io.BytesIO):
    def write(self, data: bytes) -> int:
        if self.tell() + len(data) > MAX_AUDIO_BYTES:
            raise AudioTooLarge
        return super().write(data)


class KnowledgeApi:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client

    async def chat(self, question: str, session_id: str) -> ChatResponse:
        body = ChatRequest(message=question, session_id=session_id)
        response = await self.client.post("/api/v1/chat", json=body.model_dump(exclude_none=True))
        response.raise_for_status()
        return ChatResponse.model_validate_json(response.content)

    async def transcribe(self, data: bytes, name: str, content_type: str) -> str:
        response = await self.client.post(
            "/api/v1/voice/transcribe", files={"file": (name, data, content_type)},
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("text"), str):
            raise ValueError("Поле text должно быть строкой")
        return payload["text"].strip()

    async def synthesize(self, text: str) -> bytes:
        response = await self.client.post("/api/v1/voice/synthesize", json={"text": text, "lang": "ru"})
        response.raise_for_status()
        return response.content


def _format_answer(response: ChatResponse) -> str:
    lines = [response.answer.strip()]
    if response.citations:
        lines.extend(["", "Источники"])
        for index, citation in enumerate(response.citations, start=1):
            reference = ", ".join(filter(None, (
                citation.doc_title, citation.doc_number, citation.doc_date, citation.location,
            )))
            lines.append(f"{index}. {reference}")
            if citation.quote:
                lines.append(f"«{citation.quote}»")
            if citation.url:
                lines.append(citation.url)
    return "\n".join(lines)


def _split_message(text: str) -> Iterator[str]:
    while len(text) > MAX_REPLY_LENGTH:
        split_at = text.rfind("\n", 0, MAX_REPLY_LENGTH)
        if split_at <= 0:
            split_at = text.rfind(" ", 0, MAX_REPLY_LENGTH)
        if split_at <= 0:
            split_at = MAX_REPLY_LENGTH
        yield text[:split_at]
        text = text[split_at:].lstrip()
    if text:
        yield text


def build_dispatcher(api: KnowledgeApi) -> Dispatcher:
    dp = Dispatcher()
    session_secret = secrets.token_bytes(32)

    @dp.message(CommandStart())
    async def on_start(message: Message) -> None:
        await message.answer(
            "Привет! Я «ДаРэчы». Спросите о государственных услугах Беларуси "
            "текстом или голосовым сообщением. Под ответом будут ссылки на источники."
        )

    async def answer_question(message: Message, question: str, *, voice_reply: bool = False) -> None:
        question = question.strip()
        if not question:
            return
        if len(question) > MAX_QUESTION_LENGTH:
            await message.answer("Сократите вопрос до 4096 символов и отправьте его ещё раз.")
            return

        sender = message.from_user or message.sender_chat or message.chat
        identity = f"{message.chat.id}:{message.message_thread_id or 0}:{sender.id}"
        session_id = hmac.new(session_secret, identity.encode(), hashlib.sha256).hexdigest()
        try:
            response = await api.chat(question, session_id)
        except (httpx.HTTPError, ValidationError):
            logger.warning("База знаний недоступна")
            await message.answer("База знаний сейчас недоступна. Попробуйте чуть позже.")
            return

        for part in _split_message(_format_answer(response)):
            await message.answer(part)
        if voice_reply and response.answer and not response.refusal:
            try:
                audio = await api.synthesize(response.answer)
                if audio:
                    await message.answer_voice(BufferedInputFile(audio, filename="answer.ogg"))
            except (httpx.HTTPError, TelegramAPIError):
                logger.info("Озвучивание ответа недоступно")

    @dp.message(F.text & ~F.text.startswith("/"))
    async def on_text(message: Message) -> None:
        if message.text:
            await answer_question(message, message.text)

    @dp.message(F.voice | F.audio)
    async def on_voice(message: Message, bot: Bot) -> None:
        attachment = message.voice or message.audio
        if attachment is None:
            return
        too_large = "Аудио должно быть не больше 10 МБ. Отправьте короткую запись или напишите вопрос."
        if (attachment.file_size or 0) > MAX_AUDIO_BYTES:
            await message.answer(too_large)
            return
        status = await message.answer("Распознаю запись…")
        try:
            remote = await bot.get_file(attachment.file_id)
            if remote.file_path is None:
                await status.edit_text("Файл недоступен. Напишите вопрос текстом.")
                return
            with AudioBuffer() as buffer:
                await bot.download_file(remote.file_path, destination=buffer)
                suffix = remote.file_path.rsplit(".", 1)[-1].lower()
                if suffix == "oga":
                    suffix = "ogg"
                name = f"audio.{suffix}" if suffix.isalnum() and len(suffix) <= 8 else "audio.ogg"
                text = await api.transcribe(
                    buffer.getvalue(), name, attachment.mime_type or "application/octet-stream",
                )
        except AudioTooLarge:
            await status.edit_text(too_large)
            return
        except (httpx.HTTPError, TelegramAPIError, ClientError, TimeoutError, ValueError):
            await status.edit_text("Не удалось распознать аудио. Напишите вопрос текстом.")
            return
        if not text:
            await status.edit_text("На записи не удалось разобрать речь. Напишите вопрос текстом.")
            return
        await status.edit_text("Запись распознана. Ищу ответ…")
        await answer_question(message, text, voice_reply=True)

    return dp


async def run(settings: TelegramSettings) -> None:
    if not settings.telegram_bot_token or not settings.telegram_api_key:
        raise SystemExit("Укажите TELEGRAM_BOT_TOKEN и TELEGRAM_API_KEY в окружении или backend/.env.")
    base_url = httpx.URL(settings.telegram_api_base_url)
    if base_url.scheme not in {"http", "https"} or not base_url.host:
        raise SystemExit("TELEGRAM_API_BASE_URL должен быть полным HTTP(S) URL.")
    async with (
        Bot(token=settings.telegram_bot_token) as bot,
        httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {settings.telegram_api_key}"},
            timeout=httpx.Timeout(90, connect=5),
            trust_env=False,
        ) as client,
    ):
        dispatcher = build_dispatcher(KnowledgeApi(client))
        await dispatcher.start_polling(
            bot, allowed_updates=dispatcher.resolve_used_update_types(), close_bot_session=False,
        )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    asyncio.run(run(TelegramSettings()))
